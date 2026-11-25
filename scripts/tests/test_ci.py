"""Tests for the CI wait verdicts scripts/ci.py (ADR-0006)."""

import argparse

import httpx
import pytest

from scripts import ci
from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
)


class _FakeResponse:
    """The response surface the wait driver touches: status, success, payload."""

    def __init__(self, *, status_code: int, payload: object) -> None:
        self.status_code = status_code
        self.payload = payload

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> object:
        return self.payload


def _routed_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pull_request_response: _FakeResponse,
    check_runs_response: _FakeResponse,
) -> httpx.Client:
    """A real httpx.Client whose get() routes URLs to canned responses."""

    def fake_get(url: str) -> _FakeResponse:
        if "check-runs" in url:
            return check_runs_response
        return pull_request_response

    http_client = httpx.Client()
    monkeypatch.setattr(http_client, "get", fake_get)
    return http_client


def _pull_request_response(*, status_code: int = 200) -> _FakeResponse:
    return _FakeResponse(status_code=status_code, payload={"head": {"sha": "abc123"}})


def _check_runs_response(
    *,
    status_code: int = 200,
    check_runs: list[dict[str, object]],
) -> _FakeResponse:
    return _FakeResponse(status_code=status_code, payload={"check_runs": check_runs})


def _completed_check_run(*, conclusion: str) -> dict[str, object]:
    return {"name": "quality gate", "status": "completed", "conclusion": conclusion}


