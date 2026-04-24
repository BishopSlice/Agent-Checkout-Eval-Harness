# PLAN.md — Decisions Log

This file records decisions made during development, why they were made, and what changed
from the original plan. It is not a forward-looking roadmap — it is a record of judgment
calls under uncertainty, useful for PM portfolio review and future context.

---

## Original intent vs. actual implementation

The original spec called for a harness that:
- Evaluates Claude agents on a Stripe Checkout + webhook task
- Uses 14 graders across three categories: deterministic (G1–G8), LLM-as-judge (G9–G11),
  transcript (G12–G14)
- Runs 3 baseline trials + 3 post-iteration trials and documents score movement

The core is complete. The stretch goals (G9–G11 LLM-as-judge, Variants B/C) are deferred.

---

## Key decisions and deviations

### 1. Corpus: hardcoded strings, not web scraping

**Original plan:** `fetch_corpus.py` would use `requests` + `BeautifulSoup` to scrape
Stripe documentation URLs and save as plain text.

**What happened:** Stripe's docs are JS-rendered. BeautifulSoup returns the raw HTML shell —
navigation, scripts, and empty `<div>` tags — with no readable content. The corpus produced
was garbage and would have given the agent irrelevant retrieval results.

**Decision:** Replaced with curated plain-text strings embedded directly in `fetch_corpus.py`,
written to `/corpus/*.txt` with no HTTP requests. Seven documents covering: Checkout Session
creation, webhook signature verification, fulfillment + idempotency, session retrieval,
Flask raw request body, environment variable handling, and OWASP Top 10 (grader context).

**Tradeoff:** Less authentic than live docs, but reproducible and actually useful. Every trial
runs against identical knowledge — which is the property we needed.

---

### 2. Agent SDK: Anthropic directly, not LiteLLM

**Original plan:** Use `litellm` as the model abstraction layer for portability across
providers.

**What happened:** Two failures:
- `litellm==1.44.0` didn't exist on PyPI. Pinned version was wrong.
- After fixing the version, LiteLLM's rate limit error handling was unreliable. Rate limit
  errors surfaced as opaque exceptions that the backoff logic couldn't catch correctly,
  causing trials to fail mid-run.

**Decision:** Rewrote `harness/agent.py` to use the `anthropic` Python SDK directly.
Implemented manual exponential backoff (`_call_with_backoff`) catching `anthropic.RateLimitError`
specifically. Added per-turn print statements for real-time visibility.

**Tradeoff:** Loses provider portability (now Anthropic-only). Gained reliability and
type-safe error handling. Acceptable for a single-model eval.

---

### 3. Default model: Haiku, not Sonnet

**Original plan:** Evaluate `claude-sonnet-4-6`.

**What happened:** First trial (Sonnet, with G1 bug) cost $1.66. Extrapolating: 6 trials
(3 baseline + 3 post-iteration) × $1.66 = ~$10. Too expensive for rapid iteration.

**Decision:** Switched default to `claude-haiku-4-5-20251001` (~$0.33/trial). Sonnet
remains available via `--model claude-sonnet-4-6`. Cost estimates in `runner.py` are
model-aware (correct pricing for Haiku, Sonnet, Opus).

**Tradeoff:** Haiku is less capable. If it fails graders that Sonnet passes, that gap is
a legitimate finding worth documenting, not a reason to switch back. The eval is now
model-agnostic by design.

---

### 4. RUN_ID threading fix

**Original plan:** `runner.py` builds `agent_env` with `RUN_ID` and passes it to the agent.

**What happened:** First Sonnet trial (e443dc34) scored 0.755 with G1 failing — no Stripe
session found with the correct `client_reference_id`. Root cause: `agent_env` was constructed
correctly but never passed to `run_agent()`. The `shell_exec` tool inherited the parent
process environment, which had no `RUN_ID`. Flask used `client_reference_id="unknown"`.

**Fix applied:**
1. Added `shell_env` parameter to `run_agent()` and threaded it through `_dispatch_tool()`
   → `tool_shell_exec()` → `subprocess.run(env=merged_env)`
2. Added `_trigger_checkout_session()` call inside the `FlaskManager` context before graders
   run — the harness independently creates a session with the correct `run_id`, so G1 has
   something to find even if the agent's own self-test used the wrong ID

---

### 5. System prompt: tightened in two stages

**Original:** Open-ended — "verify it works by running the Flask app and testing your endpoints."

