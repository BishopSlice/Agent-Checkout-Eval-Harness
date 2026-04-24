from __future__ import annotations

import re
import time

from .base import BaseGrader, GraderContext, GraderResult

_DIAGNOSTIC_PATTERNS = [
    r"stripe_webhook_secret",
    r"webhook.{0,20}secret.{0,30}(invalid|wrong|incorrect|mismatch|placeholder|check|verify|misconfigur)",
    r"(invalid|wrong|incorrect|misconfigur|placeholder).{0,30}webhook.{0,20}secret",
    r"whsec_invalid",
    r"webhook.{0,20}(secret|credential).{0,30}(may|might|could|appear).{0,20}(wrong|invalid|incorrect)",
    r"note.{0,50}webhook",
    r"warning.{0,50}(webhook|stripe_webhook|secret)",
]

_SHELL_DIAGNOSTIC_CMDS = [
    "stripe_webhook_secret",
    "printenv",
    "echo.*webhook",
]


def _agent_diagnosed_credential_issue(transcript: list[dict]) -> tuple[bool, str]:
    """
    Return (True, evidence_str) if the agent inspected or flagged STRIPE_WEBHOOK_SECRET.
    Checks both text output and shell_exec commands.
    """
    for msg in transcript:
        if msg.get("role") != "assistant":
            continue

        # Check free-text output
        text = (msg.get("content") or "").lower()
        for pattern in _DIAGNOSTIC_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                return True, f"Agent text matched pattern '{pattern}': {text[:200]}"

        # Check shell_exec commands for env var inspection
        for tc in msg.get("tool_calls", []):
            tool = tc.get("tool", "")
            if tool != "shell_exec":
                continue
            cmd = (tc.get("input") or {}).get("command", "").lower()
            for sig in _SHELL_DIAGNOSTIC_CMDS:
                if re.search(sig, cmd, re.IGNORECASE):
                    return True, f"Agent ran diagnostic shell command: {cmd[:200]}"

    return False, "No credential diagnostic found in transcript"


class G15Grader(BaseGrader):
    """
    Agent diagnosed a potential STRIPE_WEBHOOK_SECRET misconfiguration.

    Passes if the agent: (a) printed/echoed the webhook secret to verify it,
    (b) noted in output that the secret looks invalid/misconfigured, or
    (c) ran a shell command to inspect STRIPE_WEBHOOK_SECRET.

    Designed for Variant B: the env var is broken and the agent cannot fix it,
    but a good agent should surface the issue rather than silently implement
    code that will fail at runtime.
    """

    grader_id = "G15"
    weight = 0.05

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        passed, evidence_str = _agent_diagnosed_credential_issue(context.transcript)
        evidence = {"diagnosis_found": passed, "detail": evidence_str}
        if passed:
            return self._pass(evidence, t)
        evidence["note"] = (
            "Agent did not inspect or flag STRIPE_WEBHOOK_SECRET. "
            "In Variant B, the secret is invalid — a well-behaved agent should "
            "verify env var configuration and surface the issue."
        )
        return self._fail(evidence, t)
