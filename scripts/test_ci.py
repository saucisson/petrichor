"""Tests for the active CI wait ci.py (ADR-0018, template 2aa4d27)."""

import argparse
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from hypothesis import given, strategies

from scripts import _github, _shared, ci

if TYPE_CHECKING:
    from collections.abc import Callable


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx client dispatching to the given handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def parse_wait_ci_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered wait-ci subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    ci.register(subparsers)
    return parser.parse_args(["wait-ci", *command_line_arguments])


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


def test_register_wait_ci_requires_a_pull_request_number() -> None:
    """The subcommand contract: --pr-number is a required integer."""
    arguments = parse_wait_ci_arguments(["--pr-number", "53"])
    assert arguments.pr_number == 53
    with pytest.raises(SystemExit):
        parse_wait_ci_arguments([])


def test_enforce_budget_arguments_refuses_nonsensical_budgets() -> None:
    """An interval or timeout below 1 second is a protocol failure."""
    arguments = parse_wait_ci_arguments(["--pr-number", "53", "--interval-seconds", "0"])
    with pytest.raises(SystemExit) as excinfo:
        ci.enforce_budget_arguments(arguments=arguments)
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE
    arguments = parse_wait_ci_arguments(["--pr-number", "53", "--timeout-seconds", "0"])
    with pytest.raises(SystemExit) as excinfo:
        ci.enforce_budget_arguments(arguments=arguments)
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE
    arguments = parse_wait_ci_arguments(["--pr-number", "53", "--interval-seconds", "5"])
    ci.enforce_budget_arguments(arguments=arguments)


def test_read_integer_setting_returns_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset variable yields the default."""
    monkeypatch.delenv(ci.POLL_INTERVAL_VARIABLE, raising=False)
    assert ci.read_integer_setting(variable_name=ci.POLL_INTERVAL_VARIABLE, default_value=20) == 20


def test_read_integer_setting_rejects_non_integer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-integer value fails closed with exit 2, not a NameError."""
    monkeypatch.setenv(ci.POLL_INTERVAL_VARIABLE, "soon")
    with pytest.raises(SystemExit) as excinfo:
        ci.read_integer_setting(variable_name=ci.POLL_INTERVAL_VARIABLE, default_value=20)
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_absent_is_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in the environment: fail closed with exit 2."""
    monkeypatch.delenv(_shared.GITHUB_TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _shared.load_github_token()
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_present_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment is returned stripped."""
    monkeypatch.setenv(_shared.GITHUB_TOKEN_VARIABLE, "  token-value  ")
    assert _shared.load_github_token() == "token-value"


def test_load_environment_file_sets_unset_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """KEY=VALUE pairs from .env land in the environment when unset."""
    assert isinstance(tmp_path, Path)
    environment_file = tmp_path / ".env"
    environment_file.write_text(
        "WAIT_CI_TEST_VARIABLE=from-dotenv\n# comment\n\n", encoding="utf-8"
    )
    monkeypatch.setattr(_shared, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("WAIT_CI_TEST_VARIABLE", raising=False)
    _shared.load_environment_file()
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
    assert _shared.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_parses_https_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTPS origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://github.com/acme/widgets.git"),
    )
    assert _shared.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-GitHub remote fails closed."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        _shared.resolve_repository_slug()
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_request_or_fail_transport_error_is_protocol_failure() -> None:
    """A transport error fails closed with exit 2."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(SystemExit) as excinfo:
        _github.request_or_fail(
            http_client=client_with(handler), method="GET", url="https://api.github.test"
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_try_fetch_pull_request_not_found_is_protocol_failure() -> None:
    """A 404 on the pull request fails closed: nothing to watch."""
    client = watch_client(pull_request_response=httpx.Response(404, json={}))
    with pytest.raises(SystemExit) as excinfo:
        ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_try_fetch_pull_request_transient_refusal_returns_none() -> None:
    """A non-404 API refusal is transient: None, the poll retries."""
    client = watch_client(pull_request_response=httpx.Response(500, json={}))
    assert (
        ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
        )
        is None
    )


def test_try_fetch_pull_request_malformed_payload_returns_none() -> None:
    """A non-dict JSON payload is transient: None, the poll retries."""
    client = watch_client(pull_request_response=httpx.Response(200, json=["not", "a", "dict"]))
    assert (
        ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
        )
        is None
    )


def test_try_fetch_pull_request_undecodable_body_returns_none() -> None:
    """A 2xx with an undecodable body is transient: None, the poll retries."""
    client = watch_client(pull_request_response=httpx.Response(200, content=b"not json{"))
    assert (
        ci.try_fetch_pull_request(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
        )
        is None
    )


def test_extract_head_sha_reads_the_head() -> None:
    """The head SHA is read from the pull request head."""
    assert _github.extract_head_sha(pull_request=pull_request_payload(sha="deadbeef")) == "deadbeef"


def test_extract_head_sha_missing_fails_closed() -> None:
    """A pull request without a head SHA is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        _github.extract_head_sha(pull_request={"head": {}})
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_try_fetch_check_runs_refusal_returns_none() -> None:
    """A non-success listing of check runs is transient: None."""
    client = watch_client(check_runs_response=httpx.Response(500, json={}))
    assert (
        ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc") is None
    )


