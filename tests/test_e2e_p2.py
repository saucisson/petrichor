"""E2E P2 scenario on the REAL engines: verifypn + dummy (spec §11, issue #39).

Marked ``@engine``: runs in CI inside the real docker compose containers.
Full flow: amorçage → élicitation → checks par famille (two real tools per MC
question, cross-verified) → convergence → synthèse (contrat + dossiers de
preuve) → budget mesuré et consigné. The second tool is the dummy engine
(verdict ``unknown`` by design): the ITS-Tools image cannot build (its pinned
tag is a p2 dump, not a source checkout - follow-up, see
docs/spikes/2026-11-18-p39-mock-vs-real-e2e.md).
"""

import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import structlog
from pydantic import SecretStr
from test_e2e_p1 import MiniStaticResolver

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import CheckCompleted, CheckOutcome, CheckQuestion
from petrichor.convergence import ConvergenceStore
from petrichor.engines.api_runner import VerifypnCheckRunner
from petrichor.engines.archive import RunArchive
from petrichor.engines.contract import DUMMY_ENGINE_NAME, DUMMY_ENGINE_VERSION, EngineQuestion
from petrichor.engines.driver import DEFAULT_DUMMY_HOST_PORT, ComposeEngineDriver
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.question_edits import FormulaEditStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore
from petrichor.witness import WitnessStore

if TYPE_CHECKING:
    from petrichor.checks import CheckRunner

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}

SESSION_BUDGET_SECONDS = 600.0


def sample_profile() -> SemanticProfile:
    """A profile with a converging pair (eat / eat_b on symmetry-indexed places)."""
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
                id="eat_b",
                elements="eat_2..3",
                role="section critique",
                conf=0.9,
                evidence="meme role sous symetrie",
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


def build_app(
    tmp_path: Path,
    runner: CheckRunner,
    *,
    store: ModelStore,
    run_store: RunStore,
    session_store: SessionStore,
    arbitrator: ProfileArbitrator,
) -> httpx.Client:
    """Wire the full app around one real-engine runner, sharing the stores."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=runner,
        session_store=session_store,
        arbitrator=arbitrator,
        static_resolver=MiniStaticResolver(),
        arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
        convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
        witness_store=WitnessStore(directory=tmp_path / "witnesses"),
        llm_provider=DeterministicMockProvider(),
        edit_store=FormulaEditStore(directory=tmp_path / "formula_edits"),
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


class _DummyRunner:
    """A CheckRunner on the dummy compose service (verdict unknown by design)."""

    tool_version = DUMMY_ENGINE_VERSION

    def __init__(self, *, archive: RunArchive) -> None:
        self._driver = ComposeEngineDriver(
            archive=archive,
            host_port=DEFAULT_DUMMY_HOST_PORT,
            service_name="dummy",
            engine_name=DUMMY_ENGINE_NAME,
            engine_version=DUMMY_ENGINE_VERSION,
        )

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Run on the dummy engine."""
        record = self._driver.run(
            question=EngineQuestion(
                family=question.family,
                formula=question.formula,
                pattern=question.kind,
                parameters=question.parameters,
            ),
            timeout_seconds=timeout_seconds or 30,
        )
        return CheckCompleted(output=record.output.model_dump(mode="json"), command=record.command)


