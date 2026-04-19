"""
Gmail send + bounce detection service — Step 5 of the GetMeHired pipeline.

Handles:
  - OAuth2 authentication (browser-based first run, token cached for refresh)
  - Sending personalized outreach emails via Gmail API
  - Polling inbox for MAILER-DAEMON bounce-back messages
  - Updating recruiter send-state in the job JSON

Design:
  - Gmail API calls are synchronous (google-api-python-client is not async).
    Each call is wrapped in run_in_executor so it doesn't block the event loop.
  - Send state is persisted after EACH individual send, not at batch end,
    so a crash mid-loop leaves a consistent state.
  - "Hi there," in the email body is replaced with "Hi {FirstName}," at send time.
    No LLM call needed — just reuses _parse_name() from email_finder.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from getmehired.config import get_settings
from getmehired.models.recruiter import Recruiter
from getmehired.services.email_finder import _parse_name
from getmehired.services.storage import load, load_recruiters, save_send_state

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


# ── OAuth / service init ───────────────────────────────────────────────────────

def get_gmail_service():
    """
    Return an authenticated Gmail API service object.

    First run opens a browser for OAuth consent. Subsequent runs
    silently refresh the cached token at gmail_token_path.

    Raises:
        FileNotFoundError: If gmail_credentials_path does not exist.
        google.auth.exceptions.TransportError: On network errors during auth.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    settings = get_settings()
    creds_path = Path(settings.gmail_credentials_path).expanduser()
    token_path = Path(settings.gmail_token_path).expanduser()

    if not creds_path.exists():
        raise FileNotFoundError(
            f"Gmail credentials not found at {creds_path}\n"
            "Setup: Google Cloud Console → Enable Gmail API → Create OAuth Desktop credentials\n"
            f"→ Download client_secret.json → save as {creds_path}"
        )

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── Eligibility ────────────────────────────────────────────────────────────────

def _is_sendable(r: Recruiter) -> bool:
    """
    Return True if this recruiter should receive an email in the next send batch.

    Conditions for sendable:
      - Has at least one email address
      - Has at least one untried address remaining
      - Has never been sent to (email_sent_at is None) OR last send bounced

    Conditions for NOT sendable:
      - No email at all
      - All patterns exhausted (email_tried covers all candidates)
      - Last send is pending bounce check (email_bounced is None but sent)
      - Last send confirmed delivered (email_bounced is False)
    """
    if not r.email:
        return False
    candidates = [e.strip() for e in r.email.split(",") if e.strip()]
    untried = [e for e in candidates if e not in r.email_tried]
    if not untried:
        return False
    return r.email_sent_at is None or r.email_bounced is True


def _next_address(r: Recruiter) -> Optional[str]:
    """Return the next untried email address for this recruiter, or None."""
    if not r.email:
        return None
    candidates = [e.strip() for e in r.email.split(",") if e.strip()]
    for addr in candidates:
        if addr not in r.email_tried:
            return addr
    return None


# ── Name substitution ─────────────────────────────────────────────────────────

def _personalize_body(body: str, recruiter_name: str, sender_name: str = "") -> str:
    """
    Personalize the email body for a specific recruiter.

    - Replaces "Hi there," with "Hi {FirstName},"
    - Appends sender name after "Best," if provided
    """
    first, _ = _parse_name(recruiter_name)
    first_display = first.title() if first else "there"
    body = body.replace("Hi there,", f"Hi {first_display},", 1)
    if sender_name:
        body = body.replace("Best,", f"Best,\n{sender_name}", 1)
    return body


# ── MIME message builder ───────────────────────────────────────────────────────

def _build_raw_message(
    to: str,
    subject: str,
    body: str,
    from_name: str,
    resume_path: Optional[Path] = None,
) -> str:
    """
    Build a base64url-encoded RFC 2822 message for the Gmail API send endpoint.

    If resume_path is provided the PDF is attached as application/pdf.
    """
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["To"] = to
    msg["From"] = f"{from_name} <me>" if from_name else "me"
    msg.attach(MIMEText(body, "plain", "utf-8"))

    if resume_path and resume_path.exists():
        with open(resume_path, "rb") as f:
            part = MIMEBase("application", "pdf")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition", "attachment", filename=resume_path.name
        )
        msg.attach(part)

    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


