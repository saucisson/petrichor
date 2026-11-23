"""Tests for the editable formula and its inverse reading (spec §9 maquette P7; issue #37).

- Edit a formula from a question card; the inverse reading (formule →
  langage) is returned, an LLM proposition labeled ``provenance: llm``.
- The edit is journaled (old + new formula); the edited formula is
  re-verified through the standard check chain.
- The witness of the old formula is never lost (journal + archive).
"""

import tempfile
import time
from pathlib import Path

import httpx
import pytest
import structlog.testing
from api_test_support import accepting_verifier
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import CheckCompleted, CheckQuestion, ToolVersion
from petrichor.convergence import ConvergenceStore
from petrichor.elicitation import QuestionId
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.question_edits import FormulaEditStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore
from petrichor.witness import WitnessStore

FIXTURES = Path(__file__).parent / "fixtures"
# The maquette fixture: its content hash keys the shipped golden corpus (readings).
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3-maquette.pnml").read_text(encoding="utf-8")

TEST_PAT = "test-pat"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
WITNESS_OUTPUT = {
    "verdict": "violated",
    "witness": {
        "kind": "counterexample",
        "cyclic": False,
        "back_edge": None,
        "steps": [
            {"index": 0, "marking": ["think_1"], "fired": None},
            {"index": 1, "marking": ["hasL_1"], "fired": "takeL_1"},
        ],
    },
    "cost": {"tool_seconds": 0.1, "states_explored": 4},
    "technique": "dummy",
}


class FakeRunner:
    """A runner that completes immediately with a fixed witness output."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Serve the fixed done output."""
        return CheckCompleted(output=WITNESS_OUTPUT, command=["fake-tool"])


