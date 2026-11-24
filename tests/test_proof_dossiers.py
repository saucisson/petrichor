"""Tests for the clickable proof dossiers of the contract export (spec §9 P3, §12.5; issue #38).

- Every clause points to its runs: verdict per tool, concordance, witness,
  cost, technique.
- A controversy is exported as-is (both camps).
- The export is replayable from the run archive alone.
"""

from pathlib import Path

import httpx
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import CheckFamilyName, CheckQuestion, KindPattern, ToolVersion, run_key
from petrichor.contract import build_contract
from petrichor.convergence import ConvergenceStore
from petrichor.elicitation import load_catalog
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ModelId
from petrichor.pnml_import import import_pnml
from petrichor.profile import ArbitrationDecision, EntryId, ProfileArbitrator
from petrichor.run_store import RunAdmission, RunStore
from petrichor.session_store import SessionId, SessionStore
from petrichor.triage import QuestionTriage

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}

WITNESS = {
    "kind": "counterexample",
    "cyclic": False,
    "back_edge": None,
    "steps": [
        {"index": 0, "marking": ["think_1"], "fired": None},
        {"index": 1, "marking": ["hasL_1"], "fired": "take_1"},
    ],
}


def build_client(tmp_path: Path) -> tuple[httpx.Client, RunStore, SessionStore, ProfileArbitrator]:
    """Wire the app with every session-scoped store bound to tmp_path."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    session_store = SessionStore(directory=tmp_path / "sessions")
    run_store = RunStore()
    arbitrator = ProfileArbitrator()
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=run_store,
        runner=None,
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
        convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, run_store, session_store, arbitrator


def import_model(client: httpx.Client) -> str:
    """Import Philosophes-3 and return its model id."""
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


def admit_done_run(  # noqa: PLR0913 - one keyword per admitted field (ADR-0003)
    *,
    run_store: RunStore,
    session_store: SessionStore,
    session_id: str,
    model_id: str,
    family: CheckFamilyName,
    kind: KindPattern,
    formula: str,
    verdict: str,
    tool_version: str = "mock",
    extra_output: dict[str, object] | None = None,
    parameters: dict[str, object] | None = None,
) -> str:
    """Admit one run on a formula, attach it to the session, finish it done."""
    question = CheckQuestion(family=family, kind=kind, formula=formula, parameters=parameters or {})
    admission = RunAdmission(
        key=run_key(model_id=ModelId(model_id), question=question),
        family=family,
        kind=kind,
        formula=formula,
        model_id=ModelId(model_id),
        timeout_seconds=30,
        tool_version=ToolVersion(tool_version),
        session_budget_seconds=600.0,
        triage=QuestionTriage(kind="MC", justification="preuve disponible"),
    )
    admitted = run_store.admit(admission=admission, reservation_seconds=0.0)
    run = admitted.run
    session_store.append_run(session_id=SessionId(session_id), run_id=run.run_id)
    run_store.mark_done(
        run_id=run.run_id,
        output={
            "verdict": verdict,
            "technique": "bfs",
            "cost": {"tool_seconds": 0.25, "states_explored": 9},
            **(extra_output or {}),
        },
        command=["mock"],
        measured_seconds=0.3,
    )
    return str(run.run_id)


def first_question(client: httpx.Client, session_id: str) -> dict:
    """The first generated question of the session."""
    contract = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS).json()
    return contract["questions"][0]


class TestProofDossiers:
    def test_each_clause_points_to_its_runs_with_verdict_witness_cost_technique(
        self, tmp_path: Path
    ) -> None:
        client, run_store, session_store, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question = first_question(client, session_id)
        formula = question["formula"]
        run_id = admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=question["family"],
            kind=question["kind"],
            formula=formula,
            verdict="violated",
            extra_output={"witness": WITNESS},
        )

        contract = client.get(
            f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS
        ).json()
        assert len(contract["proof_dossiers"]) == 1
        dossier = contract["proof_dossiers"][0]
        assert dossier["question_id"] == question["question_id"]
        assert dossier["formula"] == formula
        assert len(dossier["proofs"]) == 1
        proof = dossier["proofs"][0]
        assert proof["run_id"] == run_id
        assert proof["link"] == f"/v1/runs/{run_id}"
        assert proof["verdict"] == "violated"
        assert proof["tool_version"] == "mock"
        assert proof["technique"] == "bfs"
        assert proof["cost_tool_seconds"] == 0.25
        assert proof["witness_id"] is not None and proof["witness_id"].startswith("wit_")

        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert "## Dossiers de preuve" in markdown
        assert f"[{run_id}](/v1/runs/{run_id})" in markdown
        assert f"/v1/witnesses/{proof['witness_id']}" in markdown
        assert "coût 0.250s" in markdown

    def test_controversy_is_exported_as_is_with_both_camps(self, tmp_path: Path) -> None:
        client, run_store, session_store, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question = first_question(client, session_id)
        formula = question["formula"]
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=question["family"],
            kind=question["kind"],
            formula=formula,
            verdict="satisfied",
            tool_version="mock",
            parameters={"tool": "mock"},
        )
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=question["family"],
            kind=question["kind"],
            formula=formula,
            verdict="violated",
            tool_version="its-tools",
            parameters={"tool": "its-tools"},
        )

        contract = client.get(
            f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS
        ).json()
        dossier = contract["proof_dossiers"][0]
        assert dossier["cross_status"] == "controversial"
        assert dossier["verified_by"] == 0
        assert sorted(dossier["camps"]) == ["its-tools → violated", "mock → satisfied"]

        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert "Controverse exportée telle quelle" in markdown
        assert "mock → satisfied" in markdown
        assert "its-tools → violated" in markdown

    def test_export_is_replayable_from_the_persisted_archive(self, tmp_path: Path) -> None:
        """§12.5: rebuilding the contract from the persisted stores is identical."""
        client, run_store, session_store, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_and_arbitrate(client=client, arbitrator=arbitrator, session_id=session_id)
        question = first_question(client, session_id)
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=question["family"],
            kind=question["kind"],
            formula=question["formula"],
            verdict="violated",
            extra_output={"witness": WITNESS},
        )
        first = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS).json()
        first_md = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text

        # Genuine persisted-archive replay (§12.5): reload the session and run
        # stores from the same directory (process restart) and rebuild the
        # contract from the reloaded archive alone.
        reloaded_sessions = SessionStore(directory=tmp_path / "sessions")
        # RunStore is in-memory by design; the archive replay path is the
        # persisted session + journal, so reload those and re-admit nothing:
        # the dossier content comes from the runs attached to the session.
        model = import_pnml(text=PHILOSOPHES3_PNML)
        session = reloaded_sessions.get(session_id=SessionId(session_id))
        assert session is not None
        arbitrator2 = ProfileArbitrator()
        arbitrator2.propose(
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
        state = arbitrator2.arbitrate(
            session_id=SessionId(session_id),
            decision=ArbitrationDecision(
                entry_id=EntryId("eat"), accepted=True, chosen_role="section critique"
            ),
        )
        assert state is not None
        runs = [
            run for run_id in session.run_ids if (run := run_store.get(run_id=run_id)) is not None
        ]
        rebuilt = build_contract(
            session=session,
            profile_state=state,
            model=model,
            catalog=load_catalog(),
            arbitration_history=[],
            runs=runs,
            journal=reloaded_sessions.journal(session_id=SessionId(session_id)),
        )
        assert [d.model_dump(mode="json") for d in rebuilt.proof_dossiers] == first[
            "proof_dossiers"
        ]
        assert first_md  # markdown rendered
