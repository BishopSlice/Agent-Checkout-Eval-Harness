# CLAUDE.md — Project Context for Claude Code

## What this project is

An eval harness that measures whether a Claude agent (`claude-haiku-4-5-20251001` by default)
can correctly implement a Stripe Checkout Session + webhook fulfillment flow in a minimal Flask
app — from a skeleton with TODOs. Built as a PM portfolio artifact demonstrating Anthropic eval
methodology: tasks → trials → graders → transcripts → score movement.

---

## Key files

| File | Role |
|------|------|
| `app/app.py` | Flask skeleton the agent starts from (TODOs only, not a solution) |
| `reference/app.py` | Hand-written correct implementation — written by the user, never by Claude |
| `tasks/stripe_checkout.yaml` | Machine-readable spec: variants, grader weights, task prompts |
| `harness/runner.py` | Orchestrates one trial: setup → agent → Flask → grade → report |
| `harness/agent.py` | Anthropic SDK wrapper, tool dispatch, transcript capture |
| `harness/tools.py` | Tool implementations: corpus_search, file_read, file_write, shell_exec, stripe_api |
| `harness/flask_manager.py` | Flask subprocess lifecycle (start/health-check/stop) |
| `harness/graders/` | G1–G8 deterministic, G12–G14 transcript graders |
| `scripts/run_trial.py` | CLI: run one trial |
| `scripts/run_baseline.py` | CLI: run N trials, print aggregate stats |
| `scripts/fetch_corpus.py` | Writes frozen corpus docs to `/corpus/` — no web fetching |
| `corpus/` | Frozen .txt files the agent can search. Never re-fetched during trials. |
| `trials/{label}/` | Named run sets (e.g. `baseline_v2/`, `post_iter_v1/`). `report.json` committed; `transcript.jsonl` and `agent_app.py` gitignored. |

---

## Active graders and weights

| Grader | Name | Type | Weight |
|--------|------|------|--------|
| G1 | checkout_session_exists | deterministic | 0.12 |
| G2 | line_items_correct | deterministic | 0.10 |
| G3 | urls_correct | deterministic | 0.07 |
| G4 | customer_email_collected | deterministic | 0.05 |
| G5 | webhook_route_exists | deterministic | 0.04 |
| G6a | webhook_rejects_bad_sig | deterministic | 0.07 |
| G6b | webhook_accepts_valid | deterministic | 0.07 |
| G6c | webhook_ignores_unknown | deterministic | 0.04 |
| G6d | webhook_rejects_missing_sig | deterministic | 0.03 |
| G7 | db_write_on_event | deterministic | 0.10 |
| G8 | idempotency | deterministic | 0.08 |
| G12 | doc_retrieval_behavior | transcript | 0.05 |
| G13 | self_verification_behavior | transcript | 0.07 |
| G14 | turn_count | metric | 0.03 |
| G15 | env_credential_diagnosis | transcript | 0.05 |

Score = sum(score × weight) / sum(active weights). Pass threshold: 0.75.
G9–G11 (LLM-as-judge) are stretch goals — not yet implemented.

---

## Eval integrity rules — never violate these

- **Never write to `reference/app.py`** via file tools. It is the ground truth, written by the user only.
- **Never add Stripe implementation hints to the task prompt** (`tasks/stripe_checkout.yaml`). The agent must derive patterns from the corpus, not the prompt.
- **Never add implementation hints to `app/app.py` skeleton docstrings.** Docstrings must use generic descriptions only. Forbidden: `request.data`, `INSERT OR IGNORE`, `client_reference_id`, "idempotent". These patterns must be discovered via corpus search, not read from the skeleton.
- **System prompt** (`harness/agent.py: SYSTEM_PROMPT`) controls operational behavior only (tool usage order, when to stop). It must not contain implementation knowledge.
- **Corpus** (`corpus/`) is frozen. Do not re-fetch or modify during trials.

---

## Known architecture decisions (and why)

