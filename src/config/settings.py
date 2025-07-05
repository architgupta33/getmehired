"""
Configuration settings for the CrewAI application.
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # OpenAI Configuration
    openai_api_key: Optional[str] = None
    
    # Groq Configuration
    groq_api_key: Optional[str] = None
    
    # Hugging Face Configuration
    huggingface_api_key: Optional[str] = None
    
    # CrewAI Configuration
    crewai_verbose: bool = True
    crewai_max_iterations: int = 3
    
    # Application Configuration
    app_name: str = "CrewAI Agentic App"
    app_version: str = "1.0.0"
    debug: bool = False
    
    # Logging Configuration
    log_level: str = "INFO"
    log_file: str = "logs/app.log"
    
    # Data Configuration
    data_input_dir: str = "data/input"
    data_output_dir: str = "data/output"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Global settings instance
settings = Settings() 