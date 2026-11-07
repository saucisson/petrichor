"""Local review orchestrator: one pull request, one review run (ADR-0010).

Runs on the developer machine, on the pull request branch checkout:
fetches the pull request description from the GitHub API,
materializes the diff against the pull request base, builds the review
bundle, runs the reviewer session (``scripts/agent_review.py``) and
posts the resulting comment on the pull request. The exit code is the
reviewer's verdict: 0 clean, 1 blocking findings, 2 protocol failure.

The developer agent runs this after opening a pull request, then merges
on a clean verdict (ADR-0009)::

    make review PR=42
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
NO_DESCRIPTION_TEXT = "No pull request description was provided."

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
REVIEW_DIRECTORY = REPOSITORY_ROOT / ".review"

EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1
EXIT_CODE_PROTOCOL_FAILURE = 2


def load_environment_file() -> None:
    """Read KEY=VALUE pairs from .env into the environment if unset.

    The notify tool already loads .env through its settings; these
    plain scripts read it so `GITHUB_TOKEN` can live in one place.
    Existing environment variables always win.
    """
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
            " GitHub API (fine-grained PAT: pull requests read, repository"
            " contents read).",
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


def write_review_inputs(*, pull_request: dict[str, object], review_directory: Path) -> None:
    """Write the description file the review consumes."""
    review_directory.mkdir(parents=True, exist_ok=True)
    description = pull_request.get("body")
    description_text = description.strip() if isinstance(description, str) else ""
    if not description_text:
        description_text = NO_DESCRIPTION_TEXT
    (review_directory / "pull-request-description.md").write_text(
        description_text, encoding="utf-8"
    )


def run_git(*, arguments: Sequence[str]) -> str:
    """Run git in the repository, capturing stdout; fail closed."""
    completed_process = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed_process.returncode != EXIT_CODE_SUCCESS:
        print(
            f"Error: git {' '.join(arguments)} failed: {completed_process.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return completed_process.stdout


def build_diff(*, base_ref: str, review_directory: Path) -> None:
    """Materialize the diff between the pull request base and HEAD."""
    run_git(arguments=["fetch", "origin", base_ref])
    diff_text = run_git(arguments=["diff", "--no-color", f"origin/{base_ref}...HEAD"])
    (review_directory / "pull-request.diff").write_text(diff_text, encoding="utf-8")


def run_script(*, command: Sequence[str]) -> int:
    """Run a project script; print its output; return its exit code."""
    completed_process = subprocess.run(
        list(command),
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    return completed_process.returncode


def write_review_context(
    *,
    pull_request_number: int,
    pull_request: dict[str, object],
    review_directory: Path,
) -> None:
    """Write the review context the merge driver will verify (ADR-0009).

    The report alone cannot prove which pull request it reviewed; this
    context binds it to the pull request number and the exact head SHA
    at review time. `scripts/merge_pr.py` refuses to merge unless both
    still match - a report for another PR, or for an older head, is
    stale and the merge is refused.
    """
    head_container = pull_request.get("head")
    head_sha = head_container.get("sha", "") if isinstance(head_container, dict) else ""
    review_directory.mkdir(parents=True, exist_ok=True)
    (review_directory / "review-context.json").write_text(
        json.dumps({"pr_number": pull_request_number, "head_sha": head_sha}),
        encoding="utf-8",
    )


def post_review_comment(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    pull_request_number: int,
    review_directory: Path,
) -> None:
    """Post the review comment on the pull request; warn, never mask the verdict."""
    comment_path = review_directory / "review-comment.md"
    if not comment_path.is_file():
        print("Warning: no review comment was produced (protocol failure?).")
        return
    response = http_client.post(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{pull_request_number}/comments",
        json={"body": comment_path.read_text(encoding="utf-8")},
    )
    if not response.is_success:
        print(
            f"Warning: could not post the review comment"
            f" (status {response.status_code}); the verdict is unchanged,"
            " but the audit trail is incomplete.",
            file=sys.stderr,
        )


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="pr_review",
        description="Run the local review of one pull request (ADR-0010).",
    )
    argument_parser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Number of the pull request to review.",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: pull request number in, reviewer verdict out."""
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
        pull_request = fetch_pull_request(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
        )
        base_ref_container = pull_request.get("base")
        base_ref = (
            base_ref_container.get("ref", "daily")
            if isinstance(base_ref_container, dict)
            else "daily"
        )
        print(f"Reviewing PR #{arguments.pr_number} against {base_ref}.")
        write_review_inputs(pull_request=pull_request, review_directory=REVIEW_DIRECTORY)
        write_review_context(
            pull_request_number=arguments.pr_number,
            pull_request=pull_request,
            review_directory=REVIEW_DIRECTORY,
        )
        build_diff(base_ref=base_ref, review_directory=REVIEW_DIRECTORY)
        bundle_exit_code = run_script(
            command=[
                "uv",
                "run",
                "python",
                str(SCRIPTS_DIRECTORY / "build_review_bundle.py"),
                "--diff-file",
                str(REVIEW_DIRECTORY / "pull-request.diff"),
                "--description-file",
                str(REVIEW_DIRECTORY / "pull-request-description.md"),
                "--output",
                str(REVIEW_DIRECTORY / "bundle.md"),
            ]
        )
        if bundle_exit_code != EXIT_CODE_SUCCESS:
            return EXIT_CODE_PROTOCOL_FAILURE
        review_exit_code = run_script(
            command=[
                "uv",
                "run",
                "python",
                str(SCRIPTS_DIRECTORY / "agent_review.py"),
                "--bundle-file",
                str(REVIEW_DIRECTORY / "bundle.md"),
                "--report-file",
                str(REVIEW_DIRECTORY / "review-report.json"),
                "--comment-file",
                str(REVIEW_DIRECTORY / "review-comment.md"),
            ]
        )
        post_review_comment(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
            review_directory=REVIEW_DIRECTORY,
        )
    return review_exit_code


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
