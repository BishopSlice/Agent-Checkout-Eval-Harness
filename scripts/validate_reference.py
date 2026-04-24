"""
Validate the reference solution against all deterministic graders (G1-G8).
All 8 must pass before running any agent trials.

Usage:
    python scripts/validate_reference.py

Prerequisites:
  1. reference/app.py must exist (written by you, not the agent)
  2. Flask must be installed
  3. STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must be set in .env
  4. The reference app must be running (this script starts it automatically)
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import stripe as stripe_lib

from harness.flask_manager import FlaskManager
from harness.graders.base import GraderContext, GraderStatus
from harness.graders.g1_session_exists import G1Grader
from harness.graders.g2_line_items import G2Grader
from harness.graders.g3_urls import G3Grader
from harness.graders.g4_customer_email import G4Grader
from harness.graders.g5_webhook_route import G5Grader
from harness.graders.g6_webhook_behavior import G6aGrader, G6bGrader, G6cGrader
from harness.graders.g7_db_write import G7Grader
from harness.graders.g8_idempotency import G8Grader

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REFERENCE_DIR = os.path.join(PROJECT_ROOT, "reference")
PORT = 5002


def main():
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET")

    if not stripe_key or not webhook_secret:
        print("ERROR: STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET must be set in .env")
        sys.exit(1)

    if not os.path.exists(os.path.join(REFERENCE_DIR, "app.py")):
        print(f"ERROR: reference/app.py not found at {REFERENCE_DIR}")
        print("You must write the reference solution manually (see plan for the spec).")
        sys.exit(1)

    run_id = str(uuid.uuid4())
    db_path = os.path.join(REFERENCE_DIR, "orders.db")

    # Remove stale DB from previous run
    if os.path.exists(db_path):
        os.remove(db_path)

    stripe_lib.api_key = stripe_key
    app_url = f"http://localhost:{PORT}"

    print(f"Starting reference Flask app on port {PORT}...")
    print(f"run_id = {run_id}")
    print()

    # First, create a checkout session using the reference app so G1 can find it
    context = GraderContext(
        stripe_client=stripe_lib,
        run_id=run_id,
        db_path=db_path,
        app_url=app_url,
        webhook_secret=webhook_secret,
        variant="A",
        transcript=[],
    )

    graders = [
        G1Grader(), G2Grader(), G3Grader(), G4Grader(), G5Grader(),
        G6aGrader(), G6bGrader(), G6cGrader(),
        G7Grader(), G8Grader(),
    ]

    all_passed = True
    with FlaskManager(
        app_dir=REFERENCE_DIR,
        app_module="app:app",
        port=PORT,
        env_override={
            "STRIPE_SECRET_KEY": stripe_key,
            "STRIPE_WEBHOOK_SECRET": webhook_secret,
            "DATABASE_PATH": db_path,
            "RUN_ID": run_id,
        },
    ):
        import requests

        # Trigger a checkout session so G1 has something to find
        print("Creating a checkout session via the reference app...")
        try:
            resp = requests.post(
                f"{app_url}/create-checkout-session",
                headers={"X-Run-Id": run_id, "Content-Type": "application/json"},
                json={},
                timeout=10,
            )
            if resp.status_code == 200:
                session_data = resp.json()
                print(f"  Session created: {session_data.get('session_id', 'unknown')}")
            else:
                print(f"  WARNING: checkout endpoint returned {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  ERROR creating session: {e}")

        print()
        for grader in graders:
            result = grader.run(context)
            icon = "✓ PASS" if result.status == GraderStatus.PASS else f"✗ {result.status.value.upper()}"
            print(f"  {grader.grader_id}: {icon}")
            if result.status != GraderStatus.PASS:
                all_passed = False
                print(f"    evidence: {result.evidence}")
                if result.error:
                    print(f"    error: {result.error}")

    print()
    if all_passed:
        print("✓ All G1-G8 graders passed the reference solution. Harness is ready.")
    else:
        print("✗ Some graders failed. Fix reference/app.py before running agent trials.")
        sys.exit(1)


if __name__ == "__main__":
    main()
