"""Shared GitHub API helpers for the drivers that call it (ADR-0021).

The drivers that talk to the GitHub API share the same fail-closed
fetch primitives here. The modules that never touch the API
(``day-report``, ``sync-daily``, ``review-bundle``) do not import this
module, so they stay stdlib-only (ADR-0021).
"""

import json
import sys
from typing import TYPE_CHECKING, NewType

import httpx

from scripts._shared import (
    EXIT_CODE_PROTOCOL_FAILURE,
    GITHUB_API_BASE_URL,
    PullRequestIdentifier,
    RepositorySlug,
    github_headers,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

HeadSha = NewType("HeadSha", str)
"""A commit SHA at the pull request head."""

HTTP_TIMEOUT_SECONDS = 30.0


def open_github_client(*, token: str) -> httpx.Client:
    """Build the GitHub API client every driver uses."""
    return httpx.Client(headers=github_headers(token=token), timeout=HTTP_TIMEOUT_SECONDS)


def request_or_fail(
    *,
    http_client: httpx.Client,
    method: str,
    url: str,
    json_payload: dict[str, object] | None = None,
    params: dict[str, str] | None = None,
) -> httpx.Response:
    """Send one request; a transport error fails closed with exit 2.

    A network failure is never a traceback and never exit 1: the
    caller cannot distinguish "refused" from "unreachable", so the
    protocol failure is the only honest verdict.
    """
    try:
        return http_client.request(method, url, json=json_payload, params=params)
    except httpx.TransportError as transport_error:
        print(
            f"Error: the GitHub API is unreachable ({method} {url}): {transport_error}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from transport_error


def fetch_pull_request(
    *,
    http_client: httpx.Client,
    repository_slug: RepositorySlug,
    pull_request_number: PullRequestIdentifier,
) -> dict[str, object]:
    """Fetch the pull request, or fail closed."""
    response = request_or_fail(
        http_client=http_client,
        method="GET",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}",
    )
    if not response.is_success:
        print(
            f"Error: the GitHub API returned {response.status_code}"
            f" for pull request {pull_request_number}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    try:
        pull_request: object = response.json()
    except json.JSONDecodeError as json_error:
        print("Error: the pull request payload is not valid JSON.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from json_error
    if not isinstance(pull_request, dict):
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return pull_request


def fetch_check_runs(
    *,
    http_client: httpx.Client,
    repository_slug: RepositorySlug,
    head_sha: HeadSha,
) -> list[dict[str, object]]:
    """List the CI check runs of the pull request head, or fail closed."""
    response = request_or_fail(
        http_client=http_client,
        method="GET",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/commits/{head_sha}/check-runs",
    )
    if not response.is_success:
        print(
            f"Error: the GitHub API returned {response.status_code}"
            f" for the check runs of {head_sha}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    try:
        payload: object = response.json()
    except json.JSONDecodeError as json_error:
        print(f"Error: the check-runs payload of {head_sha} is not valid JSON.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from json_error
    check_runs = payload.get("check_runs", []) if isinstance(payload, dict) else []
    if not isinstance(check_runs, list):
        return []
    return [entry for entry in check_runs if isinstance(entry, dict)]


def extract_head_sha(*, pull_request: dict[str, object]) -> HeadSha:
    """Return the pull request head SHA, or fail closed."""
    head_container = pull_request.get("head")
    head_sha = head_container.get("sha", "") if isinstance(head_container, dict) else ""
    if not head_sha:
        print("Error: the pull request has no head SHA.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return HeadSha(head_sha)


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
