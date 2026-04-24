from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult


class G4Grader(BaseGrader):
    """
    Session was created with email collection enabled:
      - customer_email was pre-filled, OR
      - billing_address_collection == "required", OR
      - customer_creation == "always"
    """

    grader_id = "G4"
    weight = 0.05

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        if not context.session_id:
            return self._skipped("G1 did not pass (no session_id)", t)

        try:
            session = context.stripe_client.checkout.Session.retrieve(context.session_id)
        except Exception as e:
            return self._error(f"Stripe API error: {e}", {}, t)

        customer_email = session.customer_email or None
        billing = session.billing_address_collection or None
        customer_creation = session.customer_creation or None

        evidence = {
            "customer_email": customer_email,
            "billing_address_collection": billing,
            "customer_creation": customer_creation,
        }

        if customer_email:
            evidence["pass_condition"] = "customer_email pre-filled"
            return self._pass(evidence, t)
        if billing == "required":
            evidence["pass_condition"] = "billing_address_collection=required"
            return self._pass(evidence, t)
        if customer_creation == "always":
            evidence["pass_condition"] = "customer_creation=always"
            return self._pass(evidence, t)

        evidence["pass_condition"] = "none"
        evidence["note"] = (
            "None of: customer_email set, billing_address_collection=required, "
            "customer_creation=always. Email collection not guaranteed."
        )
        return self._fail(evidence, t)
