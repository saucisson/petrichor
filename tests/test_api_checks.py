"""Tests for the asynchronous checks API (issue #6, spec §5, §12.1).

``POST /v1/checks/{family}`` answers ``202`` + ``Location`` and is idempotent
by hash of (model, question); ``GET /v1/runs/{rid}`` follows the states
``running`` | ``done`` | ``error``. A ``kind`` pattern outside the family is
``422`` and never reinterpreted; without ``X-API-Key`` everything is ``401``;
engine errors and timeouts are explicit error states, never a default verdict.
"""

import io
import json
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import structlog.testing
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr

from petrichor.api import WSGIApp, create_app
from petrichor.api.settings import ApiSettings
from petrichor.checks import (
    CheckCompleted,
    CheckFailed,
    CheckOutcome,
    CheckQuestion,
    CheckRunner,
    ToolVersion,
    run_key,
)
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ModelId
from petrichor.pnml_import import import_pnml
from petrichor.run_store import BudgetRefused, RunAdmission, RunStore

if TYPE_CHECKING:
    from wsgiref.types import WSGIEnvironment

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
    "technique": "dummy",
}
FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
FAKE_COMMAND = ["fake-tool", "check", "--question", "EF DEADLOCK"]


def philosophes3_pnml() -> str:
    """The Philosophes-3 P/T fixture."""
    return (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")


class FakeRunner:
    """A controllable runner: serves queued outcomes, records every question."""

    tool_version = FAKE_TOOL_VERSION

    def __init__(self, outcomes: list[object] | None = None) -> None:
        self.outcomes = list(outcomes or [])
        self.calls: list[tuple[CheckQuestion, int]] = []

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Record the call and serve the next queued outcome (or a default done)."""
        self.calls.append((question, timeout_seconds))
        if not self.outcomes:
            return CheckCompleted(output=DONE_OUTPUT, command=FAKE_COMMAND)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, (CheckCompleted, CheckFailed))
        return outcome


class BlockingRunner:
    """A runner that blocks on a gate, to observe the ``running`` state."""

    tool_version = FAKE_TOOL_VERSION

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.calls: list[CheckQuestion] = []

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Wait for the gate, then complete."""
        self.calls.append(question)
        self.gate.wait(timeout=TERMINAL_POLL_SECONDS)
        return CheckCompleted(output=DONE_OUTPUT, command=FAKE_COMMAND)


class NeverRunner:
    """A runner that ignores its timeout on purpose, to exercise the watchdog."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Block far past the timeout, then complete (the watchdog must win)."""
        threading.Event().wait(timeout=60)
        return CheckCompleted(output=DONE_OUTPUT, command=FAKE_COMMAND)


def build_client(
    runner: CheckRunner | None, *, timeout_seconds: int | None = None
) -> tuple[httpx.Client, str, RunStore]:
    """Wire the WSGI app with a known key, the Philosophes-3 model and a runner."""
    if timeout_seconds is None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    else:
        settings = ApiSettings(
            api_key=SecretStr(TEST_API_KEY), check_timeout_seconds=timeout_seconds
        )
    store = ModelStore()
    model, _ = store.save(model=import_pnml(text=philosophes3_pnml()))
    run_store = RunStore()
    app = create_app(settings=settings, store=store, run_store=run_store, runner=runner)
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, str(model.id), run_store


def post_check(client: httpx.Client, family: str, payload: dict[str, object]) -> httpx.Response:
    """POST a JSON check request to /v1/checks/{family}."""
    return client.post(f"/v1/checks/{family}", json=payload, headers=JSON_HEADERS)


def check_payload(model_id: str, **overrides: object) -> dict[str, object]:
    """A valid deadlock check payload for the stored model."""
    payload: dict[str, object] = {
        "model_id": model_id,
        "kind": "deadlock",
        "formula": "EF DEADLOCK",
    }
    payload.update(overrides)
    return payload


def wait_terminal(
    client: httpx.Client, run_id: str, timeout: float = TERMINAL_POLL_SECONDS
) -> dict[str, object]:
    """Poll GET /v1/runs/{rid} until the run leaves the running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS)
        run = response.json()
        if run["status"] != "running":
            return run
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class TestAuthentication:
    def test_post_check_without_api_key_returns_401(self) -> None:
        client, model_id, _ = build_client(FakeRunner())
        response = client.post(
            "/v1/checks/deadlock",
            json=check_payload(model_id),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 401
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        assert response.json()["extensions"]["reason"] == "missing_or_invalid_api_key"

    def test_get_run_without_api_key_returns_401(self) -> None:
        client, _, _ = build_client(FakeRunner())
        response = client.get("/v1/runs/run_000000")
        assert response.status_code == 401
        assert response.json()["status"] == 401


class TestPostCheck:
    def test_post_returns_202_location_and_polls_running_then_done(self) -> None:
        runner = BlockingRunner()
        client, model_id, _ = build_client(runner)
        response = post_check(client, "deadlock", check_payload(model_id))
        assert response.status_code == 202
        run = response.json()
        assert run["status"] == "running"
        assert response.headers["Location"] == f"/v1/runs/{run['run_id']}"
        polled = client.get(response.headers["Location"], headers=AUTH_HEADERS)
        assert polled.status_code == 200
        assert polled.json()["status"] == "running"
        runner.gate.set()
        final = wait_terminal(client, run["run_id"])
        assert final["status"] == "done"
        assert final["output"] == DONE_OUTPUT
        assert final["error"] is None

    def test_replay_same_model_and_question_returns_the_same_run(self) -> None:
        runner = FakeRunner()
        client, model_id, run_store = build_client(runner)
        payload = check_payload(model_id)
        first = post_check(client, "deadlock", payload)
        second = post_check(client, "deadlock", payload)
        assert first.status_code == 202
        assert second.status_code == 202
        assert second.json()["run_id"] == first.json()["run_id"]
        assert second.headers["Location"] == first.headers["Location"]
        wait_terminal(client, first.json()["run_id"])
        assert len(run_store) == 1
        assert len(runner.calls) == 1

    def test_different_formula_creates_a_new_run(self) -> None:
        runner = FakeRunner()
        client, model_id, run_store = build_client(runner)
        first = post_check(client, "deadlock", check_payload(model_id))
        second = post_check(
            client, "deadlock", check_payload(model_id, formula="EF DEADLOCK and EF (fork_1)")
        )
        assert second.json()["run_id"] != first.json()["run_id"]
        wait_terminal(client, first.json()["run_id"])
        wait_terminal(client, second.json()["run_id"])
        assert len(run_store) == 2
        assert len(runner.calls) == 2

    def test_pattern_outside_family_returns_422_and_is_never_reinterpreted(self) -> None:
        runner = FakeRunner()
        client, model_id, run_store = build_client(runner)
        response = post_check(
            client, "safety", check_payload(model_id, kind="liveness.unconditional")
        )
        assert response.status_code == 422
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        assert response.json()["extensions"]["reason"] == "pattern_not_in_family"
        other = post_check(client, "deadlock", check_payload(model_id, kind="safety.invariant"))
        assert other.status_code == 422
        assert other.json()["extensions"]["reason"] == "pattern_not_in_family"
        assert len(run_store) == 0
        assert runner.calls == []

    def test_unknown_family_returns_404(self) -> None:
        client, model_id, _ = build_client(FakeRunner())
        response = post_check(client, "structural", check_payload(model_id))
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "check_family_not_found"

    def test_unknown_model_returns_404(self) -> None:
        client, _, run_store = build_client(FakeRunner())
        response = post_check(client, "deadlock", check_payload("mdl_missing"))
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "model_not_found"
        assert len(run_store) == 0

    def test_wrong_content_type_returns_415(self) -> None:
        client, model_id, _ = build_client(FakeRunner())
        response = client.post(
            "/v1/checks/deadlock",
            content=json.dumps(check_payload(model_id)),
            headers={**AUTH_HEADERS, "Content-Type": "application/pnml+xml"},
        )
        assert response.status_code == 415
        assert response.json()["extensions"]["reason"] == "unsupported_media_type"

    def test_malformed_json_returns_422(self) -> None:
        client, _, _ = build_client(FakeRunner())
        response = client.post("/v1/checks/deadlock", content=b"{not json", headers=JSON_HEADERS)
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_json"

    def test_missing_formula_returns_422(self) -> None:
        client, model_id, _ = build_client(FakeRunner())
        payload = check_payload(model_id)
        del payload["formula"]
        response = post_check(client, "deadlock", payload)
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_check_request"

    def test_extra_field_returns_422(self) -> None:
        client, model_id, _ = build_client(FakeRunner())
        response = post_check(client, "deadlock", check_payload(model_id, surprise=True))
        assert response.status_code == 422
        assert response.json()["extensions"]["reason"] == "invalid_check_request"

    def test_get_on_checks_returns_405(self) -> None:
        client, _, _ = build_client(FakeRunner())
        response = client.get("/v1/checks/deadlock", headers=AUTH_HEADERS)
        assert response.status_code == 405
        assert response.json()["extensions"]["reason"] == "method_not_allowed"

    def test_no_runner_wired_returns_503(self) -> None:
        client, model_id, run_store = build_client(None)
        response = post_check(client, "deadlock", check_payload(model_id))
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "runner_not_configured"
        assert len(run_store) == 0

    def test_accepted_check_is_narrated(self) -> None:
        runner = FakeRunner()
        client, model_id, _ = build_client(runner)
        with structlog.testing.capture_logs() as events:
            response = post_check(client, "deadlock", check_payload(model_id))
            run_id = response.json()["run_id"]
            wait_terminal(client, run_id)
        accepted = [e for e in events if e["event"] == "api_check_accepted"]
        assert len(accepted) == 1
        assert accepted[0]["run_id"] == run_id
        assert accepted[0]["created"] is True
        assert any(e["event"] == "check_run_started" and e["run_id"] == run_id for e in events)
        assert any(
            e["event"] == "check_run_finished" and e["run_id"] == run_id and e["status"] == "done"
            for e in events
        )

    def test_configured_timeout_is_passed_to_the_runner(self) -> None:
        runner = FakeRunner()
        client, model_id, _ = build_client(runner, timeout_seconds=7)
        response = post_check(client, "deadlock", check_payload(model_id))
        wait_terminal(client, response.json()["run_id"])
        assert runner.calls[0][1] == 7


class TestRunStates:
    def test_engine_error_is_an_explicit_error_state_never_a_verdict(self) -> None:
        runner = FakeRunner(
            [CheckFailed(kind="engine_error", detail="engine answered 500", command=FAKE_COMMAND)]
        )
        client, model_id, _ = build_client(runner)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "error"
        assert run["error"] == {"kind": "engine_error", "detail": "engine answered 500"}
        assert run["output"] is None
        assert "verdict" not in json.dumps(run)

    def test_timeout_is_an_explicit_error_state_never_a_verdict(self) -> None:
        runner = FakeRunner(
            [CheckFailed(kind="timeout", detail="no answer within 30s", command=FAKE_COMMAND)]
        )
        client, model_id, _ = build_client(runner)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "error"
        assert run["error"]["kind"] == "timeout"
        assert run["output"] is None

    def test_crashed_runner_is_an_explicit_error_state(self) -> None:
        runner = FakeRunner([RuntimeError("runner exploded")])
        client, model_id, _ = build_client(runner)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "error"
        assert run["error"]["kind"] == "engine_error"
        assert "runner exploded" in run["error"]["detail"]

    def test_watchdog_forces_timeout_when_runner_ignores_it(self) -> None:
        client, model_id, _ = build_client(NeverRunner(), timeout_seconds=1)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"], timeout=15.0)
        assert run["status"] == "error"
        assert run["error"]["kind"] == "timeout"
        assert "watchdog" in run["error"]["detail"]
        assert run["output"] is None

    def test_terminal_state_is_final(self) -> None:
        run_store = RunStore()
        question = CheckQuestion(family="deadlock", kind="deadlock", formula="EF DEADLOCK")
        key = run_key(model_id=ModelId("mdl_x"), question=question)
        admitted = run_store.admit(
            admission=RunAdmission(
                key=key,
                family="deadlock",
                kind="deadlock",
                formula="EF DEADLOCK",
                model_id=ModelId("mdl_x"),
                timeout_seconds=30,
                tool_version=FAKE_TOOL_VERSION,
                session_budget_seconds=3600.0,
            ),
            reservation_seconds=35.0,
        )
        assert not isinstance(admitted, BudgetRefused)
        run = admitted.run
        run_store.mark_done(
            run_id=run.run_id,
            output=DONE_OUTPUT,
            command=FAKE_COMMAND,
            measured_seconds=0.5,
        )
        run_store.mark_error(
            run_id=run.run_id,
            kind="timeout",
            detail="late watchdog",
            command=[],
            measured_seconds=1.0,
        )
        final = run_store.get(run_id=run.run_id)
        assert final is not None
        assert final.status == "done"
        assert final.output == DONE_OUTPUT
        assert final.error is None

    def test_get_unknown_run_returns_404(self) -> None:
        client, _, _ = build_client(FakeRunner())
        response = client.get("/v1/runs/run_deadbeef", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "run_not_found"

    def test_post_on_run_returns_405(self) -> None:
        client, _, _ = build_client(FakeRunner())
        response = client.post("/v1/runs/run_000000", json={}, headers=JSON_HEADERS)
        assert response.status_code == 405
        assert response.json()["extensions"]["reason"] == "method_not_allowed"

    def test_run_payload_shape_is_stable(self) -> None:
        runner = FakeRunner()
        client, model_id, _ = build_client(runner)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"])
        assert set(run) == {
            "run_id",
            "status",
            "family",
            "kind",
            "formula",
            "model_id",
            "input_hash",
            "timeout_seconds",
            "tool_version",
            "command",
            "measured_seconds",
            "output",
            "error",
        }
        assert run["family"] == "deadlock"
        assert run["kind"] == "deadlock"
        assert run["formula"] == "EF DEADLOCK"
        assert run["input_hash"].startswith("sha256:")
        assert run["timeout_seconds"] == 30
        assert run["tool_version"] == str(FAKE_TOOL_VERSION)
        assert run["command"] == FAKE_COMMAND
        assert isinstance(run["measured_seconds"], float)


class TestEdgeBranches:
    def test_invalid_content_length_returns_400(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        store = ModelStore()
        store.save(model=import_pnml(text=philosophes3_pnml()))
        app: WSGIApp = create_app(
            settings=settings, store=store, run_store=RunStore(), runner=FakeRunner()
        )
        environ: WSGIEnvironment = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/checks/deadlock",
            "CONTENT_LENGTH": "abc",
            "CONTENT_TYPE": "application/json",
            "HTTP_X_API_KEY": TEST_API_KEY,
            "wsgi.input": io.BytesIO(b"{}"),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body = b"".join(app(environ, start_response))
        assert str(captured["status"]).startswith("400")
        assert json.loads(body)["extensions"]["reason"] == "invalid_content_length"

    def test_non_utf8_check_body_returns_422(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        store = ModelStore()
        store.save(model=import_pnml(text=philosophes3_pnml()))
        app: WSGIApp = create_app(
            settings=settings, store=store, run_store=RunStore(), runner=FakeRunner()
        )
        environ: WSGIEnvironment = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/v1/checks/deadlock",
            "CONTENT_LENGTH": "3",
            "CONTENT_TYPE": "application/json",
            "HTTP_X_API_KEY": TEST_API_KEY,
            "wsgi.input": io.BytesIO(b"\xff\xfe\x00"),
        }
        captured: dict[str, object] = {}

        def start_response(status: str, headers: list[tuple[str, str]]) -> None:
            captured["status"] = status

        body = b"".join(app(environ, start_response))
        assert str(captured["status"]).startswith("422")
        assert json.loads(body)["extensions"]["reason"] == "invalid_json"


class TestRunKeyProperty:
    @pytest.mark.property
    @given(model_id=st.text(min_size=1), formula=st.text(min_size=1), kind=st.text(min_size=1))
    def test_run_key_is_deterministic_and_separates_questions(
        self, model_id: str, formula: str, kind: str
    ) -> None:
        question = CheckQuestion(family="deadlock", kind=kind, formula=formula)
        first = run_key(model_id=ModelId(model_id), question=question)
        second = run_key(model_id=ModelId(model_id), question=question)
        assert first == second
        other = run_key(
            model_id=ModelId(model_id),
            question=CheckQuestion(family="deadlock", kind=kind, formula=formula + "x"),
        )
        assert other != first

    @pytest.mark.property
    @given(model_id=st.text(min_size=1), formula=st.text(min_size=1))
    def test_run_store_is_idempotent_by_key(self, model_id: str, formula: str) -> None:
        question = CheckQuestion(family="safety", kind="safety.invariant", formula=formula)
        key = run_key(model_id=ModelId(model_id), question=question)
        run_store = RunStore()
        admission = RunAdmission(
            key=key,
            family="safety",
            kind="safety.invariant",
            formula=formula,
            model_id=ModelId(model_id),
            timeout_seconds=30,
            tool_version=FAKE_TOOL_VERSION,
            session_budget_seconds=3600.0,
        )
        first = run_store.admit(admission=admission, reservation_seconds=35.0)
        second = run_store.admit(admission=admission, reservation_seconds=35.0)
        assert not isinstance(first, BudgetRefused)
        assert not isinstance(second, BudgetRefused)
        assert first.created is True
        assert second.created is False
        assert second.run.run_id == first.run.run_id
        assert len(run_store) == 1


class TestSettings:
    def test_check_timeout_defaults_and_reads_from_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PETRICHOR_CHECK_TIMEOUT_SECONDS", raising=False)
        assert ApiSettings(api_key=SecretStr(TEST_API_KEY)).check_timeout_seconds == 30
        monkeypatch.setenv("PETRICHOR_CHECK_TIMEOUT_SECONDS", "12")
        assert ApiSettings(api_key=SecretStr(TEST_API_KEY)).check_timeout_seconds == 12
