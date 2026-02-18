"""
Recruiter finder service.

Strategy: search for LinkedIn profiles matching the company and a
job-family-specific recruiter role term. Parse name + title + LinkedIn URL.

Search backends (tried in order, failing over on rate-limit/block):
  1. DuckDuckGo HTML      — free, no API key
  2. Brave Search API     — free tier (~$0.01/mo cap), needs BRAVE_API_KEY
  3. Tavily API           — free tier (1,000/mo), needs TAVILY_API_KEY
  4. Google Custom Search — free tier (100/day), needs GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX

No paid APIs required for basic usage. Uses httpx (already a project dep).
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from getmehired.models.job import JobFamily, JobPosting
from getmehired.models.recruiter import Recruiter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Job family → recruiter search term mapping
# ---------------------------------------------------------------------------

_JOB_FAMILY_TERMS: dict[JobFamily, list[str]] = {
    JobFamily.SOFTWARE_ENGINEERING:   ["technical recruiter", "engineering recruiter"],
    JobFamily.DATA_SCIENCE_ML:        ["technical recruiter", "machine learning recruiter"],
    JobFamily.DATA_ANALYTICS:         ["technical recruiter", "data recruiter"],
    JobFamily.BUSINESS_ANALYTICS:     ["talent acquisition", "recruiter"],
    JobFamily.BUSINESS_DEVELOPMENT:   ["sales recruiter", "talent acquisition"],
    JobFamily.PRODUCT_MANAGEMENT:     ["technical recruiter", "product recruiter"],
    JobFamily.DESIGN_UX:              ["design recruiter", "creative recruiter"],
    JobFamily.DEVOPS_INFRA:           ["technical recruiter", "infrastructure recruiter"],
    JobFamily.CYBERSECURITY:          ["security recruiter", "technical recruiter"],
    JobFamily.MARKETING:              ["marketing recruiter", "talent acquisition"],
    JobFamily.FINANCE:                ["finance recruiter", "talent acquisition"],
    JobFamily.LEGAL:                  ["legal recruiter", "talent acquisition"],
    JobFamily.RESEARCH:               ["research recruiter", "technical recruiter"],
    JobFamily.OPERATIONS:             ["operations recruiter", "talent acquisition"],
    JobFamily.POLICY:                 ["recruiter", "talent acquisition"],
    JobFamily.OTHER:                  ["recruiter", "talent acquisition"],
}

_DDG_SEARCH_URL   = "https://html.duckduckgo.com/html/"
_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_GOOGLE_CSE_URL   = "https://www.googleapis.com/customsearch/v1"

_USER_AGENTS = [
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) "
        "Gecko/20100101 Firefox/109.0"
    ),
]

# US states and common country names — used to filter them out when extracting city
_STATES_AND_COUNTRIES = {
    "usa", "us", "united states", "uk", "united kingdom", "canada", "india",
    "china", "germany", "france", "japan", "australia", "singapore",
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
    # common abbreviations
    "al", "ak", "az", "ar", "ca", "co", "ct", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi",
    "mn", "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc",
    "nd", "oh", "ok", "or", "pa", "ri", "sc", "sd", "tn", "tx", "ut",
    "vt", "va", "wa", "wv", "wi", "wy", "d.c.", "dc",
}


class RecruiterSearchError(Exception):
    """Raised when the search request fails or is blocked."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def find_recruiters(
    job: JobPosting,
    *,
    max_results: int = 5,
    delay_range: tuple[float, float] = (4.0, 8.0),
) -> list[Recruiter]:
    """
    Search for LinkedIn profiles of recruiters at job.company.

    Backends are tried in order (DDG → Brave → Tavily → Google CSE). On the
    first failure of any backend, all remaining queries in this session
    automatically use the next available backend.

    Query cascade (stops as soon as max_results unique recruiters are found):
      1. term_1 + city  (if location available)
      2. term_1         (no location)
      3. term_2 + city  (if location available)
      4. term_2         (no location)

    Returns list of Recruiter objects, deduplicated by linkedin_url.
    """
    company = (job.company or "").strip()
    if not company:
        raise ValueError("job.company is empty — cannot search for recruiters")

    terms = _JOB_FAMILY_TERMS.get(job.job_family, ["recruiter", "talent acquisition"])
    city = _extract_city(job.location) if job.location else None

    # Build query cascade: for each term, try with city first, then without
    queries: list[str] = []
    for term in terms[:2]:
        if city:
            queries.append(f'site:linkedin.com/in "{company}" "{term}" "{city}"')
        queries.append(f'site:linkedin.com/in "{company}" "{term}"')

    # Build the ordered list of available backends
    cfg = _load_config()
    backends: list[str] = ["ddg"]
    if cfg.get("brave_key"):
        backends.append("brave")
    if cfg.get("tavily_key"):
        backends.append("tavily")
    if cfg.get("google_cse_api_key") and cfg.get("google_cse_cx"):
        backends.append("google_cse")

    backend_idx = 0  # index into backends; advances on failure

    all_recruiters: list[Recruiter] = []
    seen_urls: set[str] = set()

    for i, query in enumerate(queries):
        if len(all_recruiters) >= max_results:
            break

        if backend_idx >= len(backends):
            print("  ✗ All search backends exhausted — stopping.")
            break

        # Delay between queries
        if i > 0:
            delay = random.uniform(*delay_range)
            print(f"  Waiting {delay:.1f}s before next query...")
            await asyncio.sleep(delay)

        print(f"  Query {i + 1}: {query}")

        # Try backends in order, advancing on failure
        batch: list[Recruiter] = []
        while backend_idx < len(backends):
            backend = backends[backend_idx]
            try:
                batch = await _dispatch(backend, query, cfg)
                print(f"  → [{backend.upper()}] Found {len(batch)} result(s)")
                break
            except RecruiterSearchError as e:
                print(f"  ⚠ {backend.upper()} failed: {e}")
                backend_idx += 1
                if backend_idx < len(backends):
                    print(f"  ↻ Switching to {backends[backend_idx].upper()}...")

        new_count = 0
        for r in batch:
            norm_url = _normalize_linkedin_url(r.linkedin_url or "")
            if norm_url and norm_url not in seen_urls:
                seen_urls.add(norm_url)
                all_recruiters.append(r)
                new_count += 1

        if batch:
            print(f"  → {new_count} new unique recruiter(s)")

    return all_recruiters[:max_results]


