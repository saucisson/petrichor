"""Tests for the active CI wait wait_ci.py (ADR-0018, template 0030b3e)."""

import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from hypothesis import given, strategies

import wait_ci
from wait_ci import PullRequestIdentifier

if TYPE_CHECKING:
    from collections.abc import Callable


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx client dispatching to the given handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def pull_request_payload(*, sha: str = "abc123") -> dict[str, object]:
    """Build a GitHub pull request payload."""
    return {"number": 53, "head": {"sha": sha, "ref": "feat/12"}}


def check_runs_payload(*, check_runs: list[object]) -> dict[str, object]:
    """Build a GitHub check-runs listing payload."""
    return {"check_runs": check_runs}


def watch_client(
    *,
    pull_request_response: httpx.Response | None = None,
    check_runs_response: httpx.Response | None = None,
) -> httpx.Client:
    """Build a client serving the pull request and the head's check runs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "/pulls/" in request.url.path:
            return pull_request_response or httpx.Response(200, json=pull_request_payload())
        if "/check-runs" in request.url.path:
            return check_runs_response or httpx.Response(
                200, json=check_runs_payload(check_runs=[])
            )
        return httpx.Response(404, json={})

    return client_with(handler)


def test_parse_arguments_requires_a_pull_request_number() -> None:
    """The script contract: --pr-number is a required integer."""
    arguments = wait_ci.parse_arguments(command_line_arguments=["--pr-number", "53"])
    assert arguments.pr_number == 53
    with pytest.raises(SystemExit):
        wait_ci.parse_arguments(command_line_arguments=[])


def test_enforce_budget_arguments_refuses_nonsensical_budgets() -> None:
    """An interval or timeout below 1 second is a protocol failure."""
    arguments = wait_ci.parse_arguments(
        command_line_arguments=["--pr-number", "53", "--interval-seconds", "0"]
    )
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.enforce_budget_arguments(arguments=arguments)
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE
    arguments = wait_ci.parse_arguments(
        command_line_arguments=["--pr-number", "53", "--timeout-seconds", "0"]
    )
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.enforce_budget_arguments(arguments=arguments)
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE
    arguments = wait_ci.parse_arguments(
        command_line_arguments=["--pr-number", "53", "--interval-seconds", "5"]
    )
    wait_ci.enforce_budget_arguments(arguments=arguments)


def test_read_integer_setting_returns_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset variable yields the default."""
    monkeypatch.delenv(wait_ci.POLL_INTERVAL_VARIABLE, raising=False)
    assert (
        wait_ci.read_integer_setting(variable_name=wait_ci.POLL_INTERVAL_VARIABLE, default_value=20)
        == 20
    )


def test_read_integer_setting_rejects_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-integer value fails closed with exit 2, not a NameError."""
    monkeypatch.setenv(wait_ci.POLL_INTERVAL_VARIABLE, "soon")
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.read_integer_setting(variable_name=wait_ci.POLL_INTERVAL_VARIABLE, default_value=20)
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_absent_is_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in the environment: fail closed with exit 2."""
    monkeypatch.delenv(wait_ci.GITHUB_TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.load_github_token()
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_present_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment is returned stripped."""
    monkeypatch.setenv(wait_ci.GITHUB_TOKEN_VARIABLE, "  token-value  ")
    assert wait_ci.load_github_token() == "token-value"


def test_load_environment_file_sets_unset_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """KEY=VALUE pairs from .env land in the environment when unset."""
    assert isinstance(tmp_path, Path)
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "WAIT_CI_TEST_VARIABLE=from-dotenv\n# comment\n\n", encoding="utf-8"
    )
    monkeypatch.setattr(wait_ci, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("WAIT_CI_TEST_VARIABLE", raising=False)
    wait_ci.load_environment_file()
    assert os.environ["WAIT_CI_TEST_VARIABLE"] == "from-dotenv"


def completed_remote(url: str) -> subprocess.CompletedProcess[str]:
    """Build the CompletedProcess returned by a mocked `git remote get-url`."""
    return subprocess.CompletedProcess(
        args=["git", "remote", "get-url", "origin"], returncode=0, stdout=url, stderr=""
    )


def test_resolve_repository_slug_parses_ssh_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An SSH origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("git@github.com:acme/widgets.git\n"),
    )
    assert wait_ci.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_parses_https_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTPS origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://github.com/acme/widgets.git"),
    )
    assert wait_ci.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-GitHub origin remote fails closed."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.resolve_repository_slug()
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE


def test_request_or_fail_transport_error_is_protocol_failure() -> None:
    """A transport error fails closed with exit 2."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(SystemExit) as excinfo:
        wait_ci.request_or_fail(
            http_client=client_with(handler), method="GET", url="https://api.github.test"
        )
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE


def test_try_fetch_pull_request_not_found_is_protocol_failure() -> None:
    """A 404 on the pull request fails closed: nothing to watch."""
    client = watch_client(pull_request_response=httpx.Response(404, json={}))
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=PullRequestIdentifier(53),
        )
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE


