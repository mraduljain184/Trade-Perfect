"""Application settings, read from environment variables / the .env file (never hard-coded)."""
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Looks for .env in backend/ and in the repo root (works with and without Docker).
    model_config = SettingsConfigDict(
        env_file=(str(BACKEND_DIR.parent / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- required ---
    database_url: str
    jwt_secret: str

    # --- auth / web ---
    jwt_expire_minutes: int = 480
    cors_origins: str = "http://localhost:5173"

    # --- seed ---
    seed_default_password: str = ""
    seed_email_domain: str = "tradeperfect.local"

    # --- paths ---
    model_dir: str = "ml_artifacts"
    data_dir: str = "data"

    # --- email (optional) ---
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "no-reply@tradeperfect.local"

    # --- chatbot LLM (optional) ---
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    app_timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3_driver(cls, value: str) -> str:
        # Render gives "postgres://..."; SQLAlchemy needs the driver name spelled out.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def _secret_long_enough(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters long")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
