"""Shared GitHub API helpers for the drivers that call it (ADR-0021).

The drivers that talk to the GitHub API share the same fail-closed
fetch primitives here. The modules that never touch the API
(``day-report``, ``sync-daily``, ``review-bundle``) do not import this
module, so they stay stdlib-only (ADR-0021).
"""

import sys
from typing import TYPE_CHECKING

import httpx

from scripts._shared import (
    EXIT_CODE_PROTOCOL_FAILURE,
    GITHUB_API_BASE_URL,
    github_headers,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def open_github_client(*, token: str) -> httpx.Client:
    """Build the GitHub API client every driver uses."""
    return httpx.Client(headers=github_headers(token=token), timeout=30.0)


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
    """List the CI check runs of the pull request head, or fail closed."""
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


def extract_head_sha(*, pull_request: dict[str, object]) -> str:
    """Return the pull request head SHA, or fail closed."""
    head_container = pull_request.get("head")
    head_sha = head_container.get("sha", "") if isinstance(head_container, dict) else ""
    if not head_sha:
        print("Error: the pull request has no head SHA.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return head_sha


def extract_head_ref(*, pull_request: dict[str, object]) -> str:
    """Return the pull request head branch name, empty when unknown."""
    head_container = pull_request.get("head")
    return head_container.get("ref", "") if isinstance(head_container, dict) else ""


def check_runs_allow_merge(*, check_runs: Sequence[dict[str, object]]) -> str | None:
    """Verify every check run concluded success; a reason means: refused."""
    if len(check_runs) == 0:
        return "no CI check runs on the head - wait for CI, or push first"
    for check_run in check_runs:
        name = str(check_run.get("name", "unnamed check"))
        if check_run.get("status") != "completed":
            return f"CI check {name} is still running - wait for CI"
        if check_run.get("conclusion") != "success":
            return f"CI check {name} did not succeed - fix CI first"
    return None