def test_try_fetch_pull_request_transient_refusal_returns_none() -> None:
    """A non-404 API refusal is transient: None, the poll retries."""
    client = watch_client(pull_request_response=httpx.Response(500, json={}))
    assert (
        wait_ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=PullRequestIdentifier(53),
        )
        is None
    )


def test_try_fetch_pull_request_malformed_payload_returns_none() -> None:
    """A non-dict JSON payload is transient: None, the poll retries."""
    client = watch_client(pull_request_response=httpx.Response(200, json=["not", "a", "dict"]))
    assert (
        wait_ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=PullRequestIdentifier(53),
        )
        is None
    )


def test_extract_head_sha_reads_the_head() -> None:
    """The head SHA is read from the pull request head."""
    assert wait_ci.extract_head_sha(pull_request=pull_request_payload(sha="deadbeef")) == "deadbeef"


def test_extract_head_sha_missing_fails_closed() -> None:
    """A pull request without a head SHA is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        wait_ci.extract_head_sha(pull_request={"head": {}})
    assert excinfo.value.code == wait_ci.EXIT_CODE_PROTOCOL_FAILURE


def test_try_fetch_check_runs_refusal_returns_none() -> None:
    """A non-success listing of check runs is transient: None."""
    client = watch_client(check_runs_response=httpx.Response(500, json={}))
    assert (
        wait_ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc")
        is None
    )


def test_try_fetch_check_runs_malformed_payload_returns_none() -> None:
    """A non-dict payload or a non-list check_runs field is transient: None."""
    client = watch_client(check_runs_response=httpx.Response(200, json=["not", "a", "dict"]))
    assert (
        wait_ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc")
        is None
    )
    client = watch_client(check_runs_response=httpx.Response(200, json={"check_runs": "many"}))
    assert (
        wait_ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc")
        is None
    )


def test_try_fetch_check_runs_filters_non_dict_entries() -> None:
    """Non-dict check run entries are dropped, never trusted."""
    client = watch_client(
        check_runs_response=httpx.Response(
            200, json=check_runs_payload(check_runs=[{"name": "ci"}, "junk"])
        )
    )
    assert wait_ci.try_fetch_check_runs(
        http_client=client, repository_slug="o/r", head_sha="abc"
    ) == [{"name": "ci"}]


def test_poll_once_green_when_all_checks_succeed() -> None:
    """Every check run completed with success: the status is green."""
    client = watch_client(
        check_runs_response=httpx.Response(
            200,
            json=check_runs_payload(
                check_runs=[
                    {"name": "ci", "status": "completed", "conclusion": "success"},
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                ]
            ),
        )
    )
    status, head_sha, detail = wait_ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=PullRequestIdentifier(53),
    )
    assert status == wait_ci.POLL_STATUS_GREEN
    assert head_sha == "abc123"
    assert detail == "2"


def test_poll_once_failed_when_a_check_concludes_non_success() -> None:
    """A completed check run with a non-success conclusion: the status is failed."""
    client = watch_client(
        check_runs_response=httpx.Response(
            200,
            json=check_runs_payload(
                check_runs=[
                    {"name": "ci", "status": "completed", "conclusion": "failure"},
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                ]
            ),
        )
    )
    status, _, detail = wait_ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=PullRequestIdentifier(53),
    )
    assert status == wait_ci.POLL_STATUS_FAILED
    assert detail == "ci"


def test_poll_once_pending_while_checks_run() -> None:
    """A check run still running: the status is pending."""
    client = watch_client(
        check_runs_response=httpx.Response(
            200,
            json=check_runs_payload(
                check_runs=[
                    {"name": "ci", "status": "in_progress"},
                    {"name": "lint", "status": "completed", "conclusion": "success"},
                ]
            ),
        )
    )
    status, _, detail = wait_ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=PullRequestIdentifier(53),
    )
    assert status == wait_ci.POLL_STATUS_PENDING
    assert detail == "1 running, 1 green"


def test_poll_once_no_checks_when_none_on_the_head() -> None:
    """No check run on the head yet: the status is no-checks."""
    client = watch_client(
        check_runs_response=httpx.Response(200, json=check_runs_payload(check_runs=[]))
    )
    status, head_sha, _ = wait_ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=PullRequestIdentifier(53),
    )
    assert status == wait_ci.POLL_STATUS_NO_CHECKS
    assert head_sha == "abc123"


def test_poll_once_fetch_failed_when_the_pr_is_unreachable() -> None:
    """A transient refusal of the pull request: the status is fetch-failed."""
    client = watch_client(pull_request_response=httpx.Response(500, json={}))
    status, head_sha, _ = wait_ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=PullRequestIdentifier(53),
    )
    assert status == wait_ci.POLL_STATUS_FETCH_FAILED
    assert head_sha == ""


def test_interpret_status_maps_verdicts_to_exit_codes() -> None:
    """Green exits 0, failed exits 1, everything else keeps waiting."""
    assert (
        wait_ci.interpret_status(status=wait_ci.POLL_STATUS_GREEN, detail="3")
        == wait_ci.EXIT_CODE_GREEN
    )
    assert (
        wait_ci.interpret_status(status=wait_ci.POLL_STATUS_FAILED, detail="ci")
        == wait_ci.EXIT_CODE_CI_FAILED
    )
    for status in (
        wait_ci.POLL_STATUS_PENDING,
        wait_ci.POLL_STATUS_NO_CHECKS,
        wait_ci.POLL_STATUS_FETCH_FAILED,
    ):
        assert wait_ci.interpret_status(status=status, detail="") is None


def test_sleep_until_next_poll_caps_at_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sleep never exceeds the remaining time budget."""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    wait_ci.sleep_until_next_poll(interval_seconds=20, remaining_seconds=5.0)
    wait_ci.sleep_until_next_poll(interval_seconds=20, remaining_seconds=50.0)
    wait_ci.sleep_until_next_poll(interval_seconds=20, remaining_seconds=-1.0)
    assert slept == [5.0, 20.0, 0.0]


