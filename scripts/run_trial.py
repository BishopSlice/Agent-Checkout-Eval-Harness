"""
CLI entrypoint for a single eval trial.

Usage:
    python scripts/run_trial.py --variant A
    python scripts/run_trial.py --variant A --port 5001
"""
import argparse
import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Run one eval trial")
    parser.add_argument("--variant", choices=["A", "B", "C"], default="A")
    parser.add_argument("--port", type=int, default=5001, help="Flask port for grading")
    parser.add_argument("--max-turns", type=int, default=30)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Model to evaluate (default: haiku)")
    parser.add_argument("--label", default="default",
                        help="Run set label, e.g. baseline_v2, post_iter_v1")
    args = parser.parse_args()

    import os
    from harness.runner import run_trial

    trials_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "trials", args.label)

    run_trial(
        variant=args.variant,
        flask_port=args.port,
        max_turns=args.max_turns,
        model=args.model,
        trials_dir=trials_dir,
    )


if __name__ == "__main__":
    main()