def test_try_fetch_check_runs_malformed_payload_returns_none() -> None:
    """A non-dict payload or a non-list check_runs field is transient: None."""
    client = watch_client(check_runs_response=httpx.Response(200, json=["not", "a", "dict"]))
    assert (
        ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc") is None
    )
    client = watch_client(check_runs_response=httpx.Response(200, json={"check_runs": "many"}))
    assert (
        ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc") is None
    )


def test_try_fetch_check_runs_filters_non_dict_entries() -> None:
    """Non-dict check run entries are dropped, never trusted."""
    client = watch_client(
        check_runs_response=httpx.Response(
            200, json=check_runs_payload(check_runs=[{"name": "ci"}, "junk"])
        )
    )
    assert ci.try_fetch_check_runs(http_client=client, repository_slug="o/r", head_sha="abc") == [
        {"name": "ci"}
    ]


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
    status, head_sha, detail = ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=_shared.PullRequestIdentifier(53),
    )
    assert status == ci.POLL_STATUS_GREEN
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
    status, _, detail = ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=_shared.PullRequestIdentifier(53),
    )
    assert status == ci.POLL_STATUS_FAILED
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
    status, _, detail = ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=_shared.PullRequestIdentifier(53),
    )
    assert status == ci.POLL_STATUS_PENDING
    assert detail == "1 running, 1 green"


def test_poll_once_no_checks_when_none_on_the_head() -> None:
    """No check run on the head yet: the status is no-checks."""
    client = watch_client(
        check_runs_response=httpx.Response(200, json=check_runs_payload(check_runs=[]))
    )
    status, head_sha, _ = ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=_shared.PullRequestIdentifier(53),
    )
    assert status == ci.POLL_STATUS_NO_CHECKS
    assert head_sha == "abc123"


def test_poll_once_fetch_failed_when_the_pr_is_unreachable() -> None:
    """A transient refusal of the pull request: the status is fetch-failed."""
    client = watch_client(pull_request_response=httpx.Response(500, json={}))
    status, head_sha, _ = ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=_shared.PullRequestIdentifier(53),
    )
    assert status == ci.POLL_STATUS_FETCH_FAILED
    assert head_sha == ""


def test_interpret_status_maps_verdicts_to_exit_codes() -> None:
    """Green exits 0, failed exits 1, everything else keeps waiting."""
    assert ci.interpret_status(status=ci.POLL_STATUS_GREEN, detail="3") == ci.EXIT_CODE_GREEN
    assert ci.interpret_status(status=ci.POLL_STATUS_FAILED, detail="ci") == ci.EXIT_CODE_CI_FAILED
    for status in (
        ci.POLL_STATUS_PENDING,
        ci.POLL_STATUS_NO_CHECKS,
        ci.POLL_STATUS_FETCH_FAILED,
    ):
        assert ci.interpret_status(status=status, detail="") is None


