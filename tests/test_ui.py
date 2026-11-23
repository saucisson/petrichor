"""Tests for the static elicitation UI served by the API (spec §9, ADR-0101).

The UI v2 is a built React SPA (issue #123): ``/ui/`` serves the Vite shell
and the hashed assets under ``/ui/assets/``. The 4 steps of the mockup
(Amorçage, Questions statiques, Vérifications MC, Synthèse) are wired to the
real /v1 endpoints by the bundle; the assets themselves carry no data and are
served without authentication - every data call goes through the API.
"""

import io
import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog.testing
from pydantic import SecretStr

from conftest import fetch_ui_bundle, fetch_ui_stylesheet
from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

if TYPE_CHECKING:
    from wsgiref.types import WSGIEnvironment

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


def build_client() -> httpx.Client:
    """Build a test client; the UI needs no store, only the app."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


class TestStaticUi:
    def test_root_redirects_to_the_ui(self) -> None:
        client = build_client()
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/ui/"

    def test_ui_index_serves_the_built_spa_shell_without_auth(self) -> None:
        client = build_client()
        response = client.get("/ui/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        html = response.text
        # The Vite shell (ADR-0101): the React app mounts on #root and loads
        # the hashed bundle.
        assert '<div id="root">' in html
        assert 'lang="fr"' in html
        assert re.search(r'src="/ui/assets/[^"]+\.js"', html) is not None
        # The 4 steps and the a11y markers (labelled inputs, live regions,
        # current-step marker - spec §9, P12) are rendered by the bundle.
        bundle = fetch_ui_bundle(client)
        for step in ("Amorçage", "Questions statiques", "Vérifications MC", "Synthèse"):
            assert step in bundle
        assert "Se connecter" in bundle  # web v1 (#207): account menu, no API key input
        assert "Identifiant de session (reprise)" in bundle
        assert "aria-live" in bundle
        assert "aria-current" in bundle

    def test_ui_spa_fallback_serves_the_shell_on_client_side_routes(self) -> None:
        client = build_client()
        response = client.get("/ui/une/route/client")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert '<div id="root">' in response.text

    def test_ui_bundle_consumes_the_real_api_endpoints(self) -> None:
        client = build_client()
        script = fetch_ui_bundle(client)
        for endpoint in (
            "/v1/models",
            "/v1/sessions",
            "/profile",
            "/profile/arbitrate",
            "/arbitration-requests",
            "/v1/runs/",
            "/contract",
        ):
            assert endpoint in script
        # The Vérifications step reads runs without inventing a verdict, and
        # the Synthèse step negotiates the Markdown export (spec §4.8). The
        # property chain is pinned on the unminified source (the built bundle
        # minifies it away).
        assert ".verdict" in script
        component = (
            Path(__file__).parent.parent / "src/petrichor/api/ui/src/components/Verifications.tsx"
        )
        assert "run.output?.verdict" in component.read_text(encoding="utf-8")
        assert "text/markdown" in script
        # Question cards show the cross-verification status and, on controversy,
        # both camps; the Synthèse step lists the arbitration journal (issue #31).
        assert "cross_status" in script
        assert "controversial" in script
        assert "journal-list" in script
        # Session reprenable: the session id is persisted client-side (P11).
        assert "petrichor.sessionId" in script

    def test_styles_served_with_visible_focus(self) -> None:
        client = build_client()
        assert ":focus-visible" in fetch_ui_stylesheet(client)

    def test_unknown_ui_asset_is_404(self) -> None:
        client = build_client()
        assert client.get("/ui/nope.js", headers=AUTH_HEADERS).status_code == 404

    def test_post_on_ui_is_405(self) -> None:
        client = build_client()
        response = client.post("/ui/", content=b"", headers=AUTH_HEADERS)
        assert response.status_code == 405
        assert response.json()["extensions"]["reason"] == "method_not_allowed"

    def test_path_traversal_is_refused_at_the_wsgi_layer(self) -> None:
        # httpx normalizes dot segments client-side, so the server-side guard
        # is exercised through a raw WSGI call with the un-normalized path.
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        app = create_app(settings=settings, store=ModelStore(), run_store=RunStore(), runner=None)
        captured: dict[str, str] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        environ: WSGIEnvironment = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/ui/../app.py",
            "HTTP_X_API_KEY": TEST_API_KEY,
            "wsgi.input": io.BytesIO(b""),
        }
        body = b"".join(app(environ, start_response))
        assert captured["status"].startswith("404")
        # The problem document is returned, never the source file content.
        assert b"petrichor.dev/problems/not_found" in body
        assert b"def _dispatch_ui" not in body

    def test_absolute_path_escape_is_refused(self, tmp_path: Path) -> None:
        # `/ui//<abs>/evil.js` yields an absolute name: pathlib would discard
        # the static base - the guard must refuse it (review PR #146).
        outside = tmp_path / "evil.js"
        outside.write_text("alert(1)", encoding="utf-8")
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        app = create_app(settings=settings, store=ModelStore(), run_store=RunStore(), runner=None)
        captured: dict[str, str] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        environ: WSGIEnvironment = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/ui//" + str(outside),
            "HTTP_X_API_KEY": TEST_API_KEY,
            "wsgi.input": io.BytesIO(b""),
        }
        body = b"".join(app(environ, start_response))
        assert captured["status"].startswith("404")
        assert b"alert(1)" not in body

    def test_ui_asset_narration_is_pinned(self) -> None:
        client = build_client()
        html = client.get("/ui/").text
        asset_path = re.search(r'src="(/ui/assets/[^"]+\.js)"', html)
        assert asset_path is not None
        with structlog.testing.capture_logs() as logs:
            response = client.get(asset_path.group(1))
        assert response.status_code == 200
        served = next(e for e in logs if e["event"] == "api_ui_asset_served")
        assert served["path"] == asset_path.group(1)
        assert served["bytes"] == len(response.content)
