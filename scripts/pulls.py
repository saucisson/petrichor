"""Pull request drivers: open one, update one body (ADR-0007, ADR-0005).

``pr`` opens each pull request into ``daily`` through the GitHub API
and nothing else - never by hand, never through ``gh``: the driver
shares the token and the protocol of the review and merge drivers, so
the flow works attended and unattended alike (the runtime profile gates
``gh`` out, ADR-0006, and an unattended session cannot answer an
approval). After opening, it writes the worklog index card
(``docs/worklog/<date>-pr-<N>.md``, ADR-0005), commits it on the branch
as ``worklog: PR #N``, and pushes.

``update-pr`` replaces the whole body of an OPEN pull request from a
file (ADR-0005): the review checklist resolves an over-budget pull
request with a split proposal written into the body, and this is the
one sanctioned write path for it. It REPLACES, it never appends: the
caller composes the new body (the existing body plus the change) into
the file first.
"""

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from scripts._github import open_github_client
from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
    GITHUB_API_BASE_URL,
    REPOSITORY_ROOT,
    load_github_token,
    resolve_repository_slug,
    run_git,
    try_git,
)

if TYPE_CHECKING:
    import argparse

PULL_REQUEST_TEMPLATE_PATH = ".github/PULL_REQUEST_TEMPLATE.md"
DEFAULT_BASE_BRANCH = "daily"
DETACHED_HEAD_NAME = "HEAD"
UNPROCESSABLE_ENTITY_STATUS_CODE = 422
EXIT_CODE_OPENED = EXIT_CODE_SUCCESS  # a subcommand-local alias, same protocol code
EXIT_CODE_UPDATED = EXIT_CODE_SUCCESS  # a subcommand-local alias, same protocol code
WORKLOG_DIR = Path("docs/worklog")
ISSUE_REFERENCE_PATTERN = re.compile(r"#(\d+)")
CLOSES_ISSUE_PATTERN = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)


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
    try:
        response = http_client.post(
            f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls",
            json={"title": title, "head": head_branch, "base": base_branch, "body": body},
        )
    except httpx.TransportError as transport_error:
        print(
            f"Error: network failure while opening the pull request: {transport_error}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from transport_error
    if response.is_success:
        try:
            pull_request: object = response.json()
        except json.JSONDecodeError as decode_error:
            print(f"Error: undecodable API response: {decode_error}", file=sys.stderr)
            raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from decode_error
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


def register_pr(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the pr subcommand."""
    subparser = subparsers.add_parser(
        "pr",
        help="Open one pull request into daily (ADR-0007).",
        description="Open one pull request into daily (ADR-0007).",
    )
    subparser.add_argument(
        "--title",
        required=True,
        help="Pull request title (Conventional Commit).",
    )
    subparser.add_argument(
        "--body-file",
        default="",
        help="Path to the pull request body; defaults to the repository template.",
    )
    subparser.add_argument(
        "--head",
        default="",
        help="Branch to merge from; defaults to the checked-out branch.",
    )
    subparser.add_argument(
        "--base",
        default=DEFAULT_BASE_BRANCH,
        help="Branch to merge into (daily, ADR-0005).",
    )
    subparser.set_defaults(runner=run_pr)


def extract_issue_number(*, body: str) -> str | None:
    """Extract the issue closed by the pull request body.

    Prefers a ``Closes #N`` / ``Fixes #N`` / ``Resolves #N`` anchor;
    falls back to the first ``#N`` reference.
    """
    closes_match = CLOSES_ISSUE_PATTERN.search(body)
    if closes_match:
        return closes_match.group(1)
    fallback = ISSUE_REFERENCE_PATTERN.search(body)
    return fallback.group(1) if fallback else None


def write_worklog_entry(
    *,
    pull_request_number: int,
    title: str,
    head_branch: str,
    body: str,
    repository_slug: str,
) -> Path:
    """Write the worklog index card for the pull request (ADR-0005).

    Returns the path of the written file. Idempotent: an existing file
    for the same PR is not overwritten.
    """
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    worklog_dir = REPOSITORY_ROOT / WORKLOG_DIR
    worklog_dir.mkdir(parents=True, exist_ok=True)
    entry_path = worklog_dir / f"{today}-pr-{pull_request_number}.md"
    if entry_path.exists():
        return entry_path
    issue_number = extract_issue_number(body=body)
    issue_line = f"- Issue: #{issue_number}" if issue_number else "- Issue: none"
    pull_request_url = f"https://github.com/{repository_slug}/pull/{pull_request_number}"
    content = (
        f"# PR #{pull_request_number}: {title}\n\n"
        f"- Date: {today}\n"
        f"- Branch: {head_branch}\n"
        f"{issue_line}\n"
        f"- Report: {pull_request_url}\n"
    )
    entry_path.write_text(content, encoding="utf-8")
    return entry_path


def commit_and_push_worklog_entry(*, entry_path: Path, pull_request_number: int) -> bool:
    """Commit the worklog entry on the current branch and push (ADR-0005).

    Returns True when a commit was created and pushed, False when the
    entry was already committed (idempotent re-run). A real commit error
    (failing hook, missing identity) is not swallowed: it propagates as
    a protocol failure.
    """
    run_git(arguments=["add", str(entry_path)])
    staged = try_git(arguments=["diff", "--cached", "--quiet"])
    if staged.returncode != 0:
        run_git(arguments=["commit", "-m", f"worklog: PR #{pull_request_number}"])
        run_git(arguments=["push"])
        return True
    return False


def run_pr(*, arguments: argparse.Namespace) -> int:
    """Entry point: title in, opened pull request out."""
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    head_branch = arguments.head or current_branch()
    body = read_pull_request_body(body_file=arguments.body_file or None)
    with open_github_client(token=github_token) as http_client:
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
    if not isinstance(pull_request_number, int):
        print("Error: no pull request number in the API response.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    entry_path = write_worklog_entry(
        pull_request_number=pull_request_number,
        title=arguments.title,
        head_branch=head_branch,
        body=body,
        repository_slug=repository_slug,
    )
    committed = commit_and_push_worklog_entry(
        entry_path=entry_path, pull_request_number=pull_request_number
    )
    print(f"Worklog entry written: {entry_path.relative_to(REPOSITORY_ROOT)}")
    if not committed:
        print("Worklog entry already committed (idempotent re-run).")
    return EXIT_CODE_OPENED


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
    try:
        response = http_client.get(
            f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{item_number}"
        )
    except httpx.TransportError as transport_error:
        print(
            f"Error: network failure while fetching #{item_number}: {transport_error}"
            " - the body is unchanged.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from transport_error
    if not response.is_success:
        print(
            f"Error: could not fetch #{item_number}"
            f" ({response.status_code}) - do not update an unverified item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    try:
        issue: object = response.json()
    except json.JSONDecodeError as decode_error:
        print(f"Error: undecodable API response: {decode_error}", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from decode_error
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
    try:
        response = http_client.patch(
            f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls/{pull_request_number}",
            json={"body": new_body},
        )
    except httpx.TransportError as transport_error:
        print(
            f"Error: network failure while updating pull request"
            f" #{pull_request_number}: {transport_error} - the body is unchanged.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from transport_error
    if not response.is_success:
        print(
            f"Error: could not update pull request #{pull_request_number}"
            f" ({response.status_code}) - the body is unchanged.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def register_update_pr(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the update-pr subcommand."""
    subparser = subparsers.add_parser(
        "update-pr",
        help="Replace a pull request body from a file (ADR-0005).",
        description="Replace a pull request body from a file (ADR-0005).",
    )
    subparser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Pull request number whose body will be replaced.",
    )
    subparser.add_argument(
        "--body-file",
        required=True,
        help="File carrying the FULL new body (the driver replaces, it never appends).",
    )
    subparser.set_defaults(runner=run_update_pr)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register both pull request subcommands."""
    register_pr(subparsers)
    register_update_pr(subparsers)


def run_update_pr(*, arguments: argparse.Namespace) -> int:
    """Entry point: pull request number and body file in, updated body out."""
    new_body = read_new_body(body_file=arguments.body_file)
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    with open_github_client(token=github_token) as http_client:
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
        " (ADR-0005 - the driver replaces, it never appends)."
    )
    return EXIT_CODE_UPDATED