def test_run_watch_returns_green_when_ci_concludes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green poll ends the watch with exit 0."""
    monkeypatch.setattr(
        wait_ci,
        "poll_once",
        lambda **kwargs: (wait_ci.POLL_STATUS_GREEN, "abc123", "3"),
    )
    assert (
        wait_ci.run_watch(
            http_client=client_with(lambda request: httpx.Response(200)),
            repository_slug="o/r",
            pull_request_number=PullRequestIdentifier(53),
            interval_seconds=1,
            timeout_seconds=60,
        )
        == wait_ci.EXIT_CODE_GREEN
    )


def test_run_watch_times_out_as_ci_failed_when_ci_never_concludes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI still pending at the timeout: the honest stop, exit 1."""
    monkeypatch.setattr(
        wait_ci,
        "poll_once",
        lambda **kwargs: (wait_ci.POLL_STATUS_PENDING, "abc123", "1 running, 0 green"),
    )
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    assert (
        wait_ci.run_watch(
            http_client=client_with(lambda request: httpx.Response(200)),
            repository_slug="o/r",
            pull_request_number=PullRequestIdentifier(53),
            interval_seconds=1,
            timeout_seconds=10,
        )
        == wait_ci.EXIT_CODE_CI_FAILED
    )


def test_run_watch_times_out_as_protocol_failure_when_never_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pull request never became reachable: exit 2, nothing was watchable."""
    monkeypatch.setattr(
        wait_ci,
        "poll_once",
        lambda **kwargs: (wait_ci.POLL_STATUS_FETCH_FAILED, "", ""),
    )
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    assert (
        wait_ci.run_watch(
            http_client=client_with(lambda request: httpx.Response(200)),
            repository_slug="o/r",
            pull_request_number=PullRequestIdentifier(53),
            interval_seconds=1,
            timeout_seconds=10,
        )
        == wait_ci.EXIT_CODE_PROTOCOL_FAILURE
    )


@pytest.mark.property
@given(
    strategies.sampled_from(
        [
            wait_ci.POLL_STATUS_FETCH_FAILED,
            wait_ci.POLL_STATUS_NO_CHECKS,
            wait_ci.POLL_STATUS_PENDING,
            wait_ci.POLL_STATUS_GREEN,
            wait_ci.POLL_STATUS_FAILED,
        ]
    )
)
def test_interpret_status_property_covers_the_whole_closed_set(status: wait_ci.PollStatus) -> None:
    """Property: every status of the closed set maps to its documented verdict."""
    exit_code = wait_ci.interpret_status(status=status, detail="")
    if status == wait_ci.POLL_STATUS_GREEN:
        assert exit_code == wait_ci.EXIT_CODE_GREEN
    elif status == wait_ci.POLL_STATUS_FAILED:
        assert exit_code == wait_ci.EXIT_CODE_CI_FAILED
    else:
        assert exit_code is None
