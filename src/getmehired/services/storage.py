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


def _slug(text: str) -> str:
    """Convert text to a safe filename segment."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "_", text)
    return text[:40]
