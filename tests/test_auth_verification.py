"""Tests of the OIDC access-token validation brick (PT-1, issue #182).

Level L1 only: RSA keys are generated in the fixtures, the discovery and JWKS
endpoints are served by an httpx mock transport — deterministic, no network.
"""

import time

import httpx
import pytest
import structlog
from hypothesis import HealthCheck, given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st
from joserfc import jwk, jwt

from petrichor.auth import (
    CLOCK_LEEWAY_SECONDS,
    AuthSettings,
    TokenRejectedError,
    TokenVerifier,
)

ISSUER = "https://auth.test"
CLIENT_ID = "petrichor-test"
API_CLIENT_ID = "petrichor-test-api"


class FakeIdp:
    """A fake IdP: one RSA key pair, discovery + JWKS served over a mock transport."""

    def __init__(self) -> None:
        self.private_key = jwk.generate_key("RSA", 2048)
        self.public_jwk = jwk.import_key(self.private_key.as_dict(private=False))
        self.kid = self.private_key.thumbprint()
        self.keyset = jwk.KeySet([self.public_jwk])
        self.jwks_fetches = 0
        self.discovery_fetches = 0

    def sign(self, claims: dict, *, key: jwk.RSAKey | None = None, kid: str | None = None) -> str:
        header = {"alg": "RS256", "typ": "JWT", "kid": kid if kid is not None else self.kid}
        return jwt.encode(header, claims, key or self.private_key)

    def valid_claims(self, **overrides: object) -> dict:
        now = int(time.time())
        claims: dict = {
            "sub": "user-1",
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "exp": now + 3600,
            "nbf": now - 10,
        }
        claims.update(overrides)
        return claims

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/.well-known/openid-configuration"):
                self.discovery_fetches += 1
                return httpx.Response(
                    200,
                    json={
                        "issuer": ISSUER,
                        "jwks_uri": ISSUER + "/jwks.json",
                    },
                )
            if request.url.path.endswith("/jwks.json"):
                self.jwks_fetches += 1
                return httpx.Response(200, json=self.keyset.as_dict())
            return httpx.Response(404)

        return httpx.MockTransport(handler)


@pytest.fixture()
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture()
def settings() -> AuthSettings:
    return AuthSettings.model_validate({"AUTH_ISSUER": ISSUER, "AUTH_CLIENT_ID": CLIENT_ID})


def make_verifier(idp: FakeIdp, settings: AuthSettings) -> TokenVerifier:
    return TokenVerifier(settings=settings, http_client=httpx.Client(transport=idp.transport()))


class TestValidToken:
    def test_valid_token_returns_its_claims(self, idp: FakeIdp, settings: AuthSettings) -> None:
        verifier = make_verifier(idp, settings)
        claims = verifier.verify_access_token(token=idp.sign(idp.valid_claims()))
        assert claims.sub == "user-1"
        assert claims.iss == ISSUER
        assert claims.aud == [CLIENT_ID]
        assert claims.exp > int(time.time())

    def test_discovery_and_jwks_are_cached(self, idp: FakeIdp, settings: AuthSettings) -> None:
        verifier = make_verifier(idp, settings)
        for _ in range(3):
            verifier.verify_access_token(token=idp.sign(idp.valid_claims()))
        assert idp.discovery_fetches == 1
        assert idp.jwks_fetches == 1

    def test_audience_list_is_accepted(self, idp: FakeIdp, settings: AuthSettings) -> None:
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(aud=["other-app", CLIENT_ID]))
        claims = verifier.verify_access_token(token=token)
        assert CLIENT_ID in claims.aud

    def test_api_client_id_audience_is_accepted_by_default(self, idp: FakeIdp) -> None:
        settings = AuthSettings.model_validate(
            {"AUTH_ISSUER": ISSUER, "AUTH_CLIENT_ID": CLIENT_ID, "AUTH_API_CLIENT_ID": API_CLIENT_ID}
        )
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(aud=API_CLIENT_ID))
        claims = verifier.verify_access_token(token=token)
        assert claims.aud == [API_CLIENT_ID]

    def test_allowed_audiences_from_configuration(self, idp: FakeIdp) -> None:
        settings = AuthSettings.model_validate(
            {
                "AUTH_ISSUER": ISSUER,
                "AUTH_CLIENT_ID": CLIENT_ID,
                "AUTH_ALLOWED_AUDIENCES": "other-app, petrichor-test",
            }
        )
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(aud="other-app"))
        claims = verifier.verify_access_token(token=token)
        assert claims.aud == ["other-app"]


