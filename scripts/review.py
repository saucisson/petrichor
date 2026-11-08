"""Local review orchestrator and bundle builder (ADR-0010).

``review`` runs on the developer machine, on the pull request branch
checkout: it fetches the pull request description from the GitHub API,
materializes the diff against the pull request base, builds the review
bundle, runs the reviewer session (``python -m scripts agent-review``)
and posts the resulting comment on the pull request. The exit code is
the reviewer's verdict: 0 clean, 1 blocking findings, 2 protocol
failure, 3 preconditions unmet (the review never started: head not
pushed or CI not concluded on it - not a verdict, not an attempt).

``review-bundle`` assembles the deterministic review bundle: the
reviewer's ENTIRE context - role contract, architecture rules, review
checklist, the relevant ADRs, the pull request specification and the
diff under review. Nothing else enters the prompt - context isolation
is enforced by construction, not by prompt discipline. Stdlib only,
so it runs in the bareest CI image: this module defers its
``httpx``-dependent imports into the ``review`` runner, on purpose.

The developer agent runs ``review`` after opening a pull request and
only once CI has concluded on the pushed head: the driver refuses to
review a stale head or a red one - the review is the last gate, never
a substitute for a failing local gate (ADR-0010).
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
    GITHUB_API_BASE_URL,
    REPOSITORY_ROOT,
    load_github_token,
    resolve_repository_slug,
    run_git,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

    import httpx

REVIEW_DIRECTORY = REPOSITORY_ROOT / ".review"
ROLE_DOCUMENT_PATH = REPOSITORY_ROOT / "docs/agents/reviewer.md"
ARCHITECTURE_DOCUMENT_PATH = REPOSITORY_ROOT / "docs/architecture.md"
CHECKLIST_DOCUMENT_PATH = REPOSITORY_ROOT / "docs/review-checklist.md"
DECISIONS_DIRECTORY_PATH = REPOSITORY_ROOT / "docs/decisions"
SECTION_SEPARATOR = "\n\n---\n\n"
NO_DESCRIPTION_TEXT = "No pull request description was provided."
EXIT_CODE_FAILURE = EXIT_CODE_CRITERIA_UNMET  # local alias: a bundle component is missing
EXIT_CODE_PRECONDITIONS_UNMET = 3  # the review never started: not a verdict, not an attempt


def read_text_file(*, path: Path) -> str:
    """Read one text file, failing loudly on a missing bundle component."""
    if not path.is_file():
        print(f"Error: bundle component missing: {path}", file=sys.stderr)
        raise SystemExit(EXIT_CODE_FAILURE)
    return path.read_text(encoding="utf-8")


def collect_decisions() -> list[tuple[str, str]]:
    """Return every ADR as (title, content), in numeric order."""
    decision_paths = sorted(DECISIONS_DIRECTORY_PATH.glob("adr-*.md"))
    decisions: list[tuple[str, str]] = []
    for decision_path in decision_paths:
        decision_title = decision_path.stem.replace("-", " ").capitalize()
        decisions.append((decision_title, decision_path.read_text(encoding="utf-8")))
    return decisions


def render_bundle(
    *,
    diff_text: str,
    description_text: str,
    decisions: Sequence[tuple[str, str]],
) -> str:
    """Assemble the bundle sections in a fixed, deterministic order."""
    sections: list[str] = []
    sections.append("## ROLE CONTRACT\n\n" + read_text_file(path=ROLE_DOCUMENT_PATH))
    sections.append("## ARCHITECTURE RULES\n\n" + read_text_file(path=ARCHITECTURE_DOCUMENT_PATH))
    sections.append("## REVIEW CHECKLIST\n\n" + read_text_file(path=CHECKLIST_DOCUMENT_PATH))
    for decision_title, decision_content in decisions:
        sections.append(f"## DECISION - {decision_title}\n\n{decision_content}")
    sections.append(f"## PULL REQUEST SPECIFICATION\n\n{description_text}")
    sections.append(f"## DIFF UNDER REVIEW\n\n{diff_text}")
    return SECTION_SEPARATOR.join(sections)


def register_review_bundle(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the review-bundle subcommand: one diff source, optional description."""
    subparser = subparsers.add_parser(
        "review-bundle",
        help="Assemble the deterministic review bundle (ADR-0010).",
        description="Assemble the deterministic review bundle (ADR-0010).",
    )
    diff_source = subparser.add_mutually_exclusive_group(required=True)
    diff_source.add_argument(
        "--diff-file",
        help="Path to a file containing the diff under review.",
    )
    diff_source.add_argument(
        "--base-ref",
        help="Git ref to diff against (merge base with HEAD), e.g. origin/daily.",
    )
    subparser.add_argument(
        "--description-file",
        default=None,
        help="Path to the pull request description (the specification).",
    )
    subparser.add_argument(
        "--output",
        default=None,
        help="Path to write the bundle to; defaults to stdout.",
    )
    subparser.set_defaults(runner=run_review_bundle)


