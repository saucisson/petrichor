"""Web v1 browser login (issue #207, contract docs/consigne-interface-web-v1.md §2).

OIDC Authorization Code + PKCE S256 flow driven by the WSGI app — the browser
never sees an OIDC token. The applicative session is a server-side record
keyed by an opaque id carried in an ``HttpOnly`` cookie; a JWT is never put
in a cookie. Endpoints come from the issuer discovery document, never
hard-coded. ``AUTH_DISABLED=1`` stays the L3 dev-only bypass (never in prod).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlencode

import httpx
import structlog

if TYPE_CHECKING:
    from petrichor.auth import AuthSettings, TokenClaims

logger = structlog.get_logger(__name__)

SESSION_COOKIE = "petrichor-session"
"""The applicative session cookie name (contract §2)."""

SESSION_TTL_SECONDS = 8 * 3600
"""Bounded session lifetime (contract §2); rotation happens at each login."""

PENDING_LOGIN_TTL_SECONDS = 600
"""TTL of a pending login (state + PKCE verifier), 10 minutes."""

OIDC_SCOPES = "openid profile email"
CODE_CHALLENGE_METHOD = "S256"


@dataclass(frozen=True)
class WebSession:
    """One applicative session, stored server-side."""

    session_id: str
    claims: TokenClaims
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class PendingLogin:
    """A login in flight: state, nonce and the PKCE verifier stay server-side."""

    state: str
    nonce: str
    code_verifier: str
    return_to: str
    created_at: float


class WebSessionStore:
    """Server-side applicative sessions, in memory, with a bounded TTL."""

    def __init__(self, *, ttl_seconds: float = SESSION_TTL_SECONDS) -> None:
        """TTL-bounded in-memory session store."""
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, WebSession] = {}
        self._lock = threading.Lock()

    def create(self, *, claims: TokenClaims) -> WebSession:
        """Create a fresh session (rotation: a new id at every login)."""
        now = time.time()
        session = WebSession(
            session_id=secrets.token_urlsafe(32),
            claims=claims,
            created_at=now,
            expires_at=now + self._ttl_seconds,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        logger.debug("web_session_created", session_id=session.session_id)
        return session

    def get(self, *, session_id: str) -> WebSession | None:
        """The live session, or None when unknown or expired."""
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        if time.time() > session.expires_at:
            self.destroy(session_id=session_id)
            return None
        return session

    def destroy(self, *, session_id: str) -> None:
        """Drop one session (logout)."""
        with self._lock:
            self._sessions.pop(session_id, None)
        logger.debug("web_session_destroyed", session_id=session_id)


class PendingLoginStore:
    """Pending logins (state -> PKCE verifier + nonce + returnTo), TTL-bounded."""

    def __init__(self, *, ttl_seconds: float = PENDING_LOGIN_TTL_SECONDS) -> None:
        """TTL-bounded in-memory pending-login store."""
        self._ttl_seconds = ttl_seconds
        self._pending: dict[str, PendingLogin] = {}
        self._lock = threading.Lock()

    def create(self, *, return_to: str) -> PendingLogin:
        """Register a new pending login with fresh state/nonce/verifier."""
        pending = PendingLogin(
            state=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(24),
            code_verifier=secrets.token_urlsafe(64)[:128],
            return_to=return_to,
            created_at=time.time(),
        )
        with self._lock:
            self._pending[pending.state] = pending
        return pending

    def pop(self, *, state: str) -> PendingLogin | None:
        """Consume the pending login (single use); None when unknown/expired."""
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None:
            return None
        if time.time() - pending.created_at > self._ttl_seconds:
            return None
        return pending


def code_challenge_s256(*, code_verifier: str) -> str:
    """The PKCE S256 challenge of a verifier."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def is_internal_path(*, path: str) -> bool:
    """A returnTo is valid only as an internal path (never an open redirect)."""
    return path.startswith("/") and not path.startswith("//")


class OidcFlowClient:
    """Server-side calls to the IdP: discovery, token exchange, revocation.

    All endpoint URLs come from the discovery document of the configured
    issuer — nothing is hard-coded (contract §2).
    """

    def __init__(self, *, settings: AuthSettings, http_client: httpx.Client | None = None) -> None:
        """Bind the flow client to the auth settings and an HTTP client."""
        self._settings = settings
        self._client = http_client or httpx.Client(timeout=10.0)
        self._discovery_cache: tuple[float, dict[str, str]] | None = None

    def discovery(self) -> dict[str, str]:
        """The cached discovery document of the issuer."""
        now = time.time()
        if self._discovery_cache is not None:
            fetched_at, document = self._discovery_cache
            if now - fetched_at < 24 * 3600:
                return document
        issuer = self._settings.auth_issuer.rstrip("/")
        response = self._client.get(issuer + "/.well-known/openid-configuration")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("discovery document is not a JSON object")
        document = {key: str(value) for key, value in payload.items()}
        self._discovery_cache = (now, document)
        return document

    def authorize_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_challenge: str,
        ui_locales: str,
    ) -> str:
        """The authorize URL of the Authorization Code + PKCE flow."""
        document = self.discovery()
        params = {
            "client_id": self._settings.auth_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": OIDC_SCOPES,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": CODE_CHALLENGE_METHOD,
            "ui_locales": ui_locales,
        }
        return document["authorization_endpoint"] + "?" + urlencode(params)

    def exchange_code(
        self, *, code: str, redirect_uri: str, code_verifier: str
    ) -> dict[str, object]:
        """Exchange one authorization code against the token endpoint."""
        document = self.discovery()
        secret = self._settings.auth_client_secret
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._settings.auth_client_id,
            "code_verifier": code_verifier,
        }
        if secret is not None:
            data["client_secret"] = secret.get_secret_value()
        response = self._client.post(document["token_endpoint"], data=data)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("token response is not a JSON object")
        return payload

    def revoke(self, *, token: str) -> None:
        """Revoke one token at the revocation endpoint (best effort, logged)."""
        document = self.discovery()
        endpoint = document.get("revocation_endpoint")
        if endpoint is None:
            return
        try:
            self._client.post(
                endpoint,
                data={
                    "token": token,
                    "client_id": self._settings.auth_client_id,
                    "client_secret": self._settings.auth_client_secret.get_secret_value()
                    if self._settings.auth_client_secret is not None
                    else "",
                },
            )
        except httpx.HTTPError as error:
            logger.warning("web_auth_revoke_failed", reason=repr(error))

    def end_session_url(self, *, id_token_hint: str, post_logout_redirect_uri: str) -> str:
        """The end_session URL of the IdP."""
        document = self.discovery()
        endpoint = document.get("end_session_endpoint")
        if endpoint is None:
            return post_logout_redirect_uri
        params = {
            "id_token_hint": id_token_hint,
            "post_logout_redirect_uri": post_logout_redirect_uri,
        }
        return endpoint + "?" + urlencode(params)


__all__ = [
    "CODE_CHALLENGE_METHOD",
    "OIDC_SCOPES",
    "PENDING_LOGIN_TTL_SECONDS",
    "SESSION_COOKIE",
    "SESSION_TTL_SECONDS",
    "OidcFlowClient",
    "PendingLogin",
    "PendingLoginStore",
    "WebSession",
    "WebSessionStore",
    "code_challenge_s256",
    "is_internal_path",
]