def wait_terminal(*, client: httpx.Client, run_id: str, timeout: float = 120.0) -> dict:
    """Poll the run until it leaves the running state (real engines are slow)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


def _import_model_and_create_session(*, client: httpx.Client) -> tuple[str, str]:
    """Amorçage: import the model, create the session with its budget cap."""
    imported = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert imported.status_code == 201
    created = client.post(
        "/v1/sessions",
        json={"model_id": imported.json()["id"], "budget_seconds": SESSION_BUDGET_SECONDS},
        headers=JSON_HEADERS,
    )
    assert created.status_code == 201
    return imported.json()["id"], created.json()["session_id"]


def _arbitrate_profile(
    *, client: httpx.Client, session_id: str, arbitrator: ProfileArbitrator
) -> list[dict]:
    """Élicitation: propose + arbitrate the profile, return the contract questions."""
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    for entry_id, chosen_role in (
        ("eat", "section critique"),
        ("eat_b", "section critique"),
        ("fork", "ressource partagée"),
    ):
        response = client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            json={"entry_id": entry_id, "accepted": True, "chosen_role": chosen_role},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 200
    contract = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS).json()
    questions = contract["questions"]
    assert questions, "the arbitrated profile must generate questions"
    return questions


def _run_cross_verified_checks(
    *,
    verifypn_client: httpx.Client,
    dummy_client: httpx.Client,
    model_id: str,
    session_id: str,
    questions: list[dict],
) -> set[str]:
    """Checks par famille, cross-vérifiés sur les DEUX moteurs réels."""
    mc_questions = [q for q in questions if q["triage"] == "MC"]
    assert mc_questions, "the scenario must produce MC questions"
    expected_verdicts = {"verifypn": ("satisfied", "violated"), "dummy": ("unknown",)}
    families_seen: set[str] = set()
    for question in mc_questions[:4]:
        families_seen.add(question["family"])
        for client, tool in ((verifypn_client, "verifypn"), (dummy_client, "dummy")):
            posted = client.post(
                f"/v1/checks/{question['family']}",
                json={
                    "model_id": model_id,
                    "kind": question["kind"],
                    "formula": question["formula"],
                    "session_id": session_id,
                    "parameters": {"tool": tool},
                },
                headers=JSON_HEADERS,
            )
            assert posted.status_code == 202
            run = wait_terminal(client=client, run_id=posted.json()["run_id"])
            assert run["status"] == "done", f"{tool} run failed: {run.get('error')}"
            assert run["output"]["verdict"] in expected_verdicts[tool]
    assert len(families_seen) >= 2, "the scenario must cover several check families"
    return families_seen


def _fuse_converging_pair(*, client: httpx.Client, session_id: str, questions: list[dict]) -> str:
    """Convergence: fuse the converging pair (human-only fusion), return the covered id."""
    with_hint = [q for q in questions if q.get("convergence_hint")]
    assert with_hint, "the profile must generate a converging pair"
    covered_candidate = with_hint[0]
    surviving = next(
        q for q in questions if q["question_id"] == covered_candidate["convergence_hint"]
    )
    fused = client.post(
        f"/v1/sessions/{session_id}/convergences",
        json={
            "question_id": covered_candidate["question_id"],
            "proposition_id": surviving["question_id"],
        },
        headers=JSON_HEADERS,
    )
    assert fused.status_code == 201
    return covered_candidate["question_id"]


def _assert_synthesis(*, client: httpx.Client, session_id: str, covered_id: str) -> None:
    """Synthèse: contract with proof dossiers, cross-verification, covered clause, Markdown."""
    final = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS).json()
    assert final["proof_dossiers"], "every proven clause must have a proof dossier"
    cross_statuses = {d["cross_status"] for d in final["proof_dossiers"]}
    assert cross_statuses <= {"verified_by_n", "controversial", "unverified"}
    covered = next(q for q in final["questions"] if q["question_id"] == covered_id)
    assert covered["status"] == "covered"
    markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
    assert "## Dossiers de preuve" in markdown
    assert "couverte par" in markdown


def _assert_budget_consigne(
    *, client: httpx.Client, session_id: str, expected_runs: int, families: set[str]
) -> None:
    """Budget mesuré et consigné (spec §12.4): consumed > 0, within the cap, event pinned."""
    with structlog.testing.capture_logs() as logs:
        budget = client.get(f"/v1/sessions/{session_id}/budget", headers=AUTH_HEADERS).json()
        assert budget["budget_seconds"] == SESSION_BUDGET_SECONDS
        assert budget["consumed_seconds"] > 0.0
        assert budget["remaining_seconds"] >= 0.0
        assert budget["run_count"] >= expected_runs
        structlog.get_logger(__name__).info(
            "e2e_p2_budget_consigne",
            consumed_seconds=budget["consumed_seconds"],
            budget_seconds=budget["budget_seconds"],
            run_count=budget["run_count"],
            families=sorted(families),
        )
    entry = next(event for event in logs if event["event"] == "e2e_p2_budget_consigne")
    assert entry["consumed_seconds"] == budget["consumed_seconds"]
    assert entry["budget_seconds"] == SESSION_BUDGET_SECONDS
    assert entry["run_count"] == budget["run_count"]


@pytest.mark.engine
def test_e2e_p2_full_scenario_on_real_engines(tmp_path: Path) -> None:
    """The P2 jalon: the mockup wired on real cross-verified verdicts (issue #39)."""
    store = ModelStore()
    run_store = RunStore()
    session_store = SessionStore(directory=tmp_path / "sessions")
    arbitrator = ProfileArbitrator()
    verifypn_client = build_app(
        tmp_path,
        VerifypnCheckRunner(archive=RunArchive(directory=tmp_path / "runs-verifypn")),
        store=store,
        run_store=run_store,
        session_store=session_store,
        arbitrator=arbitrator,
    )
    # Second compose service for the cross-verification pairing: the dummy
    # engine (verdict "unknown" by design - it makes no semantic claim). The
    # ITS-Tools image is blocked: its Dockerfile tag v10052022 is a p2 dump,
    # not a source checkout (follow-up, see docs/spikes/2026-11-18-p39-mock-vs-real-e2e.md).
    dummy_client = build_app(
        tmp_path,
        _DummyRunner(archive=RunArchive(directory=tmp_path / "runs-dummy")),
        store=store,
        run_store=run_store,
        session_store=session_store,
        arbitrator=arbitrator,
    )
    model_id, session_id = _import_model_and_create_session(client=verifypn_client)
    questions = _arbitrate_profile(
        client=verifypn_client, session_id=session_id, arbitrator=arbitrator
    )
    families_seen = _run_cross_verified_checks(
        verifypn_client=verifypn_client,
        dummy_client=dummy_client,
        model_id=model_id,
        session_id=session_id,
        questions=questions,
    )
    covered_id = _fuse_converging_pair(
        client=verifypn_client, session_id=session_id, questions=questions
    )
    _assert_synthesis(client=verifypn_client, session_id=session_id, covered_id=covered_id)
    mc_count = len([q for q in questions if q["triage"] == "MC"][:4])
    _assert_budget_consigne(
        client=verifypn_client,
        session_id=session_id,
        expected_runs=2 * mc_count,
        families=families_seen,
    )
