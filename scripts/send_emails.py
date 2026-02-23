"""
Step 5 — Send outreach emails via Gmail and monitor for bounces.

Usage:
    python scripts/send_emails.py <path-to-job.json>
    python scripts/send_emails.py <path-to-job.json> --max-send 3
    python scripts/send_emails.py <path-to-job.json> --dry-run
    python scripts/send_emails.py <path-to-job.json> --no-wait
    python scripts/send_emails.py <path-to-job.json> --check-bounces
    python scripts/send_emails.py <path-to-job.json> --retry-bounced
    python scripts/send_emails.py <path-to-job.json> --from-name "Archit Gupta"

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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from getmehired.agents.email_drafter import draft_email, make_subject
from getmehired.config import get_settings
from getmehired.services.gmail_sender import (
    _is_sendable,
    _next_address,
    _personalize_body,
    check_bounces,
    get_gmail_service,
    send_batch,
    wait_with_countdown,
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
    check_bounces_only: bool,
    retry_bounced: bool,
    from_name: str,
    wait_seconds: int,
    resume_path: Path | None = None,
) -> None:
    print(f"\n{'═' * 64}")
    print(f"  GetMeHired — Email Sender")
    print(f"{'═' * 64}")

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

    if eligible == 0 and not check_bounces_only:
        _warn("Nothing to send", "All recruiters already sent or no email addresses found.")
        if not retry_bounced:
            sys.exit(0)

    # ── STEP 1b: Re-draft email if --resume provided ───────────────────────────
    # Regenerates body (with job URL embedded) + updates subject to CTA format.
    if resume_path and not check_bounces_only:
        _section("STEP 1b — Re-draft Email")
        try:
            resume_text = read_resume(resume_path)
            _ok("Resume", f"{resume_path.name} ({len(resume_text):,} chars)")
        except (FileNotFoundError, ValueError) as e:
            _fail("Resume", str(e))
            sys.exit(1)

        import time as _time
        t0 = _time.perf_counter()
        try:
            new_body = await draft_email(job, resume_text, "there")
            elapsed = _time.perf_counter() - t0
            new_subject = make_subject(job)
            save_email_draft(job_path, new_subject, new_body)
            job = load(job_path)  # reload with updated fields
            _ok("Draft regenerated", f"in {elapsed:.1f}s")
            _ok("Subject", new_subject)
            _ok("Attachment", resume_path.name)
        except Exception as e:
            _fail("Draft failed", str(e))
            sys.exit(1)

    # ── STEP 2: Review & confirm before sending ───────────────────────────────
    if not dry_run and not check_bounces_only:
        _section("STEP 2 — Review Before Sending")

        # Show recruiter list with LinkedIn URLs
        recruiters_preview = load_recruiters(job_path)
        sendable = [r for r in recruiters_preview if _is_sendable(r)][:max_send]

        print(f"\n  Recruiters that will receive this email ({len(sendable)}):\n")
        for i, r in enumerate(sendable, 1):
            addr = _next_address(r)
            print(f"  [{i}] {r.name}")
            print(f"       Email:    {addr}")
            if r.linkedin_url:
                print(f"       LinkedIn: {r.linkedin_url}")
            else:
                print(f"       LinkedIn: (not found)")

        # Show full email (personalized with first recruiter's name)
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
        print(f"  {'─' * 60}")

        print()
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
            # Get authed address for display
            profile = service.users().getProfile(userId="me").execute()
            authed_email = profile.get("emailAddress", "unknown")
            _ok("Authenticated as", authed_email)
        except FileNotFoundError as e:
            _fail("Credentials", str(e))
            sys.exit(1)
        except Exception as e:
            _fail("Auth failed", str(e))
            sys.exit(1)

    # ── STEP 4: Check bounces (if --retry-bounced or --check-bounces) ─────────
    settings = get_settings()

    if retry_bounced or check_bounces_only:
        _section("STEP 4 — Check Bounces (pre-send)")
        t0 = time.perf_counter()
        recruiters, bounce_count = await check_bounces(
            job_path, service, settings.gmail_bounce_lookback_minutes
        )
        elapsed = time.perf_counter() - t0
        _ok("Poll complete", f"in {elapsed:.1f}s")
        _ok("Bounces found", str(bounce_count))
        _ok("File updated", str(job_path))

        if check_bounces_only:
            for r in recruiters:
                if r.email_sent_to:
                    status = "bounced" if r.email_bounced else ("pending" if r.email_bounced is None else "delivered")
                    print(f"\n  {r.name}")
                    print(f"       Sent to: {r.email_sent_to}")
                    print(f"       Status:  {status}")
            print(f"\n{'═' * 64}\n  Done.\n{'═' * 64}\n")
            return

    # ── STEP 5: Send emails ───────────────────────────────────────────────────
    if not check_bounces_only:
        _section(f"STEP {'5' if retry_bounced else '4'} — Send Emails{'  [DRY RUN]' if dry_run else ''}")

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

        sent_this_run = sum(1 for r in recruiters if r.email_sent_at and r.email_bounced is None)
        _ok("Status", f"Complete in {elapsed:.1f}s")
        if not dry_run:
            _ok("File updated", str(job_path))

        if dry_run or no_wait:
            print(f"\n{'═' * 64}\n  Done.\n{'═' * 64}\n")
            return

    # ── STEP 5: Wait + bounce detection ──────────────────────────────────────
    _section(f"STEP {'5' if retry_bounced else '4'} — Wait for Bounces")

    await wait_with_countdown(wait_seconds)

    _section(f"STEP {'6' if retry_bounced else '5'} — Check Bounces")

    t0 = time.perf_counter()
    recruiters, bounce_count = await check_bounces(
        job_path, service, settings.gmail_bounce_lookback_minutes
    )
    elapsed = time.perf_counter() - t0

    _ok("Poll complete", f"in {elapsed:.1f}s")
    _ok("File updated", str(job_path))

    if bounce_count == 0:
        _ok("Bounces", "None detected — emails appear delivered")
    else:
        _warn("Bounces", f"{bounce_count} bounce(s) detected")
        for r in recruiters:
            if r.email_bounced:
                candidates = [e.strip() for e in r.email.split(",") if e.strip()]
                untried = [e for e in candidates if e not in r.email_tried]
                if untried:
                    _warn(r.name, f"bounced → next pattern: {untried[0]}")
                else:
                    _warn(r.name, "bounced → all patterns exhausted")
        print(f"\n  Re-run with --retry-bounced to send to next email pattern.")

    print(f"\n{'═' * 64}\n  Done.\n{'═' * 64}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Send outreach emails via Gmail and monitor for bounces.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/send_emails.py data/jobs/stripe__data_scientist__*.json\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --dry-run\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --max-send 1 --wait-seconds 60\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --check-bounces\n"
            "  python scripts/send_emails.py data/jobs/stripe__*.json --retry-bounced\n"
        ),
    )
    parser.add_argument("job_path", type=Path, help="Path to the job JSON file")
    parser.add_argument(
        "--max-send", type=int, default=None,
        help="Max recruiter emails to send per run (default: from settings, usually 3)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be sent without calling the Gmail API"
    )
    parser.add_argument(
        "--no-wait", action="store_true",
        help="Send emails then exit immediately (skip bounce polling)"
    )
    parser.add_argument(
        "--check-bounces", action="store_true",
        help="Poll Gmail for bounces only — no sending"
    )
    parser.add_argument(
        "--retry-bounced", action="store_true",
        help="Check bounces first, then send to next pattern for bounced recruiters"
    )
    parser.add_argument(
        "--from-name", type=str, default=None,
        help="Sender display name in From: header (default: GMAIL_SENDER_NAME in .env)"
    )
    parser.add_argument(
        "--wait-seconds", type=int, default=None,
        help="Seconds to wait before bounce poll (default: GMAIL_BOUNCE_WAIT_SECONDS in .env)"
    )
    parser.add_argument(
        "--resume", type=Path, default=None,
        help="Path to resume PDF to attach + triggers body re-draft with job URL"
    )

    args = parser.parse_args()
    settings = get_settings()

    asyncio.run(main(
        job_path=args.job_path,
        max_send=args.max_send or settings.gmail_max_send_per_run,
        dry_run=args.dry_run,
        no_wait=args.no_wait,
        check_bounces_only=args.check_bounces,
        retry_bounced=args.retry_bounced,
        from_name=args.from_name or settings.gmail_sender_name,
        wait_seconds=args.wait_seconds or settings.gmail_bounce_wait_seconds,
        resume_path=args.resume,
    ))
