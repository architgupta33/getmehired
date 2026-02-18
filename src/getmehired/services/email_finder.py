"""
Email discovery service — Step 3 of the GetMeHired pipeline.

Given a company name and a list of recruiters (with first/last names), this
module attempts to:
  1. Discover the company's email domain  (e.g. "homedepot.com")
  2. Discover the email naming pattern    (e.g. "{first}.{last}")
  3. Generate email address(es) for each recruiter and populate Recruiter.email

Discovery tiers (tried in order, stop on first success):

  Domain discovery
    Tier 1 — Web search (Tavily): query for the company's homepage URL, derive domain
    Tier 2 — Hunter.io /domain-search: returns domain directly (needs HUNTER_API_KEY)

  Pattern discovery
    Tier 1 — Web search (Tavily): search for "@<domain>" in public content, regex-extract
              actual email addresses, infer pattern from their structure
    Tier 2 — Hunter.io /domain-search: returns the pattern key directly
    Tier 3 — Combinatorics fallback: generate all 6 common patterns, store comma-separated

No new dependencies — uses httpx (already required) and unicodedata (stdlib).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Optional
from urllib.parse import urlparse

import httpx

from getmehired.models.recruiter import Recruiter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HUNTER_DOMAIN_SEARCH = "https://api.hunter.io/v2/domain-search"
_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_APOLLO_ORG_SEARCH = "https://api.apollo.io/v1/organizations/search"

# The 6 standard corporate email patterns, in order of prevalence.
# Keys match Hunter.io's pattern naming convention so we can reuse them directly.
_PATTERNS = [
    "{first}.{last}",   # jane.doe@co.com   — most common
    "{f}{last}",        # jdoe@co.com
    "{first}{l}",       # janed@co.com
    "{first}",          # jane@co.com
    "{f}.{last}",       # j.doe@co.com
    "{first}{last}",    # janedoe@co.com
]

# Suffixes / credentials that appear after a comma in a name field.
# "Julius Harris, SHRM" → strip ", SHRM" before splitting.
_CREDENTIAL_PATTERN = re.compile(r",\s*[A-Z][\w\-\.]+(?:\s+[A-Z][\w\-\.]+)*$")

# Domains that are ATS/job-board platforms — NOT the company's own email domain.
# If web search returns one of these as the company homepage, discard it.
_ATS_DOMAINS = {
    "greenhouse.io", "lever.co", "workday.com", "myworkdayjobs.com",
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "smartrecruiters.com", "icims.com", "taleo.net", "ashbyhq.com",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def find_emails(company: str, recruiters: list[Recruiter]) -> list[Recruiter]:
    """
    Discover the email domain and pattern for `company`, then generate an email
    address for each recruiter in the list.

    Modifies each Recruiter's `.email` field in-place and also returns the list
    so callers can chain: `recruiters = await find_emails(company, recruiters)`.

    Recruiters without a parseable first+last name are skipped (email stays None).
    """
    cfg = _load_config()

    # ── Step A: find the company's email domain ────────────────────────────
    print(f"  Discovering email domain for '{company}'...")
    domain = await _discover_domain(company, cfg)
    if domain:
        print(f"  ✓ Domain               {domain}")
    else:
        print(f"  ✗ Domain               Could not determine — emails will not be generated")
        return recruiters

    # ── Step B: find the email pattern for that domain ─────────────────────
    print(f"  Discovering email pattern for '@{domain}'...")
    pattern = await _discover_pattern(domain, company, cfg)
    if pattern:
        print(f"  ✓ Pattern              {pattern}  →  e.g. {_example(pattern, domain)}")
    else:
        print(f"  ⚠ Pattern              Not found — using all 6 common patterns (combinatorics)")

    # ── Step C: generate emails for each recruiter ─────────────────────────
    for r in recruiters:
        first, last = _parse_name(r.name)
        if not first or not last:
            # Can't generate an email without both name parts
            continue
        r.email = _generate_emails(first, last, domain, pattern)

    return recruiters


# ---------------------------------------------------------------------------
# Domain discovery
# ---------------------------------------------------------------------------


async def _discover_domain(company: str, cfg: dict) -> Optional[str]:
    """
    Try to find the company's primary email domain.

    Tier 1 — Tavily web search: search for the company's official website.
              Take the first result URL that isn't an ATS/job board domain,
              strip subdomains (www., careers., etc.) to get the root domain.

    Tier 2 — Hunter.io /domain-search: pass company name, Hunter returns domain.

    Tier 3 — Apollo.io People Search: find one person at the company, enrich
              with name+company to get their work email, extract domain from it.

    Returns a bare domain string like "homedepot.com", or None if all fail.
    """
    # Tier 1: web search
    if cfg.get("tavily_key"):
        domain = await _domain_via_search(company, cfg["tavily_key"])
        if domain:
            return domain

    # Tier 2: Hunter.io
    if cfg.get("hunter_key"):
        domain = await _domain_via_hunter(company, cfg["hunter_key"])
        if domain:
            return domain

    # Tier 3: Apollo.io
    if cfg.get("apollo_key"):
        domain, _ = await _lookup_via_apollo(company, cfg["apollo_key"])
        if domain:
            return domain

    return None


async def _domain_via_search(company: str, tavily_key: str) -> Optional[str]:
    """
    Search for '<company> careers email' via Tavily and extract the most likely
    corporate email domain from the top results.

    We score candidate domains by frequency across all result URLs — the
    company's real domain will appear in multiple results (homepage, careers,
    blog, etc.) whereas PR/brand sub-sites appear only once.
    """
    # Use a query that biases toward corporate/HR pages which contain real email domains
    query = f"{company} careers jobs email contact"
    payload = {
        "api_key": tavily_key,
        "query": query,
        "search_depth": "basic",
        "max_results": 8,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_TAVILY_SEARCH_URL, json=payload)
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])

        # Count how many results share the same root domain
        domain_votes: Counter = Counter()
        for r in results:
            domain = _extract_root_domain(r.get("url", ""))
            if domain and not _is_ats_domain(domain):
                domain_votes[domain] += 1

        if not domain_votes:
            return None

        # Return the domain that appears most often — reduces noise from one-off
        # PR or brand sub-sites (e.g. "homedepotbrand.com" vs "homedepot.com")
        return domain_votes.most_common(1)[0][0]
    except Exception:
        pass
    return None


async def _domain_via_hunter(company: str, hunter_key: str) -> Optional[str]:
    """
    Call Hunter.io /domain-search with company name. Hunter returns the domain
    and email pattern it has on record. We use just the domain here; pattern
    is re-fetched in _discover_pattern for clarity.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _HUNTER_DOMAIN_SEARCH,
                params={"company": company, "api_key": hunter_key},
            )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        return data.get("domain") or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Pattern discovery
