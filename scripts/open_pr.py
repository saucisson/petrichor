"""Pull request driver: open one pull request into daily (ADR-0017).

The developer agent opens each pull request through this script and
nothing else - never by hand, never through `gh`: the driver goes
through the GitHub API with the same token as the review and merge
drivers, so the flow works attended and unattended alike (the runtime
profile gates `gh` out, ADR-0015, and an unattended session cannot
answer an approval).

Usage::

    make pr TITLE="feat: close the door" BODY_FILE=.review/pr-body.md
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
PULL_REQUEST_TEMPLATE_PATH = ".github/PULL_REQUEST_TEMPLATE.md"
DEFAULT_BASE_BRANCH = "daily"
DETACHED_HEAD_NAME = "HEAD"
UNPROCESSABLE_ENTITY_STATUS_CODE = 422

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_OPENED = 0
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
            " repository contents read).",
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


def run_git(*, arguments: Sequence[str]) -> str:
    """Run git in the repository, capturing stdout; fail closed."""
    completed_process = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed_process.returncode != EXIT_CODE_OPENED:
        print(
            f"Error: git {' '.join(arguments)} failed: {completed_process.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return completed_process.stdout


def current_branch() -> str:
    """Return the checked-out branch, or fail closed on a detached HEAD."""
    branch = run_git(arguments=["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    if not branch or branch == DETACHED_HEAD_NAME:
        print(
            "Error: no branch is checked out (detached HEAD?) - a pull request needs a branch.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return branch


def read_pull_request_body(*, body_file: str | None) -> str:
    """Return the pull request body: the given file, or the repository template."""
    if body_file:
        body_path = Path(body_file)
        if not body_path.is_file():
            print(f"Error: no pull request body at {body_file}.", file=sys.stderr)
            raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
        return body_path.read_text(encoding="utf-8")
    template_path = REPOSITORY_ROOT / PULL_REQUEST_TEMPLATE_PATH
    if not template_path.is_file():
        print(
            f"Error: no --body-file given and no template at {PULL_REQUEST_TEMPLATE_PATH}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return template_path.read_text(encoding="utf-8")


def open_pull_request(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    title: str,
    head_branch: str,
    base_branch: str,
    body: str,
) -> dict[str, object]:
    """Open the pull request; a 422 means the state, not the API, is wrong."""
    response = http_client.post(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls",
        json={"title": title, "head": head_branch, "base": base_branch, "body": body},
    )
    if response.is_success:
        pull_request: object = response.json()
        if not isinstance(pull_request, dict):
            raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
        return pull_request
    if response.status_code == UNPROCESSABLE_ENTITY_STATUS_CODE:
        print(
            f"Pull request refused: push the branch {head_branch} first, or a"
            " pull request for it already exists.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    print(
        f"Error: the GitHub API returned {response.status_code}: {response.text}",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="open_pr",
        description="Open one pull request into daily (ADR-0017).",
    )
    argument_parser.add_argument(
        "--title",
        required=True,
        help="Pull request title (Conventional Commit).",
    )
    argument_parser.add_argument(
        "--body-file",
        default="",
        help="Path to the pull request body; defaults to the repository template.",
    )
    argument_parser.add_argument(
        "--head",
        default="",
        help="Branch to merge from; defaults to the checked-out branch.",
    )
    argument_parser.add_argument(
        "--base",
        default=DEFAULT_BASE_BRANCH,
        help="Branch to merge into (daily, ADR-0009).",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: title in, opened pull request out."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    load_environment_file()
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    head_branch = arguments.head or current_branch()
    body = read_pull_request_body(body_file=arguments.body_file or None)
    with httpx.Client(
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github.v3+json",
        },
        timeout=30.0,
    ) as http_client:
        pull_request = open_pull_request(
            http_client=http_client,
            repository_slug=repository_slug,
            title=arguments.title,
            head_branch=head_branch,
            base_branch=arguments.base,
            body=body,
        )
    pull_request_number = pull_request.get("number")
    pull_request_url = pull_request.get("html_url", "")
    print(
        f"Pull request #{pull_request_number} opened:"
        f" {head_branch} -> {arguments.base} ({pull_request_url})"
    )
    return EXIT_CODE_OPENED


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
