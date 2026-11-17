"""End-to-end P1 milestone scenario (spec §11, §19.4, issue #19).

One complete elicitation session without any real MC run, entirely on the
deterministic mock + the embedded mini-engine: import → amorçage → profil →
arbitrages de profil → questions statiques résolues par le mini-moteur →
cartes arbitrées → contrat → export Markdown → journal.

Determinism (spec §19.4): the scenario is executed twice from scratch and the
journals are identical once the wall-clock timestamps and the random session
id are normalized; replaying the same session from the persisted stores
yields a byte-identical journal.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import (
    CheckCompleted,
    CheckOutcome,
    CheckQuestion,
    RunId,
    ToolVersion,
)
from petrichor.engines.mini_engine import (
    mini_capacity,
    non_vacuity_guard,
    resolve_static_payload,
)
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel, JsonValue
    from petrichor.triage import CapacityReport, NonVacuityGuard

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
FAKE_COMMAND = ["fake-tool", "check"]
DONE_OUTPUT = {"verdict": "violated", "witness": None, "cost": {}, "technique": "dummy"}


class MiniStaticResolver:
    """The StaticResolver protocol wired to the mini-engine adapters."""

    def capacity(self, *, model: CanonicalModel) -> CapacityReport:
        return mini_capacity(model=model)

    def resolve_static(
        self, *, model: CanonicalModel, question: CheckQuestion
    ) -> dict[str, JsonValue]:
        return resolve_static_payload(model=model, question=question)

    def non_vacuity_guard(
        self, *, model: CanonicalModel, family: str, kind: str, formula: str
    ) -> NonVacuityGuard:
        return non_vacuity_guard(model=model, family=family, kind=kind, formula=formula)


class FakeRunner:
    """A minimal runner serving one fixed completed outcome (mock, §19.4)."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        return CheckCompleted(output=DONE_OUTPUT, command=FAKE_COMMAND)


