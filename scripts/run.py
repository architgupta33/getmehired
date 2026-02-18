"""
Full end-to-end pipeline: scrape → analyze → store → find recruiters → append.

This script composes Step 1 (test_scraper.py) and Step 2 (find_recruiters.py)
into a single command so you don't have to run them separately. The two
individual scripts remain useful for re-running a single step in isolation
(e.g. re-running recruiter search on an already-saved job file).

Usage:
    python scripts/run.py <job-url>
    python scripts/run.py <job-url> --max-results 10
    python scripts/run.py <job-url> --debug          # verbose output for troubleshooting

Examples:
    python scripts/run.py "https://job-boards.greenhouse.io/anthropic/jobs/5096400008"
    python scripts/run.py "https://jobs.lever.co/belvederetrading/f81a8965-5537-4a4b-aec6-c02dfa51815e" --max-results 10
    python scripts/run.py "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Software-Engineer-Intern--2026_JR2008747" --debug
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import traceback
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from getmehired.agents.email_drafter import draft_email, make_subject
from getmehired.agents.job_analyzer import analyze
from getmehired.services.email_finder import find_emails
from getmehired.services.job_scraper import _detect_platform, _normalize_url, scrape_job_page
from getmehired.services.recruiter_finder import RecruiterSearchError, find_recruiters
from getmehired.services.resume_reader import read_resume
from getmehired.services.storage import append_recruiters, save, save_email_draft

SEP = "─" * 64

# ── Output helpers ─────────────────────────────────────────────────────────────
# Consistent prefix glyphs make it easy to grep the output for failures (✗) or
# warnings (⚠). Label is left-padded to 20 chars so values align vertically.

def _section(title: str) -> None:
    """Print a titled section divider."""
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def _ok(label: str, value: str) -> None:
    print(f"  ✓ {label:<20} {value}")


def _warn(label: str, value: str) -> None:
    print(f"  ⚠ {label:<20} {value}")


def _fail(label: str, value: str) -> None:
    print(f"  ✗ {label:<20} {value}")


def _debug(label: str, value: str) -> None:
    """Print a debug line (only shown when --debug is passed)."""
    print(f"  … {label:<20} {value}")


# ── Config / environment debug ─────────────────────────────────────────────────

def _print_env_debug() -> None:
    """
    Check which API keys are loaded from .env and print their status.

    Useful when a recruiter search step fails and you're not sure which
    backends are available. Run with --debug to see this section.
    """
    # Import lazily so a missing GROQ_API_KEY doesn't crash before we print the env table
    try:
        from getmehired.config import get_settings
        s = get_settings()
        _debug("GROQ model", s.groq_model)
        _debug("DATA_DIR", s.data_dir)
        _debug("BRAVE_API_KEY", "set" if s.brave_api_key else "NOT SET (Brave backend disabled)")
        _debug("TAVILY_API_KEY", "set" if s.tavily_api_key else "NOT SET (Tavily backend disabled)")
        _debug("GOOGLE_CSE_KEY", "set" if s.google_cse_api_key else "NOT SET")
        _debug("GOOGLE_CSE_CX", "set" if s.google_cse_cx else "NOT SET (Google CSE disabled)")
        if not s.google_cse_api_key or not s.google_cse_cx:
            _warn("Google CSE", "Both GOOGLE_CSE_API_KEY and GOOGLE_CSE_CX must be set to enable.")
    except Exception as e:
        _fail("Config load", str(e))


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def main(raw_url: str, max_results: int, debug: bool, resume: Path | None) -> None:
    """
    Run the full pipeline for a single job URL.

    Steps:
      1. URL Normalisation — strip tracking params, detect platform
      2. Scraping          — platform-aware fetching (Greenhouse JSON / Lever HTML / Playwright)
      3. Analysis          — Groq LLM extracts structured fields from raw text
      4. Storage           — write JobPosting to data/jobs/<company>__<title>__<ts>.json
      5. Recruiter Search  — 4-tier search (DDG → Brave → Tavily → Google CSE)
      6. Save Recruiters   — append recruiter list to the job JSON file

    If step 2 or 3 fails the script exits immediately — there's nothing useful to
    save without at least a company name and job title. If step 5 fails, the job
    file is still kept and you can retry with find_recruiters.py.
    """
    print(f"\n{'═' * 64}")
    print(f"  GetMeHired — Full Pipeline")
    print(f"{'═' * 64}")
    print(f"  Input URL : {raw_url}")
    if debug:
        print(f"  Debug mode: ON")

    # ── STEP 1: URL Normalisation ──────────────────────────────────────────────
    # Strips tracking params (utm_*, src, pid, etc.) and platform-specific quirks
    # (e.g. Workday /apply suffix) before passing to the scraper.
    _section("STEP 1 — URL Normalisation")
    normalized = _normalize_url(raw_url)
    platform = _detect_platform(normalized)

    if normalized != raw_url:
        _ok("Original", raw_url[:72])
        _ok("Normalized", normalized[:72])
    else:
        _ok("URL", normalized[:72])
    _ok("Platform detected", platform.value)

    if debug:
        _debug("Platform value", repr(platform.value))
        _debug("URL changed", str(normalized != raw_url))

    # ── STEP 2: Scraping ───────────────────────────────────────────────────────
    # Platform-aware: Greenhouse uses JSON API (~0.4s), Lever uses httpx+HTML
    # (~1-2s), Workday and generic URLs use Playwright headless Chromium (~3-15s).
    _section("STEP 2 — Scraping")
    t0 = time.perf_counter()
    result = await scrape_job_page(raw_url)
    elapsed = time.perf_counter() - t0

    if not result.success:
        _fail("Status", f"FAILED in {elapsed:.1f}s")
        _fail("Error", result.error or "unknown")
        if debug:
            _debug("Hint", "Check if the URL is accessible in a browser without login.")
            _debug("Hint", "Workday/generic pages require Playwright. Run: playwright install chromium")
        sys.exit(1)

    _ok("Status", f"Success in {elapsed:.1f}s")
    _ok("Page title", result.page_title[:60])
    _ok("Chars extracted", str(len(result.raw_text)))

    if debug:
        # Show first 400 chars of raw text so you can verify the right content was scraped
        print(f"\n  --- Raw text preview (first 400 chars) ---")
        preview = result.raw_text[:400].replace("\n", " ↵ ")
        print(f"  {preview}")
        print(f"  ---")

    # ── STEP 3: Analysis (Groq LLM) ───────────────────────────────────────────
    # Sends up to 8,000 chars of raw text to Groq (llama-3.3-70b-versatile) and
    # asks it to extract: job_title, company, job_family, location. The response
    # is validated against the JobPosting Pydantic model.
    _section("STEP 3 — Analysis (Groq LLM)")
    t0 = time.perf_counter()
    try:
        job = await analyze(result)
        elapsed = time.perf_counter() - t0
    except Exception as e:
        elapsed = time.perf_counter() - t0
        _fail("Status", f"FAILED in {elapsed:.1f}s")
        _fail("Error", str(e))
        if debug:
            print("\n  --- Traceback ---")
            traceback.print_exc()
            print("  ---")
            _debug("Hint", "If 429: Groq free tier rate limit. Wait ~1 min and retry.")
            _debug("Hint", "If JSON decode error: LLM returned non-JSON. Rare, retry.")
        sys.exit(1)

    _ok("Status", f"Success in {elapsed:.1f}s")
    _ok("Job Title", job.job_title)
    _ok("Company", job.company)
    _ok("Job Family", job.job_family.value)
    _ok("Location", job.location or "Not specified")

    # Warn on suspicious extraction results — these don't block the pipeline
    # but indicate the LLM may have received garbled or login-walled content.
    if not job.company or job.company.lower() in ("unknown", "n/a", ""):
        _warn("Company", "Could not be extracted — check raw text (re-run with --debug).")
    if not job.job_title:
        _warn("Job Title", "Empty — page title was used as fallback.")

    if debug:
        _debug("Job family (raw)", repr(job.job_family))
        _debug("URL in model", job.url or "None")
        _debug("Platform in model", job.platform or "None")
        _debug("Scraped at", str(job.scraped_at))

    # ── STEP 4: Storage ────────────────────────────────────────────────────────
    # Saves a JSON file to data/jobs/ using the pattern:
    #   <company>__<title>__<YYYYMMDD_HHMMSS>.json
    # The file persists across sessions and is the input for find_recruiters.py.
    _section("STEP 4 — Storage")
    t0 = time.perf_counter()
    job_path = save(job)
    elapsed = time.perf_counter() - t0

    _ok("Status", f"Saved in {elapsed:.3f}s")
    _ok("File", str(job_path))
    _ok("File size", f"{job_path.stat().st_size:,} bytes")

    if debug:
        _debug("Absolute path", str(job_path.resolve()))

    # ── STEP 5: Recruiter Search ───────────────────────────────────────────────
    # Tries up to 4 search queries (term1+city, term1, term2+city, term2) using
    # a 4-tier backend chain: DDG → Brave → Tavily → Google CSE. Once a backend
    # fails, all subsequent queries in this session use the next backend —
    # no per-query retry of dead backends.
    _section("STEP 5 — Recruiter Search")

    if debug:
        # Print which backends are configured — useful to diagnose "no results"
        _section("STEP 5a — Environment / Backend Config (debug)")
        _print_env_debug()
        _section("STEP 5b — Running Search")

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
        _fail("Search", f"FAILED in {elapsed:.1f}s — {e}")
        _warn("Note", "Job was saved. Retry recruiter search:")
        print(f"\n  python scripts/find_recruiters.py {job_path} --max-results {max_results}\n")
        if debug:
            print("\n  --- Traceback ---")
            traceback.print_exc()
            print("  ---")
        sys.exit(1)

    _ok("Status", f"Complete in {elapsed:.1f}s")

    if not recruiters:
        _warn("Results", "No recruiters found.")
        if debug:
            _debug("Hint", "Try a broader company name (e.g. 'Anthropic' vs 'anthropic').")
            _debug("Hint", "Check TAVILY_API_KEY is set — it's the most reliable backend.")
    else:
        _ok("Found", f"{len(recruiters)} recruiter(s)")
        for i, r in enumerate(recruiters, 1):
            print(f"\n  [{i}] {r.name}")
            if r.title:
                print(f"       Title:    {r.title}")
            if r.linkedin_url:
                print(f"       LinkedIn: {r.linkedin_url}")
            if debug and r.source:
                print(f"       Source:   {r.source}")

    # ── STEP 6: Save Recruiters (without emails yet) ───────────────────────────
    # Persist the recruiter list now so the job file is never lost even if the
    # email discovery step (Step 7) fails or has no API keys configured.
    _section("STEP 6 — Save Recruiters")
    t0 = time.perf_counter()
    append_recruiters(job_path, recruiters)
    elapsed = time.perf_counter() - t0

    _ok("Status", f"Saved in {elapsed:.3f}s")
    _ok("File", str(job_path))
    _ok("Recruiters saved", str(len(recruiters)))

    if debug:
        _debug("Key in JSON", '"recruiters"')
        _debug("Idempotent", "Yes — re-running overwrites previous recruiter list")

    # ── STEP 7: Email Discovery ────────────────────────────────────────────────
    # Discover the company's email domain and naming pattern, then generate
    # email address(es) for each recruiter. Results are written back to the
    # same job JSON file, updating the "email" field on each recruiter entry.
    #
    # Discovery order:
    #   Domain:  web search (Tavily) → Hunter.io
    #   Pattern: web search (Tavily) → Hunter.io → combinatorics (all 6 patterns)
    _section("STEP 7 — Email Discovery")
    t0 = time.perf_counter()
    recruiters_with_emails = await find_emails(job.company, recruiters)
    elapsed = time.perf_counter() - t0

    emails_found = sum(1 for r in recruiters_with_emails if r.email)
    _ok("Status", f"Complete in {elapsed:.1f}s")
    _ok("Emails generated", f"{emails_found} / {len(recruiters_with_emails)}")

    if emails_found:
        for r in recruiters_with_emails:
            if r.email:
                # Show comma-separated list (combinatorics) on separate indented lines
                emails = r.email.split(",")
                if len(emails) == 1:
                    print(f"\n  {r.name}")
                    print(f"       Email:    {r.email}")
                else:
                    print(f"\n  {r.name}  [combinatorics — {len(emails)} patterns]")
                    for e in emails:
                        print(f"       {e.strip()}")
        # Write emails back to the JSON file
        append_recruiters(job_path, recruiters_with_emails)
        _ok("File updated", str(job_path))
    else:
        _warn("Emails", "None generated — check TAVILY_API_KEY or add HUNTER_API_KEY")
        if debug:
            _debug("Hint", "Web search needs TAVILY_API_KEY to find domain/pattern.")
            _debug("Hint", "Add HUNTER_API_KEY to .env for Hunter.io fallback.")

    # ── STEP 8: Email Drafting ─────────────────────────────────────────────────
    # If --resume was provided, use the LLM to draft a personalized outreach
    # email body for each recruiter who has an email address.
    # One Groq call drafts the body using the first recruiter's name; subsequent
    # recruiters get the same body with only the first-name greeting swapped.
    _section("STEP 8 — Email Drafting")

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
    print(f"  Pipeline complete.")
    print(f"{'═' * 64}\n")


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Full GetMeHired pipeline: scrape a job URL then find recruiters.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python scripts/run.py "https://job-boards.greenhouse.io/anthropic/jobs/5096400008"\n'
            '  python scripts/run.py "https://jobs.lever.co/..." --max-results 10\n'
            '  python scripts/run.py "https://..." --resume resume.pdf\n'
        ),
    )
    # Positional: the job posting URL to process
    parser.add_argument("url", help="Job posting URL (Greenhouse, Lever, Workday, or generic)")
    # How many recruiters to collect before stopping the search cascade
    parser.add_argument(
        "--max-results",
        type=int,
        default=5,
        help="Maximum number of unique recruiters to find (default: 5)",
    )
    # --resume: path to PDF or .txt resume for email drafting
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="Path to resume file (.pdf or .txt) — enables Step 8 email drafting",
    )
    # --debug prints raw text previews, backend config, tracebacks, and extra hints
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print verbose debug info: raw text preview, backend config, tracebacks",
    )
    args = parser.parse_args()

    asyncio.run(main(raw_url=args.url, max_results=args.max_results, debug=args.debug, resume=args.resume))
