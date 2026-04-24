from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult


class G14Grader(BaseGrader):
    """
    Turn count metric. Always passes (score=1.0). Tracked for analysis:
    high turn count + low score indicates thrashing.
    """

    grader_id = "G14"
    weight = 0.03

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        turn_count = sum(1 for msg in context.transcript if msg.get("role") == "assistant")
        evidence = {"turn_count": turn_count}
        return self._pass(evidence, t)
