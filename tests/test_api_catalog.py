"""Tests of the catalog HTTP API (issue #164, spec §21.2, ADR-0016).

``GET /v1/catalog/models`` (paginated listing, name search, facets, stable
sorts, 503 ``catalog_unavailable`` on degradation) and
``POST /v1/catalog/import`` (idempotent import by name reusing the content
hash, provenance persisted in the model metadata, typed errors: 404 unknown
model, 413 quota, 422 invalid PNML, 401 without API key).
"""

from pathlib import Path

import httpx
import pytest
import structlog.testing
from pydantic import SecretStr

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.catalog import (
    CatalogEntry,
    CatalogFacets,
    CatalogFetch,
    CatalogPage,
    CatalogQuery,
    CatalogUnavailableError,
    InvalidCatalogPnmlError,
    QuotaExceededError,
    UnknownModelError,
)
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")
TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}


def make_entry(*, name: str, source: str = "pnmcc-2026") -> CatalogEntry:
    return CatalogEntry(
        name=name,
        family=name.split("-", maxsplit=1)[0],
        formalism="PT",
        edition="2026",
        source=source,
        size_bucket="small",
        url=f"https://yanntm.github.io/pnmcc-models-2026/INPUTS/{name}.tgz",
    )


class FakeCatalog:
    """A ModelCatalog test double with canned behavior."""

    def __init__(self, *, available: bool = True, pnml: str = PHILOSOPHES_PNML) -> None:
        self._available = available
        self._pnml = pnml
        self._entries = [
            make_entry(name="Philosophers-PT-010"),
            make_entry(name="TokenRing-PT-050"),
        ]

    @property
    def available(self) -> bool:
        return self._available

    def search(self, *, query: CatalogQuery) -> CatalogPage:
        if not self._available:
            raise CatalogUnavailableError("Catalogue indisponible.")
        items = [
            entry
            for entry in self._entries
            if (
                query.search.lower() in entry.name.lower()
                or query.search.lower() in entry.family.lower()
            )
            and all(
                (entry.formalism if facet == "formalism" else getattr(entry, facet, None)) == value
                for facet, value in query.facets.items()
            )
        ]
        return CatalogPage(
            items=tuple(items[query.offset : query.offset + query.limit]),
            total=len(items),
            limit=query.limit,
            offset=query.offset,
            facets=CatalogFacets(family={"Philosophers": 1, "TokenRing": 1}, formalism={"PT": 2}),
        )

    def fetch(self, *, name: str) -> CatalogFetch:
        if not self._available:
            raise CatalogUnavailableError("Catalogue indisponible.")
        if name == "Quota-PT-999":
            raise QuotaExceededError("quota")
        if name == "Broken-PT-1":
            raise InvalidCatalogPnmlError("archive illisible")
        entry = next((item for item in self._entries if item.name == name), None)
        if entry is None:
            raise UnknownModelError(f"Instance inconnue : {name!r}.")
        return CatalogFetch(
            name=name,
            url=entry.url,
            sha256="sha256:" + "0" * 64,
            pnml_text=self._pnml,
            entry=entry,
        )


@pytest.fixture
def client() -> httpx.Client:
    """An HTTP client wired to the WSGI app with a fake catalog."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        catalog=FakeCatalog(),
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


@pytest.fixture
def unavailable_client() -> httpx.Client:
    """An HTTP client wired to an unavailable catalog."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        catalog=FakeCatalog(available=False),
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


class TestCatalogListing:
    """GET /v1/catalog/models."""

    def test_listing_is_paginated_with_facets(self, client: httpx.Client) -> None:
        response = client.get("/v1/catalog/models", headers=AUTH_HEADERS)
        assert response.status_code == 200
        page = response.json()
        assert page["total"] == 2
        assert page["facets"]["formalism"] == {"PT": 2}
        assert {item["name"] for item in page["items"]} == {
            "Philosophers-PT-010",
            "TokenRing-PT-050",
        }

    def test_search_by_name_and_facet_filter(self, client: httpx.Client) -> None:
        response = client.get(
            "/v1/catalog/models", params={"search": "philosophers"}, headers=AUTH_HEADERS
        )
        assert response.json()["total"] == 1
        response = client.get(
            "/v1/catalog/models", params={"formalism": "COL"}, headers=AUTH_HEADERS
        )
        assert response.json()["total"] == 0

    def test_pagination_and_invalid_sort(self, client: httpx.Client) -> None:
        response = client.get(
            "/v1/catalog/models", params={"limit": 1, "offset": 1}, headers=AUTH_HEADERS
        )
        page = response.json()
        assert page["total"] == 2 and len(page["items"]) == 1
        response = client.get("/v1/catalog/models", params={"sort": "bogus"}, headers=AUTH_HEADERS)
        assert response.status_code == 400
        assert response.json()["extensions"]["reason"] == "invalid_sort"

    def test_unavailable_catalog_is_a_typed_503(self, unavailable_client: httpx.Client) -> None:
        response = unavailable_client.get("/v1/catalog/models", headers=AUTH_HEADERS)
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "catalog_unavailable"

    def test_listing_requires_the_api_key(self, client: httpx.Client) -> None:
        response = client.get("/v1/catalog/models")
        assert response.status_code == 401


