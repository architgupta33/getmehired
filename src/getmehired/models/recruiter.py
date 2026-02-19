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

    # Step 5 send state (all optional — populated progressively)
    email_sent_at: Optional[datetime] = None   # UTC timestamp of most recent send
    email_sent_to: Optional[str] = None        # exact address last sent to
    email_tried: list[str] = []               # all addresses tried, in order
    email_bounced: Optional[bool] = None      # True=bounced, False=no bounce, None=pending

    def model_post_init(self, __context):
        if self.found_at is None:
            self.found_at = datetime.now(timezone.utc)
