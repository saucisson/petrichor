"""API settings: the `X-API-Key` credential lives in a protected settings type.

Secrets stay in masked settings types and are never logged (ADR-0005); the
key is read from the ``PETRICHOR_API_KEY`` environment variable and has no
default, so a misconfigured server fails at startup instead of running open.
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_PREFIX = "PETRICHOR_"


class ApiSettings(BaseSettings):
    """Runtime settings of the HTTP API."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX)

    api_key: SecretStr
