"""
Run N independent Variant A trials and print aggregate stats.

Usage:
    python scripts/run_baseline.py          # 3 trials (default)
    python scripts/run_baseline.py --n 6    # 6 trials
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


def main():
    parser = argparse.ArgumentParser(description="Run baseline trials")
    parser.add_argument("--n", type=int, default=3, help="Number of trials")
    parser.add_argument("--variant", choices=["A", "B", "C"], default="A")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--delay", type=int, default=60, help="Seconds to wait between trials")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Model to evaluate (default: haiku)")
    parser.add_argument("--label", default="default",
                        help="Run set label, e.g. baseline_v2, post_iter_v1")
    args = parser.parse_args()

    import time
    from harness.runner import run_trial

    trials_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "trials", args.label)
    print(f"Run set: {args.label}  →  {trials_dir}")

    reports = []
    for i in range(args.n):
        print(f"\n[Trial {i+1}/{args.n}]")
        report = run_trial(variant=args.variant, flask_port=args.port, model=args.model,
                           trials_dir=trials_dir)
        reports.append(report)
        if i < args.n - 1:
            print(f"\n  [cooldown] waiting {args.delay}s before next trial...")
            time.sleep(args.delay)

    # Aggregate
    scores = [r["overall_score"] for r in reports]
    passes = sum(1 for r in reports if r["pass_at_1"])
    avg_turns = sum(r["turn_count"] for r in reports) / len(reports)
    total_cost = sum(r["token_usage"]["total_cost_usd"] for r in reports)

    failure_modes = {}
    for r in reports:
        fm = r["failure_mode_category"]
        failure_modes[fm] = failure_modes.get(fm, 0) + 1

    print(f"\n{'='*60}")
    print(f"AGGREGATE RESULTS ({args.n} trials, variant={args.variant})")
    print(f"{'='*60}")
    print(f"  Scores:       {[round(s, 3) for s in scores]}")
    print(f"  Mean score:   {sum(scores)/len(scores):.3f}")
    print(f"  pass@k ({args.n}): {passes}/{args.n} ({100*passes//args.n}%)")
    print(f"  Avg turns:    {avg_turns:.1f}")
    print(f"  Total cost:   ${total_cost:.4f}")
    print(f"  Failure modes: {failure_modes}")


if __name__ == "__main__":
    main()
