from __future__ import annotations

import sqlite3
import time

import requests as http_requests

from .base import BaseGrader, GraderContext, GraderResult
from .g6_webhook_behavior import build_signed_event


class G8Grader(BaseGrader):
    """
    Sending the same checkout.session.completed event twice results in exactly
    one record in orders table.
    Prerequisite: G7 must have passed (one record already exists).
    """

    grader_id = "G8"
    weight = 0.08

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()

        if not context.session_id:
            return self._skipped("G1 did not pass (no session_id)", t)

        signing_secret = context.real_webhook_secret or context.webhook_secret
        try:
            payload_bytes, sig_header = build_signed_event(
                event_type="checkout.session.completed",
                session_id=context.session_id,
                customer_email="grader-g8@test.com",
                amount_total=2900,
                webhook_secret=signing_secret,
                timestamp=int(time.time()),
            )
            resp = http_requests.post(
                f"{context.app_url}/webhook",
                data=payload_bytes,
                headers={
                    "Stripe-Signature": sig_header,
                    "Content-Type": "application/json",
                },
                timeout=5,
            )
        except Exception as e:
            return self._error(f"Webhook request failed: {e}", {}, t)

        evidence: dict = {
            "session_id": context.session_id,
            "second_webhook_status_code": resp.status_code,
        }

        try:
            conn = sqlite3.connect(context.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM orders WHERE session_id = ?",
                (context.session_id,),
            ).fetchall()
            conn.close()
        except Exception as e:
            return self._error(f"DB read failed: {e}", evidence, t)

        evidence["rows_after_second_event"] = len(rows)

        if len(rows) == 1:
            return self._pass(evidence, t)

        if len(rows) == 2:
            evidence["note"] = (
                "Second event created a duplicate row. "
                "INSERT OR IGNORE or UNIQUE constraint missing."
            )
        elif len(rows) == 0:
            evidence["note"] = "No rows found — G7 state may have been lost"
        else:
            evidence["note"] = f"Unexpected row count: {len(rows)}"

        return self._fail(evidence, t)
