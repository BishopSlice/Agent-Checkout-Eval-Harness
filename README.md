# Agent Checkout Eval Harness

An evaluation harness that measures whether a Claude AI agent can correctly implement a
Stripe Checkout Session + webhook fulfillment flow in a minimal Flask app — without
human guidance, using only documentation search and self-testing.

Built as an artifact applying [Anthropic's eval methodology](https://www.anthropic.com/research/evaluating-ai-agents) to a realistic, single-task integration scenario inspired by Stripe's own agent benchmarks.

---

## What it evaluates

The agent receives a Flask skeleton with `# TODO` comments and must implement:

1. A `/create-checkout-session` endpoint — creates a Stripe Checkout Session for a $29
   fixed-price product, collects customer email, sets a `client_reference_id` for tracking
2. A `/webhook` endpoint — verifies Stripe signatures using the **raw request body**,
   handles `checkout.session.completed` events, writes idempotent records to SQLite

The agent has access to five tools: `corpus_search` (frozen Stripe/Flask docs),
`file_read`, `file_write`, `shell_exec`, and `stripe_api` (read-only verification).

---

## Grading

15 graders score each trial across two active categories:

| Category | Graders | Weight |
|----------|---------|--------|
| Deterministic (live app + Stripe API) | G1–G8, G6d | ~72% |
| Transcript analysis | G12, G13, G14, G15 | ~28% |
| LLM-as-judge *(stretch, not yet active)* | G9–G11 | — |

Pass threshold: **0.75 overall score**. Score is normalized by active grader weights.

Key graders:
- **G1** — Did the agent create a real Stripe Checkout Session with the correct tracking ID?
- **G6a/G6b** — Does the webhook correctly reject invalid signatures and accept valid ones?
- **G7/G8** — Does the webhook write to SQLite, and is it idempotent?
- **G12** — Did the agent search documentation before writing Stripe code?
- **G13** — Did the agent verify its own implementation before declaring done?

---

## Setup

**Prerequisites:** Python 3.11+, a Stripe test account, an Anthropic API key.

```bash
# 1. Clone and install
git clone <repo>
cd agent-checkout-eval-harness
pip install -r requirements.txt

# 2. Configure credentials
cp .env.example .env
# Edit .env — add STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, ANTHROPIC_API_KEY

# 3. Build the frozen corpus (one-time)
python scripts/fetch_corpus.py

# 4. Validate the reference solution (all G1–G8 must pass before running trials)
python scripts/validate_reference.py
```

---

## Running trials

```bash
# Single trial — Variant A, Haiku (default, ~$0.17)
python scripts/run_trial.py --variant A --label my_run

# Single trial — Sonnet (for model comparison, ~$1.66)
python scripts/run_trial.py --variant A --model claude-sonnet-4-6 --label sonnet_run

# Baseline run — 3 independent trials, aggregate stats
python scripts/run_baseline.py --n 3 --variant A --label my_baseline
```

Trial output is written to `trials/{label}/{trial_id}/`:
- `report.json` — scores, grader results, token usage *(committed to git)*
- `transcript.jsonl` — full agent conversation *(gitignored, large)*
- `agent_app.py` — snapshot of what the agent wrote *(gitignored)*

Use `--label` to name run sets for easy navigation, e.g. `baseline_v2`, `post_iter_v1`.

---

## Task variants

| Variant | Description | Extra challenge |
|---------|-------------|-----------------|
| A | Baseline | Clean environment |
| B | Broken webhook secret | `STRIPE_WEBHOOK_SECRET` is invalid — tests ambiguity handling |
| C | Schema conflict | Pre-existing `orders.db` with conflicting schema |

---

## Project structure

```
agent-checkout-eval-harness/
├── app/                    # Flask skeleton the agent starts from
│   ├── app.py              # TODOs only — not the solution
│   └── schema.sql
├── reference/              # Hand-written correct implementation (eval ground truth)
│   └── app.py
├── corpus/                 # Frozen Stripe/Flask docs the agent can search
├── tasks/
│   └── stripe_checkout.yaml  # Full eval spec: variants, grader weights, prompts
├── harness/
│   ├── runner.py           # Trial orchestration
│   ├── agent.py            # Anthropic SDK agent loop + tool dispatch
│   ├── tools.py            # Tool implementations (sandboxed to app/)
│   ├── flask_manager.py    # Flask subprocess lifecycle
│   └── graders/            # G1–G8, G12–G14
├── scripts/
│   ├── fetch_corpus.py     # One-time corpus builder
│   ├── run_trial.py        # Single trial CLI
│   ├── run_baseline.py     # Multi-trial CLI with aggregate stats
│   └── validate_reference.py
└── trials/                 # Gitignored trial output
```

---

## Design decisions

**Frozen corpus, not live web scraping.** Stripe's docs are JS-rendered — `BeautifulSoup`
returns shell HTML. The corpus is curated plain text, embedded in `fetch_corpus.py` and
written once to `/corpus/`. Every trial runs against identical knowledge.

**Anthropic SDK directly, not LiteLLM.** LiteLLM's rate limit error handling was unreliable
during initial runs. The harness uses the `anthropic` Python SDK with manual exponential
backoff (60s / 120s / 180s).

**Haiku by default, Sonnet by flag.** At ~$0.45/trial vs ~$1.66, Haiku makes iteration
practical. Sonnet is available via `--model` for targeted comparison. Cost estimates in
`runner.py` are model-aware — pass the correct model name and reported USD cost is accurate.

**Eval integrity.** `reference/app.py` is written by the human evaluator, never generated
by the same model being evaluated. The task prompt contains no implementation hints —
the agent must derive correct patterns (e.g. `request.data` for raw body) from corpus
search alone. The system prompt controls only operational behavior (don't check pip, test
once then stop) — never implementation knowledge. If the agent fails, that's a finding,
not a prompt engineering fix.

---

## Results

### Harness calibration (invalidated runs)

Two early runs were invalidated during harness development:
- **baseline_v1** — orphan Flask processes from prior trials contaminated `RUN_ID`, causing G1 to fail on correct implementations. Fixed by killing ports 5000–5002 before each trial.
- **baseline_v2** — skeleton docstrings contained `request.data`, `client_reference_id`, and "idempotent" — the exact patterns the eval tests. All 3 trials scored 1.000 with performative corpus searches. Fixed by genericizing docstrings. Temperature=0 also produced three deterministically identical runs; raised to 0.3.

These are documented in `PLAN.md` as decisions, not discarded — the contamination surface they exposed (skeleton docstrings) was non-obvious and worth recording.

---

### Valid trial results

| Run set | Variant | Mean | pass@3 | Avg turns | Cost/trial | Notes |
|---------|---------|------|--------|-----------|------------|-------|
| baseline_v3 | A — clean | 1.000 | 3/3 | 18.7 | ~$0.17 | Valid baseline |
| variant_b_baseline | B — broken secret | 0.685 | 0/3 | 14.0 | ~$0.12 | Deterministic floor |
| variant_c_baseline | C — schema conflict | 1.000 | 3/3 | 26.7 | ~$0.28 | More turns, same score |
| variant_b_post_iter | B — post-iteration | **0.701** | 0/3 | 28.7 | ~$0.37 | +G15 diagnostic grader |

---

### What we learned

**1. Haiku's training knowledge is sufficient for Variant A.**
Removing skeleton hints (baseline_v2 → baseline_v3) did not drop scores. The agent writes correct `request.data`, `INSERT OR IGNORE`, and `client_reference_id` patterns from memory, not from corpus retrieval. Corpus search passes G12 (a search occurred) but doesn't drive implementation. This is a finding about the eval design, not the agent: Variant A measures execution reliability, not knowledge discovery.

**2. Environment misconfiguration is an undetectable failure mode.**
Variant B (broken `STRIPE_WEBHOOK_SECRET`) produced a deterministic 0.685 floor across all 3 trials with zero score variance. G6a and G6d both passed — the agent's signature verification *logic* is correct. The failure is purely environmental: the agent uses the wrong secret, the grader uses the real one, they never match. No code change can fix a broken env var. This is a real-world deployment risk: agentic systems that implement correctly can still fail silently when credentials are misconfigured at the infrastructure layer.

**3. Iteration shifted behavior but not the structural failure.**
The post-iteration intervention (troubleshooting corpus doc + system prompt diagnostic step + G15 grader) induced the behavioral change it targeted: agents now inspect `STRIPE_WEBHOOK_SECRET` and flag it before declaring done, G15 passed 3/3. Score moved from 0.685 → 0.701. But G6b/G6c/G7/G8 remain at zero — the env var is still broken. This demonstrates a key eval design principle: graders must be designed to measure what you can actually change. G15 rewards diagnosable behavior; the deterministic graders reward correctness the agent cannot achieve in a broken environment.

**4. Schema conflicts cost turns, not correctness.**
Variant C's conflicting `orders.db` added ~8 turns on average (18.7 → 26.7) and tripled cost per trial. The agent detected schema errors from Flask startup logs, iterated with DROP/recreate or migration, and reached a correct implementation every time. 2/3 trials hit max_turns without declaring DONE — but scored 1.000 because graders evaluate the live running app, not the stop condition. Resilience to environmental friction is within Haiku's capability ceiling for this task.

**5. Better diagnostic behavior has a measurable turn-count cost.**
Comparing Variant B baseline (avg 14 turns, $0.12/trial) to post-iteration (avg 28.7 turns, $0.37/trial): adding two verification steps doubled the turn budget and tripled cost. This is an inherent tradeoff in agentic system design — thoroughness and cost move together. For production deployments, verification steps should be scoped carefully.

