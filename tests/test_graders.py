"""
Grader unit tests.

These tests mock the Stripe API and Flask app to avoid real network calls.
They test the grader logic in isolation using controlled inputs.

Run: pytest tests/ -v
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

import pytest

from harness.graders.base import GraderContext, GraderStatus
from harness.graders.g1_session_exists import G1Grader
from harness.graders.g2_line_items import G2Grader
from harness.graders.g3_urls import G3Grader
from harness.graders.g4_customer_email import G4Grader
from harness.graders.g5_webhook_route import G5Grader
from harness.graders.g6_webhook_behavior import G6aGrader, G6bGrader, G6cGrader, build_signed_event
from harness.graders.g7_db_write import G7Grader
from harness.graders.g8_idempotency import G8Grader
from harness.graders.g12_doc_retrieval import G12Grader
from harness.graders.g13_self_verification import G13Grader
from harness.graders.g14_turn_count import G14Grader
from harness.scoring import compute_overall_score, infer_failure_mode

WEBHOOK_SECRET = "whsec_test_secret_1234567890abcdef"
RUN_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SESSION_ID = "cs_test_fakesession0001"


def _make_context(db_path=None, app_url="http://localhost:5001", transcript=None, session_id=SESSION_ID):
    mock_stripe = MagicMock()
    ctx = GraderContext(
        stripe_client=mock_stripe,
        run_id=RUN_ID,
        db_path=db_path or ":memory:",
        app_url=app_url,
        webhook_secret=WEBHOOK_SECRET,
        variant="A",
        transcript=transcript or [],
        session_id=session_id,
    )
    return ctx


# ---------------------------------------------------------------------------
# G1
# ---------------------------------------------------------------------------

class TestG1(unittest.TestCase):
    def test_pass_when_session_found(self):
        ctx = _make_context(session_id=None)
        mock_session = MagicMock()
        mock_session.id = SESSION_ID
        mock_session.mode = "payment"
        mock_session.livemode = False
        mock_session.status = "open"
        mock_session.created = int(time.time())
        mock_session.client_reference_id = RUN_ID
        mock_session.get = lambda k, d=None: getattr(mock_session, k, d)

        ctx.stripe_client.checkout.Session.list.return_value.auto_paging_iter.return_value = [mock_session]

        result = G1Grader().run(ctx)
        assert result.status == GraderStatus.PASS
        assert ctx.session_id == SESSION_ID

    def test_fail_when_no_sessions(self):
        ctx = _make_context(session_id=None)
        ctx.stripe_client.checkout.Session.list.return_value.auto_paging_iter.return_value = []
        result = G1Grader().run(ctx)
        assert result.status == GraderStatus.FAIL

    def test_fail_when_wrong_mode(self):
        ctx = _make_context(session_id=None)
        mock_session = MagicMock()
        mock_session.id = SESSION_ID
        mock_session.mode = "subscription"
        mock_session.livemode = False
        mock_session.created = int(time.time())
        mock_session.client_reference_id = RUN_ID
        mock_session.get = lambda k, d=None: getattr(mock_session, k, d)

        ctx.stripe_client.checkout.Session.list.return_value.auto_paging_iter.return_value = [mock_session]
        result = G1Grader().run(ctx)
        assert result.status == GraderStatus.FAIL


# ---------------------------------------------------------------------------
# G3
# ---------------------------------------------------------------------------

class TestG3(unittest.TestCase):
    def test_pass_with_correct_urls(self):
        ctx = _make_context()
        mock_session = MagicMock()
        mock_session.success_url = "http://localhost:5000/success?session_id={CHECKOUT_SESSION_ID}"
        mock_session.cancel_url = "http://localhost:5000/cancel"
        ctx.stripe_client.checkout.Session.retrieve.return_value = mock_session
        result = G3Grader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_fail_when_placeholder_missing(self):
        ctx = _make_context()
        mock_session = MagicMock()
        mock_session.success_url = "http://localhost:5000/success"
        mock_session.cancel_url = "http://localhost:5000/cancel"
        ctx.stripe_client.checkout.Session.retrieve.return_value = mock_session
        result = G3Grader().run(ctx)
        assert result.status == GraderStatus.FAIL

    def test_skip_when_no_session_id(self):
        ctx = _make_context(session_id=None)
        result = G3Grader().run(ctx)
        assert result.status == GraderStatus.SKIPPED


# ---------------------------------------------------------------------------
# G6a, G6b, G6c — require a real (or mocked) Flask server
# These tests use responses mock to avoid real HTTP
# ---------------------------------------------------------------------------

class TestG6Behavior(unittest.TestCase):
    def test_g6a_pass_on_400(self):
        ctx = _make_context()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.text = '{"error": "Invalid signature"}'
            mock_post.return_value = mock_resp
            result = G6aGrader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_g6a_fail_on_200(self):
        ctx = _make_context()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"status": "success"}'
            mock_post.return_value = mock_resp
            result = G6aGrader().run(ctx)
        assert result.status == GraderStatus.FAIL

    def test_g6b_pass_on_200(self):
        ctx = _make_context()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"status": "success"}'
            mock_post.return_value = mock_resp
            result = G6bGrader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_g6c_pass_on_200(self):
        ctx = _make_context()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"status": "ignored"}'
            mock_post.return_value = mock_resp
            result = G6cGrader().run(ctx)
        assert result.status == GraderStatus.PASS


# ---------------------------------------------------------------------------
# G7, G8 — use temp SQLite DB
# ---------------------------------------------------------------------------

class TestG7G8(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.tmp.name
        self.tmp.close()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL UNIQUE, "
            "customer_email TEXT, amount_total INTEGER, created_at TEXT DEFAULT (datetime('now')))"
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        os.unlink(self.db_path)

    def _make_ctx_with_db(self):
        return _make_context(db_path=self.db_path)

    def test_g7_pass_when_row_exists(self):
        ctx = self._make_ctx_with_db()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"status": "success"}'
            mock_post.return_value = mock_resp

            # Pre-insert the row (simulating the webhook handler writing it)
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "INSERT INTO orders (session_id, customer_email, amount_total) VALUES (?,?,?)",
                (SESSION_ID, "test@test.com", 2900),
            )
            conn.commit()
            conn.close()

            result = G7Grader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_g7_fail_when_no_row(self):
        ctx = self._make_ctx_with_db()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"status": "success"}'
            mock_post.return_value = mock_resp
            result = G7Grader().run(ctx)
        assert result.status == GraderStatus.FAIL

    def test_g8_pass_on_one_row(self):
        ctx = self._make_ctx_with_db()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO orders (session_id, customer_email, amount_total) VALUES (?,?,?)",
            (SESSION_ID, "test@test.com", 2900),
        )
        conn.commit()
        conn.close()

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = '{"status": "success"}'
            mock_post.return_value = mock_resp
            result = G8Grader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_g8_fail_on_two_rows(self):
        ctx = self._make_ctx_with_db()
        # Force two rows by bypassing UNIQUE (use a second session_id variant)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO orders (session_id, customer_email, amount_total) VALUES (?,?,?)",
            (SESSION_ID, "first@test.com", 2900),
        )
        # Drop UNIQUE constraint by inserting into a different table temporarily — actually
        # just create a second row with a slightly different session_id to simulate duplicate
        conn.execute(
            "INSERT INTO orders (session_id, customer_email, amount_total) VALUES (?,?,?)",
            (SESSION_ID + "_dup", "second@test.com", 2900),
        )
        conn.commit()
        conn.close()

        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            # Simulate G8 finding 2 rows for SESSION_ID by monkeypatching sqlite3
            original_connect = sqlite3.connect

            def fake_connect(path):
                conn = original_connect(path)
                original_execute = conn.execute

                def fake_execute(sql, params=()):
                    if "WHERE session_id" in sql:
                        # Return 2 rows to simulate idempotency failure
                        class FakeResult:
                            def fetchall(self):
                                return [("row1",), ("row2",)]
                        return FakeResult()
                    return original_execute(sql, params)

                conn.execute = fake_execute
                return conn

            with patch("harness.graders.g8_idempotency.sqlite3.connect", side_effect=fake_connect):
                result = G8Grader().run(ctx)

        assert result.status == GraderStatus.FAIL


# ---------------------------------------------------------------------------
# Transcript graders
# ---------------------------------------------------------------------------

class TestTranscriptGraders(unittest.TestCase):
    def _make_transcript(self, has_search_before_write=True, has_verification=True):
        transcript = []
        if has_search_before_write:
            transcript.append({
                "turn": 1, "role": "assistant", "content": "Searching docs",
                "tool_calls": [{"tool": "corpus_search", "input": {"query": "stripe webhook"}, "output": {}}],
            })
        transcript.append({
            "turn": 2, "role": "assistant", "content": "Writing code",
            "tool_calls": [{"tool": "file_write", "input": {"path": "app.py", "content": "stripe.checkout"}, "output": {}}],
        })
        if not has_search_before_write:
            transcript.insert(0, {
                "turn": 0, "role": "assistant", "content": "Writing first",
                "tool_calls": [{"tool": "file_write", "input": {"path": "app.py", "content": "stripe.checkout"}, "output": {}}],
            })
            transcript.append({
                "turn": 3, "role": "assistant", "content": "Late search",
                "tool_calls": [{"tool": "corpus_search", "input": {"query": "stripe"}, "output": {}}],
            })
        if has_verification:
            transcript.append({
                "turn": 3, "role": "assistant", "content": "Testing",
                "tool_calls": [{"tool": "shell_exec", "input": {"command": "flask run --port 5001"}, "output": {}}],
            })
        return transcript

    def test_g12_pass_when_search_before_write(self):
        ctx = _make_context(transcript=self._make_transcript(has_search_before_write=True))
        result = G12Grader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_g12_fail_when_write_before_search(self):
        ctx = _make_context(transcript=self._make_transcript(has_search_before_write=False))
        result = G12Grader().run(ctx)
        assert result.status == GraderStatus.FAIL

    def test_g13_pass_when_verification_present(self):
        ctx = _make_context(transcript=self._make_transcript(has_verification=True))
        result = G13Grader().run(ctx)
        assert result.status == GraderStatus.PASS

    def test_g13_fail_when_no_verification(self):
        ctx = _make_context(transcript=self._make_transcript(has_verification=False))
        result = G13Grader().run(ctx)
        assert result.status == GraderStatus.FAIL

    def test_g14_always_passes(self):
        ctx = _make_context(transcript=self._make_transcript())
        result = G14Grader().run(ctx)
        assert result.status == GraderStatus.PASS
        assert result.score == 1.0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

class TestScoring(unittest.TestCase):
    def _make_result(self, gid, status, score, weight):
        from harness.graders.base import GraderResult
        return GraderResult(
            grader_id=gid,
            status=status,
            score=score,
            weight=weight,
            weighted_score=score * weight,
            evidence={},
            error=None,
            duration_ms=10,
        )

    def test_perfect_score(self):
        results = [
            self._make_result("G1", GraderStatus.PASS, 1.0, 0.12),
            self._make_result("G2", GraderStatus.PASS, 1.0, 0.10),
        ]
        score = compute_overall_score(results)
        assert score == 1.0

    def test_normalized_score_ignores_skipped(self):
        results = [
            self._make_result("G1", GraderStatus.PASS, 1.0, 0.12),
            self._make_result("G2", GraderStatus.SKIPPED, 0.0, 0.10),
        ]
        score = compute_overall_score(results)
        assert score == 1.0  # only G1 is active

    def test_failure_mode_knowledge_gap(self):
        results = [
            self._make_result("G6a", GraderStatus.FAIL, 0.0, 0.07),
            self._make_result("G6b", GraderStatus.PASS, 1.0, 0.07),
        ]
        mode = infer_failure_mode(results)
        assert mode == "knowledge_gap"


# ---------------------------------------------------------------------------
# build_signed_event
# ---------------------------------------------------------------------------

class TestBuildSignedEvent(unittest.TestCase):
    def test_signature_verifiable(self):
        """The signature produced by build_signed_event must be valid."""
        import stripe
        payload_bytes, sig_header = build_signed_event(
            event_type="checkout.session.completed",
            session_id="cs_test_abc123",
            customer_email="test@test.com",
            amount_total=2900,
            webhook_secret=WEBHOOK_SECRET,
        )
        # stripe.Webhook.construct_event should NOT raise
        event = stripe.Webhook.construct_event(
            payload_bytes, sig_header, WEBHOOK_SECRET
        )
        assert event["type"] == "checkout.session.completed"


if __name__ == "__main__":
    unittest.main()