async def _dispatch(backend: str, query: str, cfg: dict) -> list[Recruiter]:
    """Route a query to the appropriate backend search function."""
    if backend == "ddg":
        return await _search_ddg(query)
    if backend == "brave":
        return await _search_brave(query, cfg["brave_key"])
    if backend == "tavily":
        return await _search_tavily(query, cfg["tavily_key"])
    if backend == "google_cse":
        return await _search_google_cse(
            query,
            api_key=cfg["google_cse_api_key"],
            cx=cfg["google_cse_cx"],
        )
    raise RecruiterSearchError(f"Unknown backend: {backend}")


# ---------------------------------------------------------------------------
# DuckDuckGo HTML search
# ---------------------------------------------------------------------------


async def _search_ddg(query: str) -> list[Recruiter]:
    """Fetch one DuckDuckGo HTML search page and parse recruiter candidates."""
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://html.duckduckgo.com/",
    }

    async with httpx.AsyncClient(
        timeout=20,
        headers=headers,
        follow_redirects=True,
    ) as client:
        try:
            resp = await client.post(
                _DDG_SEARCH_URL,
                data={"q": query, "b": ""},
            )
        except httpx.TimeoutException as e:
            raise RecruiterSearchError(f"Request timed out: {e}") from e
        except httpx.RequestError as e:
            raise RecruiterSearchError(f"Network error: {e}") from e

    if resp.status_code == 202:
        raise RecruiterSearchError(
            "DuckDuckGo bot detection triggered (HTTP 202)."
        )
    if resp.status_code == 429:
        raise RecruiterSearchError("DuckDuckGo rate limited (HTTP 429).")
    if resp.status_code != 200:
        raise RecruiterSearchError(f"DuckDuckGo returned HTTP {resp.status_code}")

    html = resp.text
    if "anomaly" in html.lower() or "challenge-form" in html:
        raise RecruiterSearchError("DuckDuckGo bot challenge detected.")

    return _parse_ddg_results(html)


