"""Tests for the contract export API (spec §4.8, issue #17).

``GET /v1/sessions/{sid}/contract`` negotiates ``application/contract+json``
and ``text/markdown``: the export aggregates the arbitrated profile, the
proposed hypotheses, the questions by family with their status, the proof
links of arbitrated questions and the journal - and it is replayable from
the session state alone.
"""

from pathlib import Path
from typing import cast

import httpx
import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import (
    ArbitrationDecisionKind,
    ArbitrationRecord,
    ArbitrationStore,
)
from petrichor.checks import (
    FAMILY_PATTERNS,
    CheckFamilyName,
    CheckQuestion,
    KindPattern,
    RunId,
    ToolVersion,
    run_key,
)
from petrichor.contract import (
    ContractProof,
    RunLink,
    build_contract,
    cross_status_for_proofs,
    render_markdown,
)
from petrichor.elicitation import QuestionId, load_catalog
from petrichor.examinations import EXAMINATION_BY_KIND
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ModelId
from petrichor.pnml_import import import_pnml
from petrichor.profile import ArbitrationDecision, EntryId, ProfileArbitrator
from petrichor.run_store import RunAdmission, RunStore
from petrichor.session_store import JournalEntry, SessionId, SessionStore, StoredSession
from petrichor.triage import QuestionTriage, TriageKind

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
    family: CheckFamilyName,
    kind: KindPattern,
    formula: str,
    triage_kind: TriageKind,
    verdict: str,
    extra_output: dict[str, object] | None = None,
    finish: bool = True,
) -> str:
    """Admit one run on a question formula, attach it to the session, finish it."""
    question = CheckQuestion(family=family, kind=kind, formula=formula)
    admission = RunAdmission(
        key=run_key(model_id=ModelId(model_id), question=question),
        family=family,
        kind=kind,
        formula=formula,
        model_id=ModelId(model_id),
        timeout_seconds=30,
        tool_version=ToolVersion("mock"),
        session_budget_seconds=600.0,
        triage=QuestionTriage(kind=triage_kind, justification="preuve disponible"),
    )
    admitted = run_store.admit(admission=admission, reservation_seconds=0.0)
    run = admitted.run
    session_store.append_run(session_id=SessionId(session_id), run_id=run.run_id)
    if finish:
        run_store.mark_done(
            run_id=run.run_id,
            output={"verdict": verdict, **(extra_output or {})},
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

    def test_questions_carry_examination_and_safety_counter_variant(self, tmp_path: Path) -> None:
        """Each question names its MCC examination; safety adds the complement (issue #30)."""
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        assert document["questions"], "the arbitrated profile must generate questions"
        for question in document["questions"]:
            assert question["examination"] == EXAMINATION_BY_KIND[question["kind"]]
            if question["family"] == "safety":
                counter_variant = question["counter_variant"]
                assert counter_variant is not None
                assert counter_variant["examination"] == "ReachabilityCardinality"
                assert counter_variant["family"] == "reachability"
                assert counter_variant["formula"] == f"EF (not ({question['formula']}))"
            else:
                assert question["counter_variant"] is None

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
            family=cast("CheckFamilyName", question["family"]),
            kind=cast("KindPattern", question["kind"]),
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

    def test_cross_status_verified_by_n_and_per_tool_proof_fields(self, tmp_path: Path) -> None:
        """Concordant proofs expose verdict/technique/cost/version and verified_by_n (#31)."""
        client, session_store, run_store, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        model_id = client.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS).json()["model_id"]
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        question = document["questions"][0]
        family = cast("CheckFamilyName", question["family"])
        kind = cast("KindPattern", question["kind"])
        other_kind = next(candidate for candidate in FAMILY_PATTERNS[family] if candidate != kind)
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=family,
            kind=kind,
            formula=question["formula"],
            triage_kind="MC",
            verdict="safe",
            extra_output={"technique": "explicit", "cost": {"tool_seconds": 0.5}},
        )
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=family,
            kind=cast("KindPattern", other_kind),
            formula=question["formula"],
            triage_kind="MC",
            verdict="safe",
        )
        document = get_contract_json(client, session_id)
        exported = next(
            q for q in document["questions"] if q["question_id"] == question["question_id"]
        )
        assert exported["cross_status"] == "verified_by_n"
        assert exported["verified_by"] == 2
        assert len(exported["proofs"]) == 2
        proof = exported["proofs"][0]
        assert proof["tool_version"] == "mock"
        assert proof["technique"] == "explicit"
        assert proof["cost_tool_seconds"] == 0.5
        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert "vérifiée par 2 outils" in markdown

    def test_discordant_proofs_make_the_clause_controversial_with_both_camps(
        self, tmp_path: Path
    ) -> None:
        """Discordant done proofs → controversial, both camps shown, never resolved (#31)."""
        client, session_store, run_store, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        model_id = client.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS).json()["model_id"]
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        question = document["questions"][0]
        family = cast("CheckFamilyName", question["family"])
        kind = cast("KindPattern", question["kind"])
        other_kind = next(candidate for candidate in FAMILY_PATTERNS[family] if candidate != kind)
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=family,
            kind=kind,
            formula=question["formula"],
            triage_kind="MC",
            verdict="safe",
        )
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family=family,
            kind=cast("KindPattern", other_kind),
            formula=question["formula"],
            triage_kind="MC",
            verdict="violated",
        )
        # A still-running proof on the same formula must not render a third camp.
        admit_done_run(
            run_store=run_store,
            session_store=session_store,
            session_id=session_id,
            model_id=model_id,
            family="deadlock",
            kind="deadlock",
            formula=question["formula"],
            triage_kind="MC",
            verdict="safe",
            finish=False,
        )
        document = get_contract_json(client, session_id)
        exported = next(
            q for q in document["questions"] if q["question_id"] == question["question_id"]
        )
        assert exported["cross_status"] == "controversial"
        assert exported["verified_by"] == 0
        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert "controversée" in markdown
        assert "mock → safe" in markdown
        assert "mock → violated" in markdown
        assert "mock → None" not in markdown

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
        for unacceptable in ("image/png", "application/json", "text/*"):
            response = client.get(
                f"/v1/sessions/{session_id}/contract",
                headers={**AUTH_HEADERS, "Accept": unacceptable},
            )
            assert response.status_code == 406
            assert response.json()["extensions"]["reason"] == "not_acceptable"

    def test_contract_negotiation_honors_q_values(self, tmp_path: Path) -> None:
        client, _, _, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        cases = (
            ("text/markdown;q=0, application/contract+json", "application/contract+json"),
            ("application/contract+json, text/markdown", "application/contract+json"),
            ("text/markdown;q=0.5, application/contract+json;q=0.9", "application/contract+json"),
            ("application/contract+json;q=0.2, text/markdown;q=0.8", "text/markdown"),
            ("*/*", "application/contract+json"),
        )
        for accept, expected_content_type in cases:
            response = client.get(
                f"/v1/sessions/{session_id}/contract",
                headers={**AUTH_HEADERS, "Accept": accept},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(expected_content_type)

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
        built = next(e for e in logs if e["event"] == "contract_built")
        assert built["session_id"] == session_id
        assert built["questions"] == exported["questions"]
        rendered = next(e for e in logs if e["event"] == "contract_markdown_rendered")
        assert rendered["session_id"] == session_id
        assert rendered["bytes"] > 0


DECISIONS: list[ArbitrationDecisionKind] = ["accept", "refuse", "verify-first"]


class TestContractPropertyTier:
    @pytest.mark.property
    @settings(deadline=None, max_examples=50)
    @given(
        proofs=st.lists(
            st.builds(
                ContractProof,
                run_id=st.just(RunId("run_prop")),
                link=st.just(RunLink("/v1/runs/run_prop")),
                status=st.sampled_from(["running", "done", "error"]),
                triage=st.sampled_from(["statique", "MC", "dégradé"]),
                verdict=st.one_of(st.none(), st.sampled_from(["safe", "violated"])),
                tool_version=st.just(ToolVersion("mock")),
            ),
            max_size=6,
        )
    )
    def test_cross_status_for_proofs_invariants(self, proofs: list[ContractProof]) -> None:
        """verified_by counts exactly the agreeing done proofs; discordance is controversial."""
        status, verified_by = cross_status_for_proofs(proofs=tuple(proofs))
        done_verdicts = [
            proof.verdict
            for proof in proofs
            if proof.status == "done" and proof.verdict is not None
        ]
        if not done_verdicts:
            assert status == "unverified"
            assert verified_by == 0
        elif len(set(done_verdicts)) == 1:
            assert status == "verified_by_n"
            assert verified_by == len(done_verdicts)
        else:
            assert status == "controversial"
            assert verified_by == 0

    @pytest.mark.property
    @settings(deadline=None, max_examples=25)
    @given(
        accepted=st.lists(st.booleans(), min_size=1, max_size=3),
        decisions=st.lists(st.sampled_from(DECISIONS), min_size=0, max_size=8),
    )
    def test_build_and_render_are_deterministic(
        self, accepted: list[bool], decisions: list[ArbitrationDecisionKind]
    ) -> None:
        """The export is a pure function of the session state (spec §4.8, issue #17)."""
        model = import_pnml(text=PHILOSOPHES3_PNML)
        session_id = SessionId("ses_prop")
        session = StoredSession(
            session_id=session_id,
            model_id=model.id,
            created_at=1.0,
            updated_at=1.0,
        )
        arbitrator = ProfileArbitrator()
        state = arbitrator.propose(session_id=session_id, profile=sample_profile())
        for entry, accept in zip(sample_profile().entries, accepted, strict=False):
            state = arbitrator.arbitrate(
                session_id=session_id,
                decision=ArbitrationDecision(
                    entry_id=EntryId(entry.id),
                    accepted=accept,
                    chosen_role=entry.role,
                ),
            )
            assert state is not None
        history = [
            ArbitrationRecord(
                session_id=session_id,
                question_id=QuestionId(f"q_{index:012x}"),
                decision=decision,
                timestamp=1.0,
            )
            for index, decision in enumerate(decisions)
        ]
        journal = [
            JournalEntry(timestamp=1.0, event="session_created", detail={"model_id": str(model.id)})
        ]
        first = build_contract(
            session=session,
            profile_state=state,
            model=model,
            catalog=load_catalog(),
            arbitration_history=history,
            runs=[],
            journal=journal,
        )
        second = build_contract(
            session=session,
            profile_state=state,
            model=model,
            catalog=load_catalog(),
            arbitration_history=history,
            runs=[],
            journal=journal,
        )
        assert first.model_dump(mode="json") == second.model_dump(mode="json")
        assert render_markdown(document=first) == render_markdown(document=second)
