"""Local validation of OIDC access tokens (PT-1, issue #182).

The access tokens are RS256 JWTs signed by the IdP. Validation is fully
local: the discovery document (``{issuer}/.well-known/openid-configuration``)
gives the JWKS URI, the JWKS gives the keys (cached with a TTL, refreshed on
an unknown ``kid``), and the claims are checked with a 60 s clock leeway —
``iss``, ``aud`` (a token issued for another app is never accepted), ``exp``
and ``nbf``. Every rejection is a typed :class:`TokenRejectedError` with the
exact reason, logged as a structured event (ADR-0005); nothing is swallowed.

JOSE is provided by ``joserfc`` (maintained library, per the cadrage — never
a hand-rolled crypto nor an unmaintained lib like python-jose).
"""

import base64
import binascii
import json
import time
from typing import TYPE_CHECKING, Literal

import httpx
import structlog
from joserfc import jwk, jwt
from joserfc.errors import (
    BadSignatureError,
    ExpiredTokenError,
    InvalidClaimError,
    InvalidKeyIdError,
    JoseError,
    MissingClaimError,
)
from joserfc.jwt import JWTClaimsRegistry
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from petrichor.auth.settings import AuthSettings

logger = structlog.get_logger(__name__)

CLOCK_LEEWAY_SECONDS = 60
"""Clock-skew tolerance for ``exp``/``nbf`` (cadrage §2 decision 4)."""

DISCOVERY_CACHE_TTL_SECONDS = 24 * 3600
"""TTL of the cached discovery document."""

JWKS_CACHE_TTL_SECONDS = 3600
"""TTL of the cached JWKS; an unknown ``kid`` forces one refresh."""

DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
"""Explicit timeout of every auth HTTP call (discovery, JWKS)."""

ACCEPTED_ALGORITHMS = ("RS256",)
"""The only accepted signing algorithm (cadrage §2 decision 4)."""


RejectionReason = Literal[
    "auth_disabled",
    "malformed_token",
    "bad_signature",
    "unknown_kid",
    "invalid_token",
    "expired",
    "invalid_issuer",
    "invalid_audience",
    "invalid_not_before",
    "invalid_claims",
    "missing_claims",
    "discovery_unavailable",
    "discovery_without_jwks_uri",
    "jwks_unavailable",
]
"""The closed set of rejection reasons (ADR-0006): one exact reason per failure type."""

_CLAIM_REASONS: dict[str, RejectionReason] = {
    "iss": "invalid_issuer",
    "aud": "invalid_audience",
    "exp": "expired",
    "nbf": "invalid_not_before",
}


def _claim_rejection_reason(error: JoseError) -> RejectionReason:
    """Map a claims-registry failure to its exact reason."""
    if isinstance(error, ExpiredTokenError):
        return "expired"
    if isinstance(error, MissingClaimError):
        return "missing_claims"
    if isinstance(error, InvalidClaimError):
        claim = getattr(error, "claim", "")
        reason = _CLAIM_REASONS.get(claim)
        return reason if reason is not None else "invalid_claims"
    return "invalid_claims"


class TokenRejectedError(Exception):
    """A typed rejection of an access token, with the exact reason."""

    def __init__(self, *, reason: RejectionReason) -> None:
        """A typed rejection carrying the exact reason (signature, expiration, audience)."""
        super().__init__(f"Access token rejected: {reason}.")
        self.reason: RejectionReason = reason


class TokenClaims(BaseModel):
    """The validated claims of an access token, exposed to the handlers."""

    model_config = ConfigDict(frozen=True, extra="allow")

    sub: str
    iss: str
    aud: list[str]
    exp: int
    nbf: int | None = None


def _header_kid(token: str) -> str | None:
    """Read the ``kid`` of a compact JWT without verifying it (header only)."""
    try:
        header_b64 = token.split(".", maxsplit=1)[0]
        padding = "=" * (-len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64 + padding))
    except (IndexError, ValueError, binascii.Error) as error:
        raise TokenRejectedError(reason="malformed_token") from error
    kid = header.get("kid") if isinstance(header, dict) else None
    return str(kid) if kid is not None else None


