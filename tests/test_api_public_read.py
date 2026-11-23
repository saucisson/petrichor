"""Tests of the web v1 public/private API split (issue #206, contract §1/§5).

``GET /v1/catalog/models`` and ``GET /v1/models/{mid}`` are public read-only
routes of the Découvrir zone: they answer without any credentials. Every other
``/v1`` route requires a session cookie or a PAT and answers ``401``
``application/problem+json`` with ``reason: "session_required"`` when hit
without one. The gate is adapted to the split, not just complemented.
"""

from pathlib import Path

import httpx
import pytest
import structlog.testing
from api_test_support import accepting_verifier

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.catalog import CatalogEntry, CatalogFacets, CatalogPage, CatalogQuery
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

FIXTURES = Path(__file__).parent / "fixtures"
TEST_PAT = "test-pat"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
PETRI_JSON_ACCEPT = "application/petri+json"
PROBLEM_JSON_CONTENT_TYPE = "application/problem+json"


def philosophes3_pnml() -> str:
    return (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")


class FakeCatalog:
    """A minimal ModelCatalog double: one canned listing."""

    @property
    def available(self) -> bool:
        return True

    def search(self, *, query: CatalogQuery) -> CatalogPage:
        entry = CatalogEntry(
            name="Philosophers-PT-010",
            family="Philosophers",
            formalism="PT",
            edition="2026",
            source="pnmcc-2026",
            size_bucket="small",
            url="https://example.invalid/Philosophers-PT-010.tgz",
        )
        return CatalogPage(
            items=(entry,), total=1, limit=query.limit, offset=query.offset, facets=CatalogFacets()
        )


@pytest.fixture
def client() -> httpx.Client:
    settings = ApiSettings()
    app = create_app(
        token_verifier=accepting_verifier(),
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        llm_provider=DeterministicMockProvider(),
        catalog=FakeCatalog(),
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


def import_philosophes(client: httpx.Client) -> str:
    """Import the PNML fixture with credentials; returns the model id."""
    response = client.post("/v1/models", content=philosophes3_pnml(), headers=PNML_HEADERS)
    assert response.status_code in (200, 201)
    return response.json()["id"]


class TestPublicReadRoutes:
    def test_catalog_listing_answers_without_credentials(self, client: httpx.Client) -> None:
        response = client.get("/v1/catalog/models")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_model_document_answers_without_credentials(self, client: httpx.Client) -> None:
        model_id = import_philosophes(client)
        response = client.get(f"/v1/models/{model_id}", headers={"Accept": PETRI_JSON_ACCEPT})
        assert response.status_code == 200

    def test_unknown_model_without_credentials_is_a_truthful_404(
        self, client: httpx.Client
    ) -> None:
        response = client.get("/v1/models/m_unknown", headers={"Accept": PETRI_JSON_ACCEPT})
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "model_not_found"

    def test_public_read_allowance_is_narrated(self, client: httpx.Client) -> None:
        # ADR-0005: the narration of the public-route decision is pinned so it
        # cannot silently vanish.
        with structlog.testing.capture_logs() as events:
            response = client.get("/v1/catalog/models")
        assert response.status_code == 200
        allowed = [e for e in events if e["event"] == "api_public_read_allowed"]
        assert len(allowed) == 1
        assert allowed[0]["path"] == "/v1/catalog/models"


class TestProtectedRoutesRequireSession:
    def test_post_models_without_credentials_is_401_session_required(
        self, client: httpx.Client
    ) -> None:
        response = client.post(
            "/v1/models",
            content=philosophes3_pnml(),
            headers={"Content-Type": "application/pnml+xml"},
        )
        assert response.status_code == 401
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        assert response.json()["extensions"]["reason"] == "session_required"

    def test_post_catalog_import_without_credentials_is_401_session_required(
        self, client: httpx.Client
    ) -> None:
        response = client.post(
            "/v1/catalog/import",
            json={"name": "Philosophers-PT-010"},
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == "session_required"

    def test_llm_usage_without_credentials_is_401_session_required(
        self, client: httpx.Client
    ) -> None:
        response = client.get("/v1/llm/usage")
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == "session_required"

    def test_sessions_without_credentials_are_401_session_required(
        self, client: httpx.Client
    ) -> None:
        response = client.get("/v1/sessions")
        assert response.status_code == 401
        assert response.json()["extensions"]["reason"] == "session_required"

    def test_protected_route_still_accepts_credentials(self, client: httpx.Client) -> None:
        response = client.get("/v1/llm/usage", headers=AUTH_HEADERS)
        assert response.status_code == 200
