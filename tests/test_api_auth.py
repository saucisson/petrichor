"""Tests of the API authentication middleware (PT-2, issue #183; #207).

The middleware accepts only a valid ``Authorization: Bearer`` JWT (validated
by a stubbed PT-1 verifier — no network); the legacy ``X-API-Key`` is removed
(web v1 bascule, issue #207). Any other request gets a uniform 401 problem
response and a structured ``api_request_unauthorized`` log with the exact
reason (ADR-0005).
"""

from typing import TYPE_CHECKING

import httpx
import pytest
import structlog.testing

if TYPE_CHECKING:
    from wsgiref.types import WSGIEnvironment

from petrichor.api import create_app
from petrichor.api.auth import (
    ENVIRON_AUTH_CLAIMS,
    REASON_INVALID_BEARER,
    REASON_SESSION_REQUIRED,
    auth_claims_from_environ,
    authenticate_request,
)
from petrichor.api.settings import ApiSettings
from petrichor.auth import AuthSettings, TokenClaims, TokenRejectedError, TokenVerifier
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.main import ConfigurationError, build_token_verifier
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

ISSUER = "https://auth.test"
CLIENT_ID = "petrichor-test"

VALID_CLAIMS = TokenClaims(sub="user-1", iss=ISSUER, aud=[CLIENT_ID], exp=4_000_000_000)


class StubVerifier(TokenVerifier):
    """A PT-1 verifier stub: no HTTP, a scripted outcome per instance."""

    def __init__(
        self, *, settings: AuthSettings, outcome: TokenClaims | TokenRejectedError
    ) -> None:
        super().__init__(
            settings=settings,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200))
            ),
        )
        self._outcome = outcome

    def verify_access_token(self, *, token: str) -> TokenClaims:
        if isinstance(self._outcome, TokenRejectedError):
            raise self._outcome
        return self._outcome


def make_settings(**overrides: str) -> AuthSettings:
    env = {"AUTH_ISSUER": ISSUER, "AUTH_CLIENT_ID": CLIENT_ID, **overrides}
    return AuthSettings.model_validate(env)


PROBE_PATH = "/v1/llm/usage"
"""A GET route that answers 200 with the mock provider, used to probe the auth gate."""


def make_app(verifier: TokenVerifier | None) -> httpx.Client:
    settings = ApiSettings()
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        llm_provider=DeterministicMockProvider(),
        token_verifier=verifier,
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


@pytest.fixture()
def accepting_verifier() -> StubVerifier:
    return StubVerifier(settings=make_settings(), outcome=VALID_CLAIMS)


@pytest.fixture()
def rejecting_verifier() -> StubVerifier:
    return StubVerifier(settings=make_settings(), outcome=TokenRejectedError(reason="expired"))


class TestBearerAuthentication:
    def test_valid_bearer_token_grants_access(self, accepting_verifier: StubVerifier) -> None:
        client = make_app(accepting_verifier)
        response = client.get(PROBE_PATH, headers={"Authorization": "Bearer good-token"})
        assert response.status_code == 200

    def test_invalid_bearer_token_returns_401_with_the_exact_reason_logged(
        self, rejecting_verifier: StubVerifier
    ) -> None:
        client = make_app(rejecting_verifier)
        with structlog.testing.capture_logs() as logs:
            response = client.get(PROBE_PATH, headers={"Authorization": "Bearer bad-token"})
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_INVALID_BEARER
        unauthorized = [event for event in logs if event["event"] == "api_request_unauthorized"]
        assert len(unauthorized) == 1
        assert unauthorized[0]["reason"] == "expired"

    def test_unsupported_authorization_scheme_is_rejected(
        self, accepting_verifier: StubVerifier
    ) -> None:
        client = make_app(accepting_verifier)
        response = client.get(PROBE_PATH, headers={"Authorization": "Basic dXNlcg=="})
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_INVALID_BEARER

    def test_bearer_without_a_wired_verifier_is_rejected(self) -> None:
        client = make_app(None)
        response = client.get(PROBE_PATH, headers={"Authorization": "Bearer any-token"})
        assert response.status_code == 401


