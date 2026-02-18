# GetMeHired

AI-powered job scraping, analysis, and recruiter-finding pipeline.

## What it does

```
Job URL → Scrape → Extract (Groq LLM) → Store as JSON → Find Recruiters → Append to JSON
```

1. **Scrape** — fetches the raw job posting text from the URL, platform-aware
2. **Analyze** — uses Groq (`llama-3.3-70b-versatile`) to extract structured fields
3. **Store** — saves a JSON file to `data/jobs/` for downstream use
4. **Find Recruiters** — searches LinkedIn for recruiters at the company relevant to the job family, appends results to the job JSON

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

# Recruiter finder — optional, tried in order as fallback
BRAVE_API_KEY=                        # Brave Search API (free tier: ~$0.01/mo cap)
TAVILY_API_KEY=                       # Tavily (free tier: 1,000 searches/month) ← recommended
GOOGLE_CSE_API_KEY=                   # Google Custom Search (100 queries/day)
GOOGLE_CSE_CX=                        # Google Custom Search Engine ID
```

---

## Usage

### Step 1 — Scrape a job posting

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

### Step 2 — Find recruiters for a saved job

```bash
python scripts/find_recruiters.py data/jobs/<filename>.json
python scripts/find_recruiters.py data/jobs/<filename>.json --max-results 10
```

**Example:**

```bash
python scripts/find_recruiters.py data/jobs/anthropic__geopolitics_analyst_policy__20260215_225512.json --max-results 5
```

**Sample output:**

```
════════════════════════════════════════════════════════════════
  GetMeHired — Recruiter Finder
════════════════════════════════════════════════════════════════

  STEP 1 — Load Job
  ✓ Job Title            Geopolitics Analyst, Policy
  ✓ Company              anthropic
  ✓ Job Family           Policy / Government Affairs
  ✓ Location             Washington, D.C.

  STEP 2 — Search for Recruiters
  Searching for recruiters at 'anthropic' (Policy / Government Affairs)
  Location hint: Washington, D.C.
  Max results: 5

  Query 1: site:linkedin.com/in "anthropic" "recruiter" "Washington"
  → [TAVILY] Found 8 result(s)
  → 5 new unique recruiter(s)

  [1] Julia Schmaltz
       Title:    Recruiter @ Anthropic
       LinkedIn: https://www.linkedin.com/in/juliaschmaltz/

  STEP 3 — Update Job File
  ✓ Recruiters saved     5
```

Recruiters are appended to the job's JSON file under a `recruiters` key.

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
│   ├── test_scraper.py             # Step 1 CLI: scrape → analyze → store
│   └── find_recruiters.py          # Step 2 CLI: find recruiters → append to JSON
├── src/
│   └── getmehired/
│       ├── config.py               # pydantic-settings: reads .env
│       ├── agents/
│       │   └── job_analyzer.py     # Groq LLM extraction agent
│       ├── models/
│       │   ├── job.py              # JobPosting and JobFamily Pydantic models
│       │   └── recruiter.py        # Recruiter Pydantic model
│       └── services/
│           ├── job_scraper.py      # Platform-aware scraping engine
│           ├── recruiter_finder.py # 4-tier recruiter search (DDG/Brave/Tavily/Google CSE)
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

---

### Recruiter finder limitations

#### Search engine blocking (DuckDuckGo)
DuckDuckGo HTML mode works under normal usage but triggers bot detection (HTTP 202) if too many queries are fired in a short session. The service auto-falls back to API-based backends (Brave → Tavily → Google CSE) when this happens.

#### Brave Search API free tier
The Brave free plan has an extremely low spending cap (~$0.01/month ≈ 2 queries). It acts as a thin buffer before Tavily and will exhaust quickly under regular use.

#### LinkedIn title parsing
Recruiter names and titles are extracted from search snippet headings (`"Name - Title at Company | LinkedIn"`). Profiles with non-standard headlines (e.g. just the company name) will be missing a title. The LinkedIn URL is still captured correctly.

#### LinkedIn URL variants
Results may include regional LinkedIn domains (e.g. `cn.linkedin.com` for China). These are valid and correct — the profile URL format is consistent regardless of regional subdomain.

#### Company name quality
Some ATS platforms (e.g. Greenhouse) return the company as a lowercase slug (`"anthropic"` instead of `"Anthropic"`). The recruiter search uses this slug verbatim, which usually works since search engines are case-insensitive, but very unusual slugs may return fewer results.

#### No email extraction
The recruiter finder returns name, title, and LinkedIn URL only. Email addresses are not extracted (LinkedIn doesn't expose them in search snippets).

#### Search quota limits
| Backend | Free limit | Notes |
|---|---|---|
| DuckDuckGo | IP-based (no hard limit) | Rate-limited under heavy use |
| Brave | ~$0.01/month | Effectively 1–2 queries/month free |
| Tavily | 1,000 searches/month | Recommended primary fallback |
| Google CSE | 100 queries/day | Last resort |
