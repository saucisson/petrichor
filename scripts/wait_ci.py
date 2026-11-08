"""Active CI wait: block until CI concludes on the pull request head (ADR-0018).

A session that ends with "the review will happen once CI finishes" is
attended thinking: a headless session never resumes, and nobody types
the next prompt at 3 a.m. The protocol's answer is an active wait -
this driver blocks, polls the head's CI check runs, and returns a
verdict:

- exit 0: every check run on the head concluded "success" - the exact
  precondition `scripts/pr_review.py` enforces, so the session can
  chain `make review PR=N` right after;
- exit 1: CI failed on the head, or did not conclude within the
  timeout - the honest stop: fix, push, wait again;
- exit 2: protocol failure (no token, pull request not found or
  never reachable within the timeout, nonsensical budgets).

The wait follows the CURRENT head: if the branch is re-pushed while
waiting, the driver switches to the new head and keeps waiting -
exactly like the review, it never judges a stale head.

Usage::

    make wait-ci PR=53
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NewType

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

PullRequestIdentifier = NewType("PullRequestIdentifier", int)

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_DOMAIN_MARKER = "github.com"
REMOTE_SCP_SEPARATOR = ":"
REMOTE_PATH_SEPARATOR = "/"
GIT_REMOTE_SUFFIX = ".git"
HTTP_TIMEOUT_SECONDS = 30.0
AUTHORIZATION_SCHEME = "Bearer"
GITHUB_ACCEPT_MEDIA_TYPE = "application/vnd.github.v3+json"
GITHUB_TOKEN_VARIABLE = "GITHUB_TOKEN"  # noqa: S105 - an env var NAME, never a secret
ENVIRONMENT_FILE_NAME = ".env"
POLL_INTERVAL_VARIABLE = "WAIT_CI_INTERVAL_SECONDS"
TIMEOUT_VARIABLE = "WAIT_CI_TIMEOUT_SECONDS"
DEFAULT_POLL_INTERVAL_SECONDS = 20
DEFAULT_TIMEOUT_SECONDS = 1800
NOT_FOUND_STATUS_CODE = 404
UNNAMED_CHECK_NAME = "unnamed check"
NON_FAILURE_CONCLUSIONS = ("success", "neutral", "skipped")

PollStatus = Literal["fetch-failed", "no-checks", "pending", "green", "failed"]

POLL_STATUS_FETCH_FAILED: PollStatus = "fetch-failed"
POLL_STATUS_NO_CHECKS: PollStatus = "no-checks"
POLL_STATUS_PENDING: PollStatus = "pending"
POLL_STATUS_GREEN: PollStatus = "green"
POLL_STATUS_FAILED: PollStatus = "failed"

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_GREEN = 0
EXIT_CODE_CI_FAILED = 1
EXIT_CODE_PROTOCOL_FAILURE = 2


def load_environment_file() -> None:
    """Read KEY=VALUE pairs from .env into the environment if unset."""
    environment_path = REPOSITORY_ROOT / ENVIRONMENT_FILE_NAME
    if not environment_path.is_file():
        return
    for raw_line in environment_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_github_token() -> str:
    """Read the GitHub token; absent means protocol failure."""
    github_token = os.environ.get(GITHUB_TOKEN_VARIABLE, "").strip()
    if not github_token:
        print(
            f"Error: {GITHUB_TOKEN_VARIABLE} is not set - cannot watch CI"
            " (fine-grained PAT: pull requests read, repository contents read).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return github_token


def resolve_repository_slug() -> str:
    """Return "owner/repository" from the origin remote, or fail closed."""
    completed_process = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True,
        text=True,
        check=False,
    )
    remote_url = completed_process.stdout.strip()
    separator_index = remote_url.find(GITHUB_DOMAIN_MARKER)
    if separator_index == -1:
        print(
            f"Error: the origin remote is not on GitHub: {remote_url}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    slug = remote_url[separator_index + len(GITHUB_DOMAIN_MARKER) :]
    slug = (
        slug.removeprefix(REMOTE_SCP_SEPARATOR)
        .removeprefix(REMOTE_PATH_SEPARATOR)
        .removesuffix(GIT_REMOTE_SUFFIX)
        .strip(REMOTE_PATH_SEPARATOR)
    )
    owner, separator, repository = slug.partition(REMOTE_PATH_SEPARATOR)
    if not owner or not separator or not repository.strip(REMOTE_PATH_SEPARATOR):
        print(
            f"Error: cannot parse owner/repository from remote: {remote_url}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return f"{owner}{REMOTE_PATH_SEPARATOR}{repository.strip(REMOTE_PATH_SEPARATOR)}"


def request_or_fail(
    *,
    http_client: httpx.Client,
    method: str,
    url: str,
) -> httpx.Response:
    """Perform an HTTP request; a transport error fails closed (exit 2)."""
    try:
        return http_client.request(method, url)
    except httpx.RequestError as error:
        print(f"Error: could not reach the GitHub API: {error}", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from error


def try_fetch_pull_request(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: PullRequestIdentifier,
) -> dict[str, object] | None:
    """Fetch the pull request; None means a transient API refusal."""
    response = request_or_fail(
        http_client=http_client,
        method="GET",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}",
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
    try:
        pull_request: object = response.json()
    except ValueError:
        return None
    return pull_request if isinstance(pull_request, dict) else None


def extract_head_sha(*, pull_request: dict[str, object]) -> str:
    """Return the pull request head SHA, or fail closed."""
    head_container = pull_request.get("head")
    head_sha = head_container.get("sha", "") if isinstance(head_container, dict) else ""
    if not head_sha:
        print("Error: the pull request has no head SHA.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return head_sha


def try_fetch_check_runs(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    head_sha: str,
) -> list[dict[str, object]] | None:
    """List the head's CI check runs; None means the API refused them."""
    response = request_or_fail(
        http_client=http_client,
        method="GET",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/commits/{head_sha}/check-runs",
    )
    if not response.is_success:
        return None
    try:
        payload: object = response.json()
    except ValueError:
        return None
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
    pull_request_number: PullRequestIdentifier,
) -> tuple[PollStatus, str, str]:
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
        str(check_run.get("name", UNNAMED_CHECK_NAME))
        for check_run in check_runs
        if check_run.get("status") == "completed"
        and check_run.get("conclusion") not in NON_FAILURE_CONCLUSIONS
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