**Anthropic SDK, not LiteLLM**
LiteLLM failed to handle rate limit errors correctly during initial trials. Replaced with direct
`anthropic` SDK usage with manual exponential backoff (`_call_with_backoff` in agent.py).

**Hardcoded corpus, not web-scraped**
`fetch_corpus.py` originally used `requests` + `BeautifulSoup`. Stripe docs are JS-rendered —
BeautifulSoup returned shell HTML with no useful content. Replaced with curated Python strings
embedded directly in the script and written to `/corpus/*.txt`.

**shell_env threading**
`RUN_ID` must reach the Flask subprocess so the agent's `/create-checkout-session` calls set
`client_reference_id` correctly. The env flows: `runner.py:agent_env` → `run_agent(shell_env=)`
→ `_dispatch_tool()` → `tool_shell_exec(env=)` → `subprocess.run(env=merged_env)`.

**Harness triggers checkout independently**
Even if the agent triggers `/create-checkout-session` during self-testing, the harness calls it
again inside the `FlaskManager` context before graders run (`_trigger_checkout_session()`).
This guarantees G1 always has a real Stripe session with the correct `run_id` to find.

**Haiku for trials, Sonnet for development**
Default model is `claude-haiku-4-5-20251001` (~$0.45/trial vs ~$1.66 for Sonnet).
Pass `--model claude-sonnet-4-6` explicitly when evaluating Sonnet specifically.

---

## System prompt design constraint

The system prompt (`harness/agent.py: SYSTEM_PROMPT`) controls operational behavior only.

Current tightening applied:
- "All dependencies pre-installed — do not run pip or check packages"
- Explicit workflow: corpus search → read skeleton → write → start Flask → one POST to
  /create-checkout-session → test /webhook returns 400 (not 404) → verify STRIPE_WEBHOOK_SECRET
  prefix looks valid → declare DONE
- "Do not test idempotency or database writes manually — those are verified externally"

Post-iteration change (variant_b_post_iter): added step 4 (POST /webhook no-sig → 400 check)
and step 5 (print first 16 chars of STRIPE_WEBHOOK_SECRET and note if it looks like a
placeholder). Removed the blanket "don't test webhook" prohibition — replaced with more
targeted guidance. This induced diagnostic behavior (G15) in Variant B trials.

Rationale for the last point: trial 71bc86a1 showed the agent spending turns 24–30
manually re-running every check the harness graders already perform (idempotency,
bad sig rejection, DB row count). This is duplicate work that adds cost without signal.

What the system prompt must never contain:
- Any hint about `request.data` vs `request.get_json()` — corpus must surface this
- `INSERT OR IGNORE` or idempotency patterns — corpus must surface this
- `client_reference_id` — corpus must surface this

The distinction matters: if Haiku needs implementation hints to pass, that is a finding
(model capability gap), not a prompt engineering problem to paper over.

---

## Current state

All baselines and post-iteration complete. Project is shipped.

| Run set | Variant | Mean | pass@3 | Avg turns | Status |
|---------|---------|------|--------|-----------|--------|
| baseline_v3 | A | 1.000 | 3/3 | 18.7 | Valid baseline |
| variant_b_baseline | B | 0.685 | 0/3 | 14.0 | Deterministic floor (broken env) |
| variant_c_baseline | C | 1.000 | 3/3 | 26.7 | At ceiling, higher turn cost |
| variant_b_post_iter | B | 0.701 | 0/3 | 28.7 | +G15 diagnostic grader |

Open (stretch only, not blocking):
- G9–G11 LLM-as-judge graders not implemented
- failure_mode_category for pre-fix Variant B reports patched directly in JSON

---

## Running trials

```bash
# One trial (Haiku, Variant A)
python scripts/run_trial.py --variant A

# Baseline (3 trials)
python scripts/run_baseline.py --n 3 --variant A

# Explicit model
python scripts/run_trial.py --variant A --model claude-sonnet-4-6
```

Required env vars in `.env`: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`
