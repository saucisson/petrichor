"""API authentication middleware (PT-2, issue #183).

Every non-static request is authenticated before routing. Two credentials
are accepted through the same middleware (cadrage §4.1):

- ``Authorization: Bearer <token>`` — an OIDC access token validated locally
  by the PT-1 brick (:class:`petrichor.auth.TokenVerifier`); the validated
  claims are exposed to the handlers as ``environ["petrichor.auth_claims"]``.
- ``X-API-Key`` — the legacy static key, kept until its removal (PT-2 keeps
  it passing through the same middleware).

``AUTH_DISABLED=1`` is the L3 local-dev bypass: the middleware lets every
request through and the composition root refuses to combine it with a
production indicator. A rejection is a uniform ``401`` problem response and
a structured ``api_request_unauthorized`` log carrying the exact reason.
"""

import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING

from petrichor.auth import TokenClaims, TokenRejectedError, TokenVerifier

if TYPE_CHECKING:
    from wsgiref.types import WSGIEnvironment

    from petrichor.api.settings import ApiSettings

HEADER_AUTHORIZATION = "Authorization"
HEADER_API_KEY = "X-API-Key"
BEARER_SCHEME = "bearer"
ENVIRON_AUTH_CLAIMS = "petrichor.auth_claims"
"""WSGI environ key carrying the validated :class:`TokenClaims` of a Bearer request."""

REASON_MISSING_CREDENTIALS = "missing_or_invalid_api_key"
REASON_INVALID_BEARER = "invalid_bearer_token"


@dataclass(frozen=True)
class AuthDecision:
    """The outcome of authenticating one request."""

    allowed: bool
    claims: TokenClaims | None
    """The validated Bearer claims, when the request used a valid Bearer token."""
    rejection_reason: str | None
    """The exact reason of a rejection (for the structured log), else ``None``."""


def _legacy_key_matches(*, environ: WSGIEnvironment, settings: ApiSettings) -> bool:
    """Constant-time comparison of the X-API-Key header against the configured key."""
    provided = environ.get("HTTP_X_API_KEY", "")
    expected = settings.api_key.get_secret_value()
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


def _authenticate_bearer(
    *, authorization: str, token_verifier: TokenVerifier | None
) -> AuthDecision:
    """Authenticate an ``Authorization`` header value as a Bearer JWT."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != BEARER_SCHEME or not token.strip():
        return AuthDecision(
            allowed=False, claims=None, rejection_reason="unsupported_authorization_scheme"
        )
    if token_verifier is None:
        return AuthDecision(
            allowed=False, claims=None, rejection_reason="bearer_verifier_not_configured"
        )
    try:
        claims = token_verifier.verify_access_token(token=token.strip())
    except TokenRejectedError as error:
        return AuthDecision(allowed=False, claims=None, rejection_reason=error.reason)
    return AuthDecision(allowed=True, claims=claims, rejection_reason=None)


def authenticate_request(
    *, environ: WSGIEnvironment, settings: ApiSettings, token_verifier: TokenVerifier | None
) -> AuthDecision:
    """Authenticate one request: Bearer JWT (PT-1) or legacy X-API-Key.

    The L3 bypass (``AUTH_DISABLED=1``) lets every request through. A Bearer
    token is validated only when a verifier is wired; without one, only the
    legacy key is accepted.
    """
    if token_verifier is not None and not token_verifier.enabled:
        return AuthDecision(allowed=True, claims=None, rejection_reason=None)
    authorization = environ.get("HTTP_AUTHORIZATION", "")
    if authorization:
        return _authenticate_bearer(authorization=authorization, token_verifier=token_verifier)
    if _legacy_key_matches(environ=environ, settings=settings):
        return AuthDecision(allowed=True, claims=None, rejection_reason=None)
    return AuthDecision(allowed=False, claims=None, rejection_reason=REASON_MISSING_CREDENTIALS)


def auth_claims_from_environ(environ: WSGIEnvironment) -> TokenClaims | None:
    """The validated Bearer claims of the request, when it used a valid Bearer token."""
    claims = environ.get(ENVIRON_AUTH_CLAIMS)
    return claims if isinstance(claims, TokenClaims) else None


__all__ = [
    "BEARER_SCHEME",
    "ENVIRON_AUTH_CLAIMS",
    "HEADER_API_KEY",
    "HEADER_AUTHORIZATION",
    "REASON_INVALID_BEARER",
    "REASON_MISSING_CREDENTIALS",
    "AuthDecision",
    "auth_claims_from_environ",
    "authenticate_request",
]
