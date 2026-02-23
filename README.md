# GetMeHired

AI-powered job application pipeline — from a job URL to personalized outreach emails sent via Gmail.

## What it does

```
Job URL → Scrape → Extract (Groq LLM) → Store → Find Recruiters → Discover Emails → Draft Email → Send via Gmail → Bounce Detection
```

1. **Scrape** — fetches the raw job posting text from the URL, platform-aware
2. **Analyze** — uses Groq (`llama-3.3-70b-versatile`) to extract structured fields
3. **Store** — saves a JSON file to `data/jobs/` for downstream use
4. **Find Recruiters** — searches LinkedIn for recruiters at the company relevant to the job family, appends results to the job JSON
5. **Discover Emails** — finds the company's email domain and naming pattern, generates email address(es) for each recruiter
6. **Draft Email** — reads your resume + job description, uses Groq to write a personalized cold-outreach email body (one LLM call; names substituted per recruiter at send time)
7. **Send via Gmail** — authenticates via OAuth2, shows a recruiter list + email preview for confirmation, then sends personalized emails with your resume attached
8. **Bounce Detection** — polls Gmail for MAILER-DAEMON bounce messages; retries the next email pattern for any bounced address on the next run

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
pip install -e .

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

# Email discovery — optional, tried in order as fallback
HUNTER_API_KEY=                       # Hunter.io (free tier: 25 searches/month)
APOLLO_API_KEY=                       # Apollo.io (free tier: org domain lookup)

# Gmail sender (Step 7 — send_emails.py)
GMAIL_SENDER_NAME=Archit Gupta        # Display name in From: header
GMAIL_MAX_SEND_PER_RUN=3              # Max emails per run (default: 3)
GMAIL_BOUNCE_WAIT_SECONDS=300         # Seconds to wait before bounce poll (default: 300)
GMAIL_BOUNCE_LOOKBACK_MINUTES=30      # Lookback window for bounce detection (default: 30)
```

### Gmail OAuth setup (one-time)

Step 7 sends emails through your Gmail account using the Gmail API. You need to set up OAuth credentials once:

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project → enable the **Gmail API**
2. Go to **APIs & Services → Credentials** → **Create Credentials → OAuth 2.0 Client ID** → Desktop app
3. Download `client_secret.json` → save as `~/.getmehired/gmail_credentials.json`
4. Run `send_emails.py` — a browser window opens for OAuth consent → token cached at `~/.getmehired/gmail_token.json`

Subsequent runs auto-refresh the token silently. Required scope: `gmail.modify` (send + inbox read for bounce detection).

---

## Usage

### Full pipeline — single command (recommended)

```bash
source .venv/bin/activate
python scripts/run.py "<job-url>"
python scripts/run.py "<job-url>" --max-results 10
python scripts/run.py "<job-url>" --resume resume.pdf   # also drafts outreach emails
python scripts/run.py "<job-url>" --debug               # verbose output for troubleshooting
```

Runs Steps 1–8: URL normalisation → scraping → LLM analysis → storage → recruiter search → save recruiters → email discovery → email drafting.

`--resume` is optional. Without it, Steps 1–7 run and Step 8 (email drafting) is skipped. Sending emails is a separate step — run `send_emails.py` after this.

**Examples:**

```bash
# Greenhouse
python scripts/run.py "https://job-boards.greenhouse.io/anthropic/jobs/5096400008"

# Lever
python scripts/run.py "https://jobs.lever.co/belvederetrading/f81a8965-5537-4a4b-aec6-c02dfa51815e" --max-results 10

# Generic (Playwright)
python scripts/run.py "https://careers.homedepot.com/job/22798636/lead-data-scientist-assortment-and-space-planning-ai-engineering-/"
```

**Sample output:**

```
════════════════════════════════════════════════════════════════
  GetMeHired — Full Pipeline
════════════════════════════════════════════════════════════════
  Input URL : https://careers.homedepot.com/job/22798636/...

────────────────────────────────────────────────────────────────
  STEP 1 — URL Normalisation
────────────────────────────────────────────────────────────────
  ✓ URL                  https://careers.homedepot.com/job/22798636/...
  ✓ Platform detected    generic

────────────────────────────────────────────────────────────────
  STEP 2 — Scraping
