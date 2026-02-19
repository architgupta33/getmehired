"""
Simple JSON file storage for JobPosting objects.

Each job is saved as:  data/jobs/<sanitized_company>_<sanitized_title>_<timestamp>.json

This is intentionally simple — no database, no ORM.
Files are human-readable and can be inspected/edited directly.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from getmehired.config import get_settings
from getmehired.models.job import JobPosting


def save(job: JobPosting) -> Path:
    """
    Persist a JobPosting to a JSON file and return the file path.
    """
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    filename = _make_filename(job)
    path = data_dir / filename

    with open(path, "w", encoding="utf-8") as f:
        json.dump(job.model_dump(mode="json"), f, indent=2, ensure_ascii=False)

    return path


def load(path: Path) -> JobPosting:
    """Load a JobPosting from a JSON file."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return JobPosting(**data)


def list_jobs() -> list[Path]:
    """Return all saved job JSON files, newest first."""
    settings = get_settings()
    data_dir = Path(settings.data_dir)
    if not data_dir.exists():
        return []
    return sorted(data_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _make_filename(job: JobPosting) -> str:
    timestamp = job.scraped_at.strftime("%Y%m%d_%H%M%S")
    company = _slug(job.company)
    title = _slug(job.job_title)
    return f"{company}__{title}__{timestamp}.json"


def append_recruiters(path: Path, recruiters: list) -> None:
    """
    Write a 'recruiters' list into an existing job JSON file.

    Uses a raw JSON merge so the full file dict is preserved — including
    any keys not present on JobPosting. Existing recruiter data is replaced
    (idempotent).
    """
    from getmehired.models.recruiter import Recruiter

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    data["recruiters"] = [
        r.model_dump(mode="json") if isinstance(r, Recruiter) else r
        for r in recruiters
    ]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_recruiters(path: Path) -> list:
    """Load the recruiter list from a job JSON file as Recruiter objects."""
    from getmehired.models.recruiter import Recruiter

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    return [Recruiter(**r) for r in data.get("recruiters", [])]


def save_send_state(path: Path, recruiters: list) -> None:
    """
    Persist updated send-state fields for each recruiter back to the job JSON.

    Only the recruiter list is overwritten; email_subject, email_body, and
    all other job-level fields are preserved.
    """
    from getmehired.models.recruiter import Recruiter

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    data["recruiters"] = [
        r.model_dump(mode="json") if isinstance(r, Recruiter) else r
        for r in recruiters
    ]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_email_draft(path: Path, subject: str, body: str) -> None:
    """
    Write email_subject and email_body into an existing job JSON file.

    Patches only those two keys — all other fields are preserved.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    data["email_subject"] = subject
    data["email_body"] = body

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _slug(text: str) -> str:
    """Convert text to a safe filename segment."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:40]
