from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from .graders.base import GraderResult
from .scoring import compute_overall_score, infer_failure_mode

PASS_THRESHOLD = 0.75


@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_cost_usd: float


@dataclass
class TrialReport:
    trial_id: str
    run_id: str
    variant: str
    timestamp: str
    model: str
    temperature: float
    grader_results: list[GraderResult]
    overall_score: float
    pass_at_1: bool
    turn_count: int
    token_usage: TokenUsage
    failure_mode_category: str
    transcript_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial_id": self.trial_id,
            "run_id": self.run_id,
            "variant": self.variant,
            "timestamp": self.timestamp,
            "model": self.model,
            "temperature": self.temperature,
            "grader_results": [r.to_dict() for r in self.grader_results],
            "overall_score": self.overall_score,
            "pass_at_1": self.pass_at_1,
            "turn_count": self.turn_count,
            "token_usage": asdict(self.token_usage),
            "failure_mode_category": self.failure_mode_category,
            "transcript_path": self.transcript_path,
        }

    def save(self, output_dir: str) -> str:
        """Write report.json to output_dir. Returns the file path."""
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, "report.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)
        return path


def build_report(
    *,
    trial_id: str,
    run_id: str,
    variant: str,
    model: str,
    temperature: float,
    grader_results: Sequence[GraderResult],
    turn_count: int,
    token_usage: TokenUsage,
    transcript_path: str,
) -> TrialReport:
    overall = compute_overall_score(grader_results)
    failure_mode = infer_failure_mode(grader_results)
    return TrialReport(
        trial_id=trial_id,
        run_id=run_id,
        variant=variant,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=model,
        temperature=temperature,
        grader_results=list(grader_results),
        overall_score=overall,
        pass_at_1=overall >= PASS_THRESHOLD,
        turn_count=turn_count,
        token_usage=token_usage,
        failure_mode_category=failure_mode,
        transcript_path=transcript_path,
    )
