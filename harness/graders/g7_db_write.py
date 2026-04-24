from __future__ import annotations

import sqlite3
import time

import requests as http_requests

from .base import BaseGrader, GraderContext, GraderResult
from .g6_webhook_behavior import build_signed_event


class G7Grader(BaseGrader):
    """
    After a valid checkout.session.completed webhook with the real session_id,
    orders table contains exactly one record with that session_id.
    Prerequisite: G6b must have passed.
    """

    grader_id = "G7"
    weight = 0.10

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()

        if not context.session_id:
            return self._skipped("G1 did not pass (no session_id)", t)

        signing_secret = context.real_webhook_secret or context.webhook_secret
        try:
            payload_bytes, sig_header = build_signed_event(
                event_type="checkout.session.completed",
                session_id=context.session_id,
                customer_email="grader-g7@test.com",
                amount_total=2900,
                webhook_secret=signing_secret,
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
            "webhook_status_code": resp.status_code,
        }

        if resp.status_code != 200:
            evidence["webhook_response"] = resp.text[:300]
            evidence["note"] = "Webhook did not return 200; DB write likely did not happen"
            return self._fail(evidence, t)

        # Inspect the SQLite DB
        try:
            conn = sqlite3.connect(context.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM orders WHERE session_id = ?",
                (context.session_id,),
            ).fetchall()
            conn.close()
        except sqlite3.OperationalError as e:
            return self._error(f"SQLite error: {e}", evidence, t)
        except Exception as e:
            return self._error(f"DB read failed: {e}", evidence, t)

        evidence["rows_found"] = len(rows)
        if len(rows) == 0:
            evidence["note"] = "Webhook returned 200 but no order record found in DB"
            return self._fail(evidence, t)

        if len(rows) > 1:
            evidence["note"] = f"Expected 1 row, found {len(rows)} (idempotency problem)"
            return self._fail(evidence, t)

        order = dict(rows[0])
        evidence["order"] = order

        # Field-level checks: amount_total must be stored correctly
        field_failures = []
        if order.get("amount_total") != 2900:
            field_failures.append(
                f"amount_total: expected 2900, got {order.get('amount_total')}"
            )
        if order.get("session_id") != context.session_id:
            field_failures.append(
                f"session_id mismatch: expected {context.session_id}, "
                f"got {order.get('session_id')}"
            )
        if field_failures:
            evidence["field_failures"] = field_failures
            return self._fail(evidence, t)

        return self._pass(evidence, t)