# ── Send batch ────────────────────────────────────────────────────────────────

async def send_batch(
    job_path: Path,
    service,
    max_send: int,
    dry_run: bool,
    from_name: str,
    resume_path: Optional[Path] = None,
) -> list[Recruiter]:
    """
    Send outreach emails to up to `max_send` eligible recruiters.

    Each send is persisted to the job JSON immediately after it completes.
    Returns the full updated recruiter list.
    """
    job = load(job_path)
    recruiters = load_recruiters(job_path)

    if not job.email_body or not job.email_subject:
        raise ValueError(
            "Job has no email draft. Run find_recruiters.py --resume <path> first."
        )

    eligible = [r for r in recruiters if _is_sendable(r)]
    to_send = eligible[:max_send]

    loop = asyncio.get_event_loop()

    for i, recruiter in enumerate(to_send, 1):
        addr = _next_address(recruiter)
        if not addr:
            continue

        body = _personalize_body(job.email_body, recruiter.name, sender_name=from_name)
        raw = _build_raw_message(addr, job.email_subject, body, from_name, resume_path)

        attempt_label = ""
        if recruiter.email_tried:
            n = len(recruiter.email_tried) + 1
            total = len([e for e in recruiter.email.split(",") if e.strip()])
            attempt_label = f"  [retry {n}/{total}]"

        if dry_run:
            print(f"  [DRY RUN {i}/{len(to_send)}] {recruiter.name}  <{addr}>{attempt_label}")
            first, _ = _parse_name(recruiter.name)
            greeting = f"Hi {first.title()}," if first else "Hi there,"
            print(f"      {greeting} ...")
            if resume_path:
                print(f"      [attachment: {resume_path.name}]")
            continue

        # Send via Gmail API (sync call, run in executor)
        await loop.run_in_executor(
            None,
            lambda raw=raw: service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
        )

        # Update send state
        now = datetime.now(timezone.utc)
        recruiter.email_sent_at = now
        recruiter.email_sent_to = addr
        recruiter.email_tried.append(addr)
        recruiter.email_bounced = None  # pending bounce check

        # Persist immediately after each send
        save_send_state(job_path, recruiters)

        print(f"  ✓ Sent [{i}/{len(to_send)}]       {recruiter.name}  <{addr}>{attempt_label}")
        if i < len(to_send):
            await asyncio.sleep(2)  # brief inter-send delay

    return recruiters


# ── Bounce detection ──────────────────────────────────────────────────────────

def _extract_failed_recipient(headers: list[dict]) -> Optional[str]:
    """
    Extract the failed delivery address from bounce message headers.

    Checks in preference order:
      1. X-Failed-Recipients header (RFC 3464 NDR, most reliable)
      2. To: header of the bounce message
    """
    header_map = {h["name"].lower(): h["value"] for h in headers}

    if "x-failed-recipients" in header_map:
        return header_map["x-failed-recipients"].strip().lower()

    if "to" in header_map:
        to_val = header_map["to"].strip().lower()
        # Handle "Name <addr>" format
        if "<" in to_val and ">" in to_val:
            return to_val.split("<")[1].rstrip(">").strip()
        return to_val

    return None


