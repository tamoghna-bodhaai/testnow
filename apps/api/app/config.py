from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite+pysqlite:///:memory:"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: str = "development-only-change-me"
    cookie_secure: bool = True
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "testnow"
    s3_region: str = "auto"
    openrouter_api_key: str | None = None
    openrouter_model: str = "google/gemini-2.5-flash"
    openrouter_fallback_model: str | None = None
    openrouter_timeout_seconds: int = 90
    max_upload_bytes: int = 52_428_800
    cors_origins: str = "http://localhost:3000"
    seed_demo_data: bool = False

@lru_cache
def settings() -> Settings: return Settings()
