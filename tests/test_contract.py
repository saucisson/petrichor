"""Tests for the contract export API (spec §4.8, issue #17).

``GET /v1/sessions/{sid}/contract`` negotiates ``application/contract+json``
and ``text/markdown``: the export aggregates the arbitrated profile, the
proposed hypotheses, the questions by family with their status, the proof
links of arbitrated questions and the journal - and it is replayable from
the session state alone.
"""

from pathlib import Path

import httpx
import structlog.testing
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import CheckQuestion, ToolVersion, run_key
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ModelId
from petrichor.profile import ProfileArbitrator
from petrichor.run_store import RunAdmission, RunStore
from petrichor.session_store import SessionId, SessionStore
from petrichor.triage import QuestionTriage

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}


def sample_profile() -> SemanticProfile:
    """A profile whose roles match the frozen catalog templates (spec §14.1)."""
    return SemanticProfile(
        entries=(
            ProfileEntry(
                id="think",
                elements="think_1..3",
                role="état d'automate",
                conf=0.62,
                evidence="le philosophe y attend",
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
                role="ressource partagée",
                conf=0.97,
                evidence="jeton initial unique",
            ),
        )
    )


def build_client(
    tmp_path: Path,
) -> tuple[httpx.Client, SessionStore, RunStore, ProfileArbitrator, ArbitrationStore]:
    """Build a test client with every session-scoped store bound to tmp_path."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    session_store = SessionStore(directory=tmp_path / "sessions")
    run_store = RunStore()
    arbitrator = ProfileArbitrator()
    arbitration_store = ArbitrationStore(directory=tmp_path / "arbitrations")
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=run_store,
        runner=None,
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=arbitration_store,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, session_store, run_store, arbitrator, arbitration_store


def create_session(client: httpx.Client) -> str:
    """Import Philosophes-3 and create one session; return its id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    created = client.post(
        "/v1/sessions", json={"model_id": response.json()["id"]}, headers=JSON_HEADERS
    )
    assert created.status_code == 201
    return created.json()["session_id"]


def propose_and_arbitrate(
    client: httpx.Client, arbitrator: ProfileArbitrator, session_id: str
) -> None:
    """Propose the sample profile and arbitrate it: fork/eat accepted, think refused."""
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    for entry_id, accepted, chosen_role in (
        ("think", False, "compteur"),
        ("eat", True, "section critique"),
        ("fork", True, "ressource partagée"),
    ):
        response = client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            json={"entry_id": entry_id, "accepted": accepted, "chosen_role": chosen_role},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 200


def admit_done_run(  # noqa: PLR0913 - one keyword per admitted field (ADR-0003)
    *,
    run_store: RunStore,
    session_store: SessionStore,
    session_id: str,
    model_id: str,
    family: str,
    kind: str,
    formula: str,
    triage_kind: str,
    verdict: str,
) -> str:
    """Admit one run on a question formula, attach it to the session, finish it."""
    question = CheckQuestion(family=family, kind=kind, formula=formula)  # type: ignore[arg-type]
    admission = RunAdmission(
        key=run_key(model_id=ModelId(model_id), question=question),
        family=family,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        formula=formula,
        model_id=ModelId(model_id),
        timeout_seconds=30,
        tool_version=ToolVersion("mock"),
        session_budget_seconds=600.0,
        triage=QuestionTriage(kind=triage_kind, justification="preuve disponible"),  # type: ignore[arg-type]
    )
    admitted = run_store.admit(admission=admission, reservation_seconds=0.0)
    run = admitted.run
    session_store.append_run(session_id=SessionId(session_id), run_id=run.run_id)
    run_store.mark_done(
        run_id=run.run_id,
        output={"verdict": verdict},
        command=["mock"],
        measured_seconds=0.1,
    )
    return str(run.run_id)


def get_contract_json(client: httpx.Client, session_id: str) -> dict:
    """GET the contract as contract+json and return the parsed document."""
    response = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/contract+json")
    return response.json()


