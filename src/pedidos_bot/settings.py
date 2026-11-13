"""Process settings, read from the environment and `.env`. Secrets live only here."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: SecretStr
    database_url: str
    pii_hash_salt: str | None = None
    log_level: str = "INFO"
