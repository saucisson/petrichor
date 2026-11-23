"""Tests for the Gérer HTTP API: POST /v1/models and GET /v1/models/{id} (issue #3).

The acceptance criteria are pinned over real HTTP semantics through httpx's
WSGI transport: 201/200 idempotent PNML import with the computed hash, 401
without a session/PAT, 415 on wrong media type, 422 problem+json on invalid or
unsupported PNML (nothing stored), 404/405/406 truthful statuses, Symmetric
stored non-unfolded, and log narration (ADR-0005, ADR-0016).
"""

import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING
from wsgiref.simple_server import WSGIServer

import httpx
import pytest
import structlog.testing
from api_test_support import StubVerifier, accepting_verifier, make_auth_settings

from petrichor.api import WSGIApp, create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.auth import TokenRejectedError
from petrichor.convergence import ConvergenceStore
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.main import ConfigurationError, build_server, main
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ModelId
from petrichor.pnml_import import import_pnml
from petrichor.profile import ProfileArbitrator
from petrichor.question_edits import FormulaEditStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore
from petrichor.witness import WitnessStore

if TYPE_CHECKING:
    from wsgiref.types import WSGIEnvironment

FIXTURES = Path(__file__).parent / "fixtures"
TEST_PAT = "test-pat"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
PETRI_JSON_CONTENT_TYPE = "application/petri+json; version=1.0"
PROBLEM_JSON_CONTENT_TYPE = "application/problem+json"


