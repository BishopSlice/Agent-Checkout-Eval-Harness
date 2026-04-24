from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult


class G1Grader(BaseGrader):
    """
    A Stripe Checkout Session exists with client_reference_id == run_id
    and mode == "payment".

    Side effect on pass: context.session_id is set for downstream graders.
    """

    grader_id = "G1"
    weight = 0.12

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        try:
            sessions = context.stripe_client.checkout.Session.list(limit=100)
        except Exception as e:
            return self._error(f"Stripe API error: {e}", {}, t)

        matching = [
            s for s in sessions.auto_paging_iter()
            if s.get("client_reference_id") == context.run_id
        ]

        if not matching:
            return self._fail(
                {"searched": "last 100 sessions (auto-paged)", "run_id": context.run_id},
                t,
            )

        # Use the most recently created matching session
        session = sorted(matching, key=lambda s: s.created, reverse=True)[0]

        evidence = {
            "session_id": session.id,
            "mode": session.mode,
            "client_reference_id": session.client_reference_id,
            "livemode": session.livemode,
            "status": session.status,
        }

        if session.mode != "payment":
            evidence["expected_mode"] = "payment"
            return self._fail(evidence, t)

        if session.livemode:
            evidence["note"] = "Session is in live mode, not test mode"
            return self._fail(evidence, t)

        # Populate context for downstream graders
        context.session_id = session.id
        return self._pass(evidence, t)
