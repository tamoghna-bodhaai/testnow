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
    # Gemini image understanding plus structured figure/option ownership.
    # Explicit deployment environment settings continue to take precedence.
    openrouter_model: str = "google/gemini-3.1-flash-lite"
    openrouter_fallback_model: str | None = None
    openrouter_timeout_seconds: int = 90
    # Applies per source file.  The web relay is configured to the same 200 MB
    # ceiling, so large chapter documents are rejected consistently.
    max_upload_bytes: int = 209_715_200
    # Long papers are processed in bounded batches instead of being rejected.
    # This is intentionally separate from the upload-size limit.
    # Small visual batches preserve question boundaries and leave enough model
    # output budget for every question on dense coaching-material pages.
    bank_import_chunk_pages: int = 4
    teacher_import_chunk_pages: int = 4
    # Large papers are chunked; these remain safety guards, not practical
    # extraction limits for a normal question bank.
    max_pdf_pages: int = 5_000
    max_rendered_pdf_bytes: int = 200_000_000
    openrouter_max_output_tokens: int = 16_384
    explainer_model: str = "google/gemini-3.1-flash-lite"
    explainer_tts_model: str = "mistralai/voxtral-mini-tts-3-0"
    explainer_tts_voice: str = "voice_3153"
    cors_origins: str = "http://localhost:3000"
    seed_demo_data: bool = False
    local_demo_mode: bool = False
    super_admin_email: str = "utils@bodhaai.tech"
    super_admin_access_code: str | None = None

@lru_cache
def settings() -> Settings: return Settings()
