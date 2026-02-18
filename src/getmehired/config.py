from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    groq_api_key: str
    groq_model: str = "llama-3.3-70b-versatile"
    data_dir: str = "data/jobs"
    brave_api_key: Optional[str] = None
    tavily_api_key: Optional[str] = None
    google_cse_api_key: Optional[str] = None
    google_cse_cx: Optional[str] = None
    hunter_api_key: Optional[str] = None
    apollo_api_key: Optional[str] = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()