class TestCatalogImport:
    """POST /v1/catalog/import."""

    def test_import_by_name_is_idempotent_and_persists_provenance(
        self, client: httpx.Client
    ) -> None:
        first = client.post(
            "/v1/catalog/import", json={"name": "Philosophers-PT-010"}, headers=AUTH_HEADERS
        )
        assert first.status_code == 201
        model = first.json()
        provenance = model["metadata"]["provenance"]
        assert provenance["name"] == "Philosophers-PT-010"
        assert provenance["source"] == "pnmcc-2026"
        assert provenance["edition"] == "2026"
        assert provenance["sha256"].startswith("sha256:")
        second = client.post(
            "/v1/catalog/import", json={"name": "Philosophers-PT-010"}, headers=AUTH_HEADERS
        )
        assert second.status_code == 200
        assert second.json()["id"] == model["id"]
        assert second.json()["content_hash"] == model["content_hash"]

    def test_import_matches_post_models_semantics(self, client: httpx.Client) -> None:
        """Same model = same id as a direct PNML import (content-hash idempotence)."""
        direct = client.post(
            "/v1/models",
            content=PHILOSOPHES_PNML,
            headers={**AUTH_HEADERS, "Content-Type": "application/pnml+xml"},
        )
        assert direct.status_code == 201
        by_name = client.post(
            "/v1/catalog/import", json={"name": "Philosophers-PT-010"}, headers=AUTH_HEADERS
        )
        assert by_name.status_code == 200  # same content hash: idempotent
        assert by_name.json()["id"] == direct.json()["id"]

    def test_unknown_model_is_404(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/catalog/import", json={"name": "Nope-PT-999"}, headers=AUTH_HEADERS
        )
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "unknown_model"

    def test_quota_is_413(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/catalog/import", json={"name": "Quota-PT-999"}, headers=AUTH_HEADERS
        )
        assert response.status_code == 413
        assert response.json()["extensions"]["reason"] == "quota_exceeded"

    def test_invalid_archive_pnml_is_422(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/catalog/import", json={"name": "Broken-PT-1"}, headers=AUTH_HEADERS
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_pnml"

    def test_invalid_body_is_422(self, client: httpx.Client) -> None:
        response = client.post("/v1/catalog/import", json={"wrong": 1}, headers=AUTH_HEADERS)
        assert response.status_code == 422

    def test_import_requires_the_api_key(self, client: httpx.Client) -> None:
        response = client.post("/v1/catalog/import", json={"name": "Philosophers-PT-010"})
        assert response.status_code == 401


class TestCatalogNarration:
    """The catalog endpoints' narration events are pinned (ADR-0005)."""

    def test_listing_and_import_are_narrated(self, client: httpx.Client) -> None:
        with structlog.testing.capture_logs() as logs:
            response = client.get("/v1/catalog/models", headers=AUTH_HEADERS)
            assert response.status_code == 200
            response = client.post(
                "/v1/catalog/import", json={"name": "Philosophers-PT-010"}, headers=AUTH_HEADERS
            )
            assert response.status_code == 201
        events = [log["event"] for log in logs]
        assert "api_catalog_listed" in events
        assert "api_catalog_imported" in events


class TestCatalogQueryDecoding:
    """Query-string parameters are percent-decoded before matching."""

    def test_percent_encoding_in_search_is_decoded(self, client: httpx.Client) -> None:
        # %54 is 'T': without percent-decoding the search would not match.
        response = client.get("/v1/catalog/models?search=%54okenRing", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_plus_decodes_to_space_in_search(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        spaced = FakeCatalog()
        spaced._entries.append(
            CatalogEntry(
                name="Odd-PT-001",
                family="Token Ring",
                formalism="PT",
                edition="2026",
                source="pnmcc-2026",
                size_bucket="small",
                url="https://yanntm.github.io/pnmcc-models-2026/INPUTS/Odd-PT-001.tgz",
            )
        )
        app = create_app(
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            catalog=spaced,
        )
        spaced_client = httpx.Client(
            transport=httpx.WSGITransport(app=app), base_url="http://testserver"
        )
        response = spaced_client.get("/v1/catalog/models?search=token+ring", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["total"] == 1
        response = spaced_client.get("/v1/catalog/models?search=token%20ring", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["total"] == 1
