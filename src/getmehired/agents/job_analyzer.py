"""
Claude-powered job posting analyzer.

Takes raw scraped text from a job page and extracts:
  - job_title   : exact title as listed
  - company     : company name
  - job_family  : broad category (e.g. Software Engineering, Data Science / ML)
  - location    : primary work location (if present)
"""
from __future__ import annotations

import json
import re

from groq import AsyncGroq

from getmehired.config import get_settings
from getmehired.models.job import JobFamily, JobPosting
from getmehired.services.job_scraper import ScrapeResult

_JOB_FAMILIES = [f.value for f in JobFamily]

_SYSTEM_PROMPT = """\
You are a precise job posting parser. You extract structured information from job posting text.
Always respond with valid JSON only — no prose, no markdown, no code fences.
"""

_USER_TEMPLATE = """\
Extract the following fields from this job posting text and return them as a JSON object:

{{
  "job_title": "<exact job title as listed in the posting>",
  "company": "<company name>",
  "job_family": "<one of the categories below>",
  "location": "<primary location, or null if fully remote / not specified>"
}}

Job family must be exactly one of:
{families}

Job posting text:
---
{text}
---

Return only the JSON object.
"""


async def analyze(result: ScrapeResult) -> JobPosting:
    """
    Use Claude to extract job_title, company, job_family, and location
    from a ScrapeResult, then return a fully-populated JobPosting.

    Args:
        result: A successful ScrapeResult from the scraper.

    Returns:
        JobPosting with all fields populated.

    Raises:
        ValueError: If Claude returns unparseable output or an invalid job_family.
    """
    settings = get_settings()
    client = AsyncGroq(api_key=settings.groq_api_key)

    prompt = _USER_TEMPLATE.format(
        families="\n".join(f"  - {f}" for f in _JOB_FAMILIES),
        text=result.raw_text[:8_000],  # stay well within context
    )

    # Groq uses the OpenAI chat completions format
    response = await client.chat.completions.create(
        model=settings.groq_model,
        max_tokens=512,
        temperature=0,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    raw = response.choices[0].message.content.strip()

    # Strip accidental markdown code fences if Claude adds them
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Claude returned non-JSON: {raw!r}") from e

    # Validate and normalise job_family
    family_str = data.get("job_family", "Other")
    try:
        job_family = JobFamily(family_str)
    except ValueError:
        # Fuzzy fallback: find closest matching value
        job_family = _fuzzy_match_family(family_str)

    return JobPosting(
        url=result.url,
        platform=result.platform,
        job_title=data.get("job_title", "").strip() or result.page_title,
        company=data.get("company", "").strip(),
        job_family=job_family,
        location=data.get("location") or None,
        description=result.raw_text,
    )


def _fuzzy_match_family(value: str) -> JobFamily:
    """Return the JobFamily whose value most closely matches `value`."""
    value_lower = value.lower()
    for family in JobFamily:
        if any(word in value_lower for word in family.value.lower().split("/")):
            return family
    return JobFamily.OTHER
