"""
Trial orchestration: setup → agent → Flask start → grade → report → teardown.

One trial = one independent run on a clean copy of app/app.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import stripe as stripe_lib

from .agent import run_agent
from .flask_manager import FlaskManager
from .graders.base import GraderContext
from .graders.g1_session_exists import G1Grader
from .graders.g2_line_items import G2Grader
from .graders.g3_urls import G3Grader
from .graders.g4_customer_email import G4Grader
from .graders.g5_webhook_route import G5Grader
from .graders.g6_webhook_behavior import G6aGrader, G6bGrader, G6cGrader, G6dGrader
from .graders.g7_db_write import G7Grader
from .graders.g8_idempotency import G8Grader
from .graders.g12_doc_retrieval import G12Grader
from .graders.g13_self_verification import G13Grader
from .graders.g14_turn_count import G14Grader
from .graders.g15_env_diagnosis import G15Grader
from .report import TokenUsage, build_report

PROJECT_ROOT = Path(__file__).parent.parent


def run_trial(
    variant: str = "A",
    model: str = "claude-haiku-4-5-20251001",
    temperature: float = 0.3,
    max_turns: int = 30,
    flask_port: int = 5001,
    trials_dir: Optional[str] = None,
    stripe_secret_key: Optional[str] = None,
    stripe_webhook_secret: Optional[str] = None,
) -> dict:
    """
    Execute one complete eval trial.
    Returns the trial report as a dict.
    """
    trial_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    if trials_dir is None:
        trials_dir = str(PROJECT_ROOT / "trials")

    trial_dir = os.path.join(trials_dir, trial_id)
    os.makedirs(trial_dir, exist_ok=True)

    # Load credentials from env if not provided
    stripe_secret_key = stripe_secret_key or os.environ["STRIPE_SECRET_KEY"]
    stripe_webhook_secret = stripe_webhook_secret or os.environ["STRIPE_WEBHOOK_SECRET"]

    # Apply variant environment overrides
    variant_env = _build_variant_env(variant, stripe_webhook_secret)

    # Build a fresh working directory for this trial
    agent_app_dir = os.path.join(trial_dir, "app")
    _setup_agent_dir(agent_app_dir, variant, variant_env)

    corpus_dir = str(PROJECT_ROOT / "corpus")
    transcript_path = os.path.join(trial_dir, "transcript.jsonl")

    # Kill any orphan Flask/Python processes left over from previous trials.
    # Without this, the agent's curl hits an old Flask instance with a stale RUN_ID,
    # creating sessions with the wrong client_reference_id and causing G1 to fail.
    import subprocess as _sp
    for _port in [5000, 5001, 5002]:
        _sp.run(f"lsof -ti:{_port} | xargs kill -9 2>/dev/null", shell=True)

    print(f"\n{'='*60}")
    print(f"Trial {trial_id[:8]} | variant={variant} | run_id={run_id[:8]}")
    print(f"{'='*60}")

    # -- Phase 1: Agent run --
    task_prompt = _load_task_prompt(variant)
    agent_env = {
        **variant_env,
        "RUN_ID": run_id,
        "STRIPE_SECRET_KEY": stripe_secret_key,
        "DATABASE_PATH": os.path.join(agent_app_dir, "orders.db"),
    }

    agent_result = run_agent(
        task_prompt=task_prompt,
        app_dir=agent_app_dir,
        corpus_dir=corpus_dir,
        stripe_api_key=stripe_secret_key,
        shell_env=agent_env,
        model=model,
        temperature=temperature,
        max_turns=max_turns,
    )

    # Save transcript
    _save_transcript(agent_result.transcript, transcript_path)

    # Snapshot the agent's app.py
    agent_app_py = os.path.join(agent_app_dir, "app.py")
    if os.path.exists(agent_app_py):
        shutil.copy(agent_app_py, os.path.join(trial_dir, "agent_app.py"))

    print(f"Agent finished: {agent_result.stop_reason} | turns={agent_result.turn_count}")

    # -- Phase 2: Grade --
    db_path = os.path.join(agent_app_dir, "orders.db")
    stripe_lib.api_key = stripe_secret_key

    grader_context = GraderContext(
        stripe_client=stripe_lib,
        run_id=run_id,
        db_path=db_path,
        app_url=f"http://localhost:{flask_port}",
        webhook_secret=variant_env.get("STRIPE_WEBHOOK_SECRET", stripe_webhook_secret),
        real_webhook_secret=stripe_webhook_secret,
        variant=variant,
        transcript=agent_result.transcript,
    )

    graders = [
        G1Grader(), G2Grader(), G3Grader(), G4Grader(), G5Grader(),
        G6aGrader(), G6bGrader(), G6cGrader(), G6dGrader(),
        G7Grader(), G8Grader(),
        G12Grader(), G13Grader(), G14Grader(), G15Grader(),
    ]

    grader_results = []
    with FlaskManager(
        app_dir=agent_app_dir,
        app_module="app:app",
        port=flask_port,
        env_override={**agent_env},
    ) as _flask:
        # Trigger a real Stripe Checkout Session from the agent's app so G1 can find it.
        # The agent may have already created one during testing, but this guarantees
        # a session with the correct client_reference_id=run_id exists.
        _trigger_checkout_session(f"http://localhost:{flask_port}", run_id)

        for grader in graders:
            result = grader.run(grader_context)
            grader_results.append(result)
            status_icon = "✓" if result.status.value == "pass" else ("✗" if result.status.value == "fail" else "·")
            print(f"  {status_icon} {result.grader_id}: {result.status.value} (score={result.score:.2f})")

    # -- Phase 3: Build and save report --
    cost = _estimate_cost(agent_result.input_tokens, agent_result.output_tokens, model)

    report = build_report(
        trial_id=trial_id,
        run_id=run_id,
        variant=variant,
        model=model,
        temperature=temperature,
        grader_results=grader_results,
        turn_count=agent_result.turn_count,
        token_usage=TokenUsage(
            input_tokens=agent_result.input_tokens,
            output_tokens=agent_result.output_tokens,
            total_cost_usd=cost,
        ),
        transcript_path=transcript_path,
    )

    report_path = report.save(trial_dir)
    print(f"\nScore: {report.overall_score:.3f} | pass_at_1={report.pass_at_1} | failure_mode={report.failure_mode_category}")
    print(f"Report: {report_path}")

    return report.to_dict()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_task_prompt(variant: str) -> str:
    import yaml
    task_file = PROJECT_ROOT / "tasks" / "stripe_checkout.yaml"
    with open(task_file) as f:
        task = yaml.safe_load(f)
    key = f"variant_{variant.lower()}"
    prompt = task["task_prompt"].get(key) or task["task_prompt"]["variant_a"]
    return prompt.strip()


def _build_variant_env(variant: str, default_webhook_secret: str) -> dict:
    if variant == "B":
        return {"STRIPE_WEBHOOK_SECRET": "whsec_INVALID_VALUE_FOR_TESTING_1234567890ab"}
    return {"STRIPE_WEBHOOK_SECRET": default_webhook_secret}


def _setup_agent_dir(agent_app_dir: str, variant: str, variant_env: dict) -> None:
    """Copy app skeleton into the trial's working directory."""
    src = str(PROJECT_ROOT / "app")
    if os.path.exists(agent_app_dir):
        shutil.rmtree(agent_app_dir)
    shutil.copytree(src, agent_app_dir)

    if variant == "C":
        # Pre-create a conflicting orders.db
        db_path = os.path.join(agent_app_dir, "orders.db")
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, amount INTEGER)"
        )
        conn.commit()
        conn.close()


def _save_transcript(transcript: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for entry in transcript:
            f.write(json.dumps(entry) + "\n")


def _trigger_checkout_session(app_url: str, run_id: str) -> None:
    """
    POST to the agent's /create-checkout-session endpoint so a real Stripe session
    exists with client_reference_id=run_id before graders run.
    Logs a warning if it fails but does not abort grading.
    """
    import requests
    try:
        resp = requests.post(
            f"{app_url}/create-checkout-session",
            headers={"X-Run-Id": run_id, "Content-Type": "application/json"},
            json={},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [harness] checkout session created: {data.get('session_id', '?')}")
        else:
            print(f"  [harness] WARNING: checkout endpoint returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        print(f"  [harness] WARNING: could not trigger checkout session: {e}")


def _estimate_cost(input_tokens: int, output_tokens: int, model: str = "claude-haiku-4-5-20251001") -> float:
    # Pricing per 1M tokens
    pricing = {
        "claude-haiku-4-5-20251001": (0.80, 4.0),
        "claude-sonnet-4-6": (3.0, 15.0),
        "claude-opus-4-7": (15.0, 75.0),
    }
    input_rate, output_rate = pricing.get(model, (3.0, 15.0))
    return round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000, 6)