**Stage 1 (operational waste):** Added explicit prohibitions on checking pip installs and
environment variables before coding. Added 5-step workflow. Rationale: first Sonnet trial
spent turns 4–6 on env verification that added no value.

**Stage 2 (duplicate testing):** Trial 71bc86a1 (Haiku, score=1.0) showed the agent spending
turns 24–30 manually re-running every check the harness graders already perform: idempotency,
bad signature rejection, DB row count. The agent never declared DONE and hit max_turns mid-test.

Updated workflow: "verify startup with one POST to /create-checkout-session → declare DONE.
Do not manually test webhook signature verification, idempotency, or DB writes."

**Integrity constraint preserved:** The system prompt still contains no Stripe implementation
hints (`request.data`, `INSERT OR IGNORE`, `client_reference_id`). Those must come from corpus
search. If Haiku can't find them, that is a finding, not a prompt fix.

---

### 6. Skeleton docstrings must not contain implementation hints

**What happened:** baseline_v2 scored 3/3 perfect at temperature=0. Transcript analysis
showed all three agents ran the same two generic corpus searches ("Stripe Checkout Session
creation API", "Stripe webhook signature verification") then immediately wrote the correct
implementation on turn 3. The corpus was not driving discovery.

Root cause: `app/app.py` docstrings contained `request.data` (the canonical Stripe gotcha),
"idempotent" (directly hints at INSERT OR IGNORE), and `client_reference_id` (tells the agent
what to set). These are the exact patterns the eval is designed to test corpus retrieval of.
The skeleton was treating docstrings as helpful scaffolding; they were actually answer leaks.

**Decision:** Skeleton docstrings now use generic descriptions only:
- `"Verify the Stripe signature"` not `"Verify signature using raw request body (request.data)"`
- `"Persist order data, handling duplicate deliveries safely"` not `"Write idempotent order"`
- `"Tag the session so it can be traced"` not `"Set client_reference_id to RUN_ID env var"`

Added to CLAUDE.md eval integrity rules so the rule survives context resets.

**Why it repeated:** The existing rule said "never add hints to the task prompt." The skeleton
is a different file with a different conceptual role — the rule didn't cover it. Unnamed rules
don't get followed across sessions. Now explicit.

### 7. Eval integrity: reference/app.py written by human only

**Decision:** `reference/app.py` (the correct implementation used to validate graders) is
written by the user, not generated by Claude via file tools.

**Rationale:** If Claude writes both the reference solution and the agent's implementation,
the eval measures self-consistency, not correctness. The reference must be independent.

---

## Trial log

### baseline_v1 — Haiku, Variant A (harness bugs present, use for reference only)

| Trial ID | Score | Turns | Cost | Notes |
|----------|-------|-------|------|-------|
| e443dc34 (Sonnet) | 0.755 | 28 | $1.66 | Pre-haiku, pre-fix. RUN_ID never passed to agent. Discarded. |
| 71bc86a1 | 1.000 | 30 | $0.33 | Perfect. Hit max_turns without DONE — re-tested harness scenarios. |
| cb172298 | 0.798 | 25 | $0.24 | G7/G8 fail: webhook 200 but no DB write. Likely process-leak side-effect. |
| 380205bf | 0.755 | 16 | $0.15 | G1 fail: agent hit orphan Flask from previous trial with stale RUN_ID. |

Mean: 0.851 | pass@3: 3/3 | Total: $0.72 | **Not a clean baseline — process-leak bug present.**

### baseline_v2 — Haiku, temp=0, Variant A (skeleton hints present — invalidated)

| Trial ID | Score | Turns | Cost | Notes |
|----------|-------|-------|------|-------|
| 1190f210 | 1.000 | 16 | $0.13 | Perfect. Skeleton hints leaked answer. |
| 956370a9 | 1.000 | 21 | $0.18 | Perfect. Identical corpus queries — deterministic at temp=0. |
| d9743250 | 1.000 | 20 | $0.17 | Perfect. Same pattern. |

Mean: 1.000 | pass@3: 3/3 | Total: $0.48 | **Invalidated.**

Root cause: `app/app.py` docstrings contained `request.data`, `client_reference_id`, and
"idempotent" — the exact patterns G6b, G1, and G8 test. Agent read them from the skeleton
and never needed to discover them via corpus. temperature=0 produced three identical runs,
measuring reproducibility rather than capability variance.

Fixes applied: skeleton docstrings genericised; temperature raised to 0.3.

### baseline_v3 — Haiku, temp=0.3, clean skeleton ✓ valid baseline

| Trial ID | Score | Turns | Cost | Notes |
|----------|-------|-------|------|-------|
| 28c2d28f | 1.000 | 21 | $0.18 | 2 corpus searches. G4 via pre-filled email. |
| b8e1b83f | 1.000 | 18 | $0.17 | 3 corpus searches (more specific). Wrote schema.sql separately. |
| d76b15b1 | 1.000 | 17 | $0.14 | 2 corpus searches. Fastest trial. Wrote app.py in T2. |

Mean: 1.000 | pass@3: 3/3 | Total: $0.50

Temperature introduced genuine behavioral variance: different query counts, different G4
strategies, different turn counts. Valid baseline.

**Key finding: parametric knowledge ceiling.** Removing skeleton hints didn't drop scores.
The agent derives correct Stripe patterns (request.data, INSERT OR IGNORE, client_reference_id)
from training knowledge, not corpus retrieval. Corpus searches are generic and happen to pass
G12, but don't drive the implementation. Variant A is within Haiku's capability ceiling —
the eval measures execution reliability on this variant, not knowledge retrieval.

**Persistent issue: python→python3 waste.** Every trial wastes 3–5 turns discovering the
binary. Fixable in the system prompt as operational guidance.

---

## Diagnosed failure modes from baseline

### G1 flakiness — CONFIRMED HARNESS BUG (process leak)
Root cause confirmed by transcript analysis of trial 380205bf. The agent ran `python app.py &`
(not `python3`) — Flask failed to start silently. A previous trial's Flask process (cb172298's
agent) was still running on port 5000, having never been killed. The next agent's curl hit that
orphan Flask, which had the previous trial's `RUN_ID` baked in as a module-level variable.
Sessions were created with the wrong `client_reference_id`. The harness's `_trigger_checkout_session`
safety net also hit the same wrong Flask. G1 found no session matching the correct `run_id`.

