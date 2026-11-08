"""Pull request body update driver: one sanctioned write path (ADR-0019).

The review checklist resolves an over-budget pull request with a
split proposal written into the pull request body. Until now that
write had no driver: ``make pr`` creates a pull request and never
updates one, and a raw API call is denied in an unattended session -
correctly, the allowlist is the only gate (ADR-0015) - so a night
session could hit a size-budget BLOCKING and be unable to apply the
required resolution. This driver is that path:

- ``make update-pr PR=N BODY_FILE=...`` replaces the whole body of
  pull request #N with the content of BODY_FILE;
- the guard refuses anything that is not an OPEN pull request, an
  empty body, or a missing file - no silent partial writes;
- it REPLACES, it does not append: the caller composes the new body
  (the existing body plus the split proposal) into the file first.

Exit codes: 0 updated, 1 criteria unmet (not a pull request, not
open, missing or empty body file), 2 protocol failure.

Usage::

    make update-pr PR=59 BODY_FILE=split-proposal.md
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

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_UPDATED = 0
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
            f"Error: {GITHUB_TOKEN_VARIABLE} is not set - cannot update the"
            " pull request body (fine-grained PAT: pull requests read/write).",
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


def read_new_body(*, body_file: str) -> str:
    """Read the replacement body; missing or empty means criteria unmet."""
    body_path = Path(body_file)
    if not body_path.is_file():
        print(
            f"Error: the body file does not exist: {body_file} - compose it"
            " (existing body plus the change) and retry.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    new_body = body_path.read_text(encoding="utf-8")
    if not new_body.strip():
        print(
            f"Error: the body file is empty: {body_file} - an empty pull"
            " request body is never the intent.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    return new_body


def fetch_issue(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    item_number: int,
) -> dict[str, object]:
    """Fetch the item, or fail closed: no item, no update."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{item_number}"
    )
    if not response.is_success:
        print(
            f"Error: could not fetch #{item_number}"
            f" ({response.status_code}) - do not update an unverified item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    issue: object = response.json()
    if not isinstance(issue, dict):
        print("Error: the item payload is unexpected - do not update it.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return issue


def update_is_allowed(*, issue: dict[str, object], item_number: int) -> None:
    """Refuse anything that is not an open pull request."""
    if "pull_request" not in issue:
        print(f"Error: #{item_number} is an issue, not a pull request.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    state = issue.get("state")
    if state != "open":
        print(
            f"Error: pull request #{item_number} is {state}, not open - a"
            " closed pull request is the morning audit's business, not a"
            " body update.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def write_body(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
    new_body: str,
) -> None:
    """Replace the pull request body; a failure means no update."""
    response = http_client.patch(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}",
        json={"body": new_body},
    )
    if not response.is_success:
        print(
            f"Error: could not update pull request #{pull_request_number}"
            f" ({response.status_code}) - the body is unchanged.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="update_pr",
        description="Replace a pull request body from a file (ADR-0019).",
    )
    argument_parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Pull request number whose body will be replaced.",
    )
    argument_parser.add_argument(
        "--body-file",
        required=True,
        help="File carrying the FULL new body (the driver replaces, it never appends).",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: pull request number and body file in, updated body out."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    new_body = read_new_body(body_file=arguments.body_file)
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
            item_number=arguments.pr_number,
        )
        update_is_allowed(issue=issue, item_number=arguments.pr_number)
        write_body(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
            new_body=new_body,
        )
    print(
        f"Pull request #{arguments.pr_number} body updated from"
        f" {arguments.body_file}: the WHOLE body now comes from that file"
        " (ADR-0019 - the driver replaces, it never appends)."
    )
    return EXIT_CODE_UPDATED


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
