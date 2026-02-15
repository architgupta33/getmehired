"""
Job page scraper with platform-aware dispatch.

Strategy:
  - Greenhouse / Lever  → direct JSON API (no browser needed, most reliable)
  - Workday             → Playwright with extended timeout + JS-ready wait
  - Everything else     → Playwright generic fallback

Entry point: scrape_job_page(url) → ScrapeResult
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

MAX_CHARS = 12_000

_NOISE_TAGS = [
    "script", "style", "noscript", "header", "nav", "footer",
    "aside", "form", "iframe", "svg", "img",
]

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "src", "ref", "domain", "start", "sort_by", "pid",
}


class Platform(str, Enum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    WORKDAY = "workday"
    GENERIC = "generic"


@dataclass
class ScrapeResult:
    url: str
    raw_text: str
    page_title: str
    platform: str
    success: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def scrape_job_page(url: str) -> ScrapeResult:
    """
    Scrape a job posting URL and return cleaned text + metadata.
    Automatically detects the platform and uses the best strategy.
    """
    clean_url = _normalize_url(url)
    platform = _detect_platform(clean_url)

    if platform == Platform.GREENHOUSE:
        return await _scrape_greenhouse(clean_url)
    if platform == Platform.LEVER:
        return await _scrape_lever(clean_url)
    if platform == Platform.WORKDAY:
        return await _scrape_playwright(clean_url, timeout_ms=60_000, workday_mode=True)
    return await _scrape_playwright(clean_url, timeout_ms=30_000)


# ---------------------------------------------------------------------------
# URL utilities
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    """Strip tracking params, ensure https, and clean platform-specific path noise."""
    parsed = urlparse(url)

    # Workday: /apply suffix opens the application form, not the job description
    path = re.sub(r"/apply/?$", "", parsed.path)

    # Strip tracking query params
    qs = {k: v for k, v in parse_qs(parsed.query).items() if k not in _TRACKING_PARAMS}
    clean_query = urlencode({k: v[0] for k, v in qs.items()})

    cleaned = parsed._replace(scheme="https", path=path, query=clean_query)
    return urlunparse(cleaned)


def _detect_platform(url: str) -> Platform:
    host = urlparse(url).hostname or ""
    if "greenhouse.io" in host:
        return Platform.GREENHOUSE
    if "lever.co" in host:
        return Platform.LEVER
    if "myworkdayjobs.com" in host or "workday.com" in host:
        return Platform.WORKDAY
    return Platform.GENERIC


# ---------------------------------------------------------------------------
# Greenhouse — public JSON API
# ---------------------------------------------------------------------------

def _parse_greenhouse_ids(url: str) -> tuple[str, str] | None:
    """
    Extract (company_slug, job_id) from Greenhouse URLs.

    Supported patterns:
      boards.greenhouse.io/{company}/jobs/{id}
      job-boards.greenhouse.io/{company}/jobs/{id}
    """
    match = re.search(r"greenhouse\.io/([^/]+)/jobs/(\d+)", url)
    if match:
        return match.group(1), match.group(2)
    return None


async def _scrape_greenhouse(url: str) -> ScrapeResult:
    ids = _parse_greenhouse_ids(url)
    if not ids:
        # Fall back to Playwright if URL pattern is unexpected
        return await _scrape_playwright(url, timeout_ms=30_000)

    company, job_id = ids
    api_url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs/{job_id}"

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(api_url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ScrapeResult(
                url=url, raw_text="", page_title="", platform=Platform.GREENHOUSE,
                success=False, error=f"Greenhouse API error {e.response.status_code}",
            )
        except Exception as e:
            return ScrapeResult(
                url=url, raw_text="", page_title="", platform=Platform.GREENHOUSE,
                success=False, error=str(e),
            )

    data = resp.json()
    title = data.get("title", "")
    location = ", ".join(loc.get("name", "") for loc in data.get("offices", []))
    content_html = data.get("content", "")

    # Greenhouse API returns HTML-entity-encoded content (e.g. &lt;p&gt;)
    # Unescape once before parsing so BeautifulSoup sees real HTML tags
    import html as _html
    content_text = _clean_html(_html.unescape(content_html))

    text = f"Job Title: {title}\nCompany: {company}\nLocation: {location}\n\n{content_text}"

    return ScrapeResult(
        url=url,
        raw_text=text[:MAX_CHARS],
        page_title=f"{title} at {company}",
        platform=Platform.GREENHOUSE,
        success=True,
    )


# ---------------------------------------------------------------------------
# Lever — fetch HTML directly (v0 API is deprecated)
# Lever pages are server-rendered; structured data lives in JSON-LD + HTML body
# ---------------------------------------------------------------------------

async def _scrape_lever(url: str) -> ScrapeResult:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            return ScrapeResult(
                url=url, raw_text="", page_title="", platform=Platform.LEVER,
                success=False, error=f"HTTP {e.response.status_code}",
            )
        except Exception as e:
            return ScrapeResult(
                url=url, raw_text="", page_title="", platform=Platform.LEVER,
                success=False, error=str(e),
            )

    soup = BeautifulSoup(resp.text, "lxml")

    # --- Structured metadata from JSON-LD ---
    title, company, location = "", "", ""
    ld_tag = soup.find("script", type="application/ld+json")
    if ld_tag:
        import json as _json
        try:
            ld = _json.loads(ld_tag.string or "")
            title = ld.get("title", "")
            company = (ld.get("hiringOrganization") or {}).get("name", "")
            location = ((ld.get("jobLocation") or {}).get("address") or {}).get("addressLocality", "")
        except Exception:
            pass

    page_title = soup.title.string.strip() if soup.title else f"{title} at {company}"

    # --- Job description from HTML body ---
    # Remove noise before extracting text
    for tag in soup(["script", "style", "noscript", "header", "nav", "footer", "aside", "form"]):
        tag.decompose()

    # Lever splits the description into multiple div.section.page-centered blocks
    sections = soup.find_all("div", class_=lambda c: c and "section" in c and "page-centered" in c)
    if sections:
        # Skip the last block — it's always the AI/privacy disclaimer, not job content
        content_sections = sections[:-1] if len(sections) > 1 else sections
        desc_text = "\n\n".join(s.get_text(separator="\n").strip() for s in content_sections)
    else:
        desc_text = _clean_html(resp.text)

    if not title:
        # Fall back to page <title>
        title = page_title

    text = f"Job Title: {title}\nCompany: {company}\nLocation: {location}\n\n{desc_text}"

    if not desc_text.strip():
        return ScrapeResult(
            url=url, raw_text="", page_title=page_title, platform=Platform.LEVER,
            success=False, error="No job description found in page.",
        )

    return ScrapeResult(
        url=url,
        raw_text=text[:MAX_CHARS],
        page_title=page_title,
        platform=Platform.LEVER,
        success=True,
    )


# ---------------------------------------------------------------------------
# Playwright — generic + Workday
# ---------------------------------------------------------------------------

async def _scrape_playwright(
    url: str,
    timeout_ms: int = 30_000,
    workday_mode: bool = False,
) -> ScrapeResult:
    platform = _detect_platform(url)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        page = await context.new_page()

        # Block images/fonts/media to speed up loading
        await page.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font"}
            else route.continue_(),
        )

        html, title = "", ""
        try:
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            if workday_mode:
                # Workday SPAs keep loading after networkidle; wait for a
                # recognisable content element before giving up
                try:
                    await page.wait_for_selector(
                        "[data-automation-id='jobPostingDescription'], "
                        ".job-description, article, main",
                        timeout=15_000,
                    )
                except PlaywrightTimeoutError:
                    pass  # grab whatever rendered
            else:
                await page.wait_for_timeout(2_000)

            html = await page.content()
            title = await page.title()

        except PlaywrightTimeoutError:
            try:
                html = await page.content()
                title = await page.title()
            except Exception as e:
                await browser.close()
                return ScrapeResult(
                    url=url, raw_text="", page_title="", platform=platform,
                    success=False, error=f"Timeout and could not recover: {e}",
                )
        except Exception as e:
            await browser.close()
            return ScrapeResult(
                url=url, raw_text="", page_title="", platform=platform,
                success=False, error=str(e),
            )
        finally:
            await browser.close()

    cleaned = _clean_html(html)
    if not cleaned.strip():
        return ScrapeResult(
            url=url, raw_text="", page_title=title, platform=platform,
            success=False,
            error="Page loaded but no text extracted — site may require login or have bot protection.",
        )

    return ScrapeResult(
        url=url,
        raw_text=cleaned[:MAX_CHARS],
        page_title=title,
        platform=platform,
        success=True,
    )


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def _clean_html(html: str) -> str:
    """Strip noise tags and collapse whitespace."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_NOISE_TAGS):
        tag.decompose()
    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\n{3,}", "\n\n", text)
