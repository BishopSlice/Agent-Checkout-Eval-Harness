from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult


class G2Grader(BaseGrader):
    """
    Line item has unit_amount=2900, currency="usd", quantity=1, non-empty
    product name.
    """

    grader_id = "G2"
    weight = 0.10

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        if not context.session_id:
            return self._skipped("G1 did not pass (no session_id)", t)

        try:
            session = context.stripe_client.checkout.Session.retrieve(
                context.session_id,
                expand=["line_items", "line_items.data.price.product"],
            )
        except Exception as e:
            return self._error(f"Stripe API error: {e}", {}, t)

        items = getattr(session.line_items, "data", None) or []
        if not items:
            return self._fail({"reason": "No line items on session"}, t)

        item = items[0]
        price = item.get("price") or {}
        product = price.get("product") or {}

        actual = {
            "unit_amount": price.get("unit_amount"),
            "currency": price.get("currency"),
            "quantity": item.get("quantity"),
            "product_name": product.get("name") if isinstance(product, dict) else getattr(product, "name", None),
        }

        failures = []
        if actual["unit_amount"] != 2900:
            failures.append(f"unit_amount: expected 2900, got {actual['unit_amount']}")
        if actual["currency"] != "usd":
            failures.append(f"currency: expected 'usd', got {actual['currency']}")
        if actual["quantity"] != 1:
            failures.append(f"quantity: expected 1, got {actual['quantity']}")
        if not actual["product_name"]:
            failures.append("product_name: empty or missing")

        evidence = {**actual, "session_id": context.session_id}
        if failures:
            evidence["failures"] = failures
            return self._fail(evidence, t)

        return self._pass(evidence, t)