────────────────────────────────────────────────────────────────
  ✓ Status               Success in 31.0s
  ✓ Page title           Lead Data Scientist, Assortment and Space Planning (AI-Engin
  ✓ Chars extracted      12000

────────────────────────────────────────────────────────────────
  STEP 3 — Analysis (Groq LLM)
────────────────────────────────────────────────────────────────
  ✓ Status               Success in 0.6s
  ✓ Job Title            Lead Data Scientist, Assortment and Space Planning (AI-Engineering)
  ✓ Company              The Home Depot
  ✓ Job Family           Data Science / ML
  ✓ Location             Atlanta, GA

────────────────────────────────────────────────────────────────
  STEP 4 — Storage
────────────────────────────────────────────────────────────────
  ✓ Status               Saved in 0.002s
  ✓ File                 data/jobs/the_home_depot__lead_data_scientist_...json
  ✓ File size            12,600 bytes

────────────────────────────────────────────────────────────────
  STEP 5 — Recruiter Search
────────────────────────────────────────────────────────────────
  Searching for recruiters at 'The Home Depot' (Data Science / ML)
  Location hint: Atlanta, GA
  Max results: 5

  Query 1: site:linkedin.com/in "The Home Depot" "technical recruiter" "Atlanta"
  ⚠ DDG failed: DuckDuckGo bot detection triggered (HTTP 202).
  ↻ Switching to BRAVE...
  → [BRAVE] Found 3 result(s)
  → 3 new unique recruiter(s)

  [1] Milica Henderson
       Title:    Sr. Technical Recruiter | The Home Depot
       LinkedIn: https://www.linkedin.com/in/milliedjurdjevic/

────────────────────────────────────────────────────────────────
  STEP 6 — Save Recruiters
────────────────────────────────────────────────────────────────
  ✓ Status               Saved in 0.001s
  ✓ Recruiters saved     5

────────────────────────────────────────────────────────────────
  STEP 7 — Email Discovery
────────────────────────────────────────────────────────────────
  Discovering email domain for 'The Home Depot'...
  ✓ Domain               homedepot.com
  Discovering email pattern for '@homedepot.com'...
  ✓ Pattern              {first}.{last}  →  e.g. jane.doe@homedepot.com
  ✓ Status               Complete in 2.1s
  ✓ Emails generated     5 / 5

  Milica Henderson
       Email:    milica.henderson@homedepot.com

  Julius Harris, SHRM
       Email:    julius.harris@homedepot.com
  ✓ File updated         data/jobs/the_home_depot__...json

────────────────────────────────────────────────────────────────
  STEP 8 — Email Drafting
────────────────────────────────────────────────────────────────
  ✓ Resume               resume.pdf (3,921 chars extracted)
  ✓ Draft generated      in 1.3s
  ✓ Subject              Exploring the Lead Data Scientist role at The Home Depot
  ✓ Body                 857 chars
════════════════════════════════════════════════════════════════
  Pipeline complete.
════════════════════════════════════════════════════════════════
```

The job JSON gains `email_subject` and `email_body` (job-level, not per-recruiter). The body uses `"Hi there,"` as a placeholder — names are substituted per recruiter at send time.

---

### Running steps individually

Use these when you want to re-run just one step (e.g. retry recruiter search on an already-saved job).

#### Step 1 — Scrape a job posting

```bash
python scripts/test_scraper.py "<job-url>"
```

```bash
# Greenhouse
python scripts/test_scraper.py "https://job-boards.greenhouse.io/anthropic/jobs/5096400008"

# Lever
python scripts/test_scraper.py "https://jobs.lever.co/belvederetrading/f81a8965-5537-4a4b-aec6-c02dfa51815e"

# Workday
python scripts/test_scraper.py "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Software-Engineer-Intern--2026_JR2008747"
```

#### Step 2 — Find recruiters + emails (+ optionally draft emails) for a saved job

```bash
python scripts/find_recruiters.py data/jobs/<filename>.json
python scripts/find_recruiters.py data/jobs/<filename>.json --max-results 10
python scripts/find_recruiters.py data/jobs/<filename>.json --resume resume.pdf
```

```bash
python scripts/find_recruiters.py data/jobs/anthropic__geopolitics_analyst_policy__20260215_225512.json --max-results 5 --resume resume.pdf
```

Runs recruiter search, email discovery, and (if `--resume` provided) email drafting on an already-saved job file. Useful for re-running steps without re-scraping.

#### Step 3 — Send outreach emails

```bash
python scripts/send_emails.py data/jobs/<filename>.json \
    --resume resume.pdf \
    --from-name "Your Name"
```

```bash
# Dry run — shows what would be sent without calling Gmail
python scripts/send_emails.py data/jobs/stripe__*.json \
    --dry-run --resume resume.pdf --from-name "Your Name"

# Send up to 3 emails (default), wait 5 min, then check for bounces
python scripts/send_emails.py data/jobs/stripe__*.json \
    --resume resume.pdf --from-name "Your Name"

# Send, skip bounce polling
python scripts/send_emails.py data/jobs/stripe__*.json \
    --resume resume.pdf --from-name "Your Name" --no-wait

# Check bounces only (no sending)
python scripts/send_emails.py data/jobs/stripe__*.json --check-bounces

# Retry bounced addresses with next email pattern
python scripts/send_emails.py data/jobs/stripe__*.json \
    --resume resume.pdf --from-name "Your Name" --retry-bounced
```

**What happens:**

1. Loads the job JSON and verifies an email draft exists
2. Re-drafts the email body fresh (includes job URL in opening sentence) if `--resume` is provided
3. Shows a confirmation preview — recruiter list with LinkedIn URLs + full personalized email — before sending
4. Authenticates with Gmail (OAuth2, cached token after first run)
5. Sends to up to `--max-send` eligible recruiters (default: 3), attaches resume PDF
6. Waits then polls Gmail inbox for MAILER-DAEMON bounce messages
7. Marks bounced addresses and suggests retrying with next email pattern

Each send is persisted to the JSON immediately, so a crash mid-batch doesn't lose progress.

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
│   ├── run.py                      # Full pipeline CLI: scrape → analyze → store → find recruiters → draft email
│   ├── test_scraper.py             # Step 1 only CLI: scrape → analyze → store
│   ├── find_recruiters.py          # Step 2 only CLI: find recruiters → emails → draft email
│   └── send_emails.py             # Step 3 only CLI: Gmail send → bounce detection → retry
├── src/
│   └── getmehired/
│       ├── config.py               # pydantic-settings: reads .env
│       ├── agents/
│       │   ├── job_analyzer.py     # Groq LLM extraction agent
│       │   └── email_drafter.py    # Groq LLM outreach email drafting agent
│       ├── models/
│       │   ├── job.py              # JobPosting and JobFamily Pydantic models
│       │   └── recruiter.py        # Recruiter Pydantic model (incl. send state)
│       └── services/
│           ├── job_scraper.py      # Platform-aware scraping engine
│           ├── recruiter_finder.py # 4-tier recruiter search (DDG/Brave/Tavily/Google CSE)
│           ├── email_finder.py     # Email domain + pattern discovery, address generation
│           ├── gmail_sender.py     # Gmail OAuth2 send + bounce detection
│           ├── resume_reader.py    # PDF and .txt resume text extraction
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

When a name is truncated to an initial (e.g. `"Jana H."`), the parser falls back to the LinkedIn profile URL slug to reconstruct the full name (e.g. `/in/janahaegi/` → `"Jana Haegi"`). This covers the most common case but may produce a single concatenated token for unusual slugs without hyphens.

#### LinkedIn URL variants
Results may include regional LinkedIn domains (e.g. `cn.linkedin.com` for China). These are valid and correct — the profile URL format is consistent regardless of regional subdomain.

#### Company name quality
Some ATS platforms (e.g. Greenhouse) return the company as a lowercase slug (`"anthropic"` instead of `"Anthropic"`). The recruiter search uses this slug verbatim, which usually works since search engines are case-insensitive, but very unusual slugs may return fewer results.

#### Search quota limits
| Backend | Free limit | Notes |
|---|---|---|
| DuckDuckGo | IP-based (no hard limit) | Rate-limited under heavy use |
| Brave | ~$0.01/month | Effectively 1–2 queries/month free |
| Tavily | 1,000 searches/month | Recommended primary fallback |
| Google CSE | 100 queries/day | Last resort |

---

### Email discovery

#### How it works
Email discovery runs after recruiter search and attempts three tiers to identify the company's email domain and naming pattern:

**Domain discovery** (tried in order, stops on first success):
1. Tavily web search — queries for the company's official site, frequency-votes across 8 results to pick the true corporate domain (avoids one-off PR sub-sites)
2. Hunter.io `/domain-search` — authoritative lookup by company name
3. Apollo.io organization search — free-tier org lookup returns `primary_domain` directly

**Pattern discovery** (tried in order):
1. Tavily web search — regex-mines search snippets for real `@domain` email addresses, infers pattern from their structure
2. Hunter.io `/domain-search` — returns the pattern key directly when it has data
3. Combinatorics fallback — generates all 6 common patterns as a comma-separated list

#### The 6 email patterns
| Pattern | Example (Jane Doe) |
|---|---|
| `{first}.{last}` | jane.doe@company.com |
| `{f}{last}` | jdoe@company.com |
| `{first}{l}` | janed@company.com |
| `{first}` | jane@company.com |
| `{f}.{last}` | j.doe@company.com |
| `{first}{last}` | janedoe@company.com |

#### Name handling
- Middle initials are skipped: `Marcus P White` → `marcus.white@…` (not `marcus.p@…`)
- Credential suffixes are stripped: `Julius Harris, SHRM` → `julius.harris@…`
- Accented characters are ASCII-normalized: `Héléne` → `helene@…`
- Truncated names (e.g. `Jana H.`) are recovered from the LinkedIn URL slug: `/in/janahaegi/` → `Jana Haegi`

#### Recruiter search resilience
- `talent` is added as a last-resort search term if all primary recruiter queries return zero results
- The search term used to find each recruiter is recorded in the `source` field (e.g. `brave [technical recruiter]`)

#### Email discovery quota limits
| Backend | Free limit | Notes |
|---|---|---|
| Tavily | 1,000 searches/month | Used for both domain and pattern search |
| Hunter.io | 25 searches/month | Domain + pattern in one call |
| Apollo.io | Unlimited (org search) | Domain only; pattern not available on free tier |