def sample_profile() -> SemanticProfile:
    """The Philosophes-3 profile: roles matching the frozen catalog templates."""
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
) -> tuple[httpx.Client, SessionStore, ProfileArbitrator]:
    """Wire the full app: mock runner, mini-engine static resolver, persisted stores."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    session_store = SessionStore(directory=tmp_path / "sessions")
    arbitrator = ProfileArbitrator()
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=FakeRunner(),
        session_store=session_store,
        arbitrator=arbitrator,
        static_resolver=MiniStaticResolver(),
        arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, session_store, arbitrator


def run_scenario(tmp_path: Path) -> dict[str, object]:
    """Run the whole P1 scenario once; return the journal, exports and session id."""
    client, session_store, arbitrator = build_client(tmp_path)

    # 1. Import the model (PNML → canonical, content-hash id).
    imported = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert imported.status_code == 201
    model_id = imported.json()["id"]

    # 2. Amorçage: create the session.
    created = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    # 3. Profil: propose (LLM layer, mocked here) then arbitrate every entry.
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

    # 4. Questions statiques: read the contract, resolve each static question
    #    with the embedded mini-engine (no MC run, cost zero).
    contract = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS).json()
    static_questions = [q for q in contract["questions"] if q["triage"] == "statique"]
    assert static_questions, "the scenario must produce static questions"
    triage_kinds: list[str] = []
    for question in static_questions:
        posted = client.post(
            f"/v1/checks/{question['family']}",
            json={"model_id": model_id, "kind": question["kind"], "formula": question["formula"]},
            headers=JSON_HEADERS,
        )
        assert posted.status_code == 202
        run_id = posted.headers["location"].rsplit("/", maxsplit=1)[-1]
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        assert run["status"] == "done"
        triage_kind = run["triage"]["kind"]
        triage_kinds.append(triage_kind)
        if triage_kind == "dégradé":
            # Outside the mini-engine fragment: explicit degradation, never a verdict (spec §14.2).
            assert "Preuve statique non trouvée" in run["triage"]["justification"]
        # Attach the proof run to the session (store-level, the API link lands later).
        session_store.append_run(session_id=SessionId(session_id), run_id=RunId(run_id))
    assert "statique" in triage_kinds, "the scenario must exercise the static path"

    # 5. Cartes + arbitrages: one decision per question, then a revision on the first.
    decisions = ["accept", "refuse", "verify-first"]
    for index, question in enumerate(contract["questions"]):
        response = client.post(
            f"/v1/sessions/{session_id}/arbitration-requests",
            json={"question_id": question["question_id"], "decision": decisions[index % 3]},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 201
    first_question = contract["questions"][0]["question_id"]
    revised = client.post(
        f"/v1/sessions/{session_id}/arbitration-requests",
        json={"question_id": first_question, "decision": "accept"},
        headers=JSON_HEADERS,
    )
    assert revised.status_code == 201
    assert revised.json()["record"]["previous_decision"] == decisions[0]

    # 6. Contrat + export Markdown + journal.
    markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
    assert markdown.status_code == 200
    final_contract = client.get(
        f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS
    ).json()
    journal = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS).json()
    return {
        "session_id": session_id,
        "journal": journal,
        "markdown": markdown.text,
        "contract": final_contract,
    }


def normalize_journal(journal: list[dict[str, object]], session_id: str) -> list[object]:
    """The journal content without the wall-clock timestamps and the random ids."""
    normalized = []
    for entry in journal:
        detail = {
            key: _normalize_id(value=value, session_id=session_id)
            for key, value in entry["detail"].items()  # type: ignore[union-attr]
        }
        normalized.append((entry["event"], detail))
    return normalized


def _normalize_id(*, value: object, session_id: str) -> object:
    """Replace the random session id and run ids by stable placeholders."""
    if value == session_id:
        return "ses_normalized"
    if isinstance(value, str) and value.startswith("run_"):
        return "run_normalized"
    return value


def normalize_markdown(markdown: str, session_id: str) -> str:
    """The export without the session id and the run ids (random per execution)."""
    normalized = markdown.replace(session_id, "ses_normalized")
    normalized = re.sub(r"run_[0-9a-f]+", "run_normalized", normalized)
    timestamp = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)"
    return re.sub(timestamp, "ts_normalized", normalized)


class TestE2eP1:
    def test_full_scenario_runs_on_mock_and_mini_engine(self, tmp_path: Path) -> None:
        result = run_scenario(tmp_path / "run1")
        journal = result["journal"]
        events = [entry["event"] for entry in journal]  # type: ignore[index]
        assert events[0] == "session_created"
        assert "arbitration_decided" in events
        assert "run_added" in events
        markdown = result["markdown"]
        assert isinstance(markdown, str)
        assert "## Questions par famille" in markdown
        assert "## Journal" in markdown
        contract = result["contract"]
        assert isinstance(contract, dict)
        statuses = {q["question_id"]: q["status"] for q in contract["questions"]}  # type: ignore[index]
        assert set(statuses.values()) <= {"accept", "refuse", "verify-first"}
        assert contract["questions"][0]["proofs"], "static runs are the proof links"  # type: ignore[index]

    def test_two_executions_produce_identical_journals(self, tmp_path: Path) -> None:
        first = run_scenario(tmp_path / "run1")
        second = run_scenario(tmp_path / "run2")
        assert normalize_journal(first["journal"], first["session_id"]) == normalize_journal(  # type: ignore[arg-type]
            second["journal"],
            second["session_id"],  # type: ignore[arg-type]
        )
        assert normalize_markdown(first["markdown"], first["session_id"]) == normalize_markdown(  # type: ignore[arg-type]
            second["markdown"],
            second["session_id"],  # type: ignore[arg-type]
        )

    def test_replay_from_persisted_state_gives_identical_journal(self, tmp_path: Path) -> None:
        result = run_scenario(tmp_path / "run1")
        # Rebuild the persisted stores from the same directory (process restart).
        client, _, _ = build_client(tmp_path / "run1")
        replayed = client.get(f"/v1/sessions/{result['session_id']}/journal", headers=AUTH_HEADERS)
        assert replayed.status_code == 200
        assert replayed.json() == result["journal"]
