"""Tests of the web v1 browser login (issue #207, contract §2).

OIDC Authorization Code + PKCE S256 against a fake IdP (httpx MockTransport —
no network): the login redirect, the state/nonce/code exchange, the
server-side session cookie, /auth/me, logout, and the session-cookie path
through the auth gate. The legacy ``X-API-Key`` is gone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import httpx
from api_test_support import VALID_CLAIMS, StubVerifier, make_auth_settings

from petrichor.api import create_app
from petrichor.api.auth import REASON_SESSION_REQUIRED
from petrichor.api.settings import ApiSettings
from petrichor.api.web_auth import (
    SESSION_COOKIE,
    OidcFlowClient,
    PendingLoginStore,
    WebSessionStore,
    code_challenge_s256,
)
from petrichor.auth import TokenClaims, TokenRejectedError
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

if TYPE_CHECKING:
    from wsgiref.types import WSGIApp

ISSUER = "https://idp.test"
AUTHORIZE_ENDPOINT = f"{ISSUER}/authorize"
TOKEN_ENDPOINT = f"{ISSUER}/token"
REVOCATION_ENDPOINT = f"{ISSUER}/revoke"
END_SESSION_ENDPOINT = f"{ISSUER}/logout"

PROBE_PATH = "/v1/llm/usage"
"""A protected GET route used to probe the session-cookie path of the gate."""

DISCOVERY_DOCUMENT = {
    "issuer": ISSUER,
    "authorization_endpoint": AUTHORIZE_ENDPOINT,
    "token_endpoint": TOKEN_ENDPOINT,
    "revocation_endpoint": REVOCATION_ENDPOINT,
    "end_session_endpoint": END_SESSION_ENDPOINT,
}


def fake_idp(*, token_status: int = 200) -> httpx.Client:
    """An httpx client serving the discovery document and the token endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=DISCOVERY_DOCUMENT)
        if request.url.path == urlparse(TOKEN_ENDPOINT).path:
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "server_error"})
            return httpx.Response(
                200,
                json={"id_token": "fake-id-token", "access_token": "fake-access-token"},
            )
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def make_app(
    *,
    oidc_flow: OidcFlowClient | None = None,
    verifier: StubVerifier | None = None,
    web_sessions: WebSessionStore | None = None,
    pending_logins: PendingLoginStore | None = None,
) -> tuple[httpx.Client, WebSessionStore, PendingLoginStore]:
    """An HTTP client wired to the WSGI app with the /auth/* routes enabled."""
    sessions = web_sessions if web_sessions is not None else WebSessionStore()
    pending = pending_logins if pending_logins is not None else PendingLoginStore()
    app: WSGIApp = create_app(
        settings=ApiSettings(),
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        llm_provider=DeterministicMockProvider(),
        token_verifier=verifier
        if verifier is not None
        else StubVerifier(settings=make_auth_settings(), outcome=VALID_CLAIMS),
        oidc_flow=oidc_flow,
        pending_logins=pending,
        web_sessions=sessions,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, sessions, pending


def make_flow(*, token_status: int = 200) -> OidcFlowClient:
    return OidcFlowClient(
        settings=make_auth_settings(),
        http_client=fake_idp(token_status=token_status),
    )


def login_and_get_state(client: httpx.Client, *, return_to: str = "/ui/") -> str:
    """Drive GET /auth/login and return the state parameter of the redirect."""
    response = client.get("/auth/login", params={"returnTo": return_to}, follow_redirects=False)
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    return str(query["state"][0])


class TestLoginRedirect:
    def test_login_redirects_to_the_authorize_endpoint_with_pkce(self) -> None:
        client, _, pending = make_app(oidc_flow=make_flow())
        response = client.get("/auth/login", params={"returnTo": "/ui/"}, follow_redirects=False)
        assert response.status_code == 302
        location = urlparse(response.headers["location"])
        assert f"{location.scheme}://{location.netloc}{location.path}" == AUTHORIZE_ENDPOINT
        query = parse_qs(location.query)
        assert query["client_id"] == ["petrichor-test"]
        assert query["response_type"] == ["code"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == ["http://testserver/auth/callback"]
        assert query["scope"] == ["openid profile email"]
        state = query["state"][0]
        nonce = query["nonce"][0]
        challenge = query["code_challenge"][0]
        stored = pending.pop(state=state)
        assert stored is not None
        assert stored.return_to == "/ui/"
        assert stored.nonce == nonce
        assert code_challenge_s256(code_verifier=stored.code_verifier) == challenge

    def test_open_redirect_return_to_is_neutralized(self) -> None:
        client, _, _pending = make_app(oidc_flow=make_flow())
        state = login_and_get_state(client, return_to="//evil.example")
        response = client.get(
            "/auth/callback",
            params={"state": state, "code": "fake-code"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/ui/"

    def test_login_without_oidc_configured_is_503(self) -> None:
        client, _, _ = make_app(oidc_flow=None)
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "oidc_not_configured"


class TestCallback:
    def test_full_flow_sets_the_session_cookie(self) -> None:
        client, sessions, _ = make_app(oidc_flow=make_flow())
        state = login_and_get_state(client)
        response = client.get(
            "/auth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/ui/"
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(f"{SESSION_COOKIE}=")
        assert "HttpOnly" in cookie
        assert "Secure" in cookie
        assert "SameSite=Lax" in cookie
        session_id = cookie.split(";", 1)[0].removeprefix(f"{SESSION_COOKIE}=")
        assert sessions.get(session_id=session_id) is not None

    def test_unknown_state_is_rejected_and_single_use(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow())
        response = client.get(
            "/auth/callback",
            params={"state": "unknown", "code": "fake-code"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == "invalid_state"
        # A consumed state is gone: replaying the same callback fails too.
        state = login_and_get_state(client)
        first = client.get(
            "/auth/callback", params={"state": state, "code": "c"}, follow_redirects=False
        )
        assert first.status_code == 302
        replay = client.get(
            "/auth/callback", params={"state": state, "code": "c"}, follow_redirects=False
        )
        assert replay.status_code == 401

    def test_failed_code_exchange_is_401(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow(token_status=500))
        state = login_and_get_state(client)
        response = client.get(
            "/auth/callback", params={"state": state, "code": "bad"}, follow_redirects=False
        )
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == "code_exchange_failed"

    def test_rejected_id_token_is_401(self) -> None:
        verifier = StubVerifier(
            settings=make_auth_settings(), outcome=TokenRejectedError(reason="bad_signature")
        )
        client, _, _ = make_app(oidc_flow=make_flow(), verifier=verifier)
        state = login_and_get_state(client)
        response = client.get(
            "/auth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False
        )
        assert response.status_code == 401

    def test_nonce_mismatch_is_401(self) -> None:
        claims = TokenClaims(
            sub="user-1",
            iss=ISSUER,
            aud=["petrichor-test"],
            exp=4_000_000_000,
            nonce="not-the-pending-nonce",
        )
        verifier = StubVerifier(settings=make_auth_settings(), outcome=claims)
        client, _, _ = make_app(oidc_flow=make_flow(), verifier=verifier)
        state = login_and_get_state(client)
        response = client.get(
            "/auth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False
        )
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == "nonce_mismatch"


class TestSessionCookieGate:
    def _login(self, client: httpx.Client) -> str:
        state = login_and_get_state(client)
        response = client.get(
            "/auth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False
        )
        cookie = response.headers["set-cookie"]
        return cookie.split(";", 1)[0]

    def test_protected_route_accepts_the_session_cookie(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow())
        cookie = self._login(client)
        response = client.get(PROBE_PATH, headers={"Cookie": cookie})
        assert response.status_code == 200

    def test_protected_route_without_session_is_401_session_required(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow())
        response = client.get(PROBE_PATH)
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_SESSION_REQUIRED

    def test_auth_me_reports_the_session_subject(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow())
        cookie = self._login(client)
        response = client.get("/auth/me", headers={"Cookie": cookie})
        assert response.status_code == 200
        assert response.json() == {"sub": "user-1", "iss": "https://auth.test"}

    def test_auth_me_without_session_is_401(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow())
        response = client.get("/auth/me")
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == REASON_SESSION_REQUIRED


class TestLogout:
    def test_logout_destroys_the_session_and_expires_the_cookie(self) -> None:
        client, sessions, _ = make_app(oidc_flow=make_flow())
        state = login_and_get_state(client)
        callback = client.get(
            "/auth/callback", params={"state": state, "code": "fake-code"}, follow_redirects=False
        )
        cookie = callback.headers["set-cookie"].split(";", 1)[0]
        session_id = cookie.removeprefix(f"{SESSION_COOKIE}=")
        response = client.post("/auth/logout", headers={"Cookie": cookie}, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/ui/")
        clear = response.headers["set-cookie"]
        assert clear.startswith(f"{SESSION_COOKIE}=;")
        assert "Max-Age=0" in clear
        assert sessions.get(session_id=session_id) is None
        me = client.get("/auth/me", headers={"Cookie": cookie})
        assert me.status_code == 401

    def test_unknown_auth_route_is_404(self) -> None:
        client, _, _ = make_app(oidc_flow=make_flow())
        response = client.get("/auth/nope")
        assert response.status_code == 404