def interpret_status(*, status: PollStatus, detail: str) -> int | None:
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
    except ValueError as error:
        print(
            f"Error: {variable_name} is not an integer: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from error


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


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="wait_ci",
        description="Block until CI concludes on the pull request head (ADR-0018).",
    )
    argument_parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Number of the pull request to watch.",
    )
    argument_parser.add_argument(
        "--interval-seconds",
        type=int,
        default=read_integer_setting(
            variable_name=POLL_INTERVAL_VARIABLE,
            default_value=DEFAULT_POLL_INTERVAL_SECONDS,
        ),
        help=f"Seconds between two polls (env {POLL_INTERVAL_VARIABLE}).",
    )
    argument_parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=read_integer_setting(
            variable_name=TIMEOUT_VARIABLE,
            default_value=DEFAULT_TIMEOUT_SECONDS,
        ),
        help=f"Give up after this many seconds (env {TIMEOUT_VARIABLE}).",
    )
    return argument_parser.parse_args(command_line_arguments)


def run_watch(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: PullRequestIdentifier,
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


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: block until CI concludes, then report the verdict."""
    load_environment_file()
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    enforce_budget_arguments(arguments=arguments)
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    with httpx.Client(
        headers={
            "Authorization": f"{AUTHORIZATION_SCHEME} {github_token}",
            "Accept": GITHUB_ACCEPT_MEDIA_TYPE,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as http_client:
        return run_watch(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=PullRequestIdentifier(arguments.pr_number),
            interval_seconds=arguments.interval_seconds,
            timeout_seconds=arguments.timeout_seconds,
        )


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