class TokenVerifier:
    """Validates access tokens locally against the IdP's JWKS (PT-1)."""

    def __init__(
        self,
        *,
        settings: AuthSettings,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        """Bind the verifier to the auth settings and an HTTP client."""
        self._settings = settings
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._owns_client = http_client is None
        self._discovery_cache: tuple[float, dict[str, str]] | None = None
        self._jwks_cache: tuple[float, jwk.KeySet] | None = None

    @property
    def enabled(self) -> bool:
        """Whether validation is active (``AUTH_DISABLED=1`` is the L3 dev bypass)."""
        return not self._settings.auth_disabled

    def _discovery(self) -> dict[str, str]:
        """The cached discovery document; refetched after the TTL."""
        now = time.time()
        if self._discovery_cache is not None:
            fetched_at, document = self._discovery_cache
            if now - fetched_at < DISCOVERY_CACHE_TTL_SECONDS:
                return document
        issuer = self._settings.auth_issuer.rstrip("/")
        url = issuer + "/.well-known/openid-configuration"
        try:
            response = self._client.get(url)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                kind = type(payload).__name__
                raise ValueError(f"discovery document is not a JSON object: {kind}")
            document = {key: str(value) for key, value in payload.items()}
        except (httpx.HTTPError, ValueError) as error:
            logger.warning(
                "auth_discovery_failed", issuer=self._settings.auth_issuer, reason=repr(error)
            )
            raise TokenRejectedError(reason="discovery_unavailable") from error
        if "jwks_uri" not in document:
            logger.warning(
                "auth_discovery_failed",
                issuer=self._settings.auth_issuer,
                reason="missing_jwks_uri",
            )
            raise TokenRejectedError(reason="discovery_without_jwks_uri")
        self._discovery_cache = (now, document)
        return document

    def _jwks(self, *, force_refresh: bool = False) -> jwk.KeySet:
        """The cached JWKS; refetched after the TTL or on an unknown ``kid``."""
        now = time.time()
        if not force_refresh and self._jwks_cache is not None:
            fetched_at, keyset = self._jwks_cache
            if now - fetched_at < JWKS_CACHE_TTL_SECONDS:
                return keyset
        jwks_uri = self._discovery()["jwks_uri"]
        try:
            response = self._client.get(jwks_uri)
            response.raise_for_status()
            keyset = jwk.KeySet.import_key_set(response.json())
        except (httpx.HTTPError, ValueError, JoseError) as error:
            logger.warning("auth_jwks_failed", jwks_uri=jwks_uri, reason=repr(error))
            raise TokenRejectedError(reason="jwks_unavailable") from error
        self._jwks_cache = (now, keyset)
        return keyset

    def verify_access_token(self, *, token: str) -> TokenClaims:
        """Validate one access token and return its claims.

        Raises :class:`TokenRejectedError` with the exact reason (signature,
        expiration, audience, issuer, unknown ``kid``) otherwise.
        """
        if not self.enabled:
            raise TokenRejectedError(reason="auth_disabled")
        header_kid = _header_kid(token)
        if header_kid is None and "." not in token:
            logger.warning("auth_token_malformed", reason="not_a_compact_jwt")
            raise TokenRejectedError(reason="malformed_token")
        try:
            keyset = self._jwks()
            decoded = jwt.decode(token, keyset, algorithms=list(ACCEPTED_ALGORITHMS))
        except InvalidKeyIdError:
            # Unknown kid: refresh the JWKS once, then re-try.
            logger.debug("auth_jwks_refresh_on_unknown_kid", kid=header_kid)
            try:
                keyset = self._jwks(force_refresh=True)
                decoded = jwt.decode(token, keyset, algorithms=list(ACCEPTED_ALGORITHMS))
            except JoseError as retry_error:
                logger.warning("auth_token_rejected", reason=repr(retry_error))
                raise TokenRejectedError(reason="unknown_kid") from retry_error
        except BadSignatureError as error:
            logger.warning("auth_token_rejected", reason=repr(error))
            raise TokenRejectedError(reason="bad_signature") from error
        except (JoseError, ValueError) as error:
            logger.warning("auth_token_rejected", reason=repr(error))
            raise TokenRejectedError(reason="invalid_token") from error
        registry = JWTClaimsRegistry(
            leeway=CLOCK_LEEWAY_SECONDS,
            iss={"essential": True, "value": self._settings.auth_issuer},
            aud={"essential": True, "values": list(self._settings.allowed_audiences())},
            exp={"essential": True},
            nbf={"essential": False},
        )
        try:
            registry.validate(decoded.claims)
        except JoseError as error:
            reason = _claim_rejection_reason(error)
            logger.warning("auth_token_rejected", reason=repr(error), rejection=reason)
            raise TokenRejectedError(reason=reason) from error
        claims = decoded.claims
        aud = claims.get("aud")
        aud_list = [aud] if isinstance(aud, str) else list(aud or [])
        try:
            return TokenClaims(
                sub=str(claims["sub"]),
                iss=str(claims["iss"]),
                aud=aud_list,
                exp=int(claims["exp"]),
                nbf=int(claims["nbf"]) if claims.get("nbf") is not None else None,
            )
        except (KeyError, TypeError, ValueError) as error:
            logger.warning("auth_token_rejected", reason=repr(error))
            raise TokenRejectedError(reason="missing_claims") from error

    def close(self) -> None:
        """Close the owned HTTP client, if any."""
        if self._owns_client:
            self._client.close()


__all__ = [
    "ACCEPTED_ALGORITHMS",
    "CLOCK_LEEWAY_SECONDS",
    "DEFAULT_HTTP_TIMEOUT_SECONDS",
    "DISCOVERY_CACHE_TTL_SECONDS",
    "JWKS_CACHE_TTL_SECONDS",
    "RejectionReason",
    "TokenClaims",
    "TokenRejectedError",
    "TokenVerifier",
]
