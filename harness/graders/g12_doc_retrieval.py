from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult

_STRIPE_KEYWORDS = {
    "stripe", "checkout", "session", "webhook", "construct_event",
    "client_reference_id", "billing_address", "line_items",
}


def _is_stripe_write(tool_call: dict) -> bool:
    """Return True if this file_write tool call contains Stripe-specific code."""
    content = ""
    if isinstance(tool_call.get("input"), dict):
        content = tool_call["input"].get("content", "")
    return any(kw in content.lower() for kw in _STRIPE_KEYWORDS)


class G12Grader(BaseGrader):
    """
    Agent used corpus_search before writing any Stripe-specific code.
    Scans the transcript messages array.
    """

    grader_id = "G12"
    weight = 0.05

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        transcript = context.transcript

        corpus_search_turn = None
        first_stripe_write_turn = None

        for i, msg in enumerate(transcript):
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls", []):
                tool = tc.get("tool") or tc.get("function", {}).get("name", "")
                if tool == "corpus_search" and corpus_search_turn is None:
                    corpus_search_turn = i
                if tool == "file_write" and _is_stripe_write(tc) and first_stripe_write_turn is None:
                    first_stripe_write_turn = i

        evidence = {
            "corpus_search_turn": corpus_search_turn,
            "first_stripe_write_turn": first_stripe_write_turn,
        }

        if corpus_search_turn is None:
            evidence["note"] = "Agent never called corpus_search"
            return self._fail(evidence, t)

        if first_stripe_write_turn is None:
            # Agent searched but never wrote Stripe code — inconclusive, give benefit of doubt
            evidence["note"] = "corpus_search found but no Stripe file_write detected"
            return self._pass(evidence, t)

        if corpus_search_turn < first_stripe_write_turn:
            return self._pass(evidence, t)

        evidence["note"] = (
            f"corpus_search (turn {corpus_search_turn}) came after "
            f"first Stripe write (turn {first_stripe_write_turn})"
        )
        return self._fail(evidence, t)
