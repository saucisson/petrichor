"""Auth configuration from normalized environment variables (cadrage §3).

``AUTH_ISSUER``, ``AUTH_CLIENT_ID``, ``AUTH_CLIENT_SECRET``,
``AUTH_ALLOWED_AUDIENCES``, ``AUTH_DISABLED`` — identical names across the
saucisson apps. No real secret is ever committed: ``.env`` is git-ignored and
``.env.example`` only documents the variables. ``AUTH_DISABLED=1`` is the L3
local-dev level only; it must never be combined with a production indicator.
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """The OIDC validation settings of the app (env names are the contract)."""

    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    auth_issuer: str = Field(
        default="https://auth.linard.fr",
        validation_alias="AUTH_ISSUER",
    )
    """The IdP issuer (``iss``); discovery is read from its well-known URL."""
    auth_client_id: str = Field(default="", validation_alias="AUTH_CLIENT_ID")
    """The OIDC client id of this app; default accepted audience."""
    auth_client_secret: SecretStr | None = Field(
        default=None, validation_alias="AUTH_CLIENT_SECRET"
    )
    """The client secret (confidential clients only); never logged (ADR-0005)."""
    auth_allowed_audiences: str = Field(default="", validation_alias="AUTH_ALLOWED_AUDIENCES")
    """Comma-separated accepted audiences; defaults to the client id."""
    auth_disabled: bool = Field(default=False, validation_alias="AUTH_DISABLED")
    """L3 local-dev level: ``AUTH_DISABLED=1`` bypasses validation (never in prod)."""

    def allowed_audiences(self) -> tuple[str, ...]:
        """The accepted ``aud`` values: the configured list, else the client id."""
        raw = self.auth_allowed_audiences.strip()
        if raw:
            return tuple(part.strip() for part in raw.split(",") if part.strip())
        return (self.auth_client_id,) if self.auth_client_id else ()
