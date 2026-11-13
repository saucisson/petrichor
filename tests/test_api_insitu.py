"""Tests for the in-situ amorçage API: state graph, annotations, net overlay (issue #13)."""

import json
from pathlib import Path

import httpx
import pytest
import structlog.testing
from philosophes3 import build_philosophes3
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.engines.mini_engine import explore_state_graph_payload
from petrichor.insitu import InvalidElementSpecError, build_net_overlay, expand_element_spec
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import (
    ElementId,
    UnknownElementError,
    with_element_annotation,
)
from petrichor.profile import CoverageGroup, GroupId, ProfileArbitrator, QuestionKind
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")
SYMMETRIC_PNML = (FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}


def sample_profile() -> SemanticProfile:
    """A small semantic profile over Philosophes-3 element ranges."""
    return SemanticProfile(
        entries=(
            ProfileEntry(
                id="eat",
                elements="eat_1..3",
                role="section critique",
                conf=0.95,
                evidence="eat precede de deux acquisitions",
            ),
            ProfileEntry(
                id="fork",
                elements="fork_1..3",
                role="ressource partagee",
                conf=0.97,
                evidence="jeton initial unique",
            ),
        )
    )


def build_client(
    tmp_path: Path, *, with_explorer: bool = True
) -> tuple[httpx.Client, ProfileArbitrator]:
    """Build a test client with the in-situ explorer wired (or not)."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    run_store = RunStore()
    session_store = SessionStore(directory=tmp_path / "sessions")
    arbitrator = ProfileArbitrator()
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=None,
        session_store=session_store,
        arbitrator=arbitrator,
        state_graph_explorer=explore_state_graph_payload if with_explorer else None,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, arbitrator


def import_model(client: httpx.Client, pnml: str = PHILOSOPHES3_PNML) -> str:
    """Import a PNML fixture and return its model id."""
    response = client.post("/v1/models", content=pnml, headers=PNML_HEADERS)
    assert response.status_code == 201
    return response.json()["id"]


def create_session(client: httpx.Client, model_id: str) -> str:
    """Create a session and return its id."""
    response = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    assert response.status_code == 201
    return response.json()["session_id"]


class TestStateGraphEndpoint:
    def test_state_graph_is_consultable_during_the_session(self, tmp_path: Path) -> None:
        """GET /v1/sessions/{id}/state-graph serves the permanent model's graph (P13)."""
        client, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.get(f"/v1/sessions/{session_id}/state-graph", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["model_id"] == model_id
        assert body["formalism"] == "pt"
        assert body["truncated"] is False
        assert len(body["states"]) > 1
        assert len(body["edges"]) > 0
        assert body["place_names"]
        # Deterministic: two reads are identical (spec §19.4)
        again = client.get(f"/v1/sessions/{session_id}/state-graph", headers=AUTH_HEADERS)
        assert again.json() == body

    def test_state_graph_unknown_session_is_404(self, tmp_path: Path) -> None:
        client, _ = build_client(tmp_path)
        response = client.get("/v1/sessions/ses_nope/state-graph", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"

    def test_state_graph_without_explorer_is_503(self, tmp_path: Path) -> None:
        client, _ = build_client(tmp_path, with_explorer=False)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.get(f"/v1/sessions/{session_id}/state-graph", headers=AUTH_HEADERS)
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "state_graph_not_configured"

    def test_state_graph_on_symmetric_net_is_422_explicit(self, tmp_path: Path) -> None:
        """Non-P/T nets have no reachability graph: explicit 422, never a silent empty graph."""
        client, _ = build_client(tmp_path)
        model_id = import_model(client, pnml=SYMMETRIC_PNML)
        session_id = create_session(client, model_id)
        response = client.get(f"/v1/sessions/{session_id}/state-graph", headers=AUTH_HEADERS)
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "unsupported_formalism"


class TestAnnotationsEndpoint:
    def test_put_annotation_persists_outside_the_hash(self, tmp_path: Path) -> None:
        """PUT annotations sets one in-situ annotation; the content hash is unchanged (spec §13)."""
        client, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        before = client.get(f"/v1/models/{model_id}", headers=AUTH_HEADERS).json()
        place_id = before["places"][0]["id"]
        with structlog.testing.capture_logs() as logs:
            response = client.put(
                f"/v1/sessions/{session_id}/annotations",
                content=json.dumps(
                    {"element_id": place_id, "key": "role", "value": "section critique"}
                ),
                headers=JSON_HEADERS,
            )
        assert response.status_code == 200
        body = response.json()
        assert body["element_id"] == place_id
        assert body["content_hash"] == before["content_hash"]
        assert body["annotations"]["role"] == "section critique"
        assert "api_annotation_set" in [entry["event"] for entry in logs]
        # Persisted in the canonical annotations section of the stored model
        after = client.get(f"/v1/models/{model_id}", headers=AUTH_HEADERS).json()
        assert after["content_hash"] == before["content_hash"]
        annotations = next(p["annotations"] for p in after["places"] if p["id"] == place_id)
        assert annotations["role"] == "section critique"

    def test_put_annotation_unknown_element_is_404(self, tmp_path: Path) -> None:
        client, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.put(
            f"/v1/sessions/{session_id}/annotations",
            content=json.dumps({"element_id": "pl_nope", "key": "role", "value": "x"}),
            headers=JSON_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "element_not_found"

    def test_put_annotation_invalid_body_is_422(self, tmp_path: Path) -> None:
        client, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.put(
            f"/v1/sessions/{session_id}/annotations",
            content=json.dumps({"element_id": "pl_x"}),
            headers=JSON_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_annotation"


class TestNetOverlayEndpoint:
    def test_overlay_without_profile_greys_everything(self, tmp_path: Path) -> None:
        """No profile yet: every element is greyed, no groups (maquette P6)."""
        client, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.get(f"/v1/sessions/{session_id}/net-overlay", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        assert body["model_id"] == model_id
        assert body["groups"] == []
        assert len(body["elements"]) > 0
        assert all(not element["covered"] for element in body["elements"])
        assert len(body["greyed"]) == len(body["elements"])

    def test_overlay_resolves_card_to_elements_and_greys_the_rest(self, tmp_path: Path) -> None:
        """A covered group resolves its range spec to element ids; the rest stays greyed (P5/P6)."""
        client, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
        arbitrator.mark_covered(
            session_id=SessionId(session_id),
            group_id=GroupId("grp_eat"),
            question_kind=QuestionKind("liveness.conditional"),
        )
        response = client.get(f"/v1/sessions/{session_id}/net-overlay", headers=AUTH_HEADERS)
        assert response.status_code == 200
        body = response.json()
        eat_group = next(g for g in body["groups"] if g["group_id"] == "grp_eat")
        assert eat_group["covered"] is True
        # Card → elements: eat_1..3 resolved to the three eat place ids
        model = client.get(f"/v1/models/{model_id}", headers=AUTH_HEADERS).json()
        eat_ids = {p["id"] for p in model["places"] if p["name"].startswith("eat_")}
        assert set(eat_group["elements"]) == eat_ids
        covered_ids = {e["id"] for e in body["elements"] if e["covered"]}
        assert covered_ids == eat_ids
        assert set(body["greyed"]) == {e["id"] for e in body["elements"]} - eat_ids


class TestElementSpecExpansion:
    def test_range_spec_expands(self) -> None:
        assert expand_element_spec(spec="think_1..3") == ("think_1", "think_2", "think_3")

    def test_comma_list_spec(self) -> None:
        assert expand_element_spec(spec="fork_1, fork_3") == ("fork_1", "fork_3")

    def test_inverted_range_is_an_explicit_error(self) -> None:
        with pytest.raises(InvalidElementSpecError, match="Inverted"):
            expand_element_spec(spec="think_3..1")

    def test_empty_spec_is_an_explicit_error(self) -> None:
        with pytest.raises(InvalidElementSpecError, match="Empty"):
            expand_element_spec(spec="  ")


class TestWithElementAnnotation:
    def test_annotation_keeps_the_content_hash(self) -> None:
        model = build_philosophes3()
        place_id = model.places[0].id
        updated = with_element_annotation(
            model=model, element_id=place_id, key="role", value="file d'attente"
        )
        assert updated.content_hash == model.content_hash
        assert updated.places[0].annotations["role"] == "file d'attente"

    def test_unknown_element_is_an_explicit_error(self) -> None:
        model = build_philosophes3()
        with pytest.raises(UnknownElementError, match="pl_nope"):
            with_element_annotation(
                model=model, element_id=ElementId("pl_nope"), key="role", value="x"
            )

    def test_overlay_is_built_from_groups(self) -> None:
        model = build_philosophes3()
        groups = (
            CoverageGroup(
                group_id=GroupId("grp_eat"),
                role="section critique",
                elements="eat_1..3",
                covered_by=(QuestionKind("liveness.conditional"),),
            ),
        )
        overlay = build_net_overlay(model=model, groups=groups)
        assert len(overlay.greyed_ids()) == len(overlay.elements) - 3