class TestRejectedTokens:
    def test_expired_token_is_rejected_with_the_exact_reason(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(exp=int(time.time()) - 3600))
        with pytest.raises(TokenRejectedError, match="expired"):
            verifier.verify_access_token(token=token)

    def test_wrong_audience_is_rejected_with_the_exact_reason(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(aud="another-app"))
        with pytest.raises(TokenRejectedError, match="invalid_audience"):
            verifier.verify_access_token(token=token)

    def test_wrong_issuer_is_rejected_with_the_exact_reason(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(iss="https://evil.test"))
        with pytest.raises(TokenRejectedError, match="invalid_issuer"):
            verifier.verify_access_token(token=token)

    def test_bad_signature_is_rejected_with_the_exact_reason(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        other_key = jwk.generate_key("RSA", 2048)
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(), key=other_key)
        with pytest.raises(TokenRejectedError, match="bad_signature"):
            verifier.verify_access_token(token=token)

    def test_unknown_kid_triggers_one_jwks_refresh_then_rejects(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(), kid="unknown-kid")
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(TokenRejectedError, match="unknown_kid"),
        ):
            verifier.verify_access_token(token=token)
        assert idp.jwks_fetches == 2  # initial fetch + one refresh
        events = [event["event"] for event in logs]
        assert "auth_jwks_refresh_on_unknown_kid" in events
        assert "auth_token_rejected" in events

    def test_malformed_token_is_rejected(self, idp: FakeIdp, settings: AuthSettings) -> None:
        verifier = make_verifier(idp, settings)
        with pytest.raises(TokenRejectedError, match="malformed_token"):
            verifier.verify_access_token(token="not-a-jwt")  # noqa: S106

    def test_rejection_is_logged_with_the_reason(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        verifier = make_verifier(idp, settings)
        token = idp.sign(idp.valid_claims(exp=int(time.time()) - 3600))
        with structlog.testing.capture_logs() as logs, pytest.raises(TokenRejectedError):
            verifier.verify_access_token(token=token)
        events = [event["event"] for event in logs]
        assert "auth_token_rejected" in events


class TestInfrastructureFailures:
    def test_discovery_unavailable_is_a_typed_rejection(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        verifier = TokenVerifier(
            settings=settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(TokenRejectedError, match="discovery_unavailable"),
        ):
            verifier.verify_access_token(token=idp.sign(idp.valid_claims()))
        assert "auth_discovery_failed" in [event["event"] for event in logs]

    def test_discovery_returning_a_non_object_is_a_typed_rejection(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=["not", "an", "object"])

        verifier = TokenVerifier(
            settings=settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(TokenRejectedError, match="discovery_unavailable"),
        ):
            verifier.verify_access_token(token=idp.sign(idp.valid_claims()))
        assert "auth_discovery_failed" in [event["event"] for event in logs]

    def test_discovery_without_jwks_uri_is_a_typed_rejection(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"issuer": ISSUER})

        verifier = TokenVerifier(
            settings=settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(TokenRejectedError, match="discovery_without_jwks_uri"),
        ):
            verifier.verify_access_token(token=idp.sign(idp.valid_claims()))
        assert "auth_discovery_failed" in [event["event"] for event in logs]

    def test_jwks_unavailable_is_a_typed_rejection(
        self, idp: FakeIdp, settings: AuthSettings
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(
                    200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks.json"}
                )
            return httpx.Response(500)

        verifier = TokenVerifier(
            settings=settings, http_client=httpx.Client(transport=httpx.MockTransport(handler))
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(TokenRejectedError, match="jwks_unavailable"),
        ):
            verifier.verify_access_token(token=idp.sign(idp.valid_claims()))
        assert "auth_jwks_failed" in [event["event"] for event in logs]

    def test_token_without_sub_is_rejected(self, idp: FakeIdp, settings: AuthSettings) -> None:
        verifier = make_verifier(idp, settings)
        claims = idp.valid_claims()
        del claims["sub"]
        with pytest.raises(TokenRejectedError, match="missing_claims"):
            verifier.verify_access_token(token=idp.sign(claims))

    def test_close_releases_only_an_owned_client(self, settings: AuthSettings) -> None:
        owned = TokenVerifier(settings=settings)  # owns its client
        owned_client = owned._client
        owned.close()
        assert owned_client.is_closed

        injected = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
        verifier = TokenVerifier(settings=settings, http_client=injected)
        verifier.close()
        assert not injected.is_closed
        injected.close()


class TestDisabledMode:
    def test_auth_disabled_rejects_explicitly(self, idp: FakeIdp) -> None:
        settings = AuthSettings.model_validate(
            {"AUTH_ISSUER": ISSUER, "AUTH_CLIENT_ID": CLIENT_ID, "AUTH_DISABLED": "1"}
        )
        verifier = make_verifier(idp, settings)
        assert verifier.enabled is False
        with pytest.raises(TokenRejectedError, match="auth_disabled"):
            verifier.verify_access_token(token=idp.sign(idp.valid_claims()))

    def test_default_settings_are_auth_active(self) -> None:
        settings = AuthSettings.model_validate(
            {"AUTH_ISSUER": ISSUER, "AUTH_CLIENT_ID": CLIENT_ID, "AUTH_API_CLIENT_ID": API_CLIENT_ID}
        )
        assert settings.auth_disabled is False
        assert settings.allowed_audiences() == (CLIENT_ID, API_CLIENT_ID)

    def test_api_client_id_alone_is_a_valid_default_audience(self) -> None:
        settings = AuthSettings.model_validate(
            {"AUTH_ISSUER": ISSUER, "AUTH_API_CLIENT_ID": API_CLIENT_ID}
        )
        assert settings.allowed_audiences() == (API_CLIENT_ID,)


@pytest.mark.property
@given(
    exp_offset=st.one_of(
        st.integers(min_value=-3600, max_value=-2 * CLOCK_LEEWAY_SECONDS),
        st.integers(min_value=2 * CLOCK_LEEWAY_SECONDS, max_value=3600),
    ),
    iss=st.sampled_from([ISSUER, "https://evil.test"]),
    aud=st.sampled_from([CLIENT_ID, "another-app"]),
)
@hypothesis_settings(
    max_examples=24,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_signed_token_invariants(
    idp: FakeIdp, settings: AuthSettings, exp_offset: int, iss: str, aud: str
) -> None:
    """Any IdP-signed token with matching iss/aud and a non-expired exp is accepted;
    any other is rejected with its exact reason (ADR-0012 property tier)."""
    verifier = make_verifier(idp, settings)
    token = idp.sign(idp.valid_claims(iss=iss, aud=aud, exp=int(time.time()) + exp_offset))
    if iss == ISSUER and aud == CLIENT_ID and exp_offset > 0:
        claims = verifier.verify_access_token(token=token)
        assert claims.iss == ISSUER
        assert claims.aud == [CLIENT_ID]
    else:
        with pytest.raises(TokenRejectedError) as excinfo:
            verifier.verify_access_token(token=token)
        # The claims registry validates iss, then aud, then exp: the first
        # failing claim determines the exact reason.
        expected = (
            "invalid_issuer"
            if iss != ISSUER
            else "invalid_audience"
            if aud != CLIENT_ID
            else "expired"
        )
        assert excinfo.value.reason == expected