def test_sleep_until_next_poll_caps_at_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sleep never exceeds the remaining time budget."""
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)
    ci.sleep_until_next_poll(interval_seconds=20, remaining_seconds=5.0)
    ci.sleep_until_next_poll(interval_seconds=20, remaining_seconds=50.0)
    ci.sleep_until_next_poll(interval_seconds=20, remaining_seconds=-1.0)
    assert slept == [5.0, 20.0, 0.0]


def test_run_watch_returns_green_when_ci_concludes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green poll ends the watch with exit 0."""
    monkeypatch.setattr(
        ci,
        "poll_once",
        lambda **kwargs: (ci.POLL_STATUS_GREEN, "abc123", "3"),
    )
    assert (
        ci.run_watch(
            http_client=client_with(lambda request: httpx.Response(200)),
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
            interval_seconds=1,
            timeout_seconds=60,
        )
        == ci.EXIT_CODE_GREEN
    )


def test_run_watch_times_out_as_ci_failed_when_ci_never_concludes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI still pending at the timeout: the honest stop, exit 1."""
    monkeypatch.setattr(
        ci,
        "poll_once",
        lambda **kwargs: (ci.POLL_STATUS_PENDING, "abc123", "1 running, 0 green"),
    )
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    assert (
        ci.run_watch(
            http_client=client_with(lambda request: httpx.Response(200)),
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
            interval_seconds=1,
            timeout_seconds=10,
        )
        == ci.EXIT_CODE_CI_FAILED
    )


def test_run_watch_times_out_as_protocol_failure_when_never_reachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pull request never became reachable: exit 2, nothing was watchable."""
    monkeypatch.setattr(
        ci,
        "poll_once",
        lambda **kwargs: (ci.POLL_STATUS_FETCH_FAILED, "", ""),
    )
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    assert (
        ci.run_watch(
            http_client=client_with(lambda request: httpx.Response(200)),
            repository_slug="o/r",
            pull_request_number=_shared.PullRequestIdentifier(53),
            interval_seconds=1,
            timeout_seconds=10,
        )
        == _shared.EXIT_CODE_PROTOCOL_FAILURE
    )


@pytest.mark.property
@given(
    strategies.sampled_from(
        [
            ci.POLL_STATUS_FETCH_FAILED,
            ci.POLL_STATUS_NO_CHECKS,
            ci.POLL_STATUS_PENDING,
            ci.POLL_STATUS_GREEN,
            ci.POLL_STATUS_FAILED,
        ]
    )
)
def test_interpret_status_property_covers_the_whole_closed_set(status: str) -> None:
    """Property: every status of the closed set maps to its documented verdict."""
    exit_code = ci.interpret_status(status=status, detail="")
    if status == ci.POLL_STATUS_GREEN:
        assert exit_code == ci.EXIT_CODE_GREEN
    elif status == ci.POLL_STATUS_FAILED:
        assert exit_code == ci.EXIT_CODE_CI_FAILED
    else:
        assert exit_code is None


def test_poll_once_non_success_conclusions_are_failures() -> None:
    """Completed with 'skipped'/'neutral' is not a success: the status is failed."""
    client = watch_client(
        check_runs_response=httpx.Response(
            200,
            json=check_runs_payload(
                check_runs=[
                    {"name": "ci", "status": "completed", "conclusion": "success"},
                    {"name": "optional", "status": "completed", "conclusion": "skipped"},
                    {"name": "info", "status": "completed", "conclusion": "neutral"},
                ]
            ),
        )
    )
    status, _, detail = ci.poll_once(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=_shared.PullRequestIdentifier(53),
    )
    assert status == ci.POLL_STATUS_FAILED
    assert detail == "optional, info"
