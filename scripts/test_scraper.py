"""
End-to-end pipeline test: scrape → analyze → store.

Usage:
    python scripts/test_scraper.py <job-url>

Example:
    python scripts/test_scraper.py "https://job-boards.greenhouse.io/anthropic/jobs/5096400008"
    python scripts/test_scraper.py "https://jobs.lever.co/belvederetrading/f81a8965-5537-4a4b-aec6-c02dfa51815e"
    python scripts/test_scraper.py "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Software-Engineer-Intern--2026_JR2008747"
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from getmehired.agents.job_analyzer import analyze
from getmehired.services.job_scraper import _normalize_url, _detect_platform, scrape_job_page
from getmehired.services.storage import save

SEP = "─" * 64


def _section(title: str) -> None:
    print(f"\n{SEP}")
    print(f"  {title}")
    print(SEP)


def _ok(label: str, value: str) -> None:
    print(f"  ✓ {label:<18} {value}")


def _warn(label: str, value: str) -> None:
    print(f"  ⚠ {label:<18} {value}")


def _fail(label: str, value: str) -> None:
    print(f"  ✗ {label:<18} {value}")


async def main(raw_url: str) -> None:
    print(f"\n{'═' * 64}")
    print(f"  GetMeHired — Pipeline Debug")
    print(f"{'═' * 64}")
    print(f"  Input URL : {raw_url}")

    # ── URL Normalisation ─────────────────────────────────────
    _section("STEP 1 — URL Normalisation")
    normalized = _normalize_url(raw_url)
    platform   = _detect_platform(normalized)

    if normalized != raw_url:
        _ok("Original", raw_url[:72])
        _ok("Normalized", normalized[:72])
    else:
        _ok("URL", normalized[:72])

    _ok("Platform detected", platform.value)

    # ── Scraping ──────────────────────────────────────────────
    _section("STEP 2 — Scraping")
    t0 = time.perf_counter()
    result = await scrape_job_page(raw_url)
    elapsed = time.perf_counter() - t0

    if not result.success:
        _fail("Status", f"FAILED in {elapsed:.1f}s")
        _fail("Error", result.error or "unknown")
        return

    _ok("Status", f"Success in {elapsed:.1f}s")
    _ok("Page title", result.page_title[:60])
    _ok("Chars extracted", str(len(result.raw_text)))

    # Show first 400 chars of extracted text as a sanity check
    print(f"\n  --- Raw text preview (first 400 chars) ---")
    preview = result.raw_text[:400].replace("\n", " ↵ ")
    print(f"  {preview}")
    print(f"  ---")

    # ── Analysis ──────────────────────────────────────────────
    _section("STEP 3 — Analysis (Groq LLM)")
    t0 = time.perf_counter()
    try:
        job = await analyze(result)
        elapsed = time.perf_counter() - t0
    except Exception as e:
        elapsed = time.perf_counter() - t0
        _fail("Status", f"FAILED in {elapsed:.1f}s")
        _fail("Error", str(e))
        return

    _ok("Status", f"Success in {elapsed:.1f}s")
    _ok("Job Title", job.job_title)
    _ok("Company", job.company)
    _ok("Job Family", job.job_family.value)
    _ok("Location", job.location or "Not specified")

    # Warn if key fields look empty or suspicious
    if not job.company or job.company.lower() in ("unknown", "n/a", ""):
        _warn("Company", "could not be extracted — check raw text")
    if not job.job_title:
        _warn("Job Title", "empty — page title used as fallback")

    # ── Storage ───────────────────────────────────────────────
    _section("STEP 4 — Storage")
    t0 = time.perf_counter()
    path = save(job)
    elapsed = time.perf_counter() - t0

    _ok("Status", f"Saved in {elapsed:.3f}s")
    _ok("File", str(path))
    _ok("File size", f"{path.stat().st_size:,} bytes")

    print(f"\n{'═' * 64}")
    print(f"  Pipeline complete.")
    print(f"{'═' * 64}\n")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_scraper.py <job-url>")
        sys.exit(1)

    asyncio.run(main(sys.argv[1]))
