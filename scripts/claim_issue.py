"""Issue claim driver: mark an issue taken before working it (ADR-0017).

In the auto-discovered night loop (``make night`` without ISSUES) each
session picks its own issue, so its FIRST act is to claim it:
``make claim ISSUE=N`` adds the ``night-claimed`` label through the
GitHub API, with the same token as the other drivers. The claim is
both the concurrency guard and the honesty record:

- two sessions never work the same issue (the loop filters claimed
  issues out of every worklist, and this driver refuses to claim
  twice);
- a failed attempt stays claimed: the morning audit sees what was
  tried, and the human - never an agent - decides the retry.

Usage::

    make claim ISSUE=12
"""

import argparse
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


def fetch_issue(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    issue_number: int,
) -> dict[str, object]:
    """Fetch the issue, or fail closed: no issue, no claim, no work."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{issue_number}"
    )
    if not response.is_success:
        print(
            f"Error: could not fetch issue #{issue_number}"
            f" ({response.status_code}) - do not work an unverified issue.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    issue: object = response.json()
    if not isinstance(issue, dict):
        print("Error: the issue payload is unexpected - do not work it.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return issue


def claim_is_allowed(*, issue: dict[str, object], issue_number: int) -> None:
    """Refuse to claim a pull request or an already-claimed issue."""
    if "pull_request" in issue:
        print(f"Error: #{issue_number} is a pull request, not an issue.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    labels = issue.get("labels", [])
    if isinstance(labels, list):
        label_names = {entry.get("name", "") for entry in labels if isinstance(entry, dict)}
        if CLAIM_LABEL_NAME in label_names:
            print(
                f"Error: issue #{issue_number} is already claimed"
                f" ({CLAIM_LABEL_NAME}) - do not work it; the human decides"
                " the retry.",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def ensure_claim_label(*, http_client: httpx.Client, repository_slug: str) -> None:
    """Create the claim label once; a 403 means the human creates it."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/labels/{CLAIM_LABEL_NAME}"
    )
    if response.is_success:
        return
    creation_response = http_client.post(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/labels",
        json={
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
    response = http_client.post(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{issue_number}/labels",
        json={"labels": [CLAIM_LABEL_NAME]},
    )
    if not response.is_success:
        print(
            f"Error: could not claim issue #{issue_number}"
            f" ({response.status_code}) - do not work an unclaimed issue.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="claim_issue",
        description="Claim one issue for an unattended session (ADR-0017).",
    )
    argument_parser.add_argument(
        "--issue",
        type=int,
        required=True,
        help="Issue number to claim (the session's first act).",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: issue number in, claimed issue out."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    load_environment_file()
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    with httpx.Client(
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json",
        },
        timeout=30.0,
    ) as http_client:
        issue = fetch_issue(
            http_client=http_client,
            repository_slug=repository_slug,
            issue_number=arguments.issue,
        )
        claim_is_allowed(issue=issue, issue_number=arguments.issue)
        ensure_claim_label(http_client=http_client, repository_slug=repository_slug)
        add_claim_label(
            http_client=http_client,
            repository_slug=repository_slug,
            issue_number=arguments.issue,
        )
    print(
        f"Issue #{arguments.issue} claimed ({CLAIM_LABEL_NAME}):"
        " it is yours, and no other session will pick it."
    )
    return EXIT_CODE_CLAIMED


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