def _fetch_bounce_addresses(service, lookback_minutes: int) -> set[str]:
    """
    Query Gmail inbox for MAILER-DAEMON / postmaster bounce messages.

    Returns a set of lowercase email addresses that appear to have bounced.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)
    epoch = int(cutoff.timestamp())

    query = f"from:(mailer-daemon OR postmaster) after:{epoch}"

    result = service.users().messages().list(
        userId="me", q=query, maxResults=50
    ).execute()

    messages = result.get("messages", [])
    bounced: set[str] = set()

    for msg in messages:
        detail = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="metadata",
            metadataHeaders=["X-Failed-Recipients", "To", "Subject", "From"],
        ).execute()
        headers = detail.get("payload", {}).get("headers", [])
        addr = _extract_failed_recipient(headers)
        if addr:
            bounced.add(addr.lower())

    return bounced


async def check_bounces(
    job_path: Path,
    service,
    lookback_minutes: int,
) -> tuple[list[Recruiter], int]:
    """
    Poll Gmail for bounce messages and update recruiter email_bounced state.

    Returns (updated_recruiters, bounce_count).
    """
    recruiters = load_recruiters(job_path)
    loop = asyncio.get_event_loop()

    bounce_addresses = await loop.run_in_executor(
        None,
        lambda: _fetch_bounce_addresses(service, lookback_minutes),
    )

    changed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes)

    for r in recruiters:
        if not r.email_sent_to:
            continue
        if r.email_sent_at and r.email_sent_at < cutoff:
            continue  # sent before the lookback window, don't touch

        if r.email_bounced is not None:
            continue  # already has a definitive status — don't overwrite

        if r.email_sent_to.lower() in bounce_addresses:
            r.email_bounced = True
            changed += 1
        elif r.email_bounced is None:
            # Sent within window, no bounce found → tentatively delivered
            r.email_bounced = False
            changed += 1

    if changed:
        save_send_state(job_path, recruiters)

    return recruiters, len([r for r in recruiters if r.email_bounced is True])


# ── Continuous bounce polling ─────────────────────────────────────────────────

async def poll_bounces_loop(
    job_path: Path,
    service,
    poll_interval_seconds: int = 60,
    lookback_minutes: int = 30,
) -> tuple[list[Recruiter], int]:
    """
    Poll Gmail for bounce messages every `poll_interval_seconds` until all
    pending recruiter sends have a definitive status (bounced or delivered).

    Prints a live status table after each poll. Exits cleanly on Ctrl+C.
    Returns (updated_recruiters, total_bounce_count).
    """
    poll_num = 0

    print(f"\n  Polling every {poll_interval_seconds}s for bounce reports. Press Ctrl+C to stop.\n")

    try:
        while True:
            recruiters = load_recruiters(job_path)
            pending = [r for r in recruiters if r.email_sent_to and r.email_bounced is None]

            if not pending:
                print(f"  All sent emails have a definitive status. Stopping poll.\n")
                break

            # Countdown to next poll
            for remaining in range(poll_interval_seconds, 0, -5):
                sent_count = sum(1 for r in recruiters if r.email_sent_to)
                bounced_count = sum(1 for r in recruiters if r.email_bounced is True)
                delivered_count = sum(1 for r in recruiters if r.email_bounced is False)
                pending_count = len(pending)
                print(
                    f"\r  [{remaining:>3}s] sent={sent_count}  "
                    f"delivered={delivered_count}  bounced={bounced_count}  "
                    f"pending={pending_count}   ",
                    end="", flush=True,
                )
                await asyncio.sleep(min(5, remaining))
            print()

            # Run bounce check
            poll_num += 1
            print(f"  Poll #{poll_num} — checking Gmail...", flush=True)
            recruiters, bounce_count = await check_bounces(job_path, service, lookback_minutes)

            # Print status table
            sent_this_check = [r for r in recruiters if r.email_sent_to]
            if sent_this_check:
                print(f"\n  {'Name':<28}  {'Sent to':<35}  Status")
                print(f"  {'─'*28}  {'─'*35}  {'─'*24}")
                for r in sent_this_check:
                    if r.email_bounced is True:
                        candidates = [e.strip() for e in r.email.split(",") if e.strip()]
                        untried = [e for e in candidates if e not in r.email_tried]
                        suffix = f" → retry: {untried[0]}" if untried else " (all exhausted)"
                        status = f"BOUNCED{suffix}"
                    elif r.email_bounced is False:
                        status = "delivered"
                    else:
                        status = "pending"
                    print(f"  {r.name:<28}  {(r.email_sent_to or ''):<35}  {status}")
                print()

    except KeyboardInterrupt:
        print(f"\n\n  Poll stopped by user.\n")

    recruiters = load_recruiters(job_path)
    bounce_count = sum(1 for r in recruiters if r.email_bounced is True)

    bounced_with_retries = [
        r for r in recruiters
        if r.email_bounced is True
        and any(e.strip() not in r.email_tried for e in r.email.split(",") if e.strip())
    ]
    if bounced_with_retries:
        names = ", ".join(r.name for r in bounced_with_retries)
        print(f"  {len(bounced_with_retries)} recruiter(s) bounced with untried patterns: {names}")
        print(f"  Re-run with --retry-bounced to send to the next email pattern.\n")

    return recruiters, bounce_count


