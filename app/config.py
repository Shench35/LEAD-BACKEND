from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    lead_mode: str = "fixture"
    serpapi_key: str | None = None
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    lead_access_key: str = Field(min_length=1)
    allowed_origins: str = "http://localhost:5173"
    monthly_search_limit: int = 240
    email_enabled: bool = False
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from_name: str = "Lead"
    per_ip_jobs_per_hour: int = 5
    per_email_per_day: int = 3
    trust_forwarded_for: bool = False
    email_hash_salt: str | None = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
