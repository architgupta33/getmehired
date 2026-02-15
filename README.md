# GetMeHired

AI-powered job scraping and analysis pipeline. Paste a job posting URL, get back structured data.

## What it does

```
Job URL → Scrape → Extract (Groq LLM) → Store as JSON
```

1. **Scrape** — fetches the raw job posting text from the URL, platform-aware
2. **Analyze** — uses Groq (`llama-3.3-70b-versatile`) to extract structured fields
3. **Store** — saves a JSON file to `data/jobs/` for downstream use

### Extracted fields

| Field | Description |
|---|---|
| `job_title` | Exact title from the posting |
| `company` | Company name |
| `job_family` | Broad category (see list below) |
| `location` | Primary work location, or `null` if not specified |
| `description` | Full cleaned text of the posting |
| `url` | Canonical (de-tracked) URL |
| `platform` | Which job board the URL came from |
| `scraped_at` | UTC timestamp |

### Job family categories

Software Engineering · Data Science / ML · Data Analytics · Business Analytics ·
Business Development / Sales · Product Management · Design / UX · DevOps / Infrastructure ·
Cybersecurity · Marketing · Finance / Accounting · Legal / Compliance ·
Research · Operations · Policy / Government Affairs · Other

---

## Setup

### Prerequisites

- Python 3.11+
- A [Groq API key](https://console.groq.com) (free tier works)
- Playwright Chromium (only needed for Workday and generic URLs)

```bash
git clone <repo-url>
cd getmehired

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# Install dependencies
pip install playwright beautifulsoup4 lxml httpx groq pydantic pydantic-settings python-dotenv

# Install Playwright browser (only needed once, for Workday/generic scraping)
playwright install chromium

# Configure environment
cp .env.example .env
# Edit .env and add your GROQ_API_KEY
```

### `.env` format

```
GROQ_API_KEY=gsk_...
GROQ_MODEL=llama-3.3-70b-versatile   # optional, this is the default
DATA_DIR=data/jobs                    # optional, this is the default
```

---

## Usage

### CLI pipeline script

```bash
source .venv/bin/activate
python scripts/test_scraper.py "<job-url>"
```

**Examples:**

```bash
# Greenhouse
python scripts/test_scraper.py "https://job-boards.greenhouse.io/anthropic/jobs/5096400008"

# Lever
python scripts/test_scraper.py "https://jobs.lever.co/belvederetrading/f81a8965-5537-4a4b-aec6-c02dfa51815e"

# Workday
python scripts/test_scraper.py "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Software-Engineer-Intern--2026_JR2008747"
```

**Sample output:**

```
════════════════════════════════════════════════════════════════
  GetMeHired — Pipeline Debug
════════════════════════════════════════════════════════════════
  Input URL : https://job-boards.greenhouse.io/anthropic/jobs/5096400008

────────────────────────────────────────────────────────────────
  STEP 1 — URL Normalisation
────────────────────────────────────────────────────────────────
  ✓ URL                https://job-boards.greenhouse.io/anthropic/jobs/5096400008
  ✓ Platform detected  greenhouse

────────────────────────────────────────────────────────────────
  STEP 2 — Scraping
────────────────────────────────────────────────────────────────
  ✓ Status             Success in 0.4s
  ✓ Page title         Geopolitics Analyst, Policy
  ✓ Chars extracted    7845

────────────────────────────────────────────────────────────────
  STEP 3 — Analysis (Groq LLM)
────────────────────────────────────────────────────────────────
  ✓ Status             Success in 0.5s
  ✓ Job Title          Geopolitics Analyst, Policy
  ✓ Company            Anthropic
  ✓ Job Family         Policy / Government Affairs
  ✓ Location           Washington, D.C.

────────────────────────────────────────────────────────────────
  STEP 4 — Storage
────────────────────────────────────────────────────────────────
  ✓ Status             Saved in 0.001s
  ✓ File               data/jobs/anthropic__geopolitics_analyst_policy__20260215_225512.json
  ✓ File size          6,241 bytes
```

---

## Supported platforms

| Platform | Strategy | Typical speed |
|---|---|---|
| **Greenhouse** | Greenhouse public JSON API | ~0.4s |
| **Lever** | httpx + HTML + JSON-LD parsing | ~1–2s |
| **Workday** | Playwright headless Chromium | ~3–10s |
| **Generic** | Playwright headless Chromium | ~5–15s |

**URL normalization** strips tracking parameters (`utm_*`, `src`, `pid`, etc.) and cleans up platform-specific path quirks (e.g. Workday `/apply` suffix) before scraping.

---

## Project structure

```
getmehired/
├── pyproject.toml                  # Build config and dependency declarations
├── .env.example                    # Environment variable template
├── scripts/
│   └── test_scraper.py             # CLI pipeline runner: scrape → analyze → store
├── src/
│   └── getmehired/
│       ├── config.py               # pydantic-settings: reads .env
│       ├── agents/
│       │   └── job_analyzer.py     # Groq LLM extraction agent
│       ├── models/
│       │   └── job.py              # JobPosting and JobFamily Pydantic models
│       └── services/
│           ├── job_scraper.py      # Platform-aware scraping engine
│           └── storage.py          # JSON persistence to data/jobs/
└── data/
    └── jobs/                       # Saved job postings (git-ignored)
```

---

## Known limitations and unsolved edge cases

### LinkedIn
LinkedIn requires login for most job detail pages and aggressively rate-limits scrapers. Not supported.

### Indeed / Glassdoor / ZipRecruiter
Heavy bot detection (Cloudflare, CAPTCHA). Playwright alone is insufficient. Not supported.

### Login-walled pages
Any page requiring authentication will return empty content or a login redirect. The scraper returns a failure with an error message; no retry or auth flow is implemented.

### Company name casing
The Greenhouse API returns the company as a lowercase slug (e.g. `"anthropic"`). The LLM usually corrects this from context in the description body, but occasionally the raw slug comes through.

### Workday subdomain variants
Detection covers `*.wd1–wd5.myworkdayjobs.com` and `*.myworkday.com`. Unusual or regional Workday subdomains may fall back to the generic Playwright strategy.

### ATS platforms without dedicated scrapers
The following are handled by the generic Playwright fallback (works in most cases, but slower and less reliable than a dedicated strategy):
Ashby · Rippling · SmartRecruiters · Taleo (Oracle HCM) · iCIMS

### Truncation
Job description text is truncated to 8,000 characters before being sent to Groq. Very long postings may lose tail content (benefits, boilerplate), but title/company/location appear early and are reliably extracted.

### Groq rate limits
The free tier has rate limits. Running many extractions in quick succession may return `429 Too Many Requests`. No retry logic is implemented yet.
