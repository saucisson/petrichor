"""Active CI wait: block until CI concludes on the pull request head (ADR-0006).

A session that ends with "the review will happen once CI finishes" is
attended thinking: a headless session never resumes, and nobody types
the next prompt at 3 a.m. The protocol's answer is an active wait -
this driver blocks, polls the head's CI check runs, and returns a
verdict:

- exit 0: every check run on the head concluded "success" - the exact
  precondition the review driver (``make review``) enforces, so the
  session can chain it right after;
- exit 1: CI failed on the head, or did not conclude within the
  timeout - the honest stop: fix, push, wait again;
- exit 2: protocol failure (no token, pull request not found or never
  reachable within the timeout, nonsensical budgets).

The wait follows the CURRENT head: if the branch is re-pushed while
waiting, the driver switches to the new head and keeps waiting -
exactly like the review, it never judges a stale head.
"""

import os
import sys
import time
from typing import TYPE_CHECKING

from scripts._github import extract_head_sha, open_github_client
from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
    GITHUB_API_BASE_URL,
    load_github_token,
    resolve_repository_slug,
)

if TYPE_CHECKING:
    import argparse

    import httpx

POLL_INTERVAL_VARIABLE = "WAIT_CI_INTERVAL_SECONDS"
TIMEOUT_VARIABLE = "WAIT_CI_TIMEOUT_SECONDS"
DEFAULT_POLL_INTERVAL_SECONDS = 20
DEFAULT_TIMEOUT_SECONDS = 1800
NOT_FOUND_STATUS_CODE = 404

POLL_STATUS_FETCH_FAILED = "fetch-failed"
POLL_STATUS_NO_CHECKS = "no-checks"
POLL_STATUS_PENDING = "pending"
POLL_STATUS_GREEN = "green"
POLL_STATUS_FAILED = "failed"

EXIT_CODE_GREEN = EXIT_CODE_SUCCESS  # a subcommand-local alias, same protocol code
EXIT_CODE_CI_FAILED = EXIT_CODE_CRITERIA_UNMET  # CI red or timed out: criteria unmet


def try_fetch_pull_request(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
) -> dict[str, object] | None:
    """Fetch the pull request; None means a transient API refusal."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}"
    )
    if response.status_code == NOT_FOUND_STATUS_CODE:
        print(
            f"Error: pull request #{pull_request_number} does not exist or"
            " is not visible to this token - nothing to watch.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    if not response.is_success:
        return None
    pull_request: object = response.json()
    return pull_request if isinstance(pull_request, dict) else None


def try_fetch_check_runs(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    head_sha: str,
) -> list[dict[str, object]] | None:
    """List the head's CI check runs; None means the API refused them."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/commits/{head_sha}/check-runs"
    )
    if not response.is_success:
        return None
    payload: object = response.json()
    if not isinstance(payload, dict):
        return None
    check_runs = payload.get("check_runs", [])
    if not isinstance(check_runs, list):
        return None
    return [entry for entry in check_runs if isinstance(entry, dict)]


def poll_once(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
) -> tuple[str, str, str]:
    """One poll: (status, head_sha, progress detail)."""
    pull_request = try_fetch_pull_request(
        http_client=http_client,
        repository_slug=repository_slug,
        pull_request_number=pull_request_number,
    )
    if pull_request is None:
        return POLL_STATUS_FETCH_FAILED, "", ""
    head_sha = extract_head_sha(pull_request=pull_request)
    check_runs = try_fetch_check_runs(
        http_client=http_client,
        repository_slug=repository_slug,
        head_sha=head_sha,
    )
    if check_runs is None:
        return POLL_STATUS_FETCH_FAILED, head_sha, ""
    if not check_runs:
        return POLL_STATUS_NO_CHECKS, head_sha, ""
    failed = [
        str(check_run.get("name", "unnamed check"))
        for check_run in check_runs
        if check_run.get("status") == "completed" and check_run.get("conclusion") != "success"
    ]
    if failed:
        return POLL_STATUS_FAILED, head_sha, ", ".join(failed)
    running = [check_run for check_run in check_runs if check_run.get("status") != "completed"]
    if not running:
        return POLL_STATUS_GREEN, head_sha, str(len(check_runs))
    return (
        POLL_STATUS_PENDING,
        head_sha,
        f"{len(running)} running, {len(check_runs) - len(running)} green",
    )


