"""Tests of the qualification exposure in the API (issue #22, spec §17.5).

``GET /v1/qualification`` serves the stored qualification status per
(adapter, family) to the orchestrator; a done run carries
``engine_qualified``, whether its engine is qualified for the family. An
unqualified adapter is never alone on a clause - the domain guard behind
this flag is pinned in ``test_engine_qualification.py``.
"""

import time
from pathlib import Path

import httpx
import structlog.testing
from pydantic import SecretStr

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.checks import (
    CheckCompleted,
    CheckFamilyName,
    CheckQuestion,
    QualificationState,
    ToolVersion,
)
from petrichor.model_store import ModelStore
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore

FIXTURES = Path(__file__).parent / "fixtures"
TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PROBLEM_JSON_CONTENT_TYPE = "application/problem+json"
TERMINAL_POLL_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.01

DONE_OUTPUT = {
    "verdict": "violated",
    "witness": None,
    "cost": {"tool_seconds": 0.1, "states_explored": 4},
    "technique": "fake",
}
FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")


class FakeRunner:
    """A runner that completes immediately with a fixed output."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Complete immediately."""
        return CheckCompleted(output=DONE_OUTPUT, command=["fake-tool"])


class FakeQualificationLookup:
    """A qualification lookup serving a fixed per-family state."""

    def __init__(self, *, qualified: bool) -> None:
        self.qualified = qualified

    def is_qualified(self, *, family: CheckFamilyName) -> bool:
        """Serve the fixed state for every family."""
        return self.qualified

    def snapshot(self) -> dict[str, dict[str, QualificationState]]:
        """Serve a fixed snapshot."""
        state: QualificationState = "qualified" if self.qualified else "unqualified"
        return {"fake": {"deadlock": state}}


def build_client(qualification: FakeQualificationLookup | None) -> tuple[httpx.Client, str]:
    """Wire the WSGI app with a known key, the Philosophes-3 model and a runner."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    model, _ = store.save(
        model=import_pnml(text=(FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8"))
    )
    app = create_app(
        settings=settings,
        store=store,
        run_store=RunStore(),
        runner=FakeRunner(),
        qualification=qualification,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, str(model.id)


def wait_terminal(client: httpx.Client, run_id: str) -> dict[str, object]:
    """Poll GET /v1/runs/{rid} until the run leaves the running state."""
    deadline = time.monotonic() + TERMINAL_POLL_SECONDS
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


def test_qualification_endpoint_without_lookup_returns_503() -> None:
    client, _ = build_client(None)
    response = client.get("/v1/qualification", headers=AUTH_HEADERS)
    assert response.status_code == 503
    assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
    assert response.json()["extensions"]["reason"] == "qualification_not_configured"


def test_qualification_endpoint_serves_the_snapshot() -> None:
    client, _ = build_client(FakeQualificationLookup(qualified=True))
    with structlog.testing.capture_logs() as events:
        response = client.get("/v1/qualification", headers=AUTH_HEADERS)
    assert response.status_code == 200
    assert response.json() == {"engines": {"fake": {"deadlock": "qualified"}}}
    reads = [event for event in events if event["event"] == "api_qualification_read"]
    assert len(reads) == 1
    assert reads[0]["engines"] == 1


def test_qualification_endpoint_rejects_post() -> None:
    client, _ = build_client(FakeQualificationLookup(qualified=True))
    response = client.post("/v1/qualification", json={}, headers=JSON_HEADERS)
    assert response.status_code == 405
    assert response.json()["extensions"]["reason"] == "method_not_allowed"


def test_qualification_endpoint_requires_the_api_key() -> None:
    client, _ = build_client(FakeQualificationLookup(qualified=True))
    response = client.get("/v1/qualification")
    assert response.status_code == 401


def test_done_run_carries_engine_qualified_true_when_qualified() -> None:
    client, model_id = build_client(FakeQualificationLookup(qualified=True))
    with structlog.testing.capture_logs() as events:
        response = client.post(
            "/v1/checks/deadlock",
            json={"model_id": model_id, "kind": "deadlock", "formula": "EF DEADLOCK"},
            headers=JSON_HEADERS,
        )
        run = wait_terminal(client, response.json()["run_id"])
    assert response.status_code == 202
    assert run["status"] == "done"
    assert run["engine_qualified"] is True
    narrated = [event for event in events if event["event"] == "check_run_engine_qualification"]
    assert len(narrated) == 1
    assert narrated[0]["engine_qualified"] is True
    assert narrated[0]["family"] == "deadlock"


def test_done_run_carries_engine_qualified_false_when_unqualified() -> None:
    client, model_id = build_client(FakeQualificationLookup(qualified=False))
    response = client.post(
        "/v1/checks/deadlock",
        json={"model_id": model_id, "kind": "deadlock", "formula": "EF DEADLOCK"},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 202
    run = wait_terminal(client, response.json()["run_id"])
    assert run["status"] == "done"
    assert run["engine_qualified"] is False


def test_done_run_without_lookup_carries_engine_qualified_null() -> None:
    client, model_id = build_client(None)
    response = client.post(
        "/v1/checks/deadlock",
        json={"model_id": model_id, "kind": "deadlock", "formula": "EF DEADLOCK"},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 202
    run = wait_terminal(client, response.json()["run_id"])
    assert run["status"] == "done"
    assert run["engine_qualified"] is None
