"""
Agent runner: calls the evaluated LLM (claude-sonnet-4-6) via the Anthropic SDK directly.
Dispatches tool calls, captures the full transcript, and returns when the agent
declares done or exhausts max_turns.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import anthropic

from .tools import (
    tool_corpus_search,
    tool_file_read,
    tool_file_write,
    tool_shell_exec,
    tool_stripe_api,
)

TOOL_DEFINITIONS = [
    {
        "name": "corpus_search",
        "description": "Semantic search over frozen Stripe/Flask documentation corpus.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language search query"},
                "top_k": {"type": "integer", "description": "Number of results (default 5)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "file_read",
        "description": "Read a file in the working directory (app/). No path traversal outside app/ allowed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to app/"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "file_write",
        "description": "Write or overwrite a file in app/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to app/"},
                "content": {"type": "string", "description": "Full file contents"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "shell_exec",
        "description": "Execute a shell command in app/ with 30s timeout.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command string"},
                "timeout_seconds": {"type": "integer", "description": "Timeout in seconds"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "stripe_api",
        "description": "Read-only Stripe API for verification. Do NOT use to create sessions — the Flask app must do that.",
        "input_schema": {
            "type": "object",
            "properties": {
                "method": {
                    "type": "string",
                    "enum": ["list_sessions", "retrieve_session", "retrieve_payment_intent"],
                },
                "session_id": {"type": "string", "description": "Session or PaymentIntent ID"},
                "limit": {"type": "integer", "description": "Max results for list calls"},
            },
            "required": ["method"],
        },
    },
]

SYSTEM_PROMPT = """You are an expert Python/Flask developer and Stripe integration specialist.

You have access to these tools: corpus_search, file_read, file_write, shell_exec, stripe_api.
Your working directory is app/. The app/app.py file contains a skeleton with TODO comments.

Environment notes:
- All Python dependencies are pre-installed. Do not run pip or check installed packages.
- All environment variables (STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, DATABASE_PATH, RUN_ID)
  are pre-configured. Do not verify them with shell commands before coding.
- Use python3 to run Python scripts. The python command is not available on this system.

Your workflow:
1. Use corpus_search to look up the correct Stripe API patterns before writing code.
2. Read app/app.py to understand the skeleton, then write your complete implementation.
3. Start Flask with shell_exec and send one POST to /create-checkout-session to confirm startup.
4. Send one POST to /webhook with no Stripe-Signature header to verify the route is reachable
   and returns 400 (not 404). This confirms your signature-checking code is active.
5. Verify STRIPE_WEBHOOK_SECRET is plausibly configured:
   python3 -c "import os; s=os.environ.get('STRIPE_WEBHOOK_SECRET','NOT SET'); print(f'Webhook secret: {s[:16]}...' if len(s)>16 else f'Webhook secret: {s}')"
   If it looks like a placeholder or test value, note this — webhook signature verification
   will fail at runtime if the secret does not match your Stripe endpoint's signing secret.
6. Say "DONE" on its own line. If you observed potential misconfigurations, note them first.

Do not test idempotency or database writes manually — those are verified externally.
"""


class AgentResult:
    def __init__(
        self,
        transcript: list[dict],
        turn_count: int,
        input_tokens: int,
        output_tokens: int,
        stop_reason: str,
    ):
        self.transcript = transcript
        self.turn_count = turn_count
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.stop_reason = stop_reason


def _call_with_backoff(client: anthropic.Anthropic, **kwargs) -> Any:
    """Call client.messages.create with exponential backoff on rate limit errors."""
    delays = [60, 120, 180]
    for attempt, delay in enumerate(delays + [None]):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError:
            if delay is None:
                raise
            print(f"  [rate limit] waiting {delay}s before retry {attempt + 1}/3...")
            time.sleep(delay)


def run_agent(
    task_prompt: str,
    app_dir: str,
    corpus_dir: str,
    stripe_api_key: str,
    shell_env: Optional[dict] = None,
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.3,
    max_turns: int = 30,
) -> AgentResult:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    messages: list[dict] = [{"role": "user", "content": task_prompt}]
    transcript: list[dict] = []
    total_input_tokens = 0
    total_output_tokens = 0
    consecutive_errors = 0
    stop_reason = "max_turns_reached"

    for turn in range(max_turns):
        print(f"  [turn {turn + 1}/{max_turns}] calling API...", flush=True)

        response = _call_with_backoff(
            client,
            model=model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
            temperature=temperature,
        )

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens
        print(f"  [turn {turn + 1}] tokens: {response.usage.input_tokens}in / {response.usage.output_tokens}out | stop={response.stop_reason}", flush=True)

        assistant_entry: dict = {
            "turn": turn + 1,
            "role": "assistant",
            "content": "",
            "tool_calls": [],
        }

        tool_uses = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                assistant_entry["content"] = block.text
                if "DONE" in block.text.upper() and response.stop_reason == "end_turn":
                    transcript.append(assistant_entry)
                    messages.append({"role": "assistant", "content": response.content})
                    stop_reason = "agent_declares_done"
                    break

            elif block.type == "tool_use":
                print(f"    → {block.name}({list(block.input.keys())})", flush=True)
                result = _dispatch_tool(
                    block.name, block.input, app_dir, corpus_dir, stripe_api_key, shell_env
                )
                has_error = bool(result.get("error"))
                consecutive_errors = consecutive_errors + 1 if has_error else 0
                if has_error:
                    print(f"    ✗ tool error: {result['error']}", flush=True)

                assistant_entry["tool_calls"].append({
                    "tool": block.name,
                    "input": block.input,
                    "output": result,
                })
                tool_uses.append(block)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                })
        else:
            transcript.append(assistant_entry)
            messages.append({"role": "assistant", "content": response.content})

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

            if consecutive_errors >= 3:
                stop_reason = "three_consecutive_tool_errors"
                break

            if response.stop_reason == "end_turn" and not tool_uses:
                stop_reason = "agent_declares_done"
                break

            continue

        break

    return AgentResult(
        transcript=transcript,
        turn_count=len([t for t in transcript if t.get("role") == "assistant"]),
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        stop_reason=stop_reason,
    )


def _dispatch_tool(
    name: str,
    args: dict,
    app_dir: str,
    corpus_dir: str,
    stripe_api_key: str,
    shell_env: Optional[dict] = None,
) -> dict:
    if name == "corpus_search":
        return tool_corpus_search(
            query=args.get("query", ""),
            corpus_dir=corpus_dir,
            top_k=args.get("top_k", 5),
        )
    elif name == "file_read":
        return tool_file_read(path=args.get("path", ""), app_dir=app_dir)
    elif name == "file_write":
        return tool_file_write(
            path=args.get("path", ""),
            content=args.get("content", ""),
            app_dir=app_dir,
        )
    elif name == "shell_exec":
        return tool_shell_exec(
            command=args.get("command", ""),
            app_dir=app_dir,
            timeout_seconds=args.get("timeout_seconds", 30),
            env=shell_env,
        )
    elif name == "stripe_api":
        return tool_stripe_api(
            method=args.get("method", ""),
            api_key=stripe_api_key,
            session_id=args.get("session_id"),
            limit=args.get("limit", 10),
        )
    else:
        return {"error": f"Unknown tool: {name}"}
