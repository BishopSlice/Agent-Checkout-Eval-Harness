from __future__ import annotations

import hashlib
import hmac
import json
import time as time_mod
from typing import Optional

import requests as http_requests

from .base import BaseGrader, GraderContext, GraderResult, GraderStatus


def build_signed_event(
    event_type: str,
    session_id: str,
    customer_email: str,
    amount_total: int,
    webhook_secret: str,
    timestamp: Optional[int] = None,
) -> tuple[bytes, str]:
    """
    Construct a syntactically valid, correctly HMAC-signed Stripe webhook event.
    Returns (payload_bytes, stripe_signature_header).
    """
    if timestamp is None:
        timestamp = int(time_mod.time())

    payload_dict = {
        "id": f"evt_test_{session_id[:8]}",
        "object": "event",
        "type": event_type,
        "data": {
            "object": {
                "id": session_id,
                "object": "checkout.session",
                "amount_total": amount_total,
                "currency": "usd",
                "customer_email": customer_email,
                "customer_details": {"email": customer_email},
                "payment_status": "paid",
                "livemode": False,
            }
        },
        "livemode": False,
    }
    payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    signed_payload = f"{timestamp}.{payload_bytes.decode('utf-8')}"
    signature = hmac.new(
        webhook_secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    sig_header = f"t={timestamp},v1={signature}"
    return payload_bytes, sig_header


class G6aGrader(BaseGrader):
    """POST with invalid signature → expect HTTP 400."""

    grader_id = "G6a"
    weight = 0.07

    def run(self, context: GraderContext) -> GraderResult:
        t = time_mod.time()
        try:
            resp = http_requests.post(
                f"{context.app_url}/webhook",
                data=b'{"id":"evt_test","type":"checkout.session.completed"}',
                headers={
                    "Stripe-Signature": "t=1234567890,v1=invalidsignaturevalue0000000",
                    "Content-Type": "application/json",
                },
                timeout=5,
            )
        except Exception as e:
            return self._error(str(e), {}, t)

        evidence = {
            "status_code": resp.status_code,
            "response_body": resp.text[:300],
        }
        if resp.status_code == 400:
            return self._pass(evidence, t)
        evidence["note"] = (
            "Expected 400 for invalid signature. "
            f"Got {resp.status_code}. Agent may not be verifying signatures."
        )
        return self._fail(evidence, t)


class G6bGrader(BaseGrader):
    """POST with valid signed event → expect HTTP 200."""

    grader_id = "G6b"
    weight = 0.07

    def run(self, context: GraderContext) -> GraderResult:
        t = time_mod.time()
        session_id = context.session_id or "cs_test_grader_g6b_00000000"
        signing_secret = context.real_webhook_secret or context.webhook_secret
        try:
            payload_bytes, sig_header = build_signed_event(
                event_type="checkout.session.completed",
                session_id=session_id,
                customer_email="grader@test.com",
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
            return self._error(str(e), {}, t)

        evidence = {
            "status_code": resp.status_code,
            "response_body": resp.text[:300],
            "session_id_used": session_id,
        }
        if resp.status_code == 200:
            return self._pass(evidence, t)

        if resp.status_code == 400:
            if context.variant == "B":
                evidence["note"] = (
                    "Got 400 on valid signed event. Expected in Variant B — "
                    "agent's STRIPE_WEBHOOK_SECRET doesn't match the real Stripe secret. "
                    "Implementation is correct; environment credential is wrong."
                )
            else:
                evidence["note"] = (
                    "Got 400 on valid signed event. "
                    "Agent likely used request.get_json() or request.json instead of "
                    "request.data — raw bytes required for signature verification."
                )
        return self._fail(evidence, t)


class G6cGrader(BaseGrader):
    """POST with valid signed but unhandled event type → expect HTTP 200."""

    grader_id = "G6c"
    weight = 0.04

    def run(self, context: GraderContext) -> GraderResult:
        t = time_mod.time()
        signing_secret = context.real_webhook_secret or context.webhook_secret
        try:
            payload_bytes, sig_header = build_signed_event(
                event_type="payment_intent.created",
                session_id="pi_test_grader_g6c_00000000",
                customer_email="grader@test.com",
                amount_total=0,
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
            return self._error(str(e), {}, t)

        evidence = {
            "status_code": resp.status_code,
            "event_type_sent": "payment_intent.created",
            "response_body": resp.text[:300],
        }
        if resp.status_code == 200:
            return self._pass(evidence, t)

        evidence["note"] = (
            f"Got {resp.status_code} for unhandled event type. "
            "Stripe requires 200 for unknown events; returning 4xx causes retries."
        )
        return self._fail(evidence, t)


class G6dGrader(BaseGrader):
    """POST with no Stripe-Signature header at all → expect HTTP 400."""

    grader_id = "G6d"
    weight = 0.03

    def run(self, context: GraderContext) -> GraderResult:
        t = time_mod.time()
        try:
            resp = http_requests.post(
                f"{context.app_url}/webhook",
                data=b'{"id":"evt_test","type":"checkout.session.completed"}',
                headers={"Content-Type": "application/json"},
                timeout=5,
            )
        except Exception as e:
            return self._error(str(e), {}, t)

        evidence = {
            "status_code": resp.status_code,
            "response_body": resp.text[:300],
        }
        if resp.status_code == 400:
            return self._pass(evidence, t)

        evidence["note"] = (
            f"Expected 400 when Stripe-Signature header is absent. Got {resp.status_code}. "
            "Agent should reject requests with no signature header before attempting verification."
        )
        return self._fail(evidence, t)