def _parse_ddg_results(html: str) -> list[Recruiter]:
    """Extract Recruiter candidates from a DuckDuckGo HTML search result page."""
    soup = BeautifulSoup(html, "lxml")
    recruiters: list[Recruiter] = []

    for anchor in soup.select("a.result__a"):
        href = anchor.get("href", "")
        linkedin_url = _extract_ddg_url(href)
        if not linkedin_url or "/in/" not in linkedin_url:
            continue

        title_text = anchor.get_text(separator=" ").strip()
        name, role_title = _parse_linkedin_title(title_text)
        if not name:
            continue

        recruiters.append(Recruiter(
            name=name,
            title=role_title or None,
            linkedin_url=linkedin_url,
            source="duckduckgo",
        ))

    return recruiters


# ---------------------------------------------------------------------------
# Brave Search API
# ---------------------------------------------------------------------------


async def _search_brave(query: str, api_key: str) -> list[Recruiter]:
    """Search Brave and parse LinkedIn recruiter profiles from results."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": api_key,
    }
    params = {"q": query, "count": "10"}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(_BRAVE_SEARCH_URL, headers=headers, params=params)
        except httpx.TimeoutException as e:
            raise RecruiterSearchError(f"Brave request timed out: {e}") from e
        except httpx.RequestError as e:
            raise RecruiterSearchError(f"Brave network error: {e}") from e

    if resp.status_code in (402, 429):
        raise RecruiterSearchError("Brave Search usage limit exceeded.")
    if resp.status_code == 401:
        raise RecruiterSearchError("Brave API key is invalid.")
    if resp.status_code != 200:
        raise RecruiterSearchError(f"Brave returned HTTP {resp.status_code}")

    return _parse_brave_results(resp.json())


def _parse_brave_results(data: dict) -> list[Recruiter]:
    """Extract Recruiter candidates from Brave Search API JSON response."""
    recruiters: list[Recruiter] = []
    for result in data.get("web", {}).get("results", []):
        url = result.get("url", "")
        if "linkedin.com/in/" not in url:
            continue
        name, role_title = _parse_linkedin_title(result.get("title", ""))
        if not name:
            continue
        recruiters.append(Recruiter(
            name=name,
            title=role_title or None,
            linkedin_url=url,
            source="brave",
        ))
    return recruiters


# ---------------------------------------------------------------------------
# Tavily API
# ---------------------------------------------------------------------------


async def _search_tavily(query: str, api_key: str) -> list[Recruiter]:
    """Search Tavily and parse LinkedIn recruiter profiles from results."""
    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_domains": ["linkedin.com"],
        "max_results": 10,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(_TAVILY_SEARCH_URL, json=payload)
        except httpx.TimeoutException as e:
            raise RecruiterSearchError(f"Tavily request timed out: {e}") from e
        except httpx.RequestError as e:
            raise RecruiterSearchError(f"Tavily network error: {e}") from e

    if resp.status_code == 429:
        raise RecruiterSearchError("Tavily rate limit exceeded.")
    if resp.status_code == 401:
        raise RecruiterSearchError("Tavily API key is invalid.")
    if resp.status_code != 200:
        raise RecruiterSearchError(f"Tavily returned HTTP {resp.status_code}")

    return _parse_tavily_results(resp.json())


def _parse_tavily_results(data: dict) -> list[Recruiter]:
    """Extract Recruiter candidates from Tavily JSON response."""
    recruiters: list[Recruiter] = []
    for result in data.get("results", []):
        url = result.get("url", "")
        if "linkedin.com/in/" not in url:
            continue
        name, role_title = _parse_linkedin_title(result.get("title", ""))
        if not name:
            continue
        recruiters.append(Recruiter(
            name=name,
            title=role_title or None,
            linkedin_url=url,
            source="tavily",
        ))
    return recruiters


# ---------------------------------------------------------------------------
# Google Custom Search API
# ---------------------------------------------------------------------------


async def _search_google_cse(query: str, *, api_key: str, cx: str) -> list[Recruiter]:
    """Search Google Custom Search API and parse LinkedIn recruiter profiles."""
    params = {"key": api_key, "cx": cx, "q": query, "num": "10"}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(_GOOGLE_CSE_URL, params=params)
        except httpx.TimeoutException as e:
            raise RecruiterSearchError(f"Google CSE request timed out: {e}") from e
        except httpx.RequestError as e:
            raise RecruiterSearchError(f"Google CSE network error: {e}") from e

    if resp.status_code == 429:
        raise RecruiterSearchError("Google CSE daily quota exceeded (100/day).")
    if resp.status_code == 403:
        raise RecruiterSearchError("Google CSE API key unauthorized or quota exceeded.")
    if resp.status_code == 400:
        error_msg = resp.json().get("error", {}).get("message", "Bad request")
        raise RecruiterSearchError(f"Google CSE bad request: {error_msg}")
    if resp.status_code != 200:
        raise RecruiterSearchError(f"Google CSE returned HTTP {resp.status_code}")

    return _parse_google_cse_results(resp.json())


def _parse_google_cse_results(data: dict) -> list[Recruiter]:
    """Extract Recruiter candidates from Google Custom Search JSON response."""
    recruiters: list[Recruiter] = []
    for item in data.get("items", []):
        url = item.get("link", "")
        if "linkedin.com/in/" not in url:
            continue
        name, role_title = _parse_linkedin_title(item.get("title", ""))
        if not name:
            continue
        recruiters.append(Recruiter(
            name=name,
            title=role_title or None,
            linkedin_url=url,
            source="google_cse",
        ))
    return recruiters


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """Load all search-related config from settings."""
    try:
        from getmehired.config import get_settings
        s = get_settings()
        return {
            "brave_key":        s.brave_api_key or None,
            "tavily_key":       s.tavily_api_key or None,
            "google_cse_api_key": s.google_cse_api_key or None,
            "google_cse_cx":    s.google_cse_cx or None,
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_ddg_url(href: str) -> Optional[str]:
    """Extract the real URL from DuckDuckGo's redirect link."""
    try:
        parsed = urlparse(href)
        qs = parse_qs(parsed.query)
        targets = qs.get("uddg", [])
        if targets:
            return unquote(targets[0])
    except Exception:
        pass
    if "linkedin.com/in/" in href:
        return href
    return None


