"""
AI-powered outreach email drafter — Step 4 of the GetMeHired pipeline.

Given a job posting and the applicant's resume text, this module uses Groq
to generate a personalized cold-outreach email body addressed to a recruiter.

Design:
  - One LLM call per pipeline run (not per recruiter).
  - The body is drafted using an anchor first name (the first recruiter's name).
  - Callers substitute first names for other recruiters via simple string replace.
  - Subject lines are generated locally (no LLM) from job title + company + recruiter name.
"""
from __future__ import annotations

from groq import AsyncGroq

from getmehired.config import get_settings
from getmehired.models.job import JobPosting

_SYSTEM_PROMPT = """\
You are a professional job applicant writing a cold outreach email to a recruiter.
Write a concise, genuine, and personalized email body (3–4 short paragraphs, under 200 words).
Do NOT include a subject line.
Start directly with "Hi {first_name}," on the very first line.
End the email with "Would love to connect." followed by a blank line and then "Best,".
Do not include a name after "Best," — the sender will add their own signature.
Return only the email body text — no extra commentary, no markdown.
"""

_USER_TEMPLATE = """\
Recruiter first name: {first_name}
Company: {company}
Job title: {job_title}

--- JOB DESCRIPTION ---
{job_description}

--- MY RESUME ---
{resume_text}

Write the email body now.
"""


async def draft_email(
    job: JobPosting,
    resume_text: str,
    first_name: str,
) -> str:
    """
    Draft a personalized cold-outreach email body using Groq.

    The body is written for `first_name` and contains that name in the
    greeting ("Hi {first_name},"). Callers replace this name for other
    recruiters to avoid making one LLM call per recruiter.

    Args:
        job:         The JobPosting (used for job_title, company, description).
        resume_text: Raw text extracted from the applicant's resume.
        first_name:  First name of the anchor recruiter (title-cased).

    Returns:
        Email body string, stripped of leading/trailing whitespace.
    """
    settings = get_settings()
    client = AsyncGroq(api_key=settings.groq_api_key)

    prompt = _USER_TEMPLATE.format(
        first_name=first_name,
        company=job.company,
        job_title=job.job_title,
        job_description=job.description[:3_000],
        resume_text=resume_text[:4_000],
    )

    response = await client.chat.completions.create(
        model=settings.groq_model,
        max_tokens=512,
        temperature=0.7,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content.strip()


def make_subject(job: JobPosting) -> str:
    """
    Generate an outreach email subject line.

    Format: "Interested in {job_title}"

    Args:
        job: The JobPosting.

    Returns:
        Subject line string.
    """
    return f"Interested in {job.job_title}"
