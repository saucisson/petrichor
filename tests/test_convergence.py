"""Tests for asymmetric convergence (spec §12.2, §14.5, §12.7; issue #34).

- Detection at generation: a question converging with an earlier one carries
  a ``convergence_hint`` (« cette question existe déjà, arbitrez-la une fois »).
- Fusion by Arbitrer alone: ``POST /convergences`` marks the question
  ``covered`` and it is never solicited again; automatic fusion is forbidden.
- Revision propagation: revising the arbitration of a question reopens the
  clauses of the questions it covers; the journal keeps old + new decisions.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from api_test_support import accepting_verifier
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.convergence import CONVERGENCE_HINT_TEXT, ConvergenceStore
from petrichor.elicitation import (
    GeneratedQuestion,
    QuestionId,
    convergence_hints,
    formulas_converge,
    instantiate_questions,
    load_catalog,
    questions_converge,
)
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.pnml_import import import_pnml
from petrichor.profile import GroupId, ProfileArbitrator
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore

if TYPE_CHECKING:
    from petrichor.checks import CheckFamilyName, KindPattern

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_PAT = "test-pat"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}


def sample_profile() -> SemanticProfile:
    """Two entries sharing the role « section critique » on symmetry-indexed places, plus a fork."""
    return SemanticProfile(
        entries=(
            ProfileEntry(
                id="eat",
                elements="eat_1",
                role="section critique",
                conf=0.95,
                evidence="eat precede de deux acquisitions",
            ),
            ProfileEntry(
                id="eat_b",
                elements="eat_2",
                role="section critique",
                conf=0.9,
                evidence="eat_2 est le meme role sous symetrie",
            ),
            ProfileEntry(
                id="fork",
                elements="fork_1",
                role="ressource partagée",
                conf=0.97,
                evidence="jeton initial unique",
            ),
        )
    )


def build_client(
    tmp_path: Path,
) -> tuple[httpx.Client, ProfileArbitrator, ConvergenceStore]:
    """Build a test client with every session-scoped store bound to tmp_path."""
    settings = ApiSettings()
    session_store = SessionStore(directory=tmp_path / "sessions")
    arbitrator = ProfileArbitrator()
    arbitration_store = ArbitrationStore(directory=tmp_path / "arbitrations")
    convergence_store = ConvergenceStore(directory=tmp_path / "convergences")
    app = create_app(
        token_verifier=accepting_verifier(),
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=None,
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=arbitration_store,
        convergence_store=convergence_store,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, arbitrator, convergence_store


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
    """Propose the sample profile and accept every entry with its proposed role."""
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    for entry in sample_profile().entries:
        response = client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            json={"entry_id": entry.id, "accepted": True, "chosen_role": entry.role},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 200


def get_contract_json(client: httpx.Client, session_id: str) -> dict:
    """GET the contract as contract+json and return the parsed document."""
    response = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS)
    assert response.status_code == 200
    return response.json()


def converging_pair(document: dict) -> tuple[dict, dict]:
    """The two liveness questions of the sample profile: (surviving, covered-candidate)."""
    liveness = [
        question
        for question in document["questions"]
        if question["family"] == "liveness" and question["kind"] == "liveness.conditional"
    ]
    assert len(liveness) == 2
    with_hint = next(q for q in liveness if q["convergence_hint"] is not None)
    surviving = next(q for q in liveness if q["question_id"] == with_hint["convergence_hint"])
    return surviving, with_hint


def post_fusion(
    client: httpx.Client, session_id: str, question_id: str, proposition_id: str
) -> httpx.Response:
    """POST one human fusion on a session."""
    return client.post(
        f"/v1/sessions/{session_id}/convergences",
        json={"question_id": question_id, "proposition_id": proposition_id},
        headers=JSON_HEADERS,
    )


def post_decision(
    client: httpx.Client, session_id: str, question_id: str, decision: str
) -> httpx.Response:
    """POST one arbitration decision on a question card."""
    return client.post(
        f"/v1/sessions/{session_id}/arbitration-requests",
        json={"question_id": question_id, "decision": decision},
        headers=JSON_HEADERS,
    )


def make_question(
    *, question_id: str, family: CheckFamilyName, kind: KindPattern, formula: str
) -> GeneratedQuestion:
    """Build one generated question for the convergence-rule unit tests."""
    return GeneratedQuestion(
        question_id=QuestionId(question_id),
        template_id="tpl_critsec_no",
        family=family,
        kind=kind,
        formula=formula,
        triage="MC",
        risk=3,
        cost=3,
        covers=(GroupId("grp_repas"),),
        scenario="s",
        guideword="No",
        role="section critique",
    )


class TestConvergenceRule:
    def test_identical_formula_after_normalization_converges(self) -> None:
        assert formulas_converge(
            first="AG (eat_1 == 1 -> AF eat_1 == 0)",
            second="AG  (eat_1 == 1  ->  AF eat_1 == 0)",
        )

    def test_same_targets_up_to_symmetry_indices_converge(self) -> None:
        assert formulas_converge(
            first="AG (eat_1 == 1 -> AF eat_1 == 0)",
            second="AG (eat_2 == 1 -> AF eat_2 == 0)",
        )

    def test_different_targets_do_not_converge(self) -> None:
        assert not formulas_converge(
            first="AG (eat_1 == 1 -> AF eat_1 == 0)",
            second="AG (fork_1 == 1 -> AF fork_1 == 0)",
        )

    def test_different_operators_do_not_converge(self) -> None:
        assert not formulas_converge(
            first="AG EF (eat_1 >= 1)",
            second="AG AF (eat_1 >= 1)",
        )

    def test_questions_converge_requires_same_family_and_kind(self) -> None:
        first = make_question(
            question_id="q_a",
            family="liveness",
            kind="liveness.conditional",
            formula="AG (eat_1 == 1 -> AF eat_1 == 0)",
        )
        same = make_question(
            question_id="q_b",
            family="liveness",
            kind="liveness.conditional",
            formula="AG (eat_2 == 1 -> AF eat_2 == 0)",
        )
        other_kind = make_question(
            question_id="q_c",
            family="liveness",
            kind="liveness.unconditional",
            formula="AG (eat_2 == 1 -> AF eat_2 == 0)",
        )
        assert questions_converge(first=first, second=same)
        assert not questions_converge(first=first, second=other_kind)

    def test_convergence_hints_map_each_question_to_the_first_earlier_one(self) -> None:
        first = make_question(
            question_id="q_a",
            family="liveness",
            kind="liveness.conditional",
            formula="AG (eat_1 == 1 -> AF eat_1 == 0)",
        )
        second = make_question(
            question_id="q_b",
            family="liveness",
            kind="liveness.conditional",
            formula="AG (eat_2 == 1 -> AF eat_2 == 0)",
        )
        third = make_question(
            question_id="q_c",
            family="safety",
            kind="safety.invariant",
            formula="AG (fork_1 <= 1)",
        )
        hints = convergence_hints(questions=(first, second, third))
        assert hints == {QuestionId("q_b"): QuestionId("q_a")}

    def test_instantiate_questions_emits_the_generation_time_hint(self) -> None:
        model = import_pnml(text=PHILOSOPHES3_PNML)
        generated = instantiate_questions(
            catalog=load_catalog(), profile=sample_profile(), model=model
        )
        liveness = [q for q in generated if q.kind == "liveness.conditional"]
        assert len(liveness) == 2
        with_hint = next(q for q in liveness if q.convergence_hint is not None)
        surviving = next(q for q in liveness if q.question_id == with_hint.convergence_hint)
        assert "eat_1" in surviving.formula
        assert "eat_2" in with_hint.formula


QUESTION_FORMULA = st.builds(
    lambda base, index: f"AG ({base}_{index} >= 1)",
    base=st.text(alphabet="abc", min_size=1, max_size=3),
    index=st.integers(min_value=1, max_value=9),
)


@pytest.mark.property
@settings(deadline=None)
@given(first=st.text(min_size=1, max_size=40), second=st.text(min_size=1, max_size=40))
def test_property_convergence_is_symmetric(first: str, second: str) -> None:
    """Convergence is a symmetric relation on formulas (spec §14.5)."""
    assert formulas_converge(first=first, second=second) == formulas_converge(
        first=second, second=first
    )


@pytest.mark.property
@settings(deadline=None)
@given(formula=st.text(alphabet="abefg ()", min_size=1, max_size=40))
def test_property_whitespace_normalization_never_breaks_convergence(formula: str) -> None:
    """A formula converges with its own whitespace-normalized form."""
    assert formulas_converge(first=formula, second=" ".join(formula.split()))


@pytest.mark.property
@settings(deadline=None)
@given(
    base=st.text(alphabet="abc", min_size=1, max_size=3),
    first_index=st.integers(min_value=1, max_value=9),
    second_index=st.integers(min_value=1, max_value=9),
)
def test_property_symmetry_index_substitution_converges(
    base: str, first_index: int, second_index: int
) -> None:
    """``eat_1`` ≡ ``eat_2``: same targets up to the symmetry index converge (spec §14.5)."""
    first = f"AG ({base}_{first_index} >= 1)"
    second = f"AG ({base}_{second_index} >= 1)"
    assert formulas_converge(first=first, second=second)
    assert formulas_converge(first=second, second=first)


@pytest.mark.property
@settings(deadline=None)
@given(
    questions=st.lists(
        st.builds(
            make_question,
            question_id=st.integers(min_value=0, max_value=10**6).map(lambda n: f"q_{n:06x}"),
            family=st.just("liveness"),
            kind=st.just("liveness.conditional"),
            formula=QUESTION_FORMULA,
        ),
        min_size=1,
        max_size=8,
        unique_by=lambda question: question.question_id,
    )
)
def test_property_hints_point_to_an_earlier_question_and_never_to_self(
    questions: list[GeneratedQuestion],
) -> None:
    """A hint always names an earlier generated question, never the question itself."""
    hints = convergence_hints(questions=tuple(questions))
    ids = [question.question_id for question in questions]
    for question_id, hint in hints.items():
        assert hint != question_id
        assert ids.index(hint) < ids.index(question_id)


class TestConvergenceStore:
    def test_fuse_history_and_covered_map(self, tmp_path: Path) -> None:
        store = ConvergenceStore(directory=tmp_path)
        session_id = SessionId("ses_1")
        store.fuse(
            session_id=session_id,
            question_id=QuestionId("q_b"),
            proposition_id=QuestionId("q_a"),
        )
        store.fuse(
            session_id=session_id,
            question_id=QuestionId("q_c"),
            proposition_id=QuestionId("q_a"),
        )
        store.fuse(
            session_id=SessionId("ses_2"),
            question_id=QuestionId("q_z"),
            proposition_id=QuestionId("q_y"),
        )
        assert len(store.history(session_id=session_id)) == 2
        covered = store.covered_map(session_id=session_id)
        assert set(covered) == {QuestionId("q_b"), QuestionId("q_c")}
        assert covered[QuestionId("q_b")].proposition_id == QuestionId("q_a")
        covered_by_a = store.covered_by_proposition(
            session_id=session_id, proposition_id=QuestionId("q_a")
        )
        assert len(covered_by_a) == 2

    def test_store_reloads_from_disk(self, tmp_path: Path) -> None:
        session_id = SessionId("ses_1")
        ConvergenceStore(directory=tmp_path).fuse(
            session_id=session_id,
            question_id=QuestionId("q_b"),
            proposition_id=QuestionId("q_a"),
        )
        reloaded = ConvergenceStore(directory=tmp_path)
        assert len(reloaded) == 1
        assert reloaded.covered_map(session_id=session_id)[
            QuestionId("q_b")
        ].proposition_id == QuestionId("q_a")


class TestConvergenceApi:
    def test_generation_hint_is_exposed_in_contract_json_and_markdown(self, tmp_path: Path) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        surviving, covered_candidate = converging_pair(document)
        assert covered_candidate["convergence_hint"] == surviving["question_id"]
        # Convergence alone never fuses: both questions stay pending (no automatic fusion).
        assert surviving["status"] == "pending"
        assert covered_candidate["status"] == "pending"
        assert covered_candidate["covered_by"] is None

        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert CONVERGENCE_HINT_TEXT in markdown
        assert f"`{surviving['question_id']}`" in markdown

    def test_fusion_marks_covered_and_removes_solicitation(self, tmp_path: Path) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        surviving, covered_candidate = converging_pair(document)

        response = post_fusion(
            client, session_id, covered_candidate["question_id"], surviving["question_id"]
        )
        assert response.status_code == 201
        record = response.json()["record"]
        assert record["question_id"] == covered_candidate["question_id"]
        assert record["proposition_id"] == surviving["question_id"]

        document = get_contract_json(client, session_id)
        by_id = {q["question_id"]: q for q in document["questions"]}
        assert by_id[covered_candidate["question_id"]]["status"] == "covered"
        assert by_id[covered_candidate["question_id"]]["covered_by"] == surviving["question_id"]
        assert by_id[surviving["question_id"]]["status"] == "pending"

        fusions = client.get(f"/v1/sessions/{session_id}/convergences", headers=AUTH_HEADERS)
        assert fusions.status_code == 200
        assert len(fusions.json()["convergences"]) == 1

        journal_response = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
        journal = journal_response.json()
        fused = [e for e in journal if e["event"] == "convergence_fused"]
        assert len(fused) == 1
        assert fused[0]["detail"]["question_id"] == covered_candidate["question_id"]

        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert "couverte par" in markdown
        assert "plus jamais resolicitée" in markdown

    def test_fusion_rejects_non_converging_unknown_and_self_pairs(self, tmp_path: Path) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        _surviving, covered_candidate = converging_pair(document)
        safety = next(q for q in document["questions"] if q["family"] == "safety")

        response = post_fusion(
            client, session_id, covered_candidate["question_id"], safety["question_id"]
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "convergence_rejected"

        response = post_fusion(client, session_id, covered_candidate["question_id"], "q_unknown")
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "convergence_rejected"

        response = post_fusion(
            client, session_id, covered_candidate["question_id"], covered_candidate["question_id"]
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "convergence_rejected"

        # Nothing was recorded: no automatic fusion, no partial state.
        fusions = client.get(f"/v1/sessions/{session_id}/convergences", headers=AUTH_HEADERS)
        assert fusions.json()["convergences"] == []

    def test_fusion_unknown_session_is_404_and_invalid_body_is_422(self, tmp_path: Path) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        response = post_fusion(client, "ses_nope", "q_a", "q_b")
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"

        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        response = client.post(
            f"/v1/sessions/{session_id}/convergences",
            json={"question_id": "q_a"},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_convergence_request"

    def test_revision_reopens_covered_clauses_and_journal_keeps_everything(
        self, tmp_path: Path
    ) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        surviving, covered_candidate = converging_pair(document)
        post_fusion(client, session_id, covered_candidate["question_id"], surviving["question_id"])

        # A first decision on the surviving question does NOT reopen the fusion.
        first = post_decision(client, session_id, surviving["question_id"], "verify-first")
        assert first.status_code == 201
        document = get_contract_json(client, session_id)
        by_id = {q["question_id"]: q for q in document["questions"]}
        assert by_id[covered_candidate["question_id"]]["status"] == "covered"

        # Revising the arbitration of the surviving question reopens the covered clause.
        second = post_decision(client, session_id, surviving["question_id"], "accept")
        assert second.status_code == 201
        assert second.json()["record"]["previous_decision"] == "verify-first"

        document = get_contract_json(client, session_id)
        by_id = {q["question_id"]: q for q in document["questions"]}
        reopened = by_id[covered_candidate["question_id"]]
        assert reopened["status"] == "pending"
        assert reopened["covered_by"] is None

        journal_response = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
        journal = journal_response.json()
        decisions = [e for e in journal if e["event"] == "arbitration_decided"]
        assert [e["detail"]["decision"] for e in decisions] == ["verify-first", "accept"]
        reopened_events = [e for e in journal if e["event"] == "convergence_reopened"]
        assert len(reopened_events) == 1
        assert reopened_events[0]["detail"]["proposition_id"] == surviving["question_id"]
        assert reopened_events[0]["detail"]["reopened"] == [covered_candidate["question_id"]]

    def test_reopened_question_falls_back_to_its_own_latest_decision(self, tmp_path: Path) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        surviving, covered_candidate = converging_pair(document)
        # The covered question carries its own decision before the fusion.
        assert (
            post_decision(
                client, session_id, covered_candidate["question_id"], "refuse"
            ).status_code
            == 201
        )
        post_fusion(client, session_id, covered_candidate["question_id"], surviving["question_id"])
        first = post_decision(client, session_id, surviving["question_id"], "verify-first")
        second = post_decision(client, session_id, surviving["question_id"], "accept")
        assert first.status_code == 201
        assert second.status_code == 201

        document = get_contract_json(client, session_id)
        by_id = {q["question_id"]: q for q in document["questions"]}
        assert by_id[covered_candidate["question_id"]]["status"] == "refuse"
        assert by_id[covered_candidate["question_id"]]["covered_by"] is None

    def test_convergence_store_is_wired_in_the_app(self, tmp_path: Path) -> None:
        client, _, convergence_store = build_client(tmp_path)
        session_id = create_session(client)
        assert len(convergence_store) == 0
        assert (
            client.get(f"/v1/sessions/{session_id}/convergences", headers=AUTH_HEADERS).status_code
            == 200
        )

    def test_repeated_fusion_is_idempotent_200_and_appends_nothing(self, tmp_path: Path) -> None:
        client, arbitrator, convergence_store = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        surviving, covered_candidate = converging_pair(document)

        first = post_fusion(
            client, session_id, covered_candidate["question_id"], surviving["question_id"]
        )
        assert first.status_code == 201
        second = post_fusion(
            client, session_id, covered_candidate["question_id"], surviving["question_id"]
        )
        assert second.status_code == 200
        assert second.json()["record"]["question_id"] == covered_candidate["question_id"]
        assert len(convergence_store) == 1

        journal_response = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
        fused = [e for e in journal_response.json() if e["event"] == "convergence_fused"]
        assert len(fused) == 1

    def test_get_convergences_paginates_with_limit_and_offset(self, tmp_path: Path) -> None:
        client, arbitrator, _ = build_client(tmp_path)
        session_id = create_session(client)
        propose_and_arbitrate(client, arbitrator, session_id)
        document = get_contract_json(client, session_id)
        surviving, covered_candidate = converging_pair(document)
        post_fusion(client, session_id, covered_candidate["question_id"], surviving["question_id"])

        page = client.get(
            f"/v1/sessions/{session_id}/convergences?limit=1&offset=0", headers=AUTH_HEADERS
        )
        assert page.status_code == 200
        payload = page.json()
        assert payload["total"] == 1
        assert payload["limit"] == 1
        assert payload["offset"] == 0
        assert len(payload["convergences"]) == 1

        empty = client.get(
            f"/v1/sessions/{session_id}/convergences?limit=10&offset=5", headers=AUTH_HEADERS
        )
        assert empty.json()["convergences"] == []
        assert empty.json()["total"] == 1

        invalid = client.get(
            f"/v1/sessions/{session_id}/convergences?limit=abc", headers=AUTH_HEADERS
        )
        assert invalid.status_code == 400
        assert invalid.json()["extensions"]["reason"] == "invalid_pagination"
