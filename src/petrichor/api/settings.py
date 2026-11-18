"""API settings: the `X-API-Key` credential lives in a protected settings type.

Secrets stay in masked settings types and are never logged (ADR-0005); the
key is read from the ``PETRICHOR_API_KEY`` environment variable and has no
default, so a misconfigured server fails at startup instead of running open.
"""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from petrichor.session_store import DEFAULT_SESSION_BUDGET_SECONDS

ENV_PREFIX = "PETRICHOR_"
DEFAULT_CHECK_TIMEOUT_SECONDS = 30

type LlmProviderKind = Literal["mock", "mistral"]
"""The selectable LLM providers (spec §8, issue #36): mock by default, mistral real."""


class ApiSettings(BaseSettings):
    """Runtime settings of the HTTP API."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX)

    api_key: SecretStr
    check_timeout_seconds: int = Field(default=DEFAULT_CHECK_TIMEOUT_SECONDS, gt=0)
    """Timeout of one check run; a timeout is an explicit error state, never a verdict."""
    session_budget_seconds: float = Field(default=DEFAULT_SESSION_BUDGET_SECONDS, gt=0)
    """Session budget cap in tool seconds (spec §12.4): cumulative measured cost, no carry-over."""
    static_first: bool = Field(default=False)
    """Opt-in static-first proof on degraded questions (spec §18.4, issue #25); off by default."""
    llm_provider: LlmProviderKind = "mock"
    """The LLM provider selected by configuration (spec §8, issue #36): the
    deterministic mock by default, the real Mistral provider with ``mistral``."""
    llm_model: str = Field(default="mistral-small-latest", min_length=1)
    """The model of the real LLM provider when ``llm_provider="mistral"``."""
    mistral_api_key: SecretStr | None = None
    """The Mistral API key (env MISTRAL_API_KEY); required when llm_provider="mistral",
    never logged (ADR-0005)."""
