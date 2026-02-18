"""
Recruiter data model.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel


class Recruiter(BaseModel):
    name: str
    title: Optional[str] = None
    linkedin_url: Optional[str] = None
    email: Optional[str] = None
    source: str = "google"          # how we found them
    found_at: Optional[datetime] = None

    def model_post_init(self, __context):
        if self.found_at is None:
            self.found_at = datetime.now(timezone.utc)
