"""Tests for the semantic profile arbitration API (spec §14, issue #12)."""

import json
from pathlib import Path

import httpx
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.profile import (
    ArbitrationDecision,
    EntryId,
    GroupId,
    ProfileArbitrator,
    QuestionKind,
)
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}


def sample_profile() -> SemanticProfile:
    """A small semantic profile with one deliberately wrong role (spec §14)."""
    return SemanticProfile(
        entries=(
            ProfileEntry(
                id="think",
                elements="think_1..3",
                role="file d'attente",
                conf=0.62,
                evidence="le philosophe y attend",
                options=("etat d'automate", "compteur"),
                wrong=True,
            ),
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
    tmp_path: Path, *, arbitrator: ProfileArbitrator | None = None
) -> tuple[httpx.Client, ProfileArbitrator]:
    """Build a test client with session store and profile arbitrator bound to tmp_path."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    run_store = RunStore()
    session_store = SessionStore(directory=tmp_path / "sessions")
    if arbitrator is None:
        arbitrator = ProfileArbitrator()
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=None,
        session_store=session_store,
        arbitrator=arbitrator,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, arbitrator


def import_model(client: httpx.Client) -> str:
    """Import the Philosophes-3 fixture and return its model id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    return response.json()["id"]


def create_session(client: httpx.Client, model_id: str) -> str:
    """Create a session and return its id."""
    response = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    assert response.status_code == 201
    return response.json()["session_id"]


def test_get_profile_returns_404_without_proposal(tmp_path: Path) -> None:
    """GET /v1/sessions/{id}/profile is 404 when no profile was proposed."""
    client, _ = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    response = client.get(f"/v1/sessions/{session_id}/profile", headers=AUTH_HEADERS)
    assert response.status_code == 404
    assert response.json()["extensions"]["reason"] == "profile_not_found"


def test_get_profile_returns_proposed_entries(tmp_path: Path) -> None:
    """GET /v1/sessions/{id}/profile returns the proposed profile (spec §14)."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    response = client.get(f"/v1/sessions/{session_id}/profile", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["proposed"]["entries"]) == 3
    assert body["arbitrations"] == []
    assert len(body["groups"]) == 3


def test_post_arbitration_accepts_role(tmp_path: Path) -> None:
    """POST /v1/sessions/{id}/profile/arbitrate with accepted=true applies the role."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    decision = ArbitrationDecision(
        entry_id=EntryId("eat"), accepted=True, chosen_role="section critique"
    )
    response = client.post(
        f"/v1/sessions/{session_id}/profile/arbitrate",
        content=json.dumps(decision.model_dump(mode="json")),
        headers=JSON_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["arbitrations"]) == 1
    assert body["arbitrations"][0]["entry_id"] == "eat"
    assert body["arbitrations"][0]["accepted"] is True


def test_post_arbitration_rejects_wrong_role(tmp_path: Path) -> None:
    """The human can reject a wrong proposed role and choose another (spec §14)."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    decision = ArbitrationDecision(
        entry_id=EntryId("think"), accepted=False, chosen_role="etat d'automate"
    )
    response = client.post(
        f"/v1/sessions/{session_id}/profile/arbitrate",
        content=json.dumps(decision.model_dump(mode="json")),
        headers=JSON_HEADERS,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["arbitrations"][0]["accepted"] is False
    assert body["arbitrations"][0]["chosen_role"] == "etat d'automate"
    # Rejected entries do not produce active groups
    group_roles = [g["role"] for g in body["groups"]]
    assert "file d'attente" not in group_roles


def test_no_role_applied_without_arbitration(tmp_path: Path) -> None:
    """No role is applied without human arbitration (spec §14 acceptance criterion)."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    state = arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    # Before any arbitration, groups exist but none are covered
    assert len(state.groups) == 3
    assert all(not g.covered_by for g in state.groups)
    # The API exposes the proposed profile but no arbitrations
    response = client.get(f"/v1/sessions/{session_id}/profile", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json()["arbitrations"] == []


def test_get_coverage_exposes_uncovered_groups(tmp_path: Path) -> None:
    """GET /v1/sessions/{id}/coverage lists groups and uncovered re-seeding targets."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    response = client.get(f"/v1/sessions/{session_id}/coverage", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["groups"]) == 3
    assert len(body["uncovered_groups"]) == 3


def test_coverage_after_mark_covered(tmp_path: Path) -> None:
    """After mark_covered, the group moves out of uncovered_groups."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    arbitrator.mark_covered(
        session_id=SessionId(session_id),
        group_id=GroupId("grp_eat"),
        question_kind=QuestionKind("section_critique"),
    )
    response = client.get(f"/v1/sessions/{session_id}/coverage", headers=AUTH_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert len(body["uncovered_groups"]) == 2
    uncovered_ids = {g["group_id"] for g in body["uncovered_groups"]}
    assert "grp_eat" not in uncovered_ids


def test_post_arbitration_invalid_body_returns_422(tmp_path: Path) -> None:
    """An invalid arbitration body is 422 invalid_arbitration."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    response = client.post(
        f"/v1/sessions/{session_id}/profile/arbitrate",
        content=json.dumps({"entry_id": "eat"}),
        headers=JSON_HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["extensions"]["reason"] == "invalid_arbitration"


def test_profile_endpoints_require_auth(tmp_path: Path) -> None:
    """All profile endpoints require the API key."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    for path in (
        f"/v1/sessions/{session_id}/profile",
        f"/v1/sessions/{session_id}/coverage",
    ):
        response = client.get(path)
        assert response.status_code == 401, f"GET {path} without auth should be 401"
    response = client.post(f"/v1/sessions/{session_id}/profile/arbitrate", content="{}")
    assert response.status_code == 401