class TestLegacyApiKeyRemoved:
    def test_legacy_api_key_header_is_no_longer_accepted(
        self, accepting_verifier: StubVerifier
    ) -> None:
        client = make_app(accepting_verifier)
        response = client.get(PROBE_PATH, headers={"X-API-Key": "any-key"})
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_SESSION_REQUIRED

    def test_missing_credentials_return_the_uniform_401(
        self, accepting_verifier: StubVerifier
    ) -> None:
        client = make_app(accepting_verifier)
        response = client.get(PROBE_PATH)
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_SESSION_REQUIRED

    def test_wrong_bearer_token_returns_401(
        self, accepting_verifier: StubVerifier, rejecting_verifier: StubVerifier
    ) -> None:
        client = make_app(rejecting_verifier)
        response = client.get(PROBE_PATH, headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401


class TestDisabledMode:
    def test_auth_disabled_lets_requests_through_without_credentials(self) -> None:
        verifier = StubVerifier(
            settings=make_settings(AUTH_DISABLED="1"),
            outcome=TokenRejectedError(reason="auth_disabled"),
        )
        assert verifier.enabled is False
        client = make_app(verifier)
        response = client.get(PROBE_PATH)
        assert response.status_code == 200


class TestIdentityExposure:
    def test_valid_bearer_claims_are_exposed_to_handlers(
        self, accepting_verifier: StubVerifier
    ) -> None:
        settings = ApiSettings()
        environ: WSGIEnvironment = {"HTTP_AUTHORIZATION": "Bearer good-token"}
        decision = authenticate_request(
            environ=environ,
            settings=settings,
            token_verifier=accepting_verifier,
        )
        assert decision.allowed
        assert decision.claims == VALID_CLAIMS
        environ[ENVIRON_AUTH_CLAIMS] = decision.claims
        assert auth_claims_from_environ(environ) == VALID_CLAIMS

    def test_legacy_key_header_carries_no_identity(self) -> None:
        settings = ApiSettings()
        environ: WSGIEnvironment = {"HTTP_X_API_KEY": "any-key"}
        decision = authenticate_request(
            environ=environ,
            settings=settings,
            token_verifier=None,
        )
        assert not decision.allowed
        assert decision.claims is None
        assert auth_claims_from_environ(environ) is None


class TestL3Guard:
    def test_auth_disabled_in_production_is_a_fatal_configuration_error(self) -> None:
        with pytest.raises(ConfigurationError, match="AUTH_DISABLED=1"):
            build_token_verifier(
                environment={
                    "AUTH_ISSUER": ISSUER,
                    "AUTH_CLIENT_ID": CLIENT_ID,
                    "AUTH_DISABLED": "1",
                    "PETRICHOR_ENV": "production",
                }
            )

    def test_l3_guard_also_fires_without_auth_issuer(self) -> None:
        # The guard must run before the AUTH_ISSUER fallback, never swallowed by it.
        with pytest.raises(ConfigurationError, match="AUTH_DISABLED=1"):
            build_token_verifier(environment={"AUTH_DISABLED": "1", "PETRICHOR_ENV": "production"})

    def test_auth_disabled_outside_production_builds_a_disabled_verifier(self) -> None:
        verifier = build_token_verifier(
            environment={
                "AUTH_ISSUER": ISSUER,
                "AUTH_CLIENT_ID": CLIENT_ID,
                "AUTH_DISABLED": "1",
                "PETRICHOR_ENV": "development",
            }
        )
        assert verifier.enabled is False

    def test_auth_is_active_when_the_flag_is_absent(self) -> None:
        verifier = build_token_verifier(
            environment={"AUTH_ISSUER": ISSUER, "AUTH_CLIENT_ID": CLIENT_ID}
        )
        assert verifier is not None
        assert verifier.enabled is True


class TestBearerFallback:
    def test_missing_auth_issuer_falls_back_to_session_required_only(self) -> None:
        # 2026-11-21: a missing AUTH_ISSUER must never crash the server (the
        # 502 on petrichor.linard.fr); without a Bearer verifier every
        # protected route answers 401 session_required (issue #207).
        with structlog.testing.capture_logs() as logs:
            verifier = build_token_verifier(environment={})
        assert verifier is None
        events = [event["event"] for event in logs]
        assert "auth_bearer_not_configured" in events

    def test_empty_auth_issuer_falls_back_to_session_required_only(self) -> None:
        verifier = build_token_verifier(environment={"AUTH_ISSUER": "  "})
        assert verifier is None

    def test_fallback_app_still_rejects_requests_without_credentials(self) -> None:
        client = make_app(build_token_verifier(environment={}))
        response = client.get(PROBE_PATH)
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_SESSION_REQUIRED

    def test_fallback_app_rejects_the_legacy_key_header(self) -> None:
        client = make_app(build_token_verifier(environment={}))
        response = client.get(PROBE_PATH, headers={"X-API-Key": "any-key"})
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_SESSION_REQUIRED
