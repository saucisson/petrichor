"""Tests of the server-side ingestion of archives and URLs (issue #165, spec §21.3).

POST /v1/models accepts PNML plus zip/gzip/tar archives under a quota with
protected extraction; the one-PNML-or-candidates rule answers a typed 409
candidate list on multi-PNML sources and never imports several models
silently. POST /v1/models/import-url applies the https-only allowlist and
the SSRF rejection (ADR-0008) before downloading, then the same rule.
"""

import io
import socket
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest
import structlog.testing
from pydantic import SecretStr

import petrichor.api.app as app_module
import petrichor.url_import as url_import_module
from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore
from petrichor.url_import import QuotaExceededError, UrlImportConfig

FIXTURES = Path(__file__).parent / "fixtures"
TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
PHILOSOPHES_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")
SYMMETRIC_PNML = (FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")


def make_tgz(*, members: dict[str, bytes]) -> bytes:
    """Build an in-memory tar.gz archive."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_zip(*, members: dict[str, bytes]) -> bytes:
    """Build an in-memory zip archive."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


@pytest.fixture
def client() -> httpx.Client:
    """An HTTP client wired to the WSGI app with a URL-import config."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        url_import=UrlImportConfig(),
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


def post_archive(
    client: httpx.Client, data: bytes, content_type: str, query: str = ""
) -> httpx.Response:
    """POST an archive body to /v1/models."""
    return client.post(
        f"/v1/models{query}",
        content=data,
        headers={**AUTH_HEADERS, "Content-Type": content_type},
    )


class TestArchiveUpload:
    """POST /v1/models with an archive body (spec §21.3)."""

    def test_mono_pnml_tgz_imports_directly(self, client: httpx.Client) -> None:
        archive = make_tgz(members={"Philosophers-PT-010/model.pnml": PHILOSOPHES_PNML.encode()})
        response = post_archive(client, archive, "application/gzip")
        assert response.status_code == 201
        assert response.headers["Location"].startswith("/v1/models/")
        provenance = response.json()["metadata"]["provenance"]
        assert provenance["source"] == "upload"
        assert provenance["candidate"] == "Philosophers-PT-010/model.pnml"
        assert provenance["sha256"].startswith("sha256:")

    def test_mono_pnml_zip_imports_directly(self, client: httpx.Client) -> None:
        archive = make_zip(members={"model.pnml": SYMMETRIC_PNML.encode()})
        response = post_archive(client, archive, "application/zip")
        assert response.status_code == 201

    def test_multi_pnml_lists_candidates_and_never_imports_silently(
        self, client: httpx.Client
    ) -> None:
        archive = make_tgz(
            members={
                "A-PT-1/model.pnml": PHILOSOPHES_PNML.encode(),
                "B-PT-2/model.pnml": SYMMETRIC_PNML.encode(),
            }
        )
        response = post_archive(client, archive, "application/gzip")
        assert response.status_code == 409
        body = response.json()
        assert body["reason"] == "multiple_candidates"
        assert {c["name"] for c in body["candidates"]} == {
            "A-PT-1/model.pnml",
            "B-PT-2/model.pnml",
        }
        assert all(c["sha256"].startswith("sha256:") for c in body["candidates"])
        assert {c["formalism"] for c in body["candidates"]} == {"pt", "symmetric"}

    def test_explicit_candidate_choice_imports(self, client: httpx.Client) -> None:
        archive = make_tgz(
            members={
                "A-PT-1/model.pnml": PHILOSOPHES_PNML.encode(),
                "B-PT-2/model.pnml": SYMMETRIC_PNML.encode(),
            }
        )
        response = post_archive(
            client, archive, "application/gzip", query="?candidate=B-PT-2%2Fmodel.pnml"
        )
        assert response.status_code == 201
        assert response.json()["metadata"]["provenance"]["candidate"] == "B-PT-2/model.pnml"

    def test_unknown_candidate_is_a_typed_404(self, client: httpx.Client) -> None:
        archive = make_tgz(
            members={
                "A-PT-1/model.pnml": PHILOSOPHES_PNML.encode(),
                "B-PT-2/model.pnml": SYMMETRIC_PNML.encode(),
            }
        )
        response = post_archive(client, archive, "application/gzip", query="?candidate=nope")
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "unknown_candidate"

    def test_corrupt_archive_is_a_typed_422(self, client: httpx.Client) -> None:
        response = post_archive(client, b"not an archive", "application/gzip")
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_archive"

    def test_zip_slip_is_refused_and_listed(self, client: httpx.Client) -> None:
        archive = make_zip(members={"../evil.pnml": PHILOSOPHES_PNML.encode()})
        response = post_archive(client, archive, "application/zip")
        assert response.status_code == 422
        payload = response.json()
        assert payload["extensions"]["reason"] == "unsafe_archive"
        assert "../evil.pnml" in payload["detail"]

    def test_archive_without_pnml_is_a_typed_422(self, client: httpx.Client) -> None:
        archive = make_tgz(members={"README.txt": b"hello"})
        response = post_archive(client, archive, "application/gzip")
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "no_pnml"

    def test_upload_quota_is_enforced(
        self, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 10)
        archive = make_tgz(members={"A-PT-1/model.pnml": PHILOSOPHES_PNML.encode()})
        response = post_archive(client, archive, "application/gzip")
        assert response.status_code == 413
        assert response.json()["extensions"]["reason"] == "quota_exceeded"

    def test_unsupported_media_type_is_a_typed_415(self, client: httpx.Client) -> None:
        response = post_archive(client, b"x", "text/plain")
        assert response.status_code == 415
        assert response.json()["extensions"]["reason"] == "unsupported_media_type"


class TestUrlImport:
    """POST /v1/models/import-url: allowlist, SSRF rejection, quota (ADR-0008)."""

    def test_off_allowlist_domain_is_rejected(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/models/import-url",
            json={"url": "https://evil.example.com/model.pnml"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "url_not_allowed"

    def test_http_scheme_is_rejected(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/models/import-url",
            json={"url": "http://mcc.lip6.fr/2026/model.pnml"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "url_not_allowed"

    def test_private_network_is_rejected(
        self, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_getaddrinfo(host: str, port: int, **kwargs: object) -> list[tuple]:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]

        monkeypatch.setattr(url_import_module.socket, "getaddrinfo", fake_getaddrinfo)
        response = client.post(
            "/v1/models/import-url",
            json={"url": "https://mcc.lip6.fr/2026/model.pnml"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "private_network_rejected"

    def test_allowlisted_pnml_url_imports(
        self, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_download(*, url: str, config: UrlImportConfig) -> bytes:
            return PHILOSOPHES_PNML.encode()

        monkeypatch.setattr(app_module, "download_url", fake_download)
        response = client.post(
            "/v1/models/import-url",
            json={"url": "https://mcc.lip6.fr/2026/INPUTS/Philosophers-PT-010.pnml"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 201
        provenance = response.json()["metadata"]["provenance"]
        assert provenance["source"] == "url"
        assert provenance["url"].endswith("Philosophers-PT-010.pnml")

    def test_url_archive_applies_the_candidates_rule(
        self, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = make_tgz(
            members={
                "A-PT-1/model.pnml": PHILOSOPHES_PNML.encode(),
                "B-PT-2/model.pnml": SYMMETRIC_PNML.encode(),
            }
        )

        def fake_download_archive(*, url: str, config: UrlImportConfig) -> bytes:
            return archive

        monkeypatch.setattr(app_module, "download_url", fake_download_archive)
        response = client.post(
            "/v1/models/import-url",
            json={"url": "https://yanntm.github.io/pnmcc-models-2026/INPUTS/all.tgz"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 409
        assert response.json()["reason"] == "multiple_candidates"
        response = client.post(
            "/v1/models/import-url",
            json={
                "url": "https://yanntm.github.io/pnmcc-models-2026/INPUTS/all.tgz",
                "candidate": "A-PT-1/model.pnml",
            },
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 201

    def test_download_quota_is_a_typed_413(
        self, client: httpx.Client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def over_quota(*, url: str, config: UrlImportConfig) -> bytes:
            raise QuotaExceededError("quota")

        monkeypatch.setattr(app_module, "download_url", over_quota)
        response = client.post(
            "/v1/models/import-url",
            json={"url": "https://mcc.lip6.fr/2026/x.tgz"},
            headers=AUTH_HEADERS,
        )
        assert response.status_code == 413
        assert response.json()["extensions"]["reason"] == "quota_exceeded"

    def test_import_url_requires_the_api_key(self, client: httpx.Client) -> None:
        response = client.post("/v1/models/import-url", json={"url": "https://mcc.lip6.fr/x"})
        assert response.status_code == 401


class TestImportNarration:
    """The ingestion narration events are pinned (ADR-0005)."""

    def test_store_and_candidates_events_are_narrated(self, client: httpx.Client) -> None:
        archive = make_tgz(
            members={
                "A-PT-1/model.pnml": PHILOSOPHES_PNML.encode(),
                "B-PT-2/model.pnml": SYMMETRIC_PNML.encode(),
            }
        )
        with structlog.testing.capture_logs() as logs:
            post_archive(client, archive, "application/gzip")
            post_archive(
                client,
                make_tgz(members={"A-PT-1/model.pnml": PHILOSOPHES_PNML.encode()}),
                "application/gzip",
            )
        events = [log["event"] for log in logs]
        assert "api_model_candidates_listed" in events
        assert "api_model_stored" in events