# ---------------------------------------------------------------------------


async def _discover_pattern(domain: str, company: str, cfg: dict) -> Optional[str]:
    """
    Try to identify the company's email naming pattern.

    Tier 1 — Tavily web search: search for actual email addresses containing
              @<domain>, extract them from snippets using regex, then infer the
              pattern from the structure (e.g. "jane.doe@co.com" → "{first}.{last}").

    Tier 2 — Hunter.io /domain-search: directly returns the `pattern` field.

    Tier 3 — Returns None (caller falls back to all-patterns combinatorics).
              Apollo free tier does not expose pattern data.

    Returns a pattern string like "{first}.{last}" or None.
    """
    # Tier 1: regex-mine search result snippets for real email addresses
    if cfg.get("tavily_key"):
        pattern = await _pattern_via_search(domain, cfg["tavily_key"])
        if pattern:
            return pattern

    # Tier 2: Hunter.io
    if cfg.get("hunter_key"):
        pattern = await _pattern_via_hunter(domain, cfg["hunter_key"])
        if pattern:
            return pattern

    return None  # triggers combinatorics fallback


async def _pattern_via_search(domain: str, tavily_key: str) -> Optional[str]:
    """
    Search for real email addresses at @<domain> in the public web.
    Parse any found addresses and infer the naming pattern from their structure.
    """
    # Search for pages that contain actual "@domain" email addresses
    query = f'"@{domain}" email contact'
    payload = {
        "api_key": tavily_key,
        "query": query,
        "search_depth": "basic",
        "max_results": 10,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(_TAVILY_SEARCH_URL, json=payload)
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
        # Collect all @domain email addresses from titles and snippets
        email_re = re.compile(
            r'\b([a-z][a-z0-9]*(?:[.\-][a-z][a-z0-9]*)*)@' + re.escape(domain) + r'\b',
            re.IGNORECASE,
        )
        found_emails: list[str] = []
        for r in results:
            for field in ("title", "content", "url"):
                found_emails.extend(email_re.findall(r.get(field, "")))

        if not found_emails:
            return None

        # Infer pattern from the most common email structure
        return _infer_pattern(found_emails)
    except Exception:
        return None


async def _pattern_via_hunter(domain: str, hunter_key: str) -> Optional[str]:
    """
    Call Hunter.io /domain-search with the domain. Hunter's `pattern` field
    uses the same {first}/{last}/{f}/{l} placeholder syntax we use.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                _HUNTER_DOMAIN_SEARCH,
                params={"domain": domain, "api_key": hunter_key},
            )
        if resp.status_code != 200:
            return None
        data = resp.json().get("data", {})
        pattern = data.get("pattern")
        # Validate it's one of our known patterns
        if pattern in _PATTERNS:
            return pattern
    except Exception:
        pass
    return None


async def _lookup_via_apollo(company: str, apollo_key: str) -> tuple[Optional[str], Optional[str]]:
    """
    Use Apollo.io Organization Search (free tier, no credits) to discover
    a company's primary email domain.

    The `/v1/organizations/search` endpoint is accessible on Apollo's free plan
    and returns `primary_domain` directly (e.g. "kalshi.com").

    Pattern is not returned by this endpoint — returns None for pattern so the
    caller falls through to combinatorics.

    Returns (domain, None) or (None, None) if lookup fails.
    """
    try:
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": apollo_key,
        }
        payload = {
            "q_organization_name": company,
            "page": 1,
            "per_page": 1,
        }
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(
                _APOLLO_ORG_SEARCH,
                headers=headers,
                json=payload,
            )
        if resp.status_code != 200:
            return None, None
        orgs = resp.json().get("organizations", [])
        if not orgs:
            return None, None
        domain = orgs[0].get("primary_domain") or None
        return domain, None  # pattern not available from org search
    except Exception:
        return None, None


def _infer_pattern(email_locals: list[str]) -> Optional[str]:
    """
    Given a list of email local-parts (before the @), vote for the most likely
    naming pattern by checking which of the 6 templates best fits the majority.

    We can't know the actual names behind the emails, so we use structural heuristics:
      - Contains a dot → likely {first}.{last} or {f}.{last}
      - Single letter prefix before dot → {f}.{last}
      - All lowercase letters, no separator → {first}{last} or {first}
      - Short (≤4 chars) → {first} or {f}{last}
    """
    if not email_locals:
        return None

    votes: dict[str, int] = {p: 0 for p in _PATTERNS}

    for local in email_locals:
        local = local.lower()
        if "." in local:
            parts = local.split(".")
            if len(parts) == 2:
                first_part, last_part = parts
                if len(first_part) == 1:
                    votes["{f}.{last}"] += 1
                else:
                    votes["{first}.{last}"] += 1
        elif "-" in local:
            # Hyphenated: treat same as dot
            votes["{first}.{last}"] += 1
        else:
            # No separator
            if len(local) <= 5:
                # Short → probably first initial + last (jdoe) or just first
                votes["{f}{last}"] += 1
            else:
                # Longer → probably first+last concatenated
                votes["{first}{last}"] += 1

    best = max(votes, key=lambda k: votes[k])
    # Only return a pattern if at least 1 vote landed on it
    return best if votes[best] > 0 else None


# ---------------------------------------------------------------------------
# Email generation
# ---------------------------------------------------------------------------


def _generate_emails(first: str, last: str, domain: str, pattern: Optional[str]) -> str:
    """
    Generate email address(es) for a person given their normalized first/last name.

    If pattern is known → return a single email string.
    If pattern is None (combinatorics fallback) → return all 6 patterns
    as a comma-separated string so the user can try each one.
    """
    f = first[0] if first else ""  # first initial
    l = last[0] if last else ""    # last initial

    def apply(p: str) -> str:
        return (
            p.replace("{first}", first)
             .replace("{last}", last)
             .replace("{f}", f)
             .replace("{l}", l)
        ) + f"@{domain}"

    if pattern:
        return apply(pattern)

    # Combinatorics fallback: return all 6, comma-separated
    return ",".join(apply(p) for p in _PATTERNS)


# ---------------------------------------------------------------------------
# Name parsing and normalization
# ---------------------------------------------------------------------------


def _parse_name(full_name: str) -> tuple[str, str]:
    """
    Split a full display name into (first, last) normalized for email use.

    Handles:
      - "Julius Harris, SHRM"   → ("julius", "harris")
      - "Milica Henderson"      → ("milica", "henderson")
      - "Max Hirsch"            → ("max", "hirsch")
      - "Julius Harris, CP"     → ("julius", "harris")  [credential stripped]
      - "Madonna"               → ("", "")              [single name, skip]

    Credential suffixes like ", SHRM", ", CP", ", MBA" are stripped first.
    Only the first two words of the cleaned name are used (first + last).
    Returns ("", "") if fewer than 2 words remain after cleaning.
    """
    if not full_name:
        return "", ""

    # Strip credential/honorific suffixes appended after a comma
    name = _CREDENTIAL_PATTERN.sub("", full_name).strip()

    # Split, skipping single-letter middle initials to get the true last name.
    # e.g. "Marcus P White" → first="marcus", skip "P", last="white"
    words = name.split()
    if len(words) < 2:
        return "", ""  # can't determine last name

    first = _normalize(words[0])
    # Walk remaining words; take the first one that is longer than 1 letter
    last = ""
    for w in words[1:]:
        if len(w.rstrip(".")) > 1:
            last = _normalize(w)
            break
    if not last:
        # All trailing words were initials — fall back to the second word
        last = _normalize(words[1])

    if not first or not last:
        return "", ""

    return first, last


def _normalize(s: str) -> str:
    """
    Normalize a name token for use in an email address:
      - Lowercase
      - Strip accents / diacritics (é→e, ñ→n, ü→u)
      - Keep only ASCII letters (drop hyphens, apostrophes, digits)

    Examples:
      "Héléne"  → "helene"
      "O'Brien" → "obrien"
      "Müller"  → "muller"
    """
    # NFKD decomposition splits characters with accents into base + combining
    nfkd = unicodedata.normalize("NFKD", s.lower())
    # Keep only ASCII letters
    return "".join(c for c in nfkd if c.isascii() and c.isalpha())


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _extract_root_domain(url: str) -> Optional[str]:
    """
    Extract the registrable root domain from a URL.

    Examples:
      "https://www.homedepot.com/about"          → "homedepot.com"
      "https://careers.anthropic.com/jobs/123"   → "anthropic.com"
      "https://job-boards.greenhouse.io/acme"    → "greenhouse.io"

    Strips exactly one level of subdomain if present (www, careers, jobs, etc.).
    For two-part domains (e.g. "github.com") returns as-is.
    """
    try:
        hostname = urlparse(url).hostname or ""
        # Strip leading "www." unconditionally
        hostname = re.sub(r"^www\.", "", hostname)
        parts = hostname.split(".")
        # If more than 2 parts, we have a subdomain — keep only last 2
        if len(parts) > 2:
            return ".".join(parts[-2:])
        return hostname or None
    except Exception:
        return None


def _is_ats_domain(domain: str) -> bool:
    """Return True if the domain belongs to a known ATS or job-board platform."""
    return any(domain == ats or domain.endswith("." + ats) for ats in _ATS_DOMAINS)


def _example(pattern: str, domain: str) -> str:
    """Return a human-readable example email for display (uses 'jane.doe' as sample)."""
    return _generate_emails("jane", "doe", domain, pattern)


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    """
    Load email-discovery-related config from settings.
    Returns a dict with 'tavily_key', 'hunter_key', and 'apollo_key' (any may be None).
    """
    try:
        from getmehired.config import get_settings
        s = get_settings()
        return {
            "tavily_key": s.tavily_api_key or None,
            "hunter_key": s.hunter_api_key or None,
            "apollo_key": s.apollo_api_key or None,
        }
    except Exception:
        return {}
