"""
Step 5 — Send outreach emails via Gmail and automatically retry bounces.

Usage:
    python scripts/send_emails.py <path-to-job.json>
    python scripts/send_emails.py <path-to-job.json> --resume resume.pdf --from-name "Your Name"
    python scripts/send_emails.py <path-to-job.json> --dry-run
    python scripts/send_emails.py <path-to-job.json> --no-wait
    python scripts/send_emails.py <path-to-job.json> --max-send 5

Flow:
  1. Load job + verify email draft exists
  2. (Optional) Re-draft email body with job URL embedded if --resume provided (once only)
  3. Show confirmation: recruiter list with LinkedIn URLs + full email preview → y/N
  4. Gmail OAuth (browser on first run, silent refresh after)
  5. Loop until all recruiters are delivered or all patterns exhausted:
       a. Send to eligible recruiters (new + retry-bounced)
       b. Poll Gmail every --wait-seconds for MAILER-DAEMON bounces
       c. If any bounced with untried patterns → retry automatically

First run opens a browser for Gmail OAuth consent.
Subsequent runs use the cached token at ~/.getmehired/gmail_token.json.

Gmail OAuth setup (one-time):
  1. Google Cloud Console → Enable Gmail API
  2. Create OAuth 2.0 Desktop credentials → Download client_secret.json
  3. Save as ~/.getmehired/gmail_credentials.json
  4. Run this script — browser opens for consent → token cached automatically
"""
from __future__ import annotations

import argparse
import asyncio
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from getmehired.agents.email_drafter import draft_email, make_subject
from getmehired.config import get_settings
from getmehired.services.gmail_sender import (
    _is_sendable,
    _next_address,
    _personalize_body,
    get_gmail_service,
    poll_bounces_loop,
    send_batch,
)
from getmehired.services.resume_reader import read_resume
from getmehired.services.storage import load, load_recruiters, save_email_draft

SEP = "─" * 64


def _section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def _ok(label: str, value: str) -> None:
    print(f"  ✓ {label:<22} {value}")


def _warn(label: str, value: str) -> None:
    print(f"  ⚠ {label:<22} {value}")


def _fail(label: str, value: str) -> None:
    print(f"  ✗ {label:<22} {value}")