def test_profile_narration_events(tmp_path: Path) -> None:
    """The profile endpoints emit the pinned DEBUG narration events (ADR-0005)."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    with structlog.testing.capture_logs() as logs:
        arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
        client.get(f"/v1/sessions/{session_id}/profile", headers=AUTH_HEADERS)
        decision = ArbitrationDecision(
            entry_id="eat", accepted=True, chosen_role="section critique"
        )
        client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            content=json.dumps(decision.model_dump(mode="json")),
            headers=JSON_HEADERS,
        )
        client.get(f"/v1/sessions/{session_id}/coverage", headers=AUTH_HEADERS)
    events = [log["event"] for log in logs]
    assert "profile_proposed" in events
    assert "api_profile_read" in events
    assert "profile_arbitrated" in events
    assert "api_profile_arbitrated" in events
    assert "api_coverage_read" in events


# --- Property-tier tests (ADR-0012) ---


@given(
    entries=st.lists(
        st.builds(
            ProfileEntry,
            id=st.text(alphabet="abcdefghij", min_size=1, max_size=8),
            elements=st.text(min_size=1, max_size=20),
            role=st.text(min_size=1, max_size=20),
            conf=st.floats(min_value=0.0, max_value=1.0),
            evidence=st.text(min_size=1, max_size=30),
        ),
        min_size=1,
        max_size=10,
        unique_by=lambda e: e.id,
    )
)
@settings(max_examples=50)
def test_propose_creates_one_group_per_entry(entries: list[ProfileEntry]) -> None:
    """Property: propose() always creates exactly one coverage group per profile entry."""
    arbitrator = ProfileArbitrator()
    profile = SemanticProfile(entries=tuple(entries))
    state = arbitrator.propose(session_id=SessionId("ses_test"), profile=profile)
    assert len(state.groups) == len(entries)
    assert all(not g.covered_by for g in state.groups)


@given(
    accepted=st.booleans(),
    entry_id=st.text(alphabet="abcdefghij", min_size=1, max_size=8),
    chosen_role=st.text(min_size=1, max_size=20),
)
@settings(max_examples=50)
def test_arbitrate_is_idempotent_for_same_entry(
    *, accepted: bool, entry_id: str, chosen_role: str
) -> None:
    """Property: re-arbitrating the same entry does not add a second arbitration."""
    arbitrator = ProfileArbitrator()
    profile = SemanticProfile(
        entries=(
            ProfileEntry(
                id=entry_id,
                elements="e",
                role="r",
                conf=0.5,
                evidence="ev",
            ),
        )
    )
    arbitrator.propose(session_id=SessionId("ses_test"), profile=profile)
    decision = ArbitrationDecision(
        entry_id=EntryId(entry_id), accepted=accepted, chosen_role=chosen_role
    )
    first = arbitrator.arbitrate(session_id=SessionId("ses_test"), decision=decision)
    second = arbitrator.arbitrate(session_id=SessionId("ses_test"), decision=decision)
    assert first is not None
    assert second is not None
    assert len(second.arbitrations) == 1


@given(covered=st.booleans())
@settings(max_examples=30)
def test_uncovered_groups_inverse_of_covered(*, covered: bool) -> None:
    """Property: a group is uncovered iff no question covers it."""
    arbitrator = ProfileArbitrator()
    profile = SemanticProfile(
        entries=(ProfileEntry(id="x", elements="e", role="r", conf=0.5, evidence="ev"),)
    )
    arbitrator.propose(session_id=SessionId("ses_test"), profile=profile)
    if covered:
        arbitrator.mark_covered(
            session_id=SessionId("ses_test"),
            group_id=GroupId("grp_x"),
            question_kind=QuestionKind("q"),
        )
    state = arbitrator.get(session_id=SessionId("ses_test"))
    assert state is not None
    uncovered = state.uncovered_groups()
    if covered:
        assert len(uncovered) == 0
    else:
        assert len(uncovered) == 1


def test_profile_domain_narration_events(tmp_path: Path) -> None:
    """The domain narration events are pinned: profile_group_covered, profile_already_arbitrated."""
    client, arbitrator = build_client(tmp_path)
    model_id = import_model(client)
    session_id = create_session(client, model_id)
    with structlog.testing.capture_logs() as logs:
        arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
        arbitrator.mark_covered(
            session_id=SessionId(session_id),
            group_id=GroupId("grp_eat"),
            question_kind=QuestionKind("section_critique"),
        )
        decision = ArbitrationDecision(
            entry_id=EntryId("eat"), accepted=True, chosen_role="section critique"
        )
        arbitrator.arbitrate(session_id=SessionId(session_id), decision=decision)
        # Re-arbitrate the same entry to trigger the warning
        arbitrator.arbitrate(session_id=SessionId(session_id), decision=decision)
    events = [log["event"] for log in logs]
    assert "profile_group_covered" in events
    assert "profile_already_arbitrated" in events
