"""API settings: the `X-API-Key` credential lives in a protected settings type.

Secrets stay in masked settings types and are never logged (ADR-0005); the
key is read from the ``PETRICHOR_API_KEY`` environment variable and has no
default, so a misconfigured server fails at startup instead of running open.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "PETRICHOR_"
DEFAULT_CHECK_TIMEOUT_SECONDS = 30
DEFAULT_SESSION_BUDGET_SECONDS = 3600.0
"""Default session budget cap in tool seconds (spec §20.2: 3600 s of cumulative tool time)."""


class ApiSettings(BaseSettings):
    """Runtime settings of the HTTP API."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX)

    api_key: SecretStr
    check_timeout_seconds: int = Field(default=DEFAULT_CHECK_TIMEOUT_SECONDS, gt=0)
    """Timeout of one check run; a timeout is an explicit error state, never a verdict."""
    session_budget_seconds: float = Field(default=DEFAULT_SESSION_BUDGET_SECONDS, gt=0)
    """Session budget cap in tool seconds (spec §12.4): cumulative measured cost, no carry-over."""
