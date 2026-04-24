from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult

# Stripe stores the placeholder as-provided; check both raw and URL-encoded forms.
_PLACEHOLDER_RAW = "{CHECKOUT_SESSION_ID}"
_PLACEHOLDER_ENCODED = "%7BCHECKOUT_SESSION_ID%7D"


class G3Grader(BaseGrader):
    """
    success_url contains {CHECKOUT_SESSION_ID} placeholder.
    cancel_url is a non-empty absolute URL.
    """

    grader_id = "G3"
    weight = 0.07

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        if not context.session_id:
            return self._skipped("G1 did not pass (no session_id)", t)

        try:
            session = context.stripe_client.checkout.Session.retrieve(context.session_id)
        except Exception as e:
            return self._error(f"Stripe API error: {e}", {}, t)

        success_url = session.success_url or ""
        cancel_url = session.cancel_url or ""

        evidence = {
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        failures = []

        has_placeholder = (
            _PLACEHOLDER_RAW in success_url or _PLACEHOLDER_ENCODED in success_url
        )
        if not has_placeholder:
            failures.append(
                f"success_url missing {{CHECKOUT_SESSION_ID}} placeholder: {success_url}"
            )

        if not cancel_url:
            failures.append("cancel_url is empty")
        elif not (cancel_url.startswith("http://") or cancel_url.startswith("https://")):
            failures.append(f"cancel_url is not absolute URL: {cancel_url}")

        if failures:
            evidence["failures"] = failures
            return self._fail(evidence, t)

        return self._pass(evidence, t)
