"""Application settings, loaded from the environment or a .env file."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Every setting is read from a CERTMASTERY_-prefixed variable.
    #
    # This prefix is deliberate, not cosmetic. Unprefixed names like DATABASE_URL are
    # commonly set machine-wide by other projects, and pydantic-settings gives
    # environment variables priority over .env -- so an unprefixed setting would let an
    # unrelated global variable silently redirect this application at another team
    # database. Prefixing makes configuration explicit and local to this project.
    model_config = SettingsConfigDict(
        env_prefix="CERTMASTERY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Claude Cert Mastery API"
    version: str = "1.0.0"

    # SQLite locally; PostgreSQL (postgresql+psycopg://...) in production. psycopg 3
    # rather than psycopg2 -- see D-5 in the spec.
    database_url: str = "sqlite:///./certmastery.db"

    # Session 2. Unset is a supported configuration: the API falls back to each
    # question's static_explanation rather than failing.
    anthropic_api_key: str | None = None
    claude_model: str = "claude-opus-5"

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def ai_explanations_enabled(self) -> bool:
        return bool(self.anthropic_api_key)


settings = Settings()
