"""Tests for the integral run archive and the session budget (issue #7, spec §12.4-5).

Every run is archived with its command, tool version, timeout, input hash
and measured cost (wall-clock tool time around the runner call - never an
estimate). No API or code path deletes a run: the guard test parses the
store modules and the API answers 405 to DELETE. The session budget cap is
configurable; a run that does not fit in the remaining budget is refused
with an explicit 429 carrying the remaining budget.
"""

import ast
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog.testing
from pydantic import SecretStr

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.checks import (
    CheckCompleted,
    CheckFailed,
    CheckOutcome,
    CheckQuestion,
    ToolVersion,
    run_key,
)
from petrichor.engines.archive import RunArchive
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ModelId
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore

if TYPE_CHECKING:
    import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PROBLEM_JSON_CONTENT_TYPE = "application/problem+json"
TERMINAL_POLL_SECONDS = 5.0
POLL_INTERVAL_SECONDS = 0.01
PRODUCT_ROOT = Path(__file__).parents[1] / "src" / "petrichor"
DELETION_VERBS = ("delete", "remove", "purge", "drop", "clear")

DONE_OUTPUT = {
    "verdict": "violated",
    "witness": None,
    "cost": {"tool_seconds": 0.1, "states_explored": 4},
    "technique": "dummy",
}
SLEEP_TOOL_VERSION = ToolVersion("1.0.0-sleep")
SLEEP_COMMAND = ["sleep-tool", "check"]


