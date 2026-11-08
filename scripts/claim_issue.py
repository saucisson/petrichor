"""Claim driver: mark one issue or pull request taken (ADR-0017).

In the auto-discovered night loop (``make night`` without ISSUES) each
session picks its own item, so its FIRST act is to claim it:
``make claim ISSUE=N`` or ``make claim-pr PR=N`` adds the
``night-claimed`` label through the GitHub API (the issues endpoint
serves pull requests too), with the same token as the other drivers.
The claim is both the concurrency guard and the honesty record:

- two sessions never work the same item (the loop filters claimed
  items out of every worklist, and this driver refuses to claim
  twice);
- a failed attempt stays claimed: the morning audit sees what was
  tried, and the human - never an agent - decides the retry.

Pull-request claims cover the recovery path (ADR-0017): a session may
be handed - or pick - a pull request left open by an earlier failed
session (red CI, unaddressed findings). The same rule applies: no
claim, no work.

Usage::

    make claim ISSUE=12
    make claim-pr PR=52
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NewType

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

ItemIdentifier = NewType("ItemIdentifier", int)

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
CLAIM_LABEL_NAME = "night-claimed"
CLAIM_LABEL_COLOR = "d4c5f9"
CLAIM_LABEL_DESCRIPTION = "Claimed by an unattended night session (ADR-0017)"

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_CLAIMED = 0
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
            f"Error: {GITHUB_TOKEN_VARIABLE} is not set - cannot claim the"
            " issue (fine-grained PAT: issues read/write).",
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
    json_payload: dict[str, object] | None = None,
) -> httpx.Response:
    """Perform an HTTP request; a transport error fails closed (exit 2)."""
    try:
        if json_payload is None:
            return http_client.request(method, url)
        return http_client.request(method, url, json=json_payload)
    except httpx.RequestError as error:
        print(f"Error: could not reach the GitHub API: {error}", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from error


def fetch_issue(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    item_number: ItemIdentifier,
) -> dict[str, object]:
    """Fetch the issue or pull request, or fail closed: no item, no claim."""
    response = request_or_fail(
        http_client=http_client,
        method="GET",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{item_number}",
    )
    if not response.is_success:
        print(
            f"Error: could not fetch #{item_number}"
            f" ({response.status_code}) - do not work an unverified item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    try:
        issue: object = response.json()
    except ValueError as error:
        print("Error: the item payload is not valid JSON - do not work it.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from error
    if not isinstance(issue, dict):
        print("Error: the item payload is unexpected - do not work it.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return issue


def claim_is_allowed(
    *,
    issue: dict[str, object],
    item_number: ItemIdentifier,
    expected_kind: str,
) -> None:
    """Refuse a kind mismatch or an already-claimed item.

    ``expected_kind`` is ``"issue"`` or ``"pull_request"``: the payload
    must be exactly that kind (the issues endpoint serves both), so an
    issue number passed to ``--pr`` - or the reverse - fails closed.
    """
    is_pull_request = "pull_request" in issue
    if expected_kind == "pull_request" and not is_pull_request:
        print(f"Error: #{item_number} is an issue, not a pull request.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    if expected_kind == "issue" and is_pull_request:
        print(f"Error: #{item_number} is a pull request, not an issue.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    labels = issue.get("labels", [])
    if not isinstance(labels, list) or any(not isinstance(entry, dict) for entry in labels):
        print(
            f"Error: #{item_number} has a malformed labels payload -"
            " do not work an unverified item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    label_names = {str(entry.get("name", "")) for entry in labels if isinstance(entry, dict)}
    if CLAIM_LABEL_NAME in label_names:
        print(
            f"Error: #{item_number} is already claimed"
            f" ({CLAIM_LABEL_NAME}) - do not work it; the human decides"
            " the retry.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def ensure_claim_label(*, http_client: httpx.Client, repository_slug: str) -> None:
    """Create the claim label once; a 403 means the human creates it."""
    response = request_or_fail(
        http_client=http_client,
        method="GET",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/labels/{CLAIM_LABEL_NAME}",
    )
    if response.is_success:
        return
    creation_response = request_or_fail(
        http_client=http_client,
        method="POST",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/labels",
        json_payload={
            "name": CLAIM_LABEL_NAME,
            "color": CLAIM_LABEL_COLOR,
            "description": CLAIM_LABEL_DESCRIPTION,
        },
    )
    if creation_response.is_success:
        return
    print(
        f"Error: could not create the {CLAIM_LABEL_NAME} label"
        f" ({creation_response.status_code}) - create it by hand or grant"
        " issues write on the token, then relaunch the night.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def add_claim_label(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    issue_number: int,
) -> None:
    """Add the claim label; a failure means no claim, no work."""
    response = request_or_fail(
        http_client=http_client,
        method="POST",
        url=f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{issue_number}/labels",
        json_payload={"labels": [CLAIM_LABEL_NAME]},
    )
    if not response.is_success:
        print(
            f"Error: could not claim #{issue_number}"
            f" ({response.status_code}) - do not work an unclaimed item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract: exactly one item, issue or pull request."""
    argument_parser = argparse.ArgumentParser(
        prog="claim_issue",
        description="Claim one issue or pull request for an unattended session (ADR-0017).",
    )
    item_group = argument_parser.add_mutually_exclusive_group(required=True)
    item_group.add_argument(
        "--issue",
        type=int,
        help="Issue number to claim (the session's first act).",
    )
    item_group.add_argument(
        "--pr",
        type=int,
        help="Pull request number to claim (recovery path, ADR-0017).",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: item number in, claimed item out."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    load_environment_file()
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    if arguments.issue is not None:
        item_number = ItemIdentifier(arguments.issue)
        expected_kind, item_word = "issue", "Issue"
    else:
        item_number = ItemIdentifier(arguments.pr)
        expected_kind, item_word = "pull_request", "Pull request"
    with httpx.Client(
        headers={
            "Authorization": f"{AUTHORIZATION_SCHEME} {github_token}",
            "Accept": GITHUB_ACCEPT_MEDIA_TYPE,
        },
        timeout=HTTP_TIMEOUT_SECONDS,
    ) as http_client:
        issue = fetch_issue(
            http_client=http_client,
            repository_slug=repository_slug,
            item_number=item_number,
        )
        claim_is_allowed(
            issue=issue,
            item_number=item_number,
            expected_kind=expected_kind,
        )
        ensure_claim_label(http_client=http_client, repository_slug=repository_slug)
        add_claim_label(
            http_client=http_client,
            repository_slug=repository_slug,
            issue_number=item_number,
        )
    print(
        f"{item_word} #{item_number} claimed ({CLAIM_LABEL_NAME}):"
        " it is yours, and no other session will pick it."
    )
    return EXIT_CODE_CLAIMED


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
