"""Merge driver: merge one reviewed pull request into daily (ADR-0009).

The developer agent merges each completed pull request into `daily` -
through this script and nothing else. The script IS the codified merge
criteria: it refuses to merge unless the review report exists, was
written for THIS pull request and its CURRENT head (review context),
contains zero BLOCKING findings, the pull request is open and
mergeable, and every check run on its head has concluded "success".
Then it squash-merges through the GitHub API (linear history on
`daily`, ADR-0009) and deletes the branch.

The reviewer session never merges: it writes the report; this script
reads it. `main` is never merged here - that stays a human action.

Usage::

    make merge-pr PR=42
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_TOKEN_VARIABLE = "GITHUB_TOKEN"  # noqa: S105 - an env var NAME, never a secret
ENVIRONMENT_FILE_NAME = ".env"
DEFAULT_REPORT_FILE_NAME = ".review/review-report.json"
DEFAULT_CONTEXT_FILE_NAME = ".review/review-context.json"
MERGE_METHOD = "squash"
METHOD_NOT_ALLOWED_STATUS_CODE = 405

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_MERGED = 0
EXIT_CODE_CRITERIA_UNMET = 1
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
            f"Error: {GITHUB_TOKEN_VARIABLE} is not set - cannot reach the"
            " GitHub API (fine-grained PAT: pull requests read/write,"
            " repository contents read/write).",
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
    marker = "github.com"
    separator_index = remote_url.find(marker)
    if separator_index == -1:
        print(
            f"Error: the origin remote is not on GitHub: {remote_url}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    slug = remote_url[separator_index + len(marker) :]
    slug = slug.removeprefix(":").removeprefix("/").removesuffix(".git").strip("/")
    owner, separator, repository = slug.partition("/")
    if not owner or not separator or not repository.strip("/"):
        print(
            f"Error: cannot parse owner/repository from remote: {remote_url}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return f"{owner}/{repository.strip('/')}"


def load_review_report(*, report_file: str) -> dict[str, object]:
    """Load the review report; missing means criteria unmet, not failure."""
    report_path = Path(report_file)
    if not report_path.is_file():
        print(
            f"Error: no review report at {report_file} - run"
            " `make review PR=...` first; merging without a review is"
            " never an option.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    try:
        report: object = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as report_error:
        print(
            f"Error: the review report is unreadable: {report_error}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from report_error
    if not isinstance(report, dict):
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return report


def load_review_context(*, context_file: str) -> dict[str, object]:
    """Load the review context; missing means criteria unmet, not failure."""
    context_path = Path(context_file)
    if not context_path.is_file():
        print(
            f"Error: no review context at {context_file} - run"
            " `make review PR=...` first; merging without a review is"
            " never an option.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    try:
        context: object = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as context_error:
        print(
            f"Error: the review context is unreadable: {context_error}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from context_error
    if not isinstance(context, dict):
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return context


def check_review_context(
    *,
    context: dict[str, object],
    pull_request_number: int,
    head_sha: str,
) -> None:
    """Verify the report belongs to this pull request and this head.

    Raises SystemExit with EXIT_CODE_CRITERIA_UNMET on any mismatch: a
    report written for another pull request, or before new commits were
    pushed, is stale and never a merge ticket.
    """
    context_pr_number = context.get("pr_number")
    if context_pr_number != pull_request_number:
        print(
            f"Merge refused: the review report was written for PR"
            f" {context_pr_number}, not PR {pull_request_number} - rerun"
            " `make review` on this pull request.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    context_head_sha = context.get("head_sha")
    if not isinstance(context_head_sha, str) or not context_head_sha:
        print(
            "Merge refused: the review context has no head SHA - rerun `make review`.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    if context_head_sha != head_sha:
        print(
            "Merge refused: commits were pushed after the review - rerun"
            " `make review` on the new head.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def fetch_pull_request(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
) -> dict[str, object]:
    """Fetch the pull request, or fail closed."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}"
    )
    if not response.is_success:
        print(
            f"Error: the GitHub API returned {response.status_code}"
            f" for pull request {pull_request_number}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    pull_request: object = response.json()
    if not isinstance(pull_request, dict):
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return pull_request


def fetch_check_runs(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    head_sha: str,
) -> list[dict[str, object]]:
    """List the CI check runs of the pull request head."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/commits/{head_sha}/check-runs"
    )
    if not response.is_success:
        print(
            f"Error: the GitHub API returned {response.status_code}"
            f" for the check runs of {head_sha}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    payload: object = response.json()
    check_runs = payload.get("check_runs", []) if isinstance(payload, dict) else []
    if not isinstance(check_runs, list):
        return []
    return [entry for entry in check_runs if isinstance(entry, dict)]


def evaluate_merge_criteria(  # noqa: PLR0911 - one return per codified criterion (ADR-0009)
    *,
    report: dict[str, object],
    pull_request: dict[str, object],
    check_runs: Sequence[dict[str, object]],
) -> tuple[bool, str]:
    """Apply the codified merge criteria (ADR-0009); return (ok, reason)."""
    blocking_count = report.get("blocking_count")
    if blocking_count is None:
        return False, "the review report has no blocking_count - rerun the review"
    if blocking_count != 0:
        return False, (
            f"the review report still has {blocking_count} BLOCKING"
            " findings - iterate until the review is clean"
        )
    if pull_request.get("state") != "open":
        return False, "the pull request is not open"
    if pull_request.get("draft") is True:
        return False, "the pull request is a draft"
    if pull_request.get("mergeable") is not True:
        return False, (
            "the pull request is not cleanly mergeable - rebase it on daily and rerun the review"
        )
    if len(check_runs) == 0:
        return False, "no CI check runs on the head - wait for CI, or push first"
    for check_run in check_runs:
        name = str(check_run.get("name", "unnamed check"))
        if check_run.get("status") != "completed":
            return False, f"CI check {name} is still running - wait for CI"
        if check_run.get("conclusion") != "success":
            return False, f"CI check {name} did not succeed - fix CI first"
    return True, "criteria satisfied: review clean, CI green, PR mergeable"


def merge_pull_request(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
    head_sha: str,
) -> bool:
    """Squash-merge the pull request; return success, or fail closed."""
    response = http_client.put(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}/merge",
        json={"sha": head_sha, "merge_method": MERGE_METHOD},
    )
    if response.is_success:
        return True
    if response.status_code == METHOD_NOT_ALLOWED_STATUS_CODE:
        payload = response.json() if response.text else {}
        message = (
            payload.get("message", "not mergeable")
            if isinstance(payload, dict)
            else "not mergeable"
        )
        print(f"Merge refused by GitHub: {message}.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    print(
        f"Error: the merge API returned {response.status_code}: {response.text}",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def delete_branch(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    head_ref: str,
) -> None:
    """Delete the merged branch; best effort, never a merge failure."""
    if not head_ref:
        return
    response = http_client.delete(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/git/refs/heads/{head_ref}"
    )
    if not response.is_success:
        print(f"Warning: could not delete branch {head_ref} (status {response.status_code}).")


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="merge_pr",
        description="Merge one reviewed pull request into daily (ADR-0009).",
    )
    argument_parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Number of the pull request to merge.",
    )
    argument_parser.add_argument(
        "--report-file",
        default=DEFAULT_REPORT_FILE_NAME,
        help="Path to the review report produced by make review.",
    )
    argument_parser.add_argument(
        "--context-file",
        default=DEFAULT_CONTEXT_FILE_NAME,
        help="Path to the review context produced by make review.",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: criteria checked, pull request merged, branch deleted."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    load_environment_file()
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    report = load_review_report(report_file=arguments.report_file)
    with httpx.Client(
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json",
        },
        timeout=30.0,
    ) as http_client:
        pull_request = fetch_pull_request(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
        )
        head_container = pull_request.get("head")
        head_sha = head_container.get("sha", "") if isinstance(head_container, dict) else ""
        head_ref = head_container.get("ref", "") if isinstance(head_container, dict) else ""
        if not head_sha:
            print("Error: the pull request has no head SHA.", file=sys.stderr)
            return EXIT_CODE_PROTOCOL_FAILURE
        context = load_review_context(context_file=arguments.context_file)
        check_review_context(
            context=context,
            pull_request_number=arguments.pr_number,
            head_sha=head_sha,
        )
        check_runs = fetch_check_runs(
            http_client=http_client,
            repository_slug=repository_slug,
            head_sha=head_sha,
        )
        criteria_met, reason = evaluate_merge_criteria(
            report=report,
            pull_request=pull_request,
            check_runs=check_runs,
        )
        if not criteria_met:
            print(f"Merge refused: {reason}.")
            return EXIT_CODE_CRITERIA_UNMET
        print(f"Criteria met ({reason}); merging PR #{arguments.pr_number}.")
        merge_pull_request(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
            head_sha=head_sha,
        )
        delete_branch(
            http_client=http_client,
            repository_slug=repository_slug,
            head_ref=head_ref,
        )
    print(f"PR #{arguments.pr_number} merged into daily; branch deleted.")
    return EXIT_CODE_MERGED


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
