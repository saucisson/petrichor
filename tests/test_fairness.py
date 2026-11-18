"""Tests for the fairness parameter and repairs_run_id (spec §12.3, §14.7, §17.5; issue #35).

- ``fairness: none|weak|strong`` per run; each parameter set is a distinct
  run, billed as such.
- Runs with a non-``none`` fairness are labeled ``semantics: tool-specific``
  when no wired tool supports it natively (P29 follow-up: the LoLA adapter
  is not wired yet) - never inter-tool comparable.
- A liveness clause links to its repairing run via ``repairs_run_id``; without
  a fairness-capable tool, the degradation « non vérifiable sous équité » is
  explicit, never masked.
"""

import time
from pathlib import Path

import httpx
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import CheckCompleted, CheckQuestion, ToolVersion
from petrichor.convergence import ConvergenceStore
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
DONE_OUTPUT = {
    "verdict": "violated",
    "witness": None,
    "cost": {"tool_seconds": 0.1, "states_explored": 4},
    "technique": "dummy",
}


class FakeRunner:
    """A runner that completes immediately with a fixed output."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Serve the fixed done output."""
        return CheckCompleted(output=DONE_OUTPUT, command=["fake-tool"])


def build_client(tmp_path: Path) -> tuple[httpx.Client, RunStore, ProfileArbitrator]:
    """Wire the app with every session-scoped store and a fake runner."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    run_store = RunStore()
    session_store = SessionStore(directory=tmp_path / "sessions")
    arbitrator = ProfileArbitrator()
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=FakeRunner(),
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
        convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, run_store, arbitrator


def import_model(client: httpx.Client) -> str:
    """Import Philosophes-3 and return its model id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    return response.json()["id"]


def create_session(client: httpx.Client, model_id: str) -> str:
    """Create a session with an arbitrated profile (the contract needs one)."""
    response = client.post("/v1/sessions", json={"model_id": model_id}, headers=JSON_HEADERS)
    assert response.status_code == 201
    return response.json()["session_id"]


def propose_profile(arbitrator: ProfileArbitrator, client: httpx.Client, session_id: str) -> None:
    """Propose and accept the eat entry so the contract generates questions."""
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
                ProfileEntry(
                    id="global",
                    elements="fork_1..3",
                    role="motif global",
                    conf=0.9,
                    evidence="le reseau dans son ensemble",
                ),
            )
        ),
    )
    for entry_id, role in (("eat", "section critique"), ("global", "motif global")):
        response = client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            json={"entry_id": entry_id, "accepted": True, "chosen_role": role},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 200


def post_check(
    client: httpx.Client, model_id: str, family: str, formula: str, fairness: str, session_id: str
) -> httpx.Response:
    """POST one check with a fairness parameter, charged to a session."""
    return client.post(
        f"/v1/checks/{family}",
        json={
            "model_id": model_id,
            "kind": "liveness.unconditional" if family == "liveness" else "deadlock",
            "formula": formula,
            "session_id": session_id,
            "fairness": fairness,
        },
        headers=JSON_HEADERS,
    )


def wait_terminal(client: httpx.Client, run_id: str, timeout: float = 5.0) -> dict:
    """Poll the run until it leaves the running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class TestFairnessParameter:
    def test_each_fairness_value_is_a_distinct_run(self, tmp_path: Path) -> None:
        client, run_store, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        none_run = post_check(client, model_id, "liveness", "AG AF (eat_1)", "none", session_id)
        strong_run = post_check(client, model_id, "liveness", "AG AF (eat_1)", "strong", session_id)
        assert none_run.status_code == 202
        assert strong_run.status_code == 202
        assert none_run.json()["run_id"] != strong_run.json()["run_id"]
        assert len(run_store) == 2

    def test_fairness_run_is_labeled_tool_specific_and_not_supported(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = post_check(client, model_id, "liveness", "AG AF (eat_1)", "weak", session_id)
        run = wait_terminal(client, response.json()["run_id"])
        assert run["fairness"] == "weak"
        assert run["semantics"] == "tool-specific"
        assert run["output"]["semantics"] == "tool-specific"
        assert run["output"]["fairness_supported"] is False

    def test_none_fairness_run_carries_no_tool_specific_label(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = post_check(client, model_id, "liveness", "AG AF (eat_1)", "none", session_id)
        run = wait_terminal(client, response.json()["run_id"])
        assert run["fairness"] == "none"
        assert run["semantics"] is None
        assert "semantics" not in run["output"]

    def test_invalid_fairness_is_422(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        response = client.post(
            "/v1/checks/liveness",
            json={
                "model_id": model_id,
                "kind": "liveness.unconditional",
                "formula": "AG AF (eat_1)",
                "session_id": session_id,
                "fairness": "absolute",
            },
            headers=JSON_HEADERS,
        )
        assert response.status_code == 422


class TestRepairsRunId:
    def test_liveness_clause_links_to_its_fairness_run(self, tmp_path: Path) -> None:
        client, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_profile(arbitrator, client, session_id)

        contract = client.get(
            f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS
        ).json()
        liveness = next(q for q in contract["questions"] if q["family"] == "liveness")
        formula = liveness["formula"]
        assert liveness["repairs_run_id"] is None

        response = post_check(client, model_id, "liveness", formula, "strong", session_id)
        run = wait_terminal(client, response.json()["run_id"])

        contract = client.get(
            f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS
        ).json()
        clause = next(
            q for q in contract["questions"] if q["question_id"] == liveness["question_id"]
        )
        assert clause["repairs_run_id"] == run["run_id"]
        assert clause["fairness"] == "strong"
        # No wired tool supports fairness natively: explicit degradation.
        assert clause["fairness_status"] == "unverifiable-under-fairness"

        markdown = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS).text
        assert f"réparée par `{run['run_id']}`" in markdown
        assert "Non vérifiable sous équité par l'outillage actuel" in markdown

    def test_non_liveness_clause_never_carries_repairs_run_id(self, tmp_path: Path) -> None:
        client, _, arbitrator = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        propose_profile(arbitrator, client, session_id)
        response = post_check(client, model_id, "deadlock", "EF DEADLOCK", "strong", session_id)
        wait_terminal(client, response.json()["run_id"])
        contract = client.get(
            f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS
        ).json()
        deadlock = next(q for q in contract["questions"] if q["family"] == "deadlock")
        assert deadlock["repairs_run_id"] is None
        assert deadlock["fairness"] == "none"
