from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, HttpUrl


class JobFamily(str, Enum):
    SOFTWARE_ENGINEERING   = "Software Engineering"
    DATA_SCIENCE_ML        = "Data Science / ML"
    DATA_ANALYTICS         = "Data Analytics"
    BUSINESS_ANALYTICS     = "Business Analytics"
    BUSINESS_DEVELOPMENT   = "Business Development / Sales"
    PRODUCT_MANAGEMENT     = "Product Management"
    DESIGN_UX              = "Design / UX"
    DEVOPS_INFRA           = "DevOps / Infrastructure"
    CYBERSECURITY          = "Cybersecurity"
    MARKETING              = "Marketing"
    FINANCE                = "Finance / Accounting"
    LEGAL                  = "Legal / Compliance"
    RESEARCH               = "Research"
    OPERATIONS             = "Operations"
    POLICY                 = "Policy / Government Affairs"
    OTHER                  = "Other"


class JobPosting(BaseModel):
    # Source
    url: str
    platform: str

    # Extracted by Claude
    job_title: str
    company: str
    job_family: JobFamily
    location: Optional[str] = None

    # Full scraped text — used downstream for email generation
    description: str

    # Metadata
    scraped_at: datetime = None

    def model_post_init(self, __context):
        if self.scraped_at is None:
            self.scraped_at = datetime.now(timezone.utc)