Fix applied: `runner.py` now kills all processes on ports 5000/5001/5002 before each trial
starts. Not an agent capability gap — both affected trials had correct implementation code.

### G7/G8 — unexplained, likely harness issue
Trial cb172298 agent_app.py is code-identical to the perfect trial (71bc86a1): same `request.data`
usage, same `INSERT OR IGNORE`, same `conn.commit()`, same error handling. The webhook returned
200 (no exception path taken) but `rows_found=0`. Root cause not yet confirmed — possibly a DB
path or state interaction between the agent's self-test Flask (port 5000) and the harness Flask
(port 5001). May also be resolved by the process-leak fix above. Flag for re-evaluation in next
baseline run before treating as an agent capability gap.

---

### variant_b_baseline — Haiku, temp=0.3, broken webhook secret ✓ complete

| Trial ID | Score | Turns | Cost | Notes |
|----------|-------|-------|------|-------|
| 6ed56d32 | 0.685 | 14 | $0.11 | G6b/G6c/G7/G8 fail. G1–G5, G6a, G6d, G12–G14 pass. |
| 4aab3b0d | 0.685 | 12 | $0.12 | Identical grader pattern. Faster (12 turns). |
| 1e011590 | 0.685 | 16 | $0.13 | Identical grader pattern. |

Mean: 0.685 | pass@3: 0/3 | Total: $0.36

**Key finding: Variant B is a deterministic failure floor.** All 3 trials scored exactly 0.685
with no variance. G6b/G6c/G7/G8 failed in every trial — not because implementation was wrong
(G6a and G6d passed, confirming signature verification logic exists), but because the agent's
`STRIPE_WEBHOOK_SECRET` env var is invalid. The grader signs with the real secret; the agent
verifies with the broken one → mismatch → 400 → no DB write.

The `failure_mode_category` is reported as `knowledge_gap` by the runner (G6b fail heuristic),
but this is a misclassification for Variant B — the failure is environment-induced, not a
knowledge or implementation gap. The correct label is `environment_mismatch`. This is a known
limitation of the current failure_mode heuristic; it was written for Variant A.

Expected score per grader redesign spec was 0.55–0.60; actual 0.685 is higher because G12–G14
transcript graders (15% weight) all pass — the agent behaves well procedurally even though the
environment is broken.

---

## Pending

### variant_c_baseline — Haiku, temp=0.3, pre-existing conflicting schema ✓ complete