def interpret_status(*, status: str, detail: str) -> int | None:
    """Map one poll to an exit code; None means: keep waiting."""
    if status == POLL_STATUS_GREEN:
        print(f"CI is green on the head ({detail} check runs): the review can start.")
        return EXIT_CODE_GREEN
    if status == POLL_STATUS_FAILED:
        print(f"CI failed on the head: {detail} - fix, push, wait again.")
        return EXIT_CODE_CI_FAILED
    if status == POLL_STATUS_NO_CHECKS:
        print("No CI check run on the head yet.")
    elif status == POLL_STATUS_PENDING:
        print(f"CI in progress: {detail}.")
    else:
        print("Warning: transient API error - retrying until the timeout.")
    return None


def sleep_until_next_poll(*, interval_seconds: int, remaining_seconds: float) -> None:
    """Sleep one poll interval, never past the timeout."""
    time.sleep(max(0.0, min(float(interval_seconds), remaining_seconds)))


def read_integer_setting(*, variable_name: str, default_value: int) -> int:
    """Read an integer environment setting; an invalid value fails closed."""
    raw_value = os.environ.get(variable_name, "").strip()
    if not raw_value:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        print(
            f"Error: {variable_name} is not an integer: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from None


def enforce_budget_arguments(*, arguments: argparse.Namespace) -> None:
    """Refuse nonsensical budgets; a broken wait is worse than no wait."""
    if arguments.interval_seconds < 1:
        print(
            f"Error: the poll interval must be at least 1 second,"
            f" not {arguments.interval_seconds}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    if arguments.timeout_seconds < 1:
        print(
            f"Error: the timeout must be at least 1 second, not {arguments.timeout_seconds}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the wait-ci subcommand."""
    subparser = subparsers.add_parser(
        "wait-ci",
        help="Block until CI concludes on the pull request head (ADR-0006).",
        description="Block until CI concludes on the pull request head (ADR-0006).",
    )
    subparser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Number of the pull request to watch.",
    )
    subparser.add_argument(
        "--interval-seconds",
        type=int,
        default=read_integer_setting(
            variable_name=POLL_INTERVAL_VARIABLE,
            default_value=DEFAULT_POLL_INTERVAL_SECONDS,
        ),
        help=f"Seconds between two polls (env {POLL_INTERVAL_VARIABLE}).",
    )
    subparser.add_argument(
        "--timeout-seconds",
        type=int,
        default=read_integer_setting(
            variable_name=TIMEOUT_VARIABLE,
            default_value=DEFAULT_TIMEOUT_SECONDS,
        ),
        help=f"Give up after this many seconds (env {TIMEOUT_VARIABLE}).",
    )
    subparser.set_defaults(runner=run)


def run_watch(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
    interval_seconds: int,
    timeout_seconds: int,
) -> int:
    """The poll loop: watch the current head until CI concludes or the timeout."""
    started_at = time.monotonic()
    observed_head = ""
    while True:
        status, head_sha, detail = poll_once(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=pull_request_number,
        )
        if head_sha and head_sha != observed_head:
            observed_head = head_sha
            print(f"Pull request #{pull_request_number}: watching CI on the head {head_sha}.")
        exit_code = interpret_status(status=status, detail=detail)
        if exit_code is not None:
            return exit_code
        remaining_seconds = timeout_seconds - (time.monotonic() - started_at)
        if remaining_seconds <= 0:
            if observed_head:
                print(
                    f"CI did not conclude on {observed_head} within"
                    f" {timeout_seconds}s - the honest stop: the session"
                    " escalates instead of waiting forever."
                )
                return EXIT_CODE_CI_FAILED
            print(
                "Error: the pull request never became reachable within"
                f" {timeout_seconds}s - nothing was watchable.",
                file=sys.stderr,
            )
            return EXIT_CODE_PROTOCOL_FAILURE
        sleep_until_next_poll(
            interval_seconds=interval_seconds,
            remaining_seconds=remaining_seconds,
        )


def run(*, arguments: argparse.Namespace) -> int:
    """Entry point: block until CI concludes, then report the verdict."""
    enforce_budget_arguments(arguments=arguments)
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    with open_github_client(token=github_token) as http_client:
        return run_watch(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
            interval_seconds=arguments.interval_seconds,
            timeout_seconds=arguments.timeout_seconds,
        )
