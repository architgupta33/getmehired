"""
Unit tests for bounce detection logic in gmail_sender.py.

Covers:
  - _extract_failed_recipient: header parsing edge cases
  - _is_sendable: eligibility logic
  - _next_address: pattern selection
  - check_bounces: full bounce-state update logic (service mocked)

No Gmail API calls are made — the Gmail service is fully mocked.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from getmehired.models.recruiter import Recruiter
from getmehired.services.gmail_sender import (
    _extract_failed_recipient,
    _is_sendable,
    _next_address,
    check_bounces,
    poll_bounces_loop,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_recruiter(
    name: str = "Test User",
    email: str = "test@example.com",
    sent_to: Optional[str] = None,
    sent_at: Optional[datetime] = None,
    bounced: Optional[bool] = None,
    tried: Optional[list[str]] = None,
) -> Recruiter:
    return Recruiter(
        name=name,
        email=email,
        email_sent_to=sent_to,
        email_sent_at=sent_at,
        email_bounced=bounced,
        email_tried=tried or [],
    )


def _make_job_json(recruiters: list[Recruiter], tmp_dir: Path) -> Path:
    """Write a minimal job JSON with the given recruiters to a temp file."""
    data = {
        "job_title": "Data Scientist",
        "company": "Acme",
        "job_family": "Data Science / ML",
        "url": "https://example.com/job",
        "platform": "greenhouse",
        "scraped_at": "2026-01-01T00:00:00Z",
        "email_subject": "Test Subject",
        "email_body": "Hi there,\nTest body.\n\nBest,",
        "recruiters": [json.loads(r.model_dump_json()) for r in recruiters],
    }
    path = tmp_dir / "test_job.json"
    path.write_text(json.dumps(data))
    return path


def _mock_service(bounce_addresses: list[str]):
    """
    Return a mock Gmail service that simulates finding bounce messages
    for the given list of email addresses.
    """
    service = MagicMock()

    # messages().list() returns one fake message per bounced address
    msg_list = [{"id": f"msg_{i}"} for i, _ in enumerate(bounce_addresses)]
    service.users().messages().list().execute.return_value = {"messages": msg_list}

    def get_message(userId, id, format, metadataHeaders):
        idx = int(id.split("_")[1])
        addr = bounce_addresses[idx]
        mock_get = MagicMock()
        mock_get.execute.return_value = {
            "payload": {
                "headers": [{"name": "X-Failed-Recipients", "value": addr}]
            }
        }
        return mock_get

    service.users().messages().get.side_effect = get_message
    return service


NOW = datetime.now(timezone.utc)
RECENT = NOW - timedelta(minutes=5)
OLD = NOW - timedelta(hours=2)


# ── _extract_failed_recipient ──────────────────────────────────────────────────

class TestExtractFailedRecipient:
    def test_x_failed_recipients_header(self):
        headers = [{"name": "X-Failed-Recipients", "value": "foo@bar.com"}]
        assert _extract_failed_recipient(headers) == "foo@bar.com"

    def test_falls_back_to_to_header(self):
        headers = [{"name": "To", "value": "foo@bar.com"}]
        assert _extract_failed_recipient(headers) == "foo@bar.com"

    def test_to_header_with_display_name(self):
        headers = [{"name": "To", "value": "Foo Bar <foo@bar.com>"}]
        assert _extract_failed_recipient(headers) == "foo@bar.com"

    def test_x_failed_takes_priority_over_to(self):
        headers = [
            {"name": "X-Failed-Recipients", "value": "real@fail.com"},
            {"name": "To", "value": "other@decoy.com"},
        ]
        assert _extract_failed_recipient(headers) == "real@fail.com"

    def test_returns_none_when_no_useful_headers(self):
        headers = [{"name": "Subject", "value": "Mail delivery failed"}]
        assert _extract_failed_recipient(headers) is None

    def test_empty_headers(self):
        assert _extract_failed_recipient([]) is None

    def test_case_insensitive_header_name(self):
        headers = [{"name": "x-failed-recipients", "value": "foo@bar.com"}]
        assert _extract_failed_recipient(headers) == "foo@bar.com"

    def test_strips_whitespace(self):
        headers = [{"name": "X-Failed-Recipients", "value": "  foo@bar.com  "}]
        assert _extract_failed_recipient(headers) == "foo@bar.com"


# ── _is_sendable ───────────────────────────────────────────────────────────────

class TestIsSendable:
    def test_never_sent_is_sendable(self):
        r = _make_recruiter(email="a@b.com")
        assert _is_sendable(r) is True

    def test_no_email_not_sendable(self):
        r = _make_recruiter(email="")
        assert _is_sendable(r) is False

    def test_pending_bounce_check_not_sendable(self):
        # email_sent_at set but email_bounced=None → still waiting for bounce result
        r = _make_recruiter(email="a@b.com", sent_to="a@b.com", sent_at=RECENT,
                            bounced=None, tried=["a@b.com"])
        assert _is_sendable(r) is False

    def test_confirmed_delivered_not_sendable(self):
        r = _make_recruiter(email="a@b.com", sent_to="a@b.com", sent_at=RECENT,
                            bounced=False, tried=["a@b.com"])
        assert _is_sendable(r) is False

    def test_bounced_with_untried_pattern_is_sendable(self):
        # Comma-separated patterns; first tried and bounced, second untried
        r = _make_recruiter(email="a@b.com,b@b.com", sent_to="a@b.com", sent_at=RECENT,
                            bounced=True, tried=["a@b.com"])
        assert _is_sendable(r) is True

    def test_bounced_all_patterns_exhausted_not_sendable(self):
        r = _make_recruiter(email="a@b.com,b@b.com", sent_to="b@b.com", sent_at=RECENT,
                            bounced=True, tried=["a@b.com", "b@b.com"])
        assert _is_sendable(r) is False

    def test_multiple_patterns_none_tried_is_sendable(self):
        r = _make_recruiter(email="a@b.com,b@b.com,c@b.com")
        assert _is_sendable(r) is True


# ── _next_address ──────────────────────────────────────────────────────────────

class TestNextAddress:
    def test_single_address_not_tried(self):
        r = _make_recruiter(email="a@b.com")
        assert _next_address(r) == "a@b.com"

    def test_picks_first_untried(self):
        r = _make_recruiter(email="a@b.com,b@b.com,c@b.com", tried=["a@b.com"])
        assert _next_address(r) == "b@b.com"

    def test_all_tried_returns_none(self):
        r = _make_recruiter(email="a@b.com,b@b.com", tried=["a@b.com", "b@b.com"])
        assert _next_address(r) is None

    def test_no_email_returns_none(self):
        r = _make_recruiter(email="")
        assert _next_address(r) is None

    def test_handles_whitespace_in_csv(self):
        r = _make_recruiter(email="a@b.com , b@b.com", tried=["a@b.com"])
        assert _next_address(r) == "b@b.com"


# ── check_bounces ──────────────────────────────────────────────────────────────

class TestCheckBounces:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_bounce_detected_sets_email_bounced_true(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=None,
                            tried=["alice@acme.com"], email="alice@acme.com,a.alice@acme.com")
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["alice@acme.com"])

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        assert count == 1
        assert recruiters[0].email_bounced is True

    def test_no_bounce_sets_email_bounced_false(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=None,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service([])  # no bounces in Gmail

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        assert count == 0
        assert recruiters[0].email_bounced is False

    def test_case_insensitive_matching(self, tmp_path):
        # Sent to lowercase; Gmail returns uppercase in bounce header
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=None,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["ALICE@ACME.COM"])

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        assert count == 1
        assert recruiters[0].email_bounced is True

    def test_bounce_for_different_address_not_matched(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=None,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["bob@acme.com"])  # different address bounced

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        # alice is still pending (no bounce found → False, confirmed delivered)
        assert count == 0
        assert recruiters[0].email_bounced is False

    def test_sent_outside_lookback_window_not_touched(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=OLD, bounced=None,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["alice@acme.com"])

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        # Sent 2h ago, lookback=30m → outside window → not touched
        assert count == 0
        assert recruiters[0].email_bounced is None

    def test_already_bounced_not_recounted(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=True,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["alice@acme.com"])

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        # Already bounced — still True, count reflects current state
        assert recruiters[0].email_bounced is True
        assert count == 1

    def test_already_delivered_not_re_evaluated(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=False,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["alice@acme.com"])  # bounce arrives after we marked delivered

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        # Already confirmed delivered — not overwritten to True
        # (cutoff check: sent_at is recent so it's in the window, but bounced is False)
        assert recruiters[0].email_bounced is False

    def test_never_sent_recruiter_is_skipped(self, tmp_path):
        r = _make_recruiter()  # no sent_to, no sent_at
        path = _make_job_json([r], tmp_path)
        service = _mock_service([])

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        assert count == 0
        assert recruiters[0].email_bounced is None  # untouched

    def test_mixed_batch(self, tmp_path):
        # 3 recruiters: one bounced, one delivered, one never sent
        alice = _make_recruiter("Alice", "alice@a.com,a.alice@a.com",
                                sent_to="alice@a.com", sent_at=RECENT, bounced=None,
                                tried=["alice@a.com"])
        bob = _make_recruiter("Bob", "bob@b.com",
                              sent_to="bob@b.com", sent_at=RECENT, bounced=None,
                              tried=["bob@b.com"])
        carol = _make_recruiter("Carol", "carol@c.com")  # never sent

        path = _make_job_json([alice, bob, carol], tmp_path)
        service = _mock_service(["alice@a.com"])  # only alice bounced

        recruiters, count = self._run(check_bounces(path, service, lookback_minutes=30))

        alice_r = next(r for r in recruiters if r.name == "Alice")
        bob_r = next(r for r in recruiters if r.name == "Bob")
        carol_r = next(r for r in recruiters if r.name == "Carol")

        assert alice_r.email_bounced is True
        assert bob_r.email_bounced is False
        assert carol_r.email_bounced is None
        assert count == 1

    def test_json_persisted_after_bounce_detected(self, tmp_path):
        r = _make_recruiter(sent_to="alice@acme.com", sent_at=RECENT, bounced=None,
                            tried=["alice@acme.com"])
        path = _make_job_json([r], tmp_path)
        service = _mock_service(["alice@acme.com"])

        self._run(check_bounces(path, service, lookback_minutes=30))

        # Reload from disk and verify persisted
        data = json.loads(path.read_text())
        assert data["recruiters"][0]["email_bounced"] is True


# ── poll_bounces_loop ──────────────────────────────────────────────────────────

class TestPollBouncesLoop:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_stops_immediately_when_no_pending(self, tmp_path):
        # All recruiters already have definitive status — loop should exit without polling
        alice = _make_recruiter(sent_to="alice@a.com", sent_at=RECENT, bounced=False,
                                tried=["alice@a.com"])
        path = _make_job_json([alice], tmp_path)
        # Use a plain MagicMock (no pre-wired return values) — any real API call would fail
        service = MagicMock()
        service.users().messages().list().execute.return_value = {"messages": []}

        # Record call count before running loop
        calls_before = service.users().messages().list().execute.call_count

        recruiters, count = self._run(
            poll_bounces_loop(path, service, poll_interval_seconds=1, lookback_minutes=30)
        )

        assert count == 0
        assert recruiters[0].email_bounced is False
        # execute() should not have been called with a real query (no pending emails)
        calls_after = service.users().messages().list().execute.call_count
        assert calls_after == calls_before

    def test_single_poll_detects_bounce(self, tmp_path):
        # Pending on first check → bounced after one poll
        alice = _make_recruiter("Alice", "alice@a.com,a.alice@a.com",
                                sent_to="alice@a.com", sent_at=RECENT, bounced=None,
                                tried=["alice@a.com"])
        path = _make_job_json([alice], tmp_path)
        service = _mock_service(["alice@a.com"])

        recruiters, count = self._run(
            poll_bounces_loop(path, service, poll_interval_seconds=1, lookback_minutes=30)
        )

        assert count == 1
        assert recruiters[0].email_bounced is True

    def test_single_poll_marks_delivered(self, tmp_path):
        # Pending → no bounce in Gmail → marked delivered, loop exits
        alice = _make_recruiter(sent_to="alice@a.com", sent_at=RECENT, bounced=None,
                                tried=["alice@a.com"])
        path = _make_job_json([alice], tmp_path)
        service = _mock_service([])  # no bounces

        recruiters, count = self._run(
            poll_bounces_loop(path, service, poll_interval_seconds=1, lookback_minutes=30)
        )

        assert count == 0
        assert recruiters[0].email_bounced is False

    def test_exits_on_keyboard_interrupt(self, tmp_path):
        # Simulate Ctrl+C during the countdown — should return gracefully
        alice = _make_recruiter(sent_to="alice@a.com", sent_at=RECENT, bounced=None,
                                tried=["alice@a.com"])
        path = _make_job_json([alice], tmp_path)

        # Service raises KeyboardInterrupt when polled
        service = MagicMock()
        service.users().messages().list().execute.side_effect = KeyboardInterrupt

        # Should not raise — must catch KeyboardInterrupt and return
        recruiters, count = self._run(
            poll_bounces_loop(path, service, poll_interval_seconds=1, lookback_minutes=30)
        )
        assert isinstance(recruiters, list)

    def test_mixed_batch_all_resolve_in_one_poll(self, tmp_path):
        alice = _make_recruiter("Alice", "alice@a.com,a.alice@a.com",
                                sent_to="alice@a.com", sent_at=RECENT, bounced=None,
                                tried=["alice@a.com"])
        bob = _make_recruiter("Bob", "bob@b.com",
                              sent_to="bob@b.com", sent_at=RECENT, bounced=None,
                              tried=["bob@b.com"])
        path = _make_job_json([alice, bob], tmp_path)
        service = _mock_service(["alice@a.com"])  # alice bounces, bob delivers

        recruiters, count = self._run(
            poll_bounces_loop(path, service, poll_interval_seconds=1, lookback_minutes=30)
        )

        alice_r = next(r for r in recruiters if r.name == "Alice")
        bob_r = next(r for r in recruiters if r.name == "Bob")
        assert alice_r.email_bounced is True
        assert bob_r.email_bounced is False
        assert count == 1

    def test_never_sent_recruiter_ignored_throughout(self, tmp_path):
        sent = _make_recruiter("Alice", "alice@a.com",
                               sent_to="alice@a.com", sent_at=RECENT, bounced=None,
                               tried=["alice@a.com"])
        unsent = _make_recruiter("Bob", "bob@b.com")  # never sent
        path = _make_job_json([sent, unsent], tmp_path)
        service = _mock_service([])

        recruiters, count = self._run(
            poll_bounces_loop(path, service, poll_interval_seconds=1, lookback_minutes=30)
        )

        bob_r = next(r for r in recruiters if r.name == "Bob")
        assert bob_r.email_bounced is None  # untouched


# ── Auto-retry logic (integration of _is_sendable + _next_address) ────────────

class TestAutoRetryLogic:
    """
    These tests verify the core logic the auto-retry loop in send_emails.py
    depends on: after a bounce, _is_sendable returns True and _next_address
    returns the next untried pattern.
    """

    def test_sendable_and_next_address_after_bounce(self):
        # First pattern tried and bounced; second pattern untried
        r = _make_recruiter(
            email="alice@a.com,a.alice@a.com",
            sent_to="alice@a.com", sent_at=RECENT,
            bounced=True, tried=["alice@a.com"],
        )
        assert _is_sendable(r) is True
        assert _next_address(r) == "a.alice@a.com"

    def test_not_sendable_and_no_address_when_all_exhausted(self):
        # Both patterns tried and bounced — loop should stop
        r = _make_recruiter(
            email="alice@a.com,a.alice@a.com",
            sent_to="a.alice@a.com", sent_at=RECENT,
            bounced=True, tried=["alice@a.com", "a.alice@a.com"],
        )
        assert _is_sendable(r) is False
        assert _next_address(r) is None

    def test_loop_stops_when_all_delivered(self):
        # Both recruiters delivered — no retryable ones
        alice = _make_recruiter("Alice", "alice@a.com",
                                sent_to="alice@a.com", sent_at=RECENT,
                                bounced=False, tried=["alice@a.com"])
        bob = _make_recruiter("Bob", "bob@b.com",
                              sent_to="bob@b.com", sent_at=RECENT,
                              bounced=False, tried=["bob@b.com"])
        retryable = [r for r in [alice, bob] if _is_sendable(r)]
        assert retryable == []

    def test_loop_continues_while_any_bounced_with_patterns(self):
        # Alice bounced with untried pattern, Bob delivered — loop should continue (alice retryable)
        alice = _make_recruiter("Alice", "alice@a.com,a.alice@a.com",
                                sent_to="alice@a.com", sent_at=RECENT,
                                bounced=True, tried=["alice@a.com"])
        bob = _make_recruiter("Bob", "bob@b.com",
                              sent_to="bob@b.com", sent_at=RECENT,
                              bounced=False, tried=["bob@b.com"])
        retryable = [r for r in [alice, bob] if _is_sendable(r)]
        assert len(retryable) == 1
        assert retryable[0].name == "Alice"
        assert _next_address(retryable[0]) == "a.alice@a.com"