def run_review_bundle(*, arguments: argparse.Namespace) -> int:
    """Entry point: gather every component, print or write the bundle."""
    if arguments.diff_file is not None:
        diff_text = read_text_file(path=Path(arguments.diff_file))
    else:
        diff_text = run_git(arguments=["diff", "--no-color", f"{arguments.base_ref}...HEAD"])
    if arguments.description_file is not None:
        description_text = read_text_file(path=Path(arguments.description_file))
    else:
        description_text = NO_DESCRIPTION_TEXT
    bundle_text = render_bundle(
        diff_text=diff_text,
        description_text=description_text,
        decisions=collect_decisions(),
    )
    if arguments.output is not None:
        Path(arguments.output).write_text(bundle_text, encoding="utf-8")
    else:
        print(bundle_text)
    return EXIT_CODE_SUCCESS


def review_preconditions_refusal(
    *,
    head_sha: str,
    local_head: str,
    pending_changes: str,
    check_runs: Sequence[dict[str, object]],
) -> str | None:
    """Return the reason the review cannot start; None means: proceed.

    The review is the last gate: it never substitutes for a failing or
    unfinished local gate (ADR-0010). Every refusal exits with code 3
    (preconditions unmet): not a review verdict, not an attempt - the
    developer agent waits (or pushes), then re-runs ``make review``.
    """
    if head_sha != local_head:
        return (
            f"the pull request head {head_sha} is not the local HEAD"
            f" {local_head} - push, wait for CI, then re-run `make review`"
        )
    if pending_changes:
        return (
            "uncommitted changes are present - commit, push, wait for CI, then re-run `make review`"
        )
    if not check_runs:
        return "no CI check runs on the head yet - wait for CI, then re-run `make review`"
    for check_run in check_runs:
        name = str(check_run.get("name", "unnamed check"))
        if check_run.get("status") != "completed":
            return f"CI check {name} is still running - wait for CI, then re-run `make review`"
        if check_run.get("conclusion") != "success":
            return f"CI check {name} did not succeed - fix CI first, then re-run `make review`"
    return None


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


def build_diff(*, base_ref: str, review_directory: Path) -> None:
    """Materialize the diff between the pull request base and HEAD."""
    run_git(arguments=["fetch", "origin", base_ref])
    diff_text = run_git(arguments=["diff", "--no-color", f"origin/{base_ref}...HEAD"])
    (review_directory / "pull-request.diff").write_text(diff_text, encoding="utf-8")


def run_script(*, command: Sequence[str]) -> int:
    """Run one project driver subcommand; print its output; return its exit code."""
    completed_process = subprocess.run(
        list(command),
        cwd=REPOSITORY_ROOT,
        check=False,
    )
    return completed_process.returncode


def write_review_context(
    *,
    pull_request_number: int,
    head_sha: str,
    review_directory: Path,
) -> None:
    """Write the review context the merge driver will verify (ADR-0009).

    The report alone cannot prove which pull request it reviewed; this
    context binds it to the pull request number and the exact head SHA
    at review time. The merge driver refuses to merge unless both
    still match - a report for another pull request, or for an older
    head, is stale and the merge is refused.
    """
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
    """Post the review comment; warn, never mask the verdict."""
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


def register_review(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the review subcommand."""
    subparser = subparsers.add_parser(
        "review",
        help="Run the local review of one pull request (ADR-0010).",
        description="Run the local review of one pull request (ADR-0010).",
    )
    subparser.add_argument(
        "--pr-number",
        type=int,
        required=True,
        help="Number of the pull request to review.",
    )
    subparser.set_defaults(runner=run_review)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register both review subcommands."""
    register_review(subparsers)
    register_review_bundle(subparsers)


def run_review(*, arguments: argparse.Namespace) -> int:
    """Entry point: pull request number in, reviewer verdict out."""
    # Deferred import, on purpose: review-bundle (this module's other
    # subcommand) stays stdlib-only and runs in the bareest CI image.
    from scripts import _github  # noqa: PLC0415

    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    with _github.open_github_client(token=github_token) as http_client:
        pull_request = _github.fetch_pull_request(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
        )
        head_sha = _github.extract_head_sha(pull_request=pull_request)
        local_head = run_git(arguments=["rev-parse", "HEAD"]).strip()
        pending_changes = run_git(arguments=["status", "--porcelain"]).strip()
        check_runs = _github.fetch_check_runs(
            http_client=http_client,
            repository_slug=repository_slug,
            head_sha=head_sha,
        )
        refusal_reason = review_preconditions_refusal(
            head_sha=head_sha,
            local_head=local_head,
            pending_changes=pending_changes,
            check_runs=check_runs,
        )
        if refusal_reason is not None:
            print(f"Review refused: {refusal_reason}.", file=sys.stderr)
            return EXIT_CODE_PRECONDITIONS_UNMET
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
            head_sha=head_sha,
            review_directory=REVIEW_DIRECTORY,
        )
        build_diff(base_ref=base_ref, review_directory=REVIEW_DIRECTORY)
        bundle_exit_code = run_script(
            command=[
                sys.executable,
                "-m",
                "scripts",
                "review-bundle",
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
                sys.executable,
                "-m",
                "scripts",
                "agent-review",
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
