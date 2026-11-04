"""Application settings, loaded from the environment.

Secrets arrive as ``SecretStr`` so no log call can leak them by accident
(ADR-0005, ADR-0009). Every field is optional-friendly: the application
must start in the barest possible environment (console fallback).
"""

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from enum import StrEnum


LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class LogFormat(StrEnum):
    """Rendering of log events: console for humans, JSON for machines."""

    CONSOLE = "console"
    JSON = "json"


class ApplicationSettings(BaseSettings):
    """Environment-driven configuration, validated at startup."""

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=True,
        frozen=True,
    )

    discord_webhook_url: SecretStr | None = None
    log_level: LogLevel = "INFO"
    log_format: LogFormat = LogFormat.CONSOLE