class TestContractExport:
    def test_markdown_contains_profile_hypotheses_questions_and_journal(
        self, tmp_path: Path
    ) -> None:
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        assert document["questions"], "the arbitrated profile must generate questions"
        question_id = document["questions"][0]["question_id"]
        decision = client.post(
            f"/v1/sessions/{session_id}/arbitration-requests",
            json={"question_id": question_id, "decision": "verify-first"},
            headers=JSON_HEADERS,
        )
        assert decision.status_code == 201

        response = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        markdown = response.text
        assert f"# Contrat d'élicitation — session `{session_id}`" in markdown
        assert "## Profil arbitré" in markdown
        assert "## Hypothèses proposées" in markdown
        assert "## Questions par famille" in markdown
        assert "## Journal" in markdown
        assert "verify-first" in markdown
        assert "arbitration_decided" in markdown
        assert "section critique" in markdown
        assert "ressource partagée" in markdown

    def test_contract_json_negotiation_and_default(self, tmp_path: Path) -> None:
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        assert document["version"] == "1.0"
        assert document["session_id"] == session_id
        assert {entry["entry_id"] for entry in document["profile"]} == {"think", "eat", "fork"}
        accepted = {e["entry_id"]: e["accepted"] for e in document["profile"]}
        assert accepted == {"think": False, "eat": True, "fork": True}
        assert all(q["status"] == "pending" for q in document["questions"])
        assert any(entry["event"] == "session_created" for entry in document["journal"])
        # No Accept header defaults to the structured contract+json representation.
        default = client.get(f"/v1/sessions/{session_id}/contract", headers=AUTH_HEADERS)
        assert default.status_code == 200
        assert default.headers["content-type"].startswith("application/contract+json")
        assert default.json() == document

    def test_arbitrated_question_references_its_proof_runs_by_link(self, tmp_path: Path) -> None:
        client, session_store, run_store, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        model_id = client.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS).json()["model_id"]
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        question = document["questions"][0]
        run_id = admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=question["family"],
            kind=question["kind"],
            formula=question["formula"],
            triage_kind="statique",
            verdict="safe",
        )
        decision = client.post(
            f"/v1/sessions/{session_id}/arbitration-requests",
            json={"question_id": question["question_id"], "decision": "accept"},
            headers=JSON_HEADERS,
        )
        assert decision.status_code == 201

        document = get_contract_json(client, session_id)
        exported = next(
            q for q in document["questions"] if q["question_id"] == question["question_id"]
        )
        assert exported["status"] == "accept"
        assert len(exported["proofs"]) == 1
        proof = exported["proofs"][0]
        assert proof["run_id"] == run_id
        assert proof["link"] == f"/v1/runs/{run_id}"
        assert proof["status"] == "done"
        assert proof["triage"] == "statique"
        assert proof["verdict"] == "safe"

        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert f"[{run_id}](/v1/runs/{run_id})" in markdown

    def test_contract_is_replayable_from_session_state(self, tmp_path: Path) -> None:
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        question = document["questions"][0]
        decision = client.post(
            f"/v1/sessions/{session_id}/arbitration-requests",
            json={"question_id": question["question_id"], "decision": "refuse"},
            headers=JSON_HEADERS,
        )
        assert decision.status_code == 201
        first = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
        second = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
        assert first.status_code == second.status_code == 200
        assert first.content == second.content

        # Rebuild the persisted stores from the same directory and re-supply the
        # same profile input (the profile and the runs live in memory by design,
        # issues #6/#12): the export is byte-identical, so it is a pure function
        # of the session state.
        reloaded_client, _, _, reloaded_arbitrator, _ = build_client(tmp_path)
        reimported = reloaded_client.post(
            "/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS
        )
        assert reimported.status_code == 201
        propose_and_arbitrate(reloaded_client, reloaded_arbitrator, session_id)
        replayed = reloaded_client.get(
            f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS
        )
        assert replayed.status_code == 200
        assert replayed.content == first.content

    def test_contract_404_unknown_session(self, tmp_path: Path) -> None:
        client, _, _, _, _ = build_client(tmp_path)
        response = client.get("/v1/sessions/ses_nope/contract", headers=MARKDOWN_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"

    def test_contract_404_without_proposed_profile(self, tmp_path: Path) -> None:
        client, _, _, _, _ = build_client(tmp_path)
        session_id = create_session(client)
        response = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "profile_not_found"

    def test_contract_406_when_accept_admits_neither_representation(self, tmp_path: Path) -> None:
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        response = client.get(
            f"/v1/sessions/{session_id}/contract",
            headers={**AUTH_HEADERS, "Accept": "image/png"},
        )
        assert response.status_code == 406
        assert response.json()["extensions"]["reason"] == "not_acceptable"

    def test_contract_401_without_api_key(self, tmp_path: Path) -> None:
        client, _, _, _, _ = build_client(tmp_path)
        session_id = create_session(client)
        response = client.get(
            f"/v1/sessions/{session_id}/contract", headers={"Accept": "text/markdown"}
        )
        assert response.status_code == 401

    def test_contract_export_narration_is_pinned(self, tmp_path: Path) -> None:
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        with structlog.testing.capture_logs() as logs:
            response = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
        assert response.status_code == 200
        exported = next(e for e in logs if e["event"] == "api_contract_exported")
        assert exported["session_id"] == session_id
        assert exported["media"] == "markdown"
        assert exported["questions"] > 0
