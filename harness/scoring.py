from __future__ import annotations

from typing import Sequence

from .graders.base import GraderResult, GraderStatus


def compute_overall_score(results: Sequence[GraderResult]) -> float:
    """
    Normalized weighted score: sum(score * weight) / sum(weight for active graders).
    Active = not SKIPPED (skipped graders contribute 0 to both numerator and denominator).
    Always returns a value in [0.0, 1.0].
    """
    active = [r for r in results if r.status != GraderStatus.SKIPPED]
    if not active:
        return 0.0
    total_weight = sum(r.weight for r in active)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(r.score * r.weight for r in active)
    return round(weighted_sum / total_weight, 4)


def infer_failure_mode(results: Sequence[GraderResult]) -> str:
    """
    Categorize the primary failure mode based on which graders failed.
    Returns one of: ambiguity_handling, self_verification, knowledge_gap,
    environment_mismatch, none.
    """
    by_id = {r.grader_id: r for r in results}

    def failed(gid: str) -> bool:
        return by_id.get(gid, None) is not None and by_id[gid].status == GraderStatus.FAIL

    def passed(gid: str) -> bool:
        return by_id.get(gid, None) is not None and by_id[gid].status == GraderStatus.PASS

    # All passed
    if all(r.status in (GraderStatus.PASS, GraderStatus.SKIPPED) for r in results):
        return "none"

    # environment_mismatch: Variant B pattern — G6a passes (agent rejects bad sigs correctly)
    # but G6b fails (agent can't verify with real secret because env var is wrong).
    # Implementation is correct; the environment is broken.
    if passed("G6a") and passed("G6d") and failed("G6b"):
        note = (by_id.get("G6b", None) and by_id["G6b"].evidence.get("note", "")) or ""
        if "Variant B" in note or "credential" in note.lower() or "doesn't match" in note.lower():
            return "environment_mismatch"

    # knowledge_gap: agent used wrong API (bad sig not rejected, wrong body parsing, etc.)
    if failed("G6a"):
        return "knowledge_gap"
    if failed("G6b"):
        return "knowledge_gap"
    if failed("G2"):
        return "knowledge_gap"
    if failed("G7") or failed("G8"):
        if passed("G6b"):
            return "knowledge_gap"

    # self_verification: agent didn't test its own code
    if failed("G13"):
        return "self_verification"

    # ambiguity_handling: agent didn't use client_reference_id or misconfigured session
    if failed("G1"):
        return "ambiguity_handling"
    if failed("G3"):
        return "ambiguity_handling"

    return "knowledge_gap"  # default for other deterministic failures