def philosophes3_pnml() -> str:
    """The Philosophes-3 P/T fixture."""
    return (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")


class SleepingRunner:
    """A runner that consumes real wall-clock tool time before answering."""

    tool_version = SLEEP_TOOL_VERSION

    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[CheckQuestion] = []

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Sleep the configured tool time, then complete."""
        self.calls.append(question)
        time.sleep(self.delay_seconds)
        return CheckCompleted(output=DONE_OUTPUT, command=SLEEP_COMMAND)


class SleepingFailureRunner:
    """A runner that consumes tool time and then fails explicitly."""

    tool_version = SLEEP_TOOL_VERSION

    def __init__(self, *, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Sleep the configured tool time, then fail with an explicit kind."""
        time.sleep(self.delay_seconds)
        return CheckFailed(kind="engine_error", detail="engine answered 500", command=SLEEP_COMMAND)


class BlockingRunner:
    """A runner that blocks on a gate, to observe the ``running`` state."""

    tool_version = SLEEP_TOOL_VERSION

    def __init__(self) -> None:
        self.gate = threading.Event()

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Wait for the gate, then complete."""
        self.gate.wait(timeout=TERMINAL_POLL_SECONDS)
        return CheckCompleted(output=DONE_OUTPUT, command=SLEEP_COMMAND)


def build_client(
    runner: object,
    *,
    timeout_seconds: int = 30,
    session_budget_seconds: float | None = None,
) -> tuple[httpx.Client, str, RunStore]:
    """Wire the WSGI app with a known key, the Philosophes-3 model and a runner."""
    settings_kwargs: dict[str, object] = {
        "api_key": SecretStr(TEST_API_KEY),
        "check_timeout_seconds": timeout_seconds,
    }
    if session_budget_seconds is not None:
        settings_kwargs["session_budget_seconds"] = session_budget_seconds
    settings = ApiSettings(**settings_kwargs)  # type: ignore[arg-type]
    store = ModelStore()
    model, _ = store.save(model=import_pnml(text=philosophes3_pnml()))
    run_store = RunStore()
    app = create_app(settings=settings, store=store, run_store=run_store, runner=runner)  # type: ignore[arg-type]
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


class TestRunArchive:
    def test_terminal_run_archives_command_tool_version_timeout_hash_and_measured_cost(
        self,
    ) -> None:
        runner = SleepingRunner(delay_seconds=0.25)
        client, model_id, _ = build_client(runner, timeout_seconds=30)
        response = post_check(client, "deadlock", check_payload(model_id))
        assert response.status_code == 202
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "done"
        assert run["command"] == SLEEP_COMMAND
        assert run["tool_version"] == str(SLEEP_TOOL_VERSION)
        assert run["timeout_seconds"] == 30
        assert run["input_hash"].startswith("sha256:")
        assert isinstance(run["measured_seconds"], float)

    def test_running_run_carries_the_known_archive_fields(self) -> None:
        runner = BlockingRunner()
        client, model_id, _ = build_client(runner, timeout_seconds=12)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = response.json()
        assert run["status"] == "running"
        assert run["timeout_seconds"] == 12
        assert run["tool_version"] == str(SLEEP_TOOL_VERSION)
        assert run["input_hash"].startswith("sha256:")
        assert run["command"] is None
        assert run["measured_seconds"] is None
        runner.gate.set()
        wait_terminal(client, run["run_id"])

    def test_cost_is_measured_tool_time_not_an_estimate(self) -> None:
        runner = SleepingRunner(delay_seconds=0.3)
        client, model_id, _ = build_client(runner, timeout_seconds=30)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"])
        measured = run["measured_seconds"]
        assert measured >= 0.3
        assert measured < 5.0

    def test_error_run_also_archives_command_and_measured_cost(self) -> None:
        runner = SleepingFailureRunner(delay_seconds=0.2)
        client, model_id, _ = build_client(runner, timeout_seconds=30)
        response = post_check(client, "deadlock", check_payload(model_id))
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "error"
        assert run["error"]["kind"] == "engine_error"
        assert run["command"] == SLEEP_COMMAND
        assert run["measured_seconds"] >= 0.2


class TestNoDeletePath:
    def test_store_modules_define_no_deletion_function(self) -> None:
        module_paths = [PRODUCT_ROOT / "run_store.py", PRODUCT_ROOT / "engines" / "archive.py"]
        for module_path in module_paths:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            offenders = [
                f"{module_path}:{node.name}"
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(verb in node.name.lower() for verb in DELETION_VERBS)
            ]
            assert offenders == []

    def test_store_classes_expose_no_deletion_method(self) -> None:
        for cls in (RunStore, RunArchive):
            offenders = [
                name for name in dir(cls) if any(verb in name.lower() for verb in DELETION_VERBS)
            ]
            assert offenders == []

    def test_delete_on_run_is_405_and_the_run_survives(self) -> None:
        runner = SleepingRunner(delay_seconds=0.05)
        client, model_id, run_store = build_client(runner)
        response = post_check(client, "deadlock", check_payload(model_id))
        run_id = response.json()["run_id"]
        final = wait_terminal(client, run_id)
        assert final["status"] == "done"
        deleted = client.delete(f"/v1/runs/{run_id}", headers=AUTH_HEADERS)
        assert deleted.status_code == 405
        assert deleted.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        assert deleted.json()["extensions"]["reason"] == "method_not_allowed"
        polled = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS)
        assert polled.status_code == 200
        assert polled.json()["status"] == "done"
        assert len(run_store) == 1


class TestSessionBudget:
    def test_budget_cap_is_configurable_and_defaults_to_3600(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PETRICHOR_SESSION_BUDGET_SECONDS", raising=False)
        assert ApiSettings(api_key=SecretStr(TEST_API_KEY)).session_budget_seconds == 3600.0
        monkeypatch.setenv("PETRICHOR_SESSION_BUDGET_SECONDS", "120.5")
        assert ApiSettings(api_key=SecretStr(TEST_API_KEY)).session_budget_seconds == 120.5

    def test_run_that_does_not_fit_the_remaining_budget_is_refused_with_the_rest(self) -> None:
        runner = SleepingRunner(delay_seconds=0.05)
        # timeout 1s + watchdog grace 5s = 6s of worst-case tool time; cap 5s refuses at once.
        client, model_id, run_store = build_client(
            runner, timeout_seconds=1, session_budget_seconds=5.0
        )
        with structlog.testing.capture_logs() as events:
            response = post_check(client, "deadlock", check_payload(model_id))
        assert response.status_code == 429
        assert response.headers["Content-Type"] == PROBLEM_JSON_CONTENT_TYPE
        problem = response.json()
        assert problem["extensions"]["reason"] == "session_budget_exceeded"
        assert problem["extensions"]["remaining_budget_seconds"] == 5.0
        assert problem["extensions"]["session_budget_seconds"] == 5.0
        assert problem["extensions"]["consumed_seconds"] == 0.0
        assert problem["extensions"]["timeout_seconds"] == 1
        assert len(run_store) == 0
        assert runner.calls == []
        refused = [e for e in events if e["event"] == "api_check_budget_refused"]
        assert len(refused) == 1
        assert refused[0]["remaining_budget_seconds"] == 5.0

    def test_budget_spent_by_measured_runs_refuses_the_next_run_with_the_rest(self) -> None:
        runner = SleepingRunner(delay_seconds=0.2)
        # cap 6s covers exactly one run (timeout 1s + grace 5s); once its cost is
        # measured, the remaining budget no longer covers another run.
        client, model_id, run_store = build_client(
            runner, timeout_seconds=1, session_budget_seconds=6.0
        )
        first = post_check(client, "deadlock", check_payload(model_id))
        assert first.status_code == 202
        final = wait_terminal(client, first.json()["run_id"])
        consumed = final["measured_seconds"]
        assert consumed >= 0.2
        second = post_check(
            client, "deadlock", check_payload(model_id, formula="EF DEADLOCK and EF (fork_1)")
        )
        assert second.status_code == 429
        extensions = second.json()["extensions"]
        assert extensions["reason"] == "session_budget_exceeded"
        assert extensions["consumed_seconds"] == consumed
        assert extensions["remaining_budget_seconds"] == 6.0 - consumed
        assert 0.0 < extensions["remaining_budget_seconds"] < 6.0
        assert len(run_store) == 1

    def test_idempotent_replay_is_not_refused_by_the_budget(self) -> None:
        runner = SleepingRunner(delay_seconds=0.05)
        client, model_id, run_store = build_client(runner)
        payload = check_payload(model_id)
        first = post_check(client, "deadlock", payload)
        assert first.status_code == 202
        wait_terminal(client, first.json()["run_id"])
        # A second app over the same store with an exhausted budget: the replay
        # of the known (model, question) still returns the existing run.
        starved_settings = ApiSettings(
            api_key=SecretStr(TEST_API_KEY),
            check_timeout_seconds=30,
            session_budget_seconds=5.0,
        )
        store = ModelStore()
        store.save(model=import_pnml(text=philosophes3_pnml()))
        starved_app = create_app(
            settings=starved_settings, store=store, run_store=run_store, runner=runner
        )
        starved_client = httpx.Client(
            transport=httpx.WSGITransport(app=starved_app), base_url="http://testserver"
        )
        replay = post_check(starved_client, "deadlock", payload)
        assert replay.status_code == 202
        assert replay.json()["run_id"] == first.json()["run_id"]
        assert len(run_store) == 1

    def test_budget_refusal_is_narrated_with_named_fields(self) -> None:
        runner = SleepingRunner(delay_seconds=0.05)
        client, model_id, _ = build_client(runner, timeout_seconds=1, session_budget_seconds=1.0)
        with structlog.testing.capture_logs() as events:
            response = post_check(client, "deadlock", check_payload(model_id))
        assert response.status_code == 429
        refused = [e for e in events if e["event"] == "api_check_budget_refused"]
        assert len(refused) == 1
        assert refused[0]["session_budget_seconds"] == 1.0
        assert refused[0]["consumed_seconds"] == 0.0
        assert refused[0]["timeout_seconds"] == 1
        assert "remaining_budget_seconds" in refused[0]


class TestRunStoreAccounting:
    def test_consumed_seconds_sums_only_measured_terminal_runs(self) -> None:
        run_store = RunStore()
        question = CheckQuestion(family="deadlock", kind="deadlock", formula="EF DEADLOCK")
        key = run_key(model_id=ModelId("mdl_x"), question=question)
        run, _ = run_store.create_or_get(
            key=key,
            family="deadlock",
            kind="deadlock",
            formula="EF DEADLOCK",
            model_id=ModelId("mdl_x"),
            timeout_seconds=30,
            tool_version=SLEEP_TOOL_VERSION,
        )
        assert run_store.consumed_seconds() == 0.0
        run_store.mark_done(
            run_id=run.run_id,
            output=DONE_OUTPUT,
            command=SLEEP_COMMAND,
            measured_seconds=1.5,
        )
        assert run_store.consumed_seconds() == 1.5
        other_key = run_key(
            model_id=ModelId("mdl_x"),
            question=CheckQuestion(family="deadlock", kind="deadlock", formula="EF (fork_1)"),
        )
        other, _ = run_store.create_or_get(
            key=other_key,
            family="deadlock",
            kind="deadlock",
            formula="EF (fork_1)",
            model_id=ModelId("mdl_x"),
            timeout_seconds=30,
            tool_version=SLEEP_TOOL_VERSION,
        )
        assert run_store.consumed_seconds() == 1.5
        run_store.mark_error(
            run_id=other.run_id,
            kind="timeout",
            detail="watchdog",
            command=[],
            measured_seconds=2.5,
        )
        assert run_store.consumed_seconds() == 4.0
        stored = run_store.get(run_id=run.run_id)
        assert stored is not None
        assert stored.measured_seconds == 1.5
        assert stored.command == SLEEP_COMMAND
