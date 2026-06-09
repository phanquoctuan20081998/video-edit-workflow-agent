"""Central config — reads from .env / environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "sqlite+aiosqlite:///./video_agent.db"

    # LLM
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""

    # Vision QA
    vision_provider: str = "anthropic"
    vision_model: str = "claude-sonnet-4-6"

    # TTS
    tts_provider: str = "edge"
    tts_voice: str = "vi-VN-HoaiMyNeural"
    azure_speech_key: str = ""
    azure_speech_region: str = ""

    # Sandbox
    sandbox_mode: str = "local"   # "docker" | "local"
    sandbox_timeout: int = 90
    sandbox_docker_image: str = "manim-sandbox"

    # Storage
    artifact_dir: str = "./artifacts"

    # Stock
    pexels_api_key: str = ""

    # Celery
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Reddit
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "video-agent/0.1"

    # Google Trends
    google_trends_geo: str = ""          # "" = worldwide, "US", "VN", etc.
    google_trends_keywords: str = "mathematics,physics,algorithm,machine learning"

    # YouTube Data API
    youtube_api_key: str = ""
    youtube_search_keywords: str = "math explained,physics explained,algorithm visualization"
    youtube_max_results: int = 10


def get_settings() -> Settings:
    return Settings()