async def main(
    job_path: Path,
    max_send: int,
    dry_run: bool,
    no_wait: bool,
    from_name: str,
    poll_interval: int,
    resume_path: Path | None = None,
) -> None:
    print(f"\n{'═' * 64}")
    print(f"  GetMeHired — Email Sender")
    print(f"{'═' * 64}")

    settings = get_settings()

    # ── STEP 1: Load job ──────────────────────────────────────────────────────
    _section("STEP 1 — Load Job")

    if not job_path.exists():
        _fail("File", f"Not found: {job_path}")
        sys.exit(1)

    job = load(job_path)
    recruiters = load_recruiters(job_path)
    _ok("Job Title", job.job_title)
    _ok("Company", job.company)

    if not job.email_subject or not job.email_body:
        _fail("Email draft", "Missing — run find_recruiters.py --resume <path> first")
        sys.exit(1)

    _ok("Subject", job.email_subject)
    _ok("Body", f"{len(job.email_body)} chars")

    total = len(recruiters)
    with_email = sum(1 for r in recruiters if r.email)
    already_sent = sum(1 for r in recruiters if r.email_sent_at)
    bounced = sum(1 for r in recruiters if r.email_bounced is True)
    eligible = sum(1 for r in recruiters if _is_sendable(r))

    _ok("Recruiters", f"{total} total, {with_email} with email")
    if already_sent:
        _ok("Already sent", f"{already_sent} (bounced: {bounced})")
    _ok("Eligible to send", str(eligible))

    if eligible == 0:
        _warn("Nothing to send", "All recruiters already delivered or all patterns exhausted.")
        sys.exit(0)

    # ── STEP 1b: Re-draft email (once only — not repeated on retry rounds) ────
    if resume_path:
        _section("STEP 1b — Re-draft Email")
        try:
            resume_text = read_resume(resume_path)
            _ok("Resume", f"{resume_path.name} ({len(resume_text):,} chars)")
        except (FileNotFoundError, ValueError) as e:
            _fail("Resume", str(e))
            sys.exit(1)

        t0 = time.perf_counter()
        try:
            new_body = await draft_email(job, resume_text, "there")
            elapsed = time.perf_counter() - t0
            new_subject = make_subject(job)
            save_email_draft(job_path, new_subject, new_body)
            job = load(job_path)
            _ok("Draft regenerated", f"in {elapsed:.1f}s")
            _ok("Subject", new_subject)
            _ok("Attachment", resume_path.name)
        except Exception as e:
            _fail("Draft failed", str(e))
            sys.exit(1)

    # ── STEP 2: Review & confirm before sending ───────────────────────────────
    if not dry_run:
        _section("STEP 2 — Review Before Sending")

        recruiters_preview = load_recruiters(job_path)
        all_sendable = [r for r in recruiters_preview if _is_sendable(r)]
        sendable = all_sendable[:max_send]

        print(f"\n  Recruiters that will receive this email ({len(sendable)}):\n")
        for i, r in enumerate(sendable, 1):
            addr = _next_address(r)
            print(f"  [{i}] {r.name}")
            print(f"       Email:    {addr}")
            print(f"       LinkedIn: {r.linkedin_url or '(not found)'}")

        if len(all_sendable) > max_send:
            remaining = len(all_sendable) - max_send
            print(f"\n  Sending to {max_send} of {len(all_sendable)} eligible recruiters this batch.")
            print(f"  Remaining {remaining} will be sent automatically in subsequent rounds.")

        preview_body = _personalize_body(
            job.email_body, sendable[0].name if sendable else "there", sender_name=from_name
        )
        print(f"\n  {'─' * 60}")
        print(f"  Subject: {job.email_subject}")
        print(f"  {'─' * 60}")
        for line in preview_body.splitlines():
            print(f"  {line}")
        if resume_path:
            print(f"\n  [Attachment: {resume_path.name}]")
        print(f"  {'─' * 60}\n")

        try:
            answer = input("  Send these emails? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(0)

        if answer != "y":
            print("  Cancelled — no emails sent.")
            sys.exit(0)

    # ── STEP 3: Gmail auth ────────────────────────────────────────────────────
    _section("STEP 3 — Gmail Auth")

    if dry_run:
        _ok("Skipped", "Dry run — no Gmail auth needed")
        service = None
    else:
        try:
            service = get_gmail_service()
            profile = service.users().getProfile(userId="me").execute()
            _ok("Authenticated as", profile.get("emailAddress", "unknown"))
        except FileNotFoundError as e:
            _fail("Credentials", str(e))
            sys.exit(1)
        except Exception as e:
            _fail("Auth failed", str(e))
            sys.exit(1)

    # ── STEP 4+: Send → poll → auto-retry loop ────────────────────────────────
    # Re-draft (STEP 1b) already happened once above — not repeated here.
    # The loop sends to eligible recruiters, polls for bounces, and retries
    # automatically until all are delivered or all patterns are exhausted.
    round_num = 0
    while True:
        round_num += 1
        label = f"STEP 4 — Send Emails{'  [DRY RUN]' if dry_run else ''}"
        if round_num > 1:
            label = f"STEP 4 — Retry Round {round_num}{'  [DRY RUN]' if dry_run else ''}"
        _section(label)

        t0 = time.perf_counter()
        try:
            recruiters = await send_batch(
                job_path, service, max_send=max_send, dry_run=dry_run,
                from_name=from_name, resume_path=resume_path,
            )
            elapsed = time.perf_counter() - t0
        except ValueError as e:
            _fail("Error", str(e))
            sys.exit(1)
        except Exception as e:
            _fail("Send failed", str(e))
            sys.exit(1)

        _ok("Status", f"Complete in {elapsed:.1f}s")
        if not dry_run:
            _ok("File updated", str(job_path))

        # Check if anything was actually sent this round
        sent_this_round = [r for r in recruiters if r.email_bounced is None and r.email_sent_at]
        if not sent_this_round or dry_run or no_wait:
            break

        # Poll for bounces until all sent-this-round have a definitive status
        _section(f"STEP 5 — Bounce Detection (polling every {poll_interval}s)")
        recruiters, _ = await poll_bounces_loop(
            job_path, service,
            poll_interval_seconds=poll_interval,
            lookback_minutes=settings.gmail_bounce_lookback_minutes,
        )

        # Check if any bounced recruiters still have untried patterns
        retryable = [r for r in recruiters if _is_sendable(r)]
        if not retryable:
            break  # all delivered or all patterns exhausted

        print(f"  {len(retryable)} recruiter(s) bounced with untried patterns — retrying automatically...\n")

    print(f"\n{'═' * 64}\n  Done.\n{'═' * 64}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send outreach emails via Gmail, poll for bounces, and retry automatically.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json \\\n"
            "      --resume ~/Downloads/resume.pdf --from-name 'Your Name'\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --dry-run\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --max-send 1 --wait-seconds 60\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --no-wait\n"
        ),
    )
    parser.add_argument("job_path", type=Path, help="Path to the job JSON file")
    parser.add_argument(
        "--max-send", type=int, default=None,
        help=(
            "Max emails to send per batch round (default: 3). "
            "Bounced recruiters are automatically retried with the next "
            "email pattern in subsequent rounds."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be sent without calling the Gmail API"
    )
    parser.add_argument(
        "--no-wait", action="store_true",
        help="Send emails then exit immediately — skip bounce polling and auto-retry"
    )
    parser.add_argument(
        "--from-name", type=str, default=None,
        help="Sender display name in From: header (default: GMAIL_SENDER_NAME in .env)"
    )
    parser.add_argument(
        "--wait-seconds", type=int, default=None,
        help="Seconds between each bounce poll (default: GMAIL_BOUNCE_POLL_INTERVAL_SECONDS in .env, 60)"
    )
    parser.add_argument(
        "--resume", type=Path, default=None,
        help="Path to resume PDF — attached to emails and triggers a fresh body re-draft with job URL"
    )

    args = parser.parse_args()
    settings = get_settings()

    asyncio.run(main(
        job_path=args.job_path,
        max_send=args.max_send or settings.gmail_max_send_per_run,
        dry_run=args.dry_run,
        no_wait=args.no_wait,
        from_name=args.from_name or settings.gmail_sender_name,
        poll_interval=args.wait_seconds or settings.gmail_bounce_poll_interval_seconds,
        resume_path=args.resume,
    ))