def build_client(
    tmp_path: Path,
) -> tuple[httpx.Client, FormulaEditStore, WitnessStore, ProfileArbitrator]:
    """Wire the app with every session-scoped store, the mock LLM and a fake runner."""
    settings = ApiSettings()
    store = ModelStore()
    run_store = RunStore()
    session_store = SessionStore(directory=tmp_path / "sessions")
    edit_store = FormulaEditStore(directory=tmp_path / "formula_edits")
    witness_store = WitnessStore(directory=tmp_path / "witnesses")
    arbitrator = ProfileArbitrator()
    app = create_app(
        token_verifier=accepting_verifier(),
        settings=settings,
        store=store,
        run_store=run_store,
        runner=FakeRunner(),
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
        convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
        witness_store=witness_store,
        llm_provider=DeterministicMockProvider(),
        edit_store=edit_store,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, edit_store, witness_store, arbitrator


def import_model(client: httpx.Client) -> str:
    """Import the maquette fixture and return its model id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    return response.json()["id"]


def create_session(client: httpx.Client, model_id: str) -> str:
    """Create a session and return its id."""
    response = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    assert response.status_code == 201
    return response.json()["session_id"]


def propose_and_arbitrate(
    *, client: httpx.Client, arbitrator: ProfileArbitrator, session_id: str
) -> None:
    """Propose the sample profile and accept the eat entry."""
    arbitrator.propose(
        session_id=SessionId(session_id),
        profile=SemanticProfile(
            entries=(
                ProfileEntry(
                    id="eat",
                    elements="eat_1..3",
                    role="section critique",
                    conf=0.95,
                    evidence="eat precede de deux acquisitions",
                ),
            )
        ),
    )
    response = client.post(
        f"/v1/sessions/{session_id}/profile/arbitrate",
        json={"entry_id": "eat", "accepted": True, "chosen_role": "section critique"},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 200


def first_question_id(client: httpx.Client, session_id: str) -> tuple[str, str]:
    """The first generated question of the session: (question_id, formula)."""
    contract = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS).json()
    question = contract["questions"][0]
    return question["question_id"], question["formula"]


def post_check(
    *, client: httpx.Client, model_id: str, family: str, kind: str, formula: str, session_id: str
) -> httpx.Response:
    """POST one check through the standard chain, charged to the session."""
    return client.post(
        f"/v1/checks/{family}",
        json={
            "model_id": model_id,
            "kind": kind,
            "formula": formula,
            "session_id": session_id,
        },
        headers=JSON_HEADERS,
    )


def wait_terminal(*, client: httpx.Client, run_id: str, timeout: float = 5.0) -> dict:
    """Poll the run until it leaves the running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class TestFormulaEdit:
    def test_edit_is_journaled_and_returns_the_inverse_reading(self, tmp_path: Path) -> None:
        client, edit_store, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question_id, formula = first_question_id(client, session_id)

        new_formula = "EF (eat_1)"
        response = client.post(
            f"/v1/sessions/{session_id}/questions/{question_id}/formula",
            json={"formula": new_formula},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 201
        payload = response.json()
        assert payload["record"]["old_formula"] == formula
        assert payload["record"]["new_formula"] == new_formula
        # Inverse reading: formule → langage, an LLM proposition labeled provenance llm.
        assert payload["reading"] is not None
        assert payload["reading"]["provenance"] == "llm"
        assert payload["reading"]["degraded"] is False
        assert "reading" in payload["reading"]

        journal = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS).json()
        edits = [e for e in journal if e["event"] == "formula_edited"]
        assert len(edits) == 1
        assert edits[0]["detail"]["old_formula"] == formula
        assert edits[0]["detail"]["new_formula"] == new_formula
        assert len(edit_store.history(session_id=SessionId(session_id))) == 1

    def test_edited_formula_is_reverified_through_the_standard_chain(self, tmp_path: Path) -> None:
        client, _, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question_id, _formula = first_question_id(client, session_id)
        new_formula = "EF (eat_1)"
        client.post(
            f"/v1/sessions/{session_id}/questions/{question_id}/formula",
            json={"formula": new_formula},
            headers=JSON_HEADERS,
        )
        # Re-verification through the standard chain: POST /v1/checks/{family}.
        response = post_check(
            client=client,
            model_id=model_id,
            family="reachability",
            kind="reachability.ef",
            formula=new_formula,
            session_id=session_id,
        )
        assert response.status_code == 202
        run = wait_terminal(client=client, run_id=response.json()["run_id"])
        assert run["formula"] == new_formula
        assert run["status"] == "done"

    def test_old_formula_witness_is_never_lost(self, tmp_path: Path) -> None:
        client, _, witness_store, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question_id, formula = first_question_id(client, session_id)

        # A run on the ORIGINAL formula produces a witness.
        run = post_check(
            client=client,
            model_id=model_id,
            family="deadlock",
            kind="deadlock",
            formula=formula,
            session_id=session_id,
        )
        old_run = wait_terminal(client=client, run_id=run.json()["run_id"])
        witness = client.get(f"/v1/runs/{old_run['run_id']}/witness", headers=AUTH_HEADERS)
        assert witness.status_code == 200
        witness_id = witness.json()["witness_id"]

        # Edit the formula: the old witness stays stored and referenced.
        client.post(
            f"/v1/sessions/{session_id}/questions/{question_id}/formula",
            json={"formula": "EF (eat_1)"},
            headers=JSON_HEADERS,
        )
        stored = client.get(f"/v1/witnesses/{witness_id}", headers=AUTH_HEADERS)
        assert stored.status_code == 200
        assert stored.json()["referenced_by"] == [old_run["run_id"]]
        assert len(witness_store) == 1
        journal = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS).json()
        assert any(e["event"] == "formula_edited" for e in journal)

    def test_edit_unknown_question_or_session_is_404_and_bad_body_is_422(
        self, tmp_path: Path
    ) -> None:
        client, _, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)

        response = client.post(
            "/v1/sessions/ses_nope/questions/q_x/formula",
            json={"formula": "EF (eat_1)"},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"

        response = client.post(
            f"/v1/sessions/{session_id}/questions/q_nope/formula",
            json={"formula": "EF (eat_1)"},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "question_not_found"

        question_id, _ = first_question_id(client, session_id)
        response = client.post(
            f"/v1/sessions/{session_id}/questions/{question_id}/formula",
            json={"formula": ""},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_formula_edit"


class TestReadingEndpoint:
    def test_reading_of_a_scripted_formula_is_served_with_llm_provenance(
        self, tmp_path: Path
    ) -> None:
        client, _, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        response = client.get(
            f"/v1/sessions/{session_id}/reading?formula=EF%20(eat_1)", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["formula"] == "EF (eat_1)"
        assert payload["reading"]["provenance"] == "llm"
        assert payload["reading"]["degraded"] is False

    def test_unscripted_formula_degrades_explicitly(self, tmp_path: Path) -> None:
        client, _, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        response = client.get(
            f"/v1/sessions/{session_id}/reading?formula=AG%20ZZZ", headers=AUTH_HEADERS
        )
        assert response.status_code == 200
        assert response.json()["reading"]["degraded"] is True

    def test_missing_formula_param_is_422(self, tmp_path: Path) -> None:
        client, _, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.get(f"/v1/sessions/{session_id}/reading", headers=AUTH_HEADERS)
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "missing_formula"


class TestFormulaEditLogNarration:
    """The formula-edit log events are part of the narration contract (ADR-0005)."""

    def test_edit_and_reading_events_are_pinned(self, tmp_path: Path) -> None:
        client, _, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question_id, _ = first_question_id(client, session_id)
        with structlog.testing.capture_logs() as logs:
            FormulaEditStore(directory=tmp_path / "formula_edits")  # reload event
            response = client.post(
                f"/v1/sessions/{session_id}/questions/{question_id}/formula",
                json={"formula": "EF (eat_1)"},
                headers=JSON_HEADERS,
            )
            assert response.status_code == 201
            reading = client.get(
                f"/v1/sessions/{session_id}/reading?formula=EF%20(eat_1)", headers=AUTH_HEADERS
            )
            assert reading.status_code == 200
        events = [event["event"] for event in logs]
        assert "formula_edit_store_reloaded" in events
        assert "formula_edited" in events
        assert "api_question_formula_edited" in events
        assert "api_reading_read" in events


class TestFormulaEditStoreProperty:
    @pytest.mark.property
    @settings(deadline=None)
    @given(
        edits=st.lists(
            st.text(alphabet="ABCDEF ()", min_size=1, max_size=12), min_size=1, max_size=8
        )
    )
    def test_property_latest_formula_is_the_last_edit_or_the_original(
        self, edits: list[str]
    ) -> None:
        """Append-only store: latest_formula returns the last edit's formula, else the original."""
        store = FormulaEditStore(directory=Path(tempfile.mkdtemp()))
        session_id = SessionId("ses_prop")
        question_id = QuestionId("q_prop")
        original = "AG (eat_1)"
        assert (
            store.latest_formula(session_id=session_id, question_id=question_id, original=original)
            == original
        )
        for formula in edits:
            store.edit(
                session_id=session_id,
                question_id=question_id,
                old_formula=store.latest_formula(
                    session_id=session_id, question_id=question_id, original=original
                ),
                new_formula=formula,
            )
        assert (
            store.latest_formula(session_id=session_id, question_id=question_id, original=original)
            == edits[-1]
        )
        # Append-only: the full history is preserved in order.
        assert [r.new_formula for r in store.history(session_id=session_id)] == edits
