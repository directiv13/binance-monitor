"""Application configuration (``.env`` / environment variables)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from cryptography.fernet import Fernet
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    telegram_bot_token: str = Field(min_length=10)
    telegram_chat_id: int
    telegram_topic_id: int = Field(gt=0)
    admin_ids: Annotated[list[int], NoDecode] = Field(min_length=1)
    pushover_app_token: str = Field(min_length=1)
    encryption_key: str = Field(min_length=1)

    db_path: Path = Path("/data/monitor.db")
    binance_fapi_url: str = "https://fapi.binance.com"
    binance_fstream_url: str = "wss://fstream.binance.com"
    pushover_api_url: str = "https://api.pushover.net"
    telegram_api_url: str = "https://api.telegram.org"

    # Pushover priority: 2 = emergency (repeats until acknowledged), -2..1 = quieter levels.
    pushover_priority: int = Field(default=2, ge=-2, le=2)
    # Emergency only: re-alert every N seconds (Pushover minimum 30) for at most M seconds (max 3h).
    pushover_retry_seconds: int = Field(default=60, ge=30, le=10800)
    pushover_expire_seconds: int = Field(default=3600, ge=30, le=10800)

    pinned_refresh_seconds: int = Field(default=60, ge=5)
    reconcile_seconds: int = Field(default=300, ge=10)
    log_level: str = "INFO"
    heartbeat_path: Path = Path("/data/heartbeat")

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: Any) -> Any:
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            try:
                return [int(p) for p in parts]
            except ValueError as exc:
                raise ValueError("ADMIN_IDS must be comma-separated integers") from exc
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("encryption_key")
    @classmethod
    def _check_fernet_key(cls, value: str) -> str:
        try:
            Fernet(value.encode())
        except Exception as exc:
            raise ValueError(
                "ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return level

    @field_validator(
        "binance_fapi_url", "binance_fstream_url", "pushover_api_url", "telegram_api_url"
    )
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")