def _parse_linkedin_title(text: str) -> tuple[str, Optional[str]]:
    """
    Parse a LinkedIn profile title from a search snippet heading.

    Typical formats:
      "Jane Doe - Technical Recruiter at Acme | LinkedIn"
      "John Smith – Talent Acquisition | LinkedIn"
      "Alice Brown • Senior Recruiter at Company"

    Returns (name, title_or_none). Returns ("", None) if parsing fails.
    """
    text = re.sub(r"\s*[|–\-]\s*LinkedIn\s*$", "", text, flags=re.IGNORECASE).strip()
    match = re.match(r"^(.+?)\s*[-–|•]\s*(.+)$", text)
    if match:
        name = match.group(1).strip()
        role = match.group(2).strip()
        if 2 <= len(name) <= 60 and not re.search(r"\d", name):
            return name, role or None
    return "", None


def _extract_city(location: str) -> Optional[str]:
    """
    Extract the primary city name from a job location string.

    Examples:
      "Orlando, FL, USA"      → "Orlando"
      "Chicago, Illinois"     → "Chicago"
      "Washington, D.C."      → "Washington"
      "China, Shanghai"       → "Shanghai"
    """
    if not location:
        return None
    parts = [p.strip() for p in location.split(",")]
    city_candidates = [
        p for p in parts
        if p.lower() not in _STATES_AND_COUNTRIES and len(p) > 1
    ]
    if city_candidates:
        return city_candidates[0]
    return parts[0] if parts else None


def _normalize_linkedin_url(url: str) -> str:
    """Lowercase and strip trailing slash for deduplication."""
    return url.lower().rstrip("/")
