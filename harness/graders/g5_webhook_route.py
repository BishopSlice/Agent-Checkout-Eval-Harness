from __future__ import annotations

import time

import requests as http_requests

from .base import BaseGrader, GraderContext, GraderResult


class G5Grader(BaseGrader):
    """
    POST to /webhook returns non-404.
    400 (missing sig) = PASS. 405 (wrong method) = FAIL.
    """

    grader_id = "G5"
    weight = 0.04

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        try:
            resp = http_requests.post(
                f"{context.app_url}/webhook",
                json={},
                timeout=5,
            )
        except http_requests.ConnectionError:
            return self._error("Could not connect to Flask app", {}, t)
        except http_requests.Timeout:
            return self._error("Request to /webhook timed out", {}, t)

        evidence = {"status_code": resp.status_code}

        if resp.status_code == 404:
            evidence["note"] = "/webhook route not found"
            return self._fail(evidence, t)

        if resp.status_code == 405:
            evidence["note"] = "Method Not Allowed — route may only accept GET"
            return self._fail(evidence, t)

        return self._pass(evidence, t)