def test_poll_once_reports_green(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every completed check run succeeded: green, with the head and the count."""
    http_client = _routed_client(
        monkeypatch,
        pull_request_response=_pull_request_response(),
        check_runs_response=_check_runs_response(
            check_runs=[_completed_check_run(conclusion="success")]
        ),
    )
    try:
        status, head_sha, detail = ci.poll_once(
            http_client=http_client,
            repository_slug="owner/repo",
            pull_request_number=7,
        )
    finally:
        http_client.close()
    assert status == ci.POLL_STATUS_GREEN
    assert head_sha == "abc123"
    assert detail == "1"


def test_poll_once_names_the_failed_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed non-success check run fails the head, named in the detail."""
    http_client = _routed_client(
        monkeypatch,
        pull_request_response=_pull_request_response(),
        check_runs_response=_check_runs_response(
            check_runs=[_completed_check_run(conclusion="failure")]
        ),
    )
    try:
        status, head_sha, detail = ci.poll_once(
            http_client=http_client,
            repository_slug="owner/repo",
            pull_request_number=7,
        )
    finally:
        http_client.close()
    assert status == ci.POLL_STATUS_FAILED
    assert head_sha == "abc123"
    assert detail == "quality gate"


def test_poll_once_reports_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check run still running keeps the head pending, detail counts it."""
    check_runs: list[dict[str, object]] = [
        {"name": "quality gate", "status": "in_progress", "conclusion": None}
    ]
    http_client = _routed_client(
        monkeypatch,
        pull_request_response=_pull_request_response(),
        check_runs_response=_check_runs_response(check_runs=check_runs),
    )
    try:
        status, head_sha, detail = ci.poll_once(
            http_client=http_client,
            repository_slug="owner/repo",
            pull_request_number=7,
        )
    finally:
        http_client.close()
    assert status == ci.POLL_STATUS_PENDING
    assert head_sha == "abc123"
    assert detail == "1 running, 0 green"


def test_poll_once_reports_no_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """A head without any check run is still waiting for CI to start."""
    http_client = _routed_client(
        monkeypatch,
        pull_request_response=_pull_request_response(),
        check_runs_response=_check_runs_response(check_runs=[]),
    )
    try:
        status, head_sha, detail = ci.poll_once(
            http_client=http_client,
            repository_slug="owner/repo",
            pull_request_number=7,
        )
    finally:
        http_client.close()
    assert status == ci.POLL_STATUS_NO_CHECKS
    assert head_sha == "abc123"
    assert detail == ""


def test_poll_once_reports_fetch_failed_when_check_runs_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pull request fetch worked, the check runs fetch was refused."""
    http_client = _routed_client(
        monkeypatch,
        pull_request_response=_pull_request_response(),
        check_runs_response=_check_runs_response(status_code=500, check_runs=[]),
    )
    try:
        status, head_sha, detail = ci.poll_once(
            http_client=http_client,
            repository_slug="owner/repo",
            pull_request_number=7,
        )
    finally:
        http_client.close()
    assert status == ci.POLL_STATUS_FETCH_FAILED
    assert head_sha == "abc123"
    assert detail == ""


def test_poll_once_exits_when_the_pull_request_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 pull request is a protocol failure, nothing to watch."""
    http_client = _routed_client(
        monkeypatch,
        pull_request_response=_pull_request_response(status_code=404),
        check_runs_response=_check_runs_response(check_runs=[]),
    )
    try:
        with pytest.raises(SystemExit) as exit_information:
            ci.poll_once(
                http_client=http_client,
                repository_slug="owner/repo",
                pull_request_number=7,
            )
    finally:
        http_client.close()
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_interpret_status_green_is_success() -> None:
    """Green maps to the success exit code."""
    assert ci.interpret_status(status=ci.POLL_STATUS_GREEN, detail="1") == EXIT_CODE_SUCCESS


def test_interpret_status_failed_is_criteria_unmet() -> None:
    """A failed CI maps to the criteria-unmet exit code."""
    exit_code = ci.interpret_status(status=ci.POLL_STATUS_FAILED, detail="quality gate")
    assert exit_code == EXIT_CODE_CRITERIA_UNMET


def test_interpret_status_pending_keeps_waiting() -> None:
    """Pending keeps the loop waiting: None, no exit code."""
    assert ci.interpret_status(status=ci.POLL_STATUS_PENDING, detail="1 running") is None


def test_interpret_status_no_checks_keeps_waiting() -> None:
    """No check run yet keeps the loop waiting: None, no exit code."""
    assert ci.interpret_status(status=ci.POLL_STATUS_NO_CHECKS, detail="") is None


def test_interpret_status_fetch_failed_keeps_waiting() -> None:
    """A transient API error keeps the loop waiting: None, no exit code."""
    assert ci.interpret_status(status=ci.POLL_STATUS_FETCH_FAILED, detail="") is None


def test_run_watch_returns_green(monkeypatch: pytest.MonkeyPatch) -> None:
    """A green poll ends the wait with the success exit code."""

    def fake_poll_once(**_unused: object) -> tuple[str, str, str]:
        return ci.POLL_STATUS_GREEN, "abc123", "1"

    monkeypatch.setattr(ci, "poll_once", fake_poll_once)
    exit_code = ci.run_watch(
        http_client=httpx.Client(),
        repository_slug="owner/repo",
        pull_request_number=7,
        interval_seconds=1,
        timeout_seconds=10,
    )
    assert exit_code == EXIT_CODE_SUCCESS


def test_run_watch_fails_closed_on_an_unreachable_pull_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable pull request past the timeout is a protocol failure."""

    def fake_poll_once(**_unused: object) -> tuple[str, str, str]:
        return ci.POLL_STATUS_FETCH_FAILED, "", ""

    monkeypatch.setattr(ci, "poll_once", fake_poll_once)
    exit_code = ci.run_watch(
        http_client=httpx.Client(),
        repository_slug="owner/repo",
        pull_request_number=7,
        interval_seconds=1,
        timeout_seconds=0,
    )
    assert exit_code == EXIT_CODE_PROTOCOL_FAILURE


def test_run_watch_times_out_on_a_pending_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending head past the timeout is the honest CI-failed stop."""

    def fake_poll_once(**_unused: object) -> tuple[str, str, str]:
        return ci.POLL_STATUS_PENDING, "abc123", "1 running, 0 green"

    monkeypatch.setattr(ci, "poll_once", fake_poll_once)
    exit_code = ci.run_watch(
        http_client=httpx.Client(),
        repository_slug="owner/repo",
        pull_request_number=7,
        interval_seconds=1,
        timeout_seconds=0,
    )
    assert exit_code == EXIT_CODE_CRITERIA_UNMET


def test_enforce_budget_arguments_refuses_a_zero_interval() -> None:
    """A zero poll interval is a protocol failure."""
    arguments = argparse.Namespace(interval_seconds=0, timeout_seconds=10)
    with pytest.raises(SystemExit) as exit_information:
        ci.enforce_budget_arguments(arguments=arguments)
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_enforce_budget_arguments_refuses_a_zero_timeout() -> None:
    """A zero timeout is a protocol failure."""
    arguments = argparse.Namespace(interval_seconds=10, timeout_seconds=0)
    with pytest.raises(SystemExit) as exit_information:
        ci.enforce_budget_arguments(arguments=arguments)
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_read_integer_setting_returns_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset variable yields the default."""
    monkeypatch.delenv("WAIT_CI_TEST_VARIABLE", raising=False)
    assert ci.read_integer_setting(variable_name="WAIT_CI_TEST_VARIABLE", default_value=30) == 30


def test_read_integer_setting_reads_the_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """A set integer variable overrides the default."""
    monkeypatch.setenv("WAIT_CI_TEST_VARIABLE", "45")
    assert ci.read_integer_setting(variable_name="WAIT_CI_TEST_VARIABLE", default_value=30) == 45


def test_read_integer_setting_fails_closed_on_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-integer variable is a protocol failure."""
    monkeypatch.setenv("WAIT_CI_TEST_VARIABLE", "soon")
    with pytest.raises(SystemExit) as exit_information:
        ci.read_integer_setting(variable_name="WAIT_CI_TEST_VARIABLE", default_value=30)
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE
