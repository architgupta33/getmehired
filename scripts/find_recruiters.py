"""
Step 2 — Find recruiters for a saved job posting.

Usage:
    python scripts/find_recruiters.py <path-to-job.json>
    python scripts/find_recruiters.py <path-to-job.json> --max-results 10
    python scripts/find_recruiters.py <path-to-job.json> --resume resume.pdf

Examples:
    python scripts/find_recruiters.py data/jobs/belvedere_trading__software_engineer_entry_level_2026__20260215_230035.json
    python scripts/find_recruiters.py data/jobs/the_home_depot__*.json --resume resume.pdf
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from getmehired.agents.email_drafter import draft_email, make_subject
from getmehired.services.email_finder import find_emails
from getmehired.services.recruiter_finder import RecruiterSearchError, find_recruiters
from getmehired.services.resume_reader import read_resume
from getmehired.services.storage import append_recruiters, load, save_email_draft

SEP = "─" * 64


def _section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def _ok(label: str, value: str) -> None:
    print(f"  ✓ {label:<20} {value}")


def _warn(label: str, value: str) -> None:
    print(f"  ⚠ {label:<20} {value}")


def _fail(label: str, value: str) -> None:
    print(f"  ✗ {label:<20} {value}")


async def main(job_path: Path, max_results: int, resume: Path | None) -> None:
    print(f"\n{'═' * 64}")
    print(f"  GetMeHired — Recruiter Finder")
    print(f"{'═' * 64}")

    # ── Load job file ────────────────────────────────────────
    _section("STEP 1 — Load Job")
    if not job_path.exists():
        _fail("File", f"Not found: {job_path}")
        sys.exit(1)

    job = load(job_path)
    _ok("Job Title", job.job_title)
    _ok("Company", job.company)
    _ok("Job Family", job.job_family.value)
    _ok("Location", job.location or "Not specified")

    # ── Search for recruiters ────────────────────────────────
    _section("STEP 2 — Search for Recruiters")
    print(f"  Searching for recruiters at '{job.company}' ({job.job_family.value})")
    if job.location:
        print(f"  Location hint: {job.location}")
    print(f"  Max results: {max_results}")
    print()

    t0 = time.perf_counter()
    try:
        recruiters = await find_recruiters(job, max_results=max_results)
        elapsed = time.perf_counter() - t0
    except RecruiterSearchError as e:
        elapsed = time.perf_counter() - t0
        _fail("Search", f"FAILED in {elapsed:.1f}s")
        _fail("Error", str(e))
        sys.exit(1)
    except ValueError as e:
        _fail("Validation", str(e))
        sys.exit(1)

    _ok("Status", f"Complete in {elapsed:.1f}s")

    if not recruiters:
        _warn("Results", "No recruiters found. Try a different company name or check Google access.")
    else:
        _ok("Found", f"{len(recruiters)} recruiter(s)")
        for i, r in enumerate(recruiters, 1):
            print(f"\n  [{i}] {r.name}")
            if r.title:
                print(f"       Title:    {r.title}")
            if r.linkedin_url:
                print(f"       LinkedIn: {r.linkedin_url}")

    # ── Save recruiters (without emails yet) ─────────────────
    # Persist now so the file is never lost even if email discovery fails.
    _section("STEP 3 — Update Job File")
    t0 = time.perf_counter()
    append_recruiters(job_path, recruiters)
    elapsed = time.perf_counter() - t0

    _ok("Status", f"Saved in {elapsed:.3f}s")
    _ok("File", str(job_path))
    _ok("Recruiters saved", str(len(recruiters)))

    # ── Discover emails ───────────────────────────────────────
    # Tries: domain via web search → Hunter.io; pattern via web search →
    # Hunter.io → combinatorics fallback (all 6 patterns comma-separated).
    _section("STEP 4 — Email Discovery")
    t0 = time.perf_counter()
    recruiters_with_emails = await find_emails(job.company, recruiters)
    elapsed = time.perf_counter() - t0

    emails_found = sum(1 for r in recruiters_with_emails if r.email)
    _ok("Status", f"Complete in {elapsed:.1f}s")
    _ok("Emails generated", f"{emails_found} / {len(recruiters_with_emails)}")

    if emails_found:
        for r in recruiters_with_emails:
            if r.email:
                emails = r.email.split(",")
                if len(emails) == 1:
                    print(f"\n  {r.name}")
                    print(f"       Email:    {r.email}")
                else:
                    print(f"\n  {r.name}  [combinatorics — {len(emails)} patterns]")
                    for e in emails:
                        print(f"       {e.strip()}")
        append_recruiters(job_path, recruiters_with_emails)
        _ok("File updated", str(job_path))
    else:
        _warn("Emails", "None generated — check TAVILY_API_KEY or add HUNTER_API_KEY")

    # ── Email Drafting ────────────────────────────────────────
    _section("STEP 5 — Email Drafting")

    if not resume:
        _warn("Skipped", "No --resume provided. Pass --resume <path> to draft emails.")
    else:
        try:
            resume_text = read_resume(resume)
            _ok("Resume", f"{resume.name} ({len(resume_text):,} chars extracted)")
        except (FileNotFoundError, ValueError) as e:
            _fail("Resume", str(e))
            resume_text = None

        if resume_text:
            t0 = time.perf_counter()
            try:
                draft_body = await draft_email(job, resume_text, "there")
                elapsed = time.perf_counter() - t0
                _ok("Draft generated", f"in {elapsed:.1f}s")
            except Exception as e:
                elapsed = time.perf_counter() - t0
                _fail("Draft", f"FAILED in {elapsed:.1f}s — {e}")
                draft_body = None

            if draft_body:
                subject = make_subject(job)
                save_email_draft(job_path, subject, draft_body)
                _ok("File updated", str(job_path))

                recruiters_with_email = [r for r in recruiters_with_emails if r.email]
                _ok("Emails drafted", f"{len(recruiters_with_email)} / {len(recruiters_with_emails)}")

                print(f"\n  Subject: {subject}")
                print(f"  ---")
                for line in draft_body.splitlines():
                    print(f"  {line}")

                print()
                for r in recruiters_with_email:
                    email_addr = r.email.split(",")[0].strip()
                    print(f"  → {r.name}  <{email_addr}>")

    print(f"\n{'═' * 64}")
    print(f"  Done.")
    print(f"{'═' * 64}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Find recruiters for a saved job posting.")
    parser.add_argument("job_path", type=Path, help="Path to the job JSON file")
    parser.add_argument("--max-results", type=int, default=5, help="Max recruiters to find (default: 5)")
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="Path to resume file (.pdf or .txt) — enables Step 5 email drafting",
    )
    args = parser.parse_args()

    asyncio.run(main(job_path=args.job_path, max_results=args.max_results, resume=args.resume))
