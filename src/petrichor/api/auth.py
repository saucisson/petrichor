"""API authentication middleware (PT-2, issue #183; web v1 bascule, issue #207).

Every non-public request is authenticated before routing. Two credentials
are accepted through the same middleware:

- ``Authorization: Bearer <token>`` — a PAT, an OIDC access token validated
  locally by the PT-1 brick (:class:`petrichor.auth.TokenVerifier`); the
  validated claims are exposed to the handlers as
  ``environ["petrichor.auth_claims"]``.
- The ``petrichor-session`` cookie — the applicative web session, checked by
  the caller (``WebSessionStore``) before this middleware runs.

The legacy ``X-API-Key`` is removed (web v1 bascule, issue #207): no static
key, no shared secret remains. ``AUTH_DISABLED=1`` is the L3 local-dev
bypass: the middleware lets every request through and the composition root
refuses to combine it with a production indicator. A rejection is a uniform
``401`` problem response and a structured ``api_request_unauthorized`` log
carrying the exact reason.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from petrichor.auth import TokenClaims, TokenRejectedError, TokenVerifier

if TYPE_CHECKING:
    from wsgiref.types import WSGIEnvironment

    from petrichor.api.settings import ApiSettings

HEADER_AUTHORIZATION = "Authorization"
BEARER_SCHEME = "bearer"
ENVIRON_AUTH_CLAIMS = "petrichor.auth_claims"
"""WSGI environ key carrying the validated :class:`TokenClaims` of a Bearer request."""

REASON_MISSING_CREDENTIALS = "missing_credentials"
REASON_INVALID_BEARER = "invalid_bearer_token"
REASON_UNSUPPORTED_SCHEME = "unsupported_authorization_scheme"
REASON_VERIFIER_NOT_CONFIGURED = "bearer_verifier_not_configured"
REASON_SESSION_REQUIRED = "session_required"
"""Web v1 (issue #206): a non-public route hit without a session/PAT."""


@dataclass(frozen=True)
class AuthDecision:
    """The outcome of authenticating one request."""

    allowed: bool
    claims: TokenClaims | None
    """The validated Bearer claims, when the request used a valid Bearer token."""
    rejection_reason: str | None
    """The exact reason of a rejection (for the structured log), else ``None``."""


def _authenticate_bearer(
    *, authorization: str, token_verifier: TokenVerifier | None
) -> AuthDecision:
    """Authenticate an ``Authorization`` header value as a Bearer JWT."""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != BEARER_SCHEME or not token.strip():
        return AuthDecision(allowed=False, claims=None, rejection_reason=REASON_UNSUPPORTED_SCHEME)
    if token_verifier is None:
        return AuthDecision(
            allowed=False, claims=None, rejection_reason=REASON_VERIFIER_NOT_CONFIGURED
        )
    try:
        claims = token_verifier.verify_access_token(token=token.strip())
    except TokenRejectedError as error:
        return AuthDecision(allowed=False, claims=None, rejection_reason=error.reason)
    return AuthDecision(allowed=True, claims=claims, rejection_reason=None)


def authenticate_request(
    *, environ: WSGIEnvironment, settings: ApiSettings, token_verifier: TokenVerifier | None
) -> AuthDecision:
    """Authenticate one request: Bearer JWT (PAT) validated by the PT-1 brick.

    The L3 bypass (``AUTH_DISABLED=1``) lets every request through. The web
    session cookie is checked by the caller before this middleware; the
    legacy ``X-API-Key`` is gone (issue #207) — no static key is accepted.
    """
    if token_verifier is not None and not token_verifier.enabled:
        return AuthDecision(allowed=True, claims=None, rejection_reason=None)
    authorization = environ.get("HTTP_AUTHORIZATION", "")
    if authorization:
        return _authenticate_bearer(authorization=authorization, token_verifier=token_verifier)
    return AuthDecision(allowed=False, claims=None, rejection_reason=REASON_MISSING_CREDENTIALS)


def auth_claims_from_environ(environ: WSGIEnvironment) -> TokenClaims | None:
    """The validated Bearer claims of the request, when it used a valid Bearer token."""
    claims = environ.get(ENVIRON_AUTH_CLAIMS)
    return claims if isinstance(claims, TokenClaims) else None


__all__ = [
    "BEARER_SCHEME",
    "ENVIRON_AUTH_CLAIMS",
    "HEADER_AUTHORIZATION",
    "REASON_INVALID_BEARER",
    "REASON_MISSING_CREDENTIALS",
    "REASON_SESSION_REQUIRED",
    "REASON_UNSUPPORTED_SCHEME",
    "REASON_VERIFIER_NOT_CONFIGURED",
    "AuthDecision",
    "auth_claims_from_environ",
    "authenticate_request",
]
