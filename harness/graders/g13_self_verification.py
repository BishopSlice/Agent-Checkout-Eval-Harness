from __future__ import annotations

import time

from .base import BaseGrader, GraderContext, GraderResult

_VERIFICATION_COMMANDS = {
    "flask run", "python app.py", "python -m flask",
    "curl ", "requests.", "stripe_api",
}


def _is_post_code_verification(msg: dict, code_written_by: int, turn: int) -> bool:
    """
    Return True if this assistant message (after code was written) contains
    a verification action: shell_exec running Flask, stripe_api call,
    or shell command with curl/requests.
    """
    if turn <= code_written_by:
        return False
    for tc in msg.get("tool_calls", []):
        tool = tc.get("tool") or tc.get("function", {}).get("name", "")
        if tool == "stripe_api":
            return True
        if tool == "shell_exec":
            cmd = ""
            if isinstance(tc.get("input"), dict):
                cmd = tc["input"].get("command", "").lower()
            if any(kw in cmd for kw in _VERIFICATION_COMMANDS):
                return True
    return False


class G13Grader(BaseGrader):
    """
    Agent attempted to verify its implementation after writing code:
    ran Flask, sent a test request, or called stripe_api to inspect output.
    """

    grader_id = "G13"
    weight = 0.07

    def run(self, context: GraderContext) -> GraderResult:
        t = time.time()
        transcript = context.transcript

        last_code_write_turn = None
        for i, msg in enumerate(transcript):
            if msg.get("role") != "assistant":
                continue
            for tc in msg.get("tool_calls", []):
                tool = tc.get("tool") or tc.get("function", {}).get("name", "")
                if tool == "file_write":
                    last_code_write_turn = i

        evidence = {"last_code_write_turn": last_code_write_turn}

        if last_code_write_turn is None:
            evidence["note"] = "No file_write found — agent did not write any code"
            return self._fail(evidence, t)

        for i, msg in enumerate(transcript):
            if msg.get("role") != "assistant":
                continue
            if _is_post_code_verification(msg, last_code_write_turn, i):
                evidence["verification_turn"] = i
                return self._pass(evidence, t)

        evidence["note"] = (
            "No verification action found after code was written. "
            "Agent did not run Flask, call stripe_api, or send a test request."
        )
        return self._fail(evidence, t)
