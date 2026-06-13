"""Application configuration.

Exposes a Pydantic-settings ``Settings`` loader and a cached ``get_settings()``
accessor. All values can be overridden via environment variables (optionally
from a local ``.env`` file). Sensible, demo-safe defaults are provided so the
app boots and tests run without any external configuration.

Environment variables use the ``SECONDLIFE_`` prefix, e.g. ``SECONDLIFE_STUB_MODE=true``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the SecondLife AI backend.

    Attributes:
        stub_mode: When True, AI/LLM calls are served from deterministic
            fixtures instead of contacting OpenAI. Defaults to True so CI and
            local demos are reproducible and need no API key.
        database_url: SQLAlchemy database URL. Defaults to a local SQLite file.
        openai_model: OpenAI model name used for vision/condition assessment
            and the hybrid decision call.
        openai_model_version: Pinned model version recorded for reproducibility.
        openai_api_key: OpenAI API key (only required when stub_mode is False).
        openai_temperature: Decoding temperature; 0 for deterministic output.
        encryption_key: Symmetric key used for application-layer encryption of
            bank details (demo only; use a KMS-managed key in production). A
            safe local default is provided so the demo boots without secrets.
    """

    model_config = SettingsConfigDict(
        env_prefix="SECONDLIFE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Core behavior
    stub_mode: bool = True

    # Persistence
    database_url: str = "sqlite:///./secondlife.db"

    # OpenAI / AI configuration
    openai_model: str = "gpt-4o"
    openai_model_version: str = "gpt-4o-2024-08-06"
    openai_api_key: str = ""
    openai_temperature: float = 0.0

    # Encryption-at-rest (demo). NOTE: replace with a KMS-managed key in prod.
    # This is a Fernet-compatible urlsafe base64 key encoding exactly 32 bytes,
    # safe for local/demo use.
    encryption_key: str = "c2Vjb25kbGlmZS1haS1kZW1vLWtleS0zMi1ieXRlISE="


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached ``Settings`` instance.

    Cached so settings are read once per process. Tests that need to override
    environment variables can call ``get_settings.cache_clear()``.
    """

    return Settings()