| Trial ID | Score | Turns | Stop | Cost | Notes |
|----------|-------|-------|------|------|-------|
| 1afe57d2 | 1.000 | 30 | max_turns | ~$0.28 | All graders pass. Hit turn limit — still ran correctly. |
| d7f9425a | 1.000 | 20 | done | ~$0.27 | Clean finish. |
| 8b529a0d | 1.000 | 30 | max_turns | ~$0.28 | All graders pass. Hit turn limit again. |

Mean: 1.000 | pass@3: 3/3 | Total: $0.83 | Avg turns: 26.7

**Key finding: Variant C is within Haiku's capability ceiling, but costs ~67% more turns.**
The conflicting schema triggers errors when Flask starts (old table missing UNIQUE + columns).
The agent detects this from startup errors, iterates with schema migration or DROP/recreate,
and ultimately succeeds. 2/3 trials hit max_turns without declaring DONE — still scored 1.000
because graders run against the live app, not the stop condition.

Variant C task prompt had same meta-note contamination as Variant B (`"The agent must detect the
schema conflict and resolve it."`). Fixed before running: prompt now identical to Variant A.

**Remaining finding: no variant surface found Haiku's ceiling yet.** A, B (env mismatch aside),
and C all score 1.000. Corpus retrieval is performative — Haiku uses training knowledge. The
eval currently measures execution reliability + resilience, not knowledge discovery.

---

### variant_b_post_iter — Haiku, temp=0.3, post-iteration Variant B ✓ complete

**Intervention applied:**
- Added `corpus/08_webhook_troubleshooting.txt` — covers STRIPE_WEBHOOK_SECRET mismatch as a common failure mode, diagnostic steps
- Updated system prompt: added explicit step to verify STRIPE_WEBHOOK_SECRET value and test /webhook route before declaring DONE
- Added G15 grader (transcript): passes if agent inspected or flagged webhook secret configuration

| Trial ID | Score | Turns | Notes |
|----------|-------|-------|-------|
| (trial 1) | 0.701 | 30 | G15 pass. max_turns_reached. |
| 72ab36be | 0.701 | 30 | G15 pass. max_turns_reached. |
| bb8042bd | 0.701 | 30 | G15 pass. max_turns_reached. |

Mean: 0.701 | pass@3: 0/3 | Total: $1.10 | Avg turns: 28.7

**Score movement: 0.685 → 0.701 (+2.4%)**

**Key finding: intervention was effective at the behavioral level, not the implementation level.**
G15 passed 3/3 — agents consistently inspected STRIPE_WEBHOOK_SECRET and flagged it as potentially
invalid. The diagnostic behavior was successfully induced. However, G6b/G6c/G7/G8 remain unchanged:
the structural failure in Variant B (wrong env var) is unresolvable from code. Score improvement
is bounded by grader design, not agent capability.

**Side effect: turn count cost.** Avg turns jumped from 14 to 28.7 — the additional diagnostic
steps consumed the turn budget. All 3 trials hit max_turns. This is a concrete tradeoff:
better diagnostic behavior comes at the cost of more turns and ~3x higher cost.

---

## Pending

- README polish: add "what we learned" narrative section — the only remaining MVP item
- Fix failure_mode_category heuristic: Variant B `environment_mismatch` vs `knowledge_gap` (minor, non-blocking)
- Stretch: G9–G11 LLM-as-judge graders (code quality signal, not required for portfolio story)
- Stretch: Variant D — novel API pattern not in training data

## Grader changes log

### G6 redesign (Variant B fix)
- G6b/G6c/G7/G8: now sign test events with `context.real_webhook_secret` (always the real
  Stripe secret) instead of `context.webhook_secret` (which is invalid in Variant B).
  For Variant A: behaviour unchanged (real == env). For Variant B: grader signs with real
  secret, agent verifies with invalid env secret → mismatch → G6b/G6c/G7/G8 correctly fail.
- G6d added: POST with no Stripe-Signature header → expect 400. Weight 0.03.
  Closes gap between G5 (route exists) and G6a (wrong sig).
- G7 strengthened: field-level checks on stored row (amount_total, session_id match).
- Variant B task prompt: now identical to Variant A. Agent not told about broken secret.

### Expected Variant B score (correct implementation, wrong env)
G1–G5 pass, G6a pass, G6d pass, G6b/G6c fail (sig mismatch), G7/G8 fail (webhook rejects).
G12/G13/G14 pass. Expected normalised score: ~0.55–0.60. Below pass threshold (0.75).