def philosophes3_pnml() -> str:
    """The Philosophes-3 P/T fixture."""
    return (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")


def philosophes3_symmetric_pnml() -> str:
    """The Philosophes-3 Symmetric fixture."""
    return (FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")


@pytest.fixture
def client() -> httpx.Client:
    """An HTTP client wired to the WSGI app with a known API key."""
    settings = ApiSettings()
    app = create_app(
        token_verifier=accepting_verifier(),
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


def post_model(
    client: httpx.Client, body: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    """POST a PNML body to /v1/models."""
    return client.post("/v1/models", content=body, headers=headers or PNML_HEADERS)


class TestAuthentication:
    def test_post_without_credentials_returns_401_problem(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/models",
            content=philosophes3_pnml(),
            headers={"Content-Type": "application/pnml+xml"},
        )
        assert response.status_code == 401
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        problem = response.json()
        assert problem["status"] == 401
        assert problem["extensions"]["reason"] == "session_required"

    def test_post_with_wrong_bearer_token_returns_401(self) -> None:
        rejecting = StubVerifier(
            settings=make_auth_settings(), outcome=TokenRejectedError(reason="expired")
        )
        app = create_app(
            settings=ApiSettings(),
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            token_verifier=rejecting,
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        headers = {**PNML_HEADERS, "Authorization": "Bearer wrong-token"}
        response = post_model(client, philosophes3_pnml(), headers=headers)
        assert response.status_code == 401

    def test_get_model_is_public_read_only(self, client: httpx.Client) -> None:
        # Web v1 (issue #206): GET /v1/models/{id} is a public route of the
        # Découvrir zone - an unknown id answers a truthful 404, not a 401.
        response = client.get("/v1/models/mdl_whatever")
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "model_not_found"

    def test_unauthorized_request_is_narrated_as_a_warning(self, client: httpx.Client) -> None:
        # A protected route (POST /v1/models) without credentials: 401 +
        # structured warning (the public GET /v1/models/{id} no longer logs it).
        with structlog.testing.capture_logs() as events:
            client.post(
                "/v1/models",
                content=philosophes3_pnml(),
                headers={"Content-Type": "application/pnml+xml"},
            )
        assert any(e["event"] == "api_request_unauthorized" for e in events)


class TestPostModels:
    def test_pt_import_returns_201_with_canonical_model_and_hash(
        self, client: httpx.Client
    ) -> None:
        response = post_model(client, philosophes3_pnml())
        assert response.status_code == 201
        assert response.headers["Content-Type"] == PETRI_JSON_CONTENT_TYPE
        model = response.json()
        assert model["formalism"] == "pt"
        assert model["content_hash"].startswith("sha256:")
        assert len(model["content_hash"]) == len("sha256:") + 64
        assert len(model["places"]) == 9
        assert len(model["transitions"]) == 6
        assert len(model["arcs"]) == 24
        assert response.headers["Location"] == f"/v1/models/{model['id']}"

    def test_reposting_the_same_pnml_returns_200_with_the_same_model(
        self, client: httpx.Client
    ) -> None:
        first = post_model(client, philosophes3_pnml())
        second = post_model(client, philosophes3_pnml())
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json() == first.json()
        assert "Location" not in second.headers

    def test_cosmetically_different_pnml_shares_the_hash(self, client: httpx.Client) -> None:
        variant = philosophes3_pnml().replace('x="100" y="100"', 'x="999" y="999"')
        first = post_model(client, philosophes3_pnml())
        second = post_model(client, variant)
        assert second.status_code == 200
        assert second.json()["content_hash"] == first.json()["content_hash"]
        assert second.json()["id"] == first.json()["id"]

    def test_wrong_content_type_returns_415(self, client: httpx.Client) -> None:
        headers = {**AUTH_HEADERS, "Content-Type": "application/json"}
        response = post_model(client, philosophes3_pnml(), headers=headers)
        assert response.status_code == 415
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        assert response.json()["extensions"]["reason"] == "unsupported_media_type"

    def test_invalid_pnml_returns_422_and_stores_nothing(self, client: httpx.Client) -> None:
        response = post_model(client, "<pnml><net>")
        assert response.status_code == 422
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        problem = response.json()
        assert problem["status"] == 422
        assert problem["extensions"]["reason"] == "invalid_pnml"
        assert problem["instance"] == "/v1/models"
        not_found = client.get("/v1/models/mdl_00000000", headers=AUTH_HEADERS)
        assert not_found.status_code == 404

    def test_unsupported_pnml_returns_422_never_unfolded(self, client: httpx.Client) -> None:
        stochastic = (
            '<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">'
            '<net type="http://www.pnml.org/version-2009/grammar/pnml+stochastic">'
            '<place id="p1"/></net></pnml>'
        )
        response = post_model(client, stochastic)
        assert response.status_code == 422
        assert "Unsupported PNML net type" in response.json()["detail"]

    def test_symmetric_import_is_stored_non_unfolded(self, client: httpx.Client) -> None:
        response = post_model(client, philosophes3_symmetric_pnml())
        assert response.status_code == 201
        model = response.json()
        assert model["formalism"] == "symmetric"
        assert model["color_domain"] == {"classes": ["Fork", "Philosopher"]}
        assert len(model["places"]) == 9
        assert len(model["transitions"]) == 6
        assert len(model["arcs"]) == 24

    def test_successful_import_is_narrated(self, client: httpx.Client) -> None:
        with structlog.testing.capture_logs() as events:
            response = post_model(client, philosophes3_pnml())
        stored = [e for e in events if e["event"] == "api_model_stored"]
        assert len(stored) == 1
        assert stored[0]["model_id"] == response.json()["id"]
        assert stored[0]["created"] is True


class TestGetModel:
    def test_get_returns_the_canonical_document(self, client: httpx.Client) -> None:
        posted = post_model(client, philosophes3_pnml())
        model_id = posted.json()["id"]
        response = client.get(f"/v1/models/{model_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.headers["Content-Type"] == PETRI_JSON_CONTENT_TYPE
        assert response.json() == posted.json()
        assert response.json()["content_hash"] == posted.json()["content_hash"]

    def test_get_unknown_model_returns_404_problem(self, client: httpx.Client) -> None:
        response = client.get("/v1/models/mdl_deadbeef", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        assert response.json()["extensions"]["reason"] == "model_not_found"

    def test_get_with_pnml_only_accept_returns_406(self, client: httpx.Client) -> None:
        posted = post_model(client, philosophes3_pnml())
        model_id = posted.json()["id"]
        response = client.get(
            f"/v1/models/{model_id}", headers={**AUTH_HEADERS, "Accept": "application/pnml+xml"}
        )
        assert response.status_code == 406
        assert response.json()["extensions"]["reason"] == "not_acceptable"

    def test_get_symmetric_model_returns_it_non_unfolded(self, client: httpx.Client) -> None:
        posted = post_model(client, philosophes3_symmetric_pnml())
        model_id = posted.json()["id"]
        response = client.get(f"/v1/models/{model_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["formalism"] == "symmetric"
        assert len(response.json()["places"]) == 9


class TestRouting:
    def test_unknown_path_returns_404(self, client: httpx.Client) -> None:
        response = client.get("/v1/nope", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "not_found"

    def test_delete_on_models_returns_405(self, client: httpx.Client) -> None:
        response = client.delete("/v1/models", headers=AUTH_HEADERS)
        assert response.status_code == 405
        assert response.json()["extensions"]["reason"] == "method_not_allowed"

    def test_error_payload_shape_is_stable(self, client: httpx.Client) -> None:
        response = client.get("/v1/models/mdl_deadbeef", headers=AUTH_HEADERS)
        problem = response.json()
        assert set(problem) == {"type", "title", "status", "detail", "instance", "extensions"}
        assert problem["type"].startswith("https://")
        assert isinstance(json.dumps(problem), str)


def call_app(
    app: WSGIApp,
    *,
    method: str,
    path: str,
    body: bytes,
    content_length: str | None = None,
    content_type: str | None = "application/pnml+xml",
) -> tuple[str, dict[str, str], bytes]:
    """Invoke the WSGI app directly with a crafted environ."""
    environ: WSGIEnvironment = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": content_length if content_length is not None else str(len(body)),
        "HTTP_AUTHORIZATION": f"Bearer {TEST_PAT}",
        "wsgi.input": io.BytesIO(body),
    }
    if content_type is not None:
        environ["CONTENT_TYPE"] = content_type
    captured: dict[str, object] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    result = b"".join(app(environ, start_response))
    status = str(captured["status"])
    headers = dict(captured["headers"])
    return status, headers, result


@pytest.fixture
def app() -> WSGIApp:
    """The WSGI app with a known API key and a fresh store."""
    settings = ApiSettings()
    return create_app(
        token_verifier=accepting_verifier(),
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
    )


class TestEdgeBranches:
    def test_post_without_content_type_returns_415(self, app: WSGIApp) -> None:
        environ_body = philosophes3_pnml().encode("utf-8")
        status, _, body = call_app(
            app, method="POST", path="/v1/models", body=environ_body, content_type=None
        )
        assert status.startswith("415")
        assert json.loads(body)["extensions"]["reason"] == "unsupported_media_type"

    def test_invalid_content_length_returns_400(self, app: WSGIApp) -> None:
        status, _, body = call_app(
            app, method="POST", path="/v1/models", body=b"<pnml/>", content_length="abc"
        )
        assert status.startswith("400")
        assert json.loads(body)["extensions"]["reason"] == "invalid_content_length"

    def test_non_utf8_body_returns_422(self, app: WSGIApp) -> None:
        status, _, body = call_app(app, method="POST", path="/v1/models", body=b"\xff\xfe\x00")
        assert status.startswith("422")
        assert json.loads(body)["extensions"]["reason"] == "invalid_pnml"

    def test_post_on_a_model_resource_returns_405(self, client: httpx.Client) -> None:
        response = client.post(
            "/v1/models/mdl_deadbeef", content=philosophes3_pnml(), headers=PNML_HEADERS
        )
        assert response.status_code == 405
        assert response.json()["extensions"]["reason"] == "method_not_allowed"

    def test_get_with_empty_model_id_returns_404(self, client: httpx.Client) -> None:
        response = client.get("/v1/models/", headers=AUTH_HEADERS)
        assert response.status_code == 404

    def test_get_with_empty_accept_header_serves_canonical(self, client: httpx.Client) -> None:
        posted = post_model(client, philosophes3_pnml())
        model_id = posted.json()["id"]
        response = client.get(f"/v1/models/{model_id}", headers={**AUTH_HEADERS, "Accept": ""})
        assert response.status_code == 200
        assert response.headers["Content-Type"] == PETRI_JSON_CONTENT_TYPE


class TestCompositionRoot:
    def test_build_server_wires_the_app(self, tmp_path: Path) -> None:
        settings = ApiSettings()
        server = build_server(
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            session_store=SessionStore(directory=tmp_path / "sessions"),
            arbitrator=ProfileArbitrator(),
            arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
            convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
            witness_store=WitnessStore(directory=tmp_path / "witnesses"),
            llm_provider=DeterministicMockProvider(),
            edit_store=FormulaEditStore(directory=tmp_path / "formula_edits"),
        )
        assert isinstance(server.application, Callable)
        assert server.server_port == 8080
        server.server_close()

    def test_main_builds_and_serves_until_stopped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTH_ISSUER", "https://auth.test")
        monkeypatch.setenv("AUTH_CLIENT_ID", "petrichor-test")
        monkeypatch.setattr(WSGIServer, "serve_forever", lambda self: self.server_close())
        main()

    def test_build_server_uses_host_and_port_parameters(self, tmp_path: Path) -> None:
        settings = ApiSettings()
        server = build_server(
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            session_store=SessionStore(directory=tmp_path / "sessions"),
            arbitrator=ProfileArbitrator(),
            arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
            convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
            witness_store=WitnessStore(directory=tmp_path / "witnesses"),
            llm_provider=DeterministicMockProvider(),
            edit_store=FormulaEditStore(directory=tmp_path / "formula_edits"),
            host="127.0.0.2",
            port=9999,
        )
        assert server.server_address == ("127.0.0.2", 9999)
        server.server_close()

    def test_main_reads_host_and_port_from_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("PETRICHOR_HOST", "127.0.0.2")
        monkeypatch.setenv("PETRICHOR_PORT", "9998")
        monkeypatch.setenv("AUTH_ISSUER", "https://auth.test")
        monkeypatch.setenv("AUTH_CLIENT_ID", "petrichor-test")
        captured: dict[str, object] = {}

        def fake_serve_forever(self: WSGIServer) -> None:
            captured["address"] = self.server_address
            self.server_close()

        monkeypatch.setattr(WSGIServer, "serve_forever", fake_serve_forever)
        main()
        assert captured["address"] == ("127.0.0.2", 9998)

    def test_main_rejects_non_integer_port_with_typed_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PETRICHOR_PORT", "not-a-number")
        with pytest.raises(ConfigurationError, match="PETRICHOR_PORT"):
            main()


SAMPLE_PNML_FOR_STORE = (
    '<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">'
    '<net><place id="p1"><name><value>only</value></name></place></net></pnml>'
)


class TestModelStore:
    def test_save_is_idempotent_by_hash_and_len_counts_models(self) -> None:
        store = ModelStore()
        model = import_pnml(text=SAMPLE_PNML_FOR_STORE)
        stored, created = store.save(model=model)
        assert created is True
        again, created_again = store.save(model=model)
        assert created_again is False
        assert again == stored
        assert len(store) == 1
        assert store.get(model_id=model.id) == model
        assert store.get(model_id=ModelId("mdl_missing")) is None
