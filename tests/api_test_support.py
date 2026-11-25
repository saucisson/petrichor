"""Shared support for the API test suite (web v1 bascule, issue #207).

The legacy ``X-API-Key`` is gone: every authenticated test request carries a
``Authorization: Bearer`` header validated by a stubbed PT-1 verifier — no
network, a scripted outcome (same pattern as ``test_api_auth.StubVerifier``).
"""

from __future__ import annotations

import httpx
from petrichor.auth import AuthSettings, TokenClaims, TokenRejectedError, TokenVerifier

TEST_PAT = "test-pat"
"""The Bearer PAT the stub verifier accepts in tests."""

INVALID_PAT = "wrong-pat"
"""A Bearer PAT the stub verifier rejects in tests."""

AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}
"""Headers of an authenticated test request."""

INVALID_AUTH_HEADERS = {"Authorization": f"Bearer {INVALID_PAT}"}
"""Headers of a request whose Bearer token is rejected."""

ISSUER = "https://auth.test"
CLIENT_ID = "petrichor-test"
API_CLIENT_ID = "petrichor-test-api"

VALID_CLAIMS = TokenClaims(sub="user-1", iss=ISSUER, aud=[CLIENT_ID], exp=4_000_000_000)


def make_auth_settings(**overrides: str) -> AuthSettings:
    """Auth settings from a minimal test environment (Web + API clients)."""
    env = {
        "AUTH_ISSUER": ISSUER,
        "AUTH_CLIENT_ID": CLIENT_ID,
        "AUTH_API_CLIENT_ID": API_CLIENT_ID,
        **overrides,
    }
    return AuthSettings.model_validate(env)


class StubVerifier(TokenVerifier):
    """A PT-1 verifier stub: no HTTP, a scripted outcome per instance."""

    def __init__(
        self, *, settings: AuthSettings, outcome: TokenClaims | TokenRejectedError
    ) -> None:
        """Store the scripted outcome on top of the no-network HTTP client."""
        super().__init__(
            settings=settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200))
            ),
        )
        self._outcome = outcome

    def verify_access_token(self, *, token: str) -> TokenClaims:
        """Return the scripted outcome without any HTTP call."""
        if isinstance(self._outcome, TokenRejectedError):
            raise self._outcome
        return self._outcome


def accepting_verifier() -> StubVerifier:
    """A stub verifier that accepts every Bearer token with the valid claims."""
    return StubVerifier(settings=make_auth_settings(), outcome=VALID_CLAIMS)
