# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (editable, includes dev extras)
pip install -e ".[dev]"
playwright install chromium   # one-time, needed for Workday/generic scraping

# Run all tests
python -m pytest

# Run a single test file
python -m pytest tests/test_bounce_detection.py -v

# Run a single test by name
python -m pytest tests/test_bounce_detection.py::TestCheckBounces::test_mixed_batch -v

# Lint
ruff check src/ scripts/ tests/
```

## Pipeline Overview

Three scripts, meant to run in order:

```
run.py <url> --resume <pdf>         → scrape + analyze + store + recruiters + emails + draft
find_recruiters.py <job.json>       → re-run recruiter/email discovery on existing job file
send_emails.py <job.json> --resume  → confirm + send + poll bounces in a loop
```

Each job is persisted as a single JSON file in `data/jobs/` (git-ignored). That file is the shared state across all three scripts — recruiters and send-state are patched into it in-place rather than re-written from scratch.

## Architecture

### Data flow

`JobPosting` (Pydantic model, `models/job.py`) holds job-level fields plus two job-level draft fields: `email_subject` and `email_body`. These are drafted once (one LLM call) with `"Hi there,"` as a placeholder — first-name substitution happens at send time, not draft time.

`Recruiter` (Pydantic model, `models/recruiter.py`) holds per-recruiter fields plus four send-state fields: `email_sent_at`, `email_sent_to`, `email_tried: list[str]`, `email_bounced: Optional[bool]`. `email_bounced=None` means "sent, pending bounce check" — this prevents double-sends on re-run.

The JSON file on disk stores the `JobPosting` fields at the top level and a `recruiters` array alongside them. Because `recruiters` is not a field on `JobPosting`, `storage.load()` and `storage.load_recruiters()` are separate calls.

### Key design decisions

**Email field is comma-separated patterns.** When the email naming pattern can't be determined, `email_finder.py` stores all 6 combinatoric patterns as `"a@co.com,b@co.com,..."`. `_is_sendable()` and `_next_address()` in `gmail_sender.py` treat commas as pattern candidates and pick the first untried one.

**Bounce state machine.** `email_bounced` has three states: `None` (pending — do not re-send), `True` (bounced — retry next pattern), `False` (delivered — do not re-send). `_is_sendable()` only returns True for `sent_at=None` (never sent) or `bounced=True` (retry). The fix for overwriting `False→True` on late MAILER-DAEMON arrival is in `check_bounces()` — skip recruiters where `email_bounced is not None`.

**Tiered API fallbacks.** Both `recruiter_finder.py` (DDG → Brave → Tavily → Google CSE) and `email_finder.py` (Tavily → Hunter.io → Apollo / combinatorics fallback) try backends in order and stop on first success. Adding a new backend means inserting it into the try-chain; no factory or registry pattern.

**Gmail API is synchronous.** `google-api-python-client` is sync-only. Every Gmail call in `gmail_sender.py` is wrapped with `await loop.run_in_executor(None, lambda: ...)` to avoid blocking the asyncio event loop.

**`config.py` is a single `pydantic-settings` class** read from `.env`. `get_settings()` is `@lru_cache`-wrapped — don't call `get_settings()` before setting env vars in tests; use `monkeypatch` or pass settings explicitly.

### Services

| File | Responsibility |
|---|---|
| `job_scraper.py` | Platform dispatch (Greenhouse JSON API / Lever / Workday Playwright / generic Playwright). Entry: `scrape_job_page(url)` |
| `recruiter_finder.py` | LinkedIn recruiter search via 4-tier search engine fallback. Entry: `find_recruiters(job)` |
| `email_finder.py` | Domain + pattern discovery, address generation. Entry: `discover_emails(job, recruiters)`. Also exports `_parse_name()` used by `gmail_sender.py` |
| `gmail_sender.py` | OAuth2 auth, send batch, continuous bounce polling loop. Key exports: `send_batch()`, `poll_bounces_loop()`, `_is_sendable()`, `_next_address()`, `_personalize_body()` |
| `storage.py` | JSON read/write. `load()` + `load_recruiters()` are always separate calls. `save_send_state()` and `save_email_draft()` patch specific keys without rewriting the whole file |
| `resume_reader.py` | PDF → plain text via pdfplumber |

### Agents

| File | Model | Notes |
|---|---|---|
| `job_analyzer.py` | Groq (`llama-3.3-70b-versatile`) | Returns structured JSON; regex-strips markdown fences before parse |
| `email_drafter.py` | Groq (`llama-3.3-70b-versatile`) | One call per run; body always uses `"Hi there,"` placeholder |

## Testing

Tests live in `tests/`. Currently only `test_bounce_detection.py` exists, covering `_extract_failed_recipient`, `_is_sendable`, `_next_address`, `check_bounces`, and `poll_bounces_loop`. The Gmail service is fully mocked — no real API calls.

`_mock_service(bounce_addresses)` in the test file builds a MagicMock that simulates MAILER-DAEMON messages with `X-Failed-Recipients` headers. When adding tests for new bounce-related logic, use this helper and `_make_job_json()` to write temp job files.

## Environment

Required in `.env`:
- `GROQ_API_KEY` — used by both agents
- `GMAIL_SENDER_NAME` — display name in From: header

Optional (each enables a fallback tier):
- `TAVILY_API_KEY`, `BRAVE_API_KEY`, `GOOGLE_CSE_API_KEY` + `GOOGLE_CSE_CX` — recruiter search
- `HUNTER_API_KEY`, `APOLLO_API_KEY` — email domain/pattern discovery

Gmail OAuth credentials go to `~/.getmehired/gmail_credentials.json` (outside the repo). Token is cached at `~/.getmehired/gmail_token.json` after first browser consent.
