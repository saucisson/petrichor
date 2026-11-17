"""Tests for the static elicitation UI served by the API (spec §9, issue #18).

The 4 steps of the mockup (Amorçage, Questions statiques, Vérifications MC,
Synthèse) are wired to the real /v1 endpoints by ``app.js``; the assets
themselves carry no data and are served without authentication - every data
call goes through the authenticated API.
"""

import httpx
import structlog.testing
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

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

    def test_ui_index_serves_the_four_steps_without_auth(self) -> None:
        client = build_client()
        response = client.get("/ui/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        html = response.text
        for step in ("Amorçage", "Questions statiques", "Vérifications MC", "Synthèse"):
            assert step in html
        # a11y: labelled inputs, live regions, current-step marker (spec §9, P12)
        assert 'aria-label="Clé API"' in html
        assert 'aria-label="Identifiant de session (reprise)"' in html
        assert 'aria-live="polite"' in html
        assert 'aria-current="step"' in html

    def test_app_js_consumes_the_real_api_endpoints(self) -> None:
        client = build_client()
        response = client.get("/ui/app.js")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
        script = response.text
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
        # the Synthèse step negotiates the Markdown export (spec §4.8).
        assert "run.output.verdict" in script
        assert "text/markdown" in script
        # Session reprenable: the session id is persisted client-side (P11).
        assert "petrichor.sessionId" in script

    def test_styles_served_with_visible_focus(self) -> None:
        client = build_client()
        response = client.get("/ui/styles.css")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/css")
        assert ":focus-visible" in response.text

    def test_unknown_ui_asset_is_404(self) -> None:
        client = build_client()
        assert client.get("/ui/nope.js", headers=AUTH_HEADERS).status_code == 404
        # Path traversal is refused: only the whitelisted asset names are served.
        assert client.get("/ui/../app.py", headers=AUTH_HEADERS).status_code == 404

    def test_post_on_ui_is_not_served(self) -> None:
        client = build_client()
        response = client.post("/ui/", content=b"", headers=AUTH_HEADERS)
        assert response.status_code == 404

    def test_ui_asset_narration_is_pinned(self) -> None:
        client = build_client()
        with structlog.testing.capture_logs() as logs:
            response = client.get("/ui/app.js")
        assert response.status_code == 200
        served = next(e for e in logs if e["event"] == "api_ui_asset_served")
        assert served["path"] == "/ui/app.js"
        assert served["bytes"] == len(response.content)
