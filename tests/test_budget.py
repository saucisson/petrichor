"""Tests for the per-session MC budget (spec §12.4, issue #33).

The budget lives continuously per session: cap set at creation, consumed =
measured tool time of the session's terminal runs, refusal with an explicit
429 carrying the remaining budget - and NEVER a carry-over to another
session.
"""

import time
from pathlib import Path

import httpx
import structlog.testing
from pydantic import SecretStr

from conftest import fetch_ui_bundle
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

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
DONE_OUTPUT = {
    "verdict": "satisfied",
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


def build_client(
    tmp_path: Path, *, timeout_seconds: int = 30
) -> tuple[httpx.Client, RunStore, SessionStore]:
    """Wire the app with a session store, a run store and a fake runner."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY), check_timeout_seconds=timeout_seconds)
    store = ModelStore()
    run_store = RunStore()
    session_store = SessionStore(directory=tmp_path / "sessions")
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=FakeRunner(),
        session_store=session_store,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, run_store, session_store


def import_model(client: httpx.Client) -> str:
    """Import Philosophes-3 and return its model id."""
    response = client.post("/v1/models", content=PHILOSOPHES3_PNML, headers=PNML_HEADERS)
    assert response.status_code == 201
    return response.json()["id"]


def create_session(client: httpx.Client, model_id: str, **extra: object) -> str:
    """Create a session and return its id."""
    response = client.post(
        "/v1/sessions", json={"model_id": model_id, **extra}, headers=JSON_HEADERS
    )
    assert response.status_code == 201
    return response.json()["session_id"]


def post_check(
    client: httpx.Client, model_id: str, formula: str, session_id: str | None
) -> httpx.Response:
    """POST one deadlock check, optionally charged to a session."""
    payload: dict[str, object] = {"model_id": model_id, "kind": "deadlock", "formula": formula}
    if session_id is not None:
        payload["session_id"] = session_id
    return client.post("/v1/checks/deadlock", json=payload, headers=JSON_HEADERS)


def wait_terminal(client: httpx.Client, run_id: str, timeout: float = 5.0) -> dict:
    """Poll the run until it leaves the running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class TestSessionBudgetCap:
    def test_session_creation_accepts_a_custom_budget_and_defaults_to_3600(
        self, tmp_path: Path
    ) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        custom = create_session(client, model_id, budget_seconds=120.5)
        default = create_session(client, model_id)
        assert (
            client.get(f"/v1/sessions/{custom}", headers=AUTH_HEADERS).json()["budget_seconds"]
            == 120.5
        )
        assert (
            client.get(f"/v1/sessions/{default}", headers=AUTH_HEADERS).json()["budget_seconds"]
            == 3600.0
        )

    def test_invalid_budget_seconds_is_422(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        response = client.post(
            "/v1/sessions",
            json={"model_id": model_id, "budget_seconds": -3},
            headers=JSON_HEADERS,
        )
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_session_request"


class TestBudgetEndpoint:
    def test_budget_is_exposed_live_per_session(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id, budget_seconds=600.0)

        before = client.get(f"/v1/sessions/{session_id}/budget", headers=AUTH_HEADERS)
        assert before.status_code == 200
        payload = before.json()
        assert payload["budget_seconds"] == 600.0
        assert payload["consumed_seconds"] == 0.0
        assert payload["run_count"] == 0
        assert payload["remaining_seconds"] == 600.0

        run_id = post_check(client, model_id, "EF DEADLOCK", session_id).json()["run_id"]
        wait_terminal(client, run_id)

        after = client.get(f"/v1/sessions/{session_id}/budget", headers=AUTH_HEADERS).json()
        assert after["run_count"] == 1
        assert after["consumed_seconds"] > 0.0
        assert (
            after["remaining_seconds"]
            == 600.0 - after["consumed_seconds"] - after["reserved_seconds"]
        )

    def test_budget_unknown_session_is_404(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        response = client.get("/v1/sessions/ses_nope/budget", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"


class TestNoCarryOver:
    def test_one_sessions_consumption_never_touches_another_sessions_budget(
        self, tmp_path: Path
    ) -> None:
        """The §12.4 rule: no carry-over - each session has its own budget."""
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_a = create_session(client, model_id, budget_seconds=600.0)
        session_b = create_session(client, model_id, budget_seconds=600.0)

        run_a = post_check(client, model_id, "EF DEADLOCK", session_a).json()["run_id"]
        wait_terminal(client, run_a)
        budget_a = client.get(f"/v1/sessions/{session_a}/budget", headers=AUTH_HEADERS).json()
        assert budget_a["consumed_seconds"] > 0.0

        budget_b = client.get(f"/v1/sessions/{session_b}/budget", headers=AUTH_HEADERS).json()
        assert budget_b["consumed_seconds"] == 0.0
        assert budget_b["remaining_seconds"] == 600.0

    def test_run_charged_to_a_session_is_attached_to_it(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id)
        run_id = post_check(client, model_id, "EF DEADLOCK", session_id).json()["run_id"]
        wait_terminal(client, run_id)
        session = client.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS).json()
        assert session["run_ids"] == [run_id]
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        assert run["session_id"] == session_id

    def test_check_with_unknown_session_is_404(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        response = post_check(client, model_id, "EF DEADLOCK", "ses_nope")
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "session_not_found"


class TestBudgetRefusal:
    def test_run_over_the_session_cap_is_refused_with_the_remaining_budget(
        self, tmp_path: Path
    ) -> None:
        client, _, _ = build_client(tmp_path, timeout_seconds=30)
        model_id = import_model(client)
        session_id = create_session(client, model_id, budget_seconds=10.0)
        response = post_check(client, model_id, "EF DEADLOCK", session_id)
        assert response.status_code == 429
        payload = response.json()
        assert payload["extensions"]["reason"] == "session_budget_exceeded"
        assert payload["extensions"]["session_budget_seconds"] == 10.0
        assert payload["extensions"]["remaining_budget_seconds"] == 10.0

    def test_refusal_is_per_session_another_session_still_admits(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path, timeout_seconds=30)
        model_id = import_model(client)
        tiny = create_session(client, model_id, budget_seconds=10.0)
        roomy = create_session(client, model_id, budget_seconds=3600.0)
        assert post_check(client, model_id, "EF DEADLOCK", tiny).status_code == 429
        accepted = post_check(client, model_id, "EF DEADLOCK", roomy)
        assert accepted.status_code == 202
        wait_terminal(client, accepted.json()["run_id"])


class TestContractBudget:
    def test_contract_carries_the_session_budget_and_per_run_costs(self, tmp_path: Path) -> None:

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
        model_id = import_model(client)
        session_id = create_session(client, model_id, budget_seconds=600.0)
        # The contract requires an arbitrated profile (spec §4.8).
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
        client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            json={"entry_id": "eat", "accepted": True, "chosen_role": "section critique"},
            headers=JSON_HEADERS,
        )
        run_id = post_check(client, model_id, "EF DEADLOCK", session_id).json()["run_id"]
        wait_terminal(client, run_id)

        contract = client.get(
            f"/v1/sessions/{session_id}/contract",
            headers={**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"},
        ).json()
        assert contract["budget"]["budget_seconds"] == 600.0
        assert contract["budget"]["consumed_seconds"] > 0.0
        assert contract["budget"]["remaining_seconds"] < 600.0

        markdown = client.get(
            f"/v1/sessions/{session_id}/contract",
            headers={**AUTH_HEADERS, "Accept": "text/markdown"},
        ).text
        assert "## Budget MC (session)" in markdown
        assert "aucun report" in markdown

    def test_ui_bundle_displays_the_live_budget(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        script = fetch_ui_bundle(client)
        assert "/budget" in script
        assert "budget-line" in script
        assert "aucun report" in script


class TestBudgetLogNarration:
    """The budget endpoint's log event is part of the narration contract (ADR-0005)."""

    def test_api_session_budget_read_is_pinned(self, tmp_path: Path) -> None:
        client, _, _ = build_client(tmp_path)
        model_id = import_model(client)
        session_id = create_session(client, model_id, budget_seconds=600.0)
        with structlog.testing.capture_logs() as logs:
            response = client.get(f"/v1/sessions/{session_id}/budget", headers=AUTH_HEADERS)
            assert response.status_code == 200
        events = [event["event"] for event in logs]
        assert "api_session_budget_read" in events
        entry = next(event for event in logs if event["event"] == "api_session_budget_read")
        assert entry["budget_seconds"] == 600.0
        assert entry["remaining_seconds"] == 600.0
