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
You write short, human outreach emails from a job applicant to a recruiter.
The applicant has already submitted an application and wants the recruiter to notice it.

Rules:
- Start with "Hi {first_name}," on the very first line.
- Sentence 1: state you've applied for the role and include the job URL as a plain URL in parentheses — no markdown, no angle brackets.
- Then write exactly 3 short bullet points (using "- ") explaining why you're a strong fit. \
Each bullet is one sentence. Pull specifics from the resume and job description — no vague claims.
- Final sentence: say you hope to hear back on next steps. Keep it direct, not grovelling.
- End with a blank line then "Best,". Do not add a name after "Best,".
- Whole email: under 150 words.
- Voice: plain, direct, human. No buzzwords. Never use "excited", "thrilled", "passionate", \
"leverage", "synergy", "dynamic", "seasoned", or "proven track record".

Here is an example of the format and tone you should match:

---
Hi Sarah,

I applied for the Data Scientist role at Stripe (https://stripe.com/jobs/listing/data-scientist/123) and wanted to make sure it's on your radar.

A few reasons I think it's a strong match:
- I've built end-to-end ML pipelines in Python at scale, including a fraud-detection model that cut false positives by 30%.
- The JD mentions experimentation — I've designed and analysed A/B tests across growth and payments features.
- I have experience working directly with PMs and finance stakeholders to translate data into decisions, which maps to the cross-functional scope here.

Happy to share more. Hope to hear back on next steps.

Best,
---

Return only the email body. No commentary, no markdown outside the bullets.
"""

_USER_TEMPLATE = """\
Recruiter first name: {first_name}
Company: {company}
Job title: {job_title}
Job URL: {job_url}

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
        job_url=job.url,
        job_description=job.description[:3_000],
        resume_text=resume_text[:4_000],
    )

    response = await client.chat.completions.create(
        model=settings.groq_model,
        max_tokens=400,
        temperature=0.85,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    return response.choices[0].message.content.strip()


def make_subject(job: JobPosting) -> str:
    """
    Generate an outreach email subject line.

    Format: "Exploring the {job_title} role at {company}"

    Args:
        job: The JobPosting.

    Returns:
        Subject line string.
    """
    return f"Exploring the {job.job_title} role at {job.company}"
