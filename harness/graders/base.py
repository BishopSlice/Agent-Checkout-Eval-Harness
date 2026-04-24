from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class GraderStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"      # grader itself errored, not agent failure
    SKIPPED = "skipped"  # prerequisite grader failed


@dataclass
class GraderResult:
    grader_id: str
    status: GraderStatus
    score: float          # 0.0 or 1.0 for deterministic; 0.0–1.0 for LLM
    weight: float
    weighted_score: float
    evidence: dict[str, Any]
    error: Optional[str]
    duration_ms: int

    def to_dict(self) -> dict:
        return {
            "grader_id": self.grader_id,
            "status": self.status.value,
            "score": self.score,
            "weight": self.weight,
            "weighted_score": self.weighted_score,
            "evidence": self.evidence,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class GraderContext:
    stripe_client: Any           # stripe module pre-configured with test key
    run_id: str                  # UUID4; matches client_reference_id on session
    db_path: str                 # absolute path to orders.db
    app_url: str                 # e.g. "http://localhost:5000"
    webhook_secret: str          # STRIPE_WEBHOOK_SECRET value (may be invalid in Variant B)
    variant: str                 # "A", "B", or "C"
    transcript: list[dict]       # full messages array (for G12, G13, G14)
    session_id: Optional[str] = None        # populated by G1 on pass; used by G2-G4, G7, G8
    real_webhook_secret: Optional[str] = None  # always the real Stripe secret; G6b/G6c/G7/G8
                                                # sign with this so Variant B correctly fails


class BaseGrader:
    grader_id: str
    weight: float

    def run(self, context: GraderContext) -> GraderResult:
        raise NotImplementedError

    def _make_result(
        self,
        status: GraderStatus,
        score: float,
        evidence: dict,
        error: Optional[str],
        start_time: float,
    ) -> GraderResult:
        duration_ms = int((time.time() - start_time) * 1000)
        return GraderResult(
            grader_id=self.grader_id,
            status=status,
            score=score,
            weight=self.weight,
            weighted_score=score * self.weight,
            evidence=evidence,
            error=error,
            duration_ms=duration_ms,
        )

    def _skipped(self, reason: str, start_time: float) -> GraderResult:
        return self._make_result(
            GraderStatus.SKIPPED, 0.0, {"reason": reason}, None, start_time
        )

    def _pass(self, evidence: dict, start_time: float) -> GraderResult:
        return self._make_result(GraderStatus.PASS, 1.0, evidence, None, start_time)

    def _fail(self, evidence: dict, start_time: float) -> GraderResult:
        return self._make_result(GraderStatus.FAIL, 0.0, evidence, None, start_time)

    def _error(self, error: str, evidence: dict, start_time: float) -> GraderResult:
        return self._make_result(GraderStatus.ERROR, 0.0, evidence, error, start_time)
