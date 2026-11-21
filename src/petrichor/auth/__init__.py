"""Authentication brick (PT-1, issues #182-#184): OIDC access-token validation.

Zitadel is the single IdP (issuer ``https://auth.linard.fr``); access tokens
are RS256 JWTs validated locally against the JWKS fetched from the discovery
document — no password, no session and no endpoint URL is ever hard-coded
(cadrage « Authentification unifiée saucisson », décisions §2).
"""

from petrichor.auth.settings import AuthSettings
from petrichor.auth.verification import (
    CLOCK_LEEWAY_SECONDS,
    TokenClaims,
    TokenRejectedError,
    TokenVerifier,
)

__all__ = [
    "CLOCK_LEEWAY_SECONDS",
    "AuthSettings",
    "TokenClaims",
    "TokenRejectedError",
    "TokenVerifier",
]
