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
is enforced by construction, not by prompt discipline. Machine
artifacts (execution records, ``audit.json``, golden corpus, vendored
PNML) are excluded from the diff and replaced by digest lines (P58,
ADR-0010 appendix 2026-11-19). A bundle larger than
``REVIEWER_BUNDLE_MAX_BYTES`` (default 256 KiB, P59) is refused before
any session starts, with the "preconditions unmet" exit code.

P61 (issue #133): review attempts are journaled per pull request and
head in ``.review/review-attempts.jsonl`` with a typed cause (deadline /
turn-limit / preconditions / protocol - read from the session's failure
report, never a bare exit code). After ``REVIEWER_MAX_ATTEMPTS`` failed
session attempts (default 2) the review is refused before any new
session starts (exit 3, not a verdict, not an attempt) and the human is
escalated via the existing notify pipeline (``make notify``). Session
attempts also journal the bundle sizes (total and diff part, P62) so the
day-report can confront bundle size with failures. Stdlib
only, so it runs in the bareest CI image: this module defers its
``httpx``-dependent imports into the ``review`` runner, on purpose.

The developer agent runs ``review`` after opening a pull request and
only once CI has concluded on the pushed head: the driver refuses to
review a stale head or a red one - the review is the last gate, never
a substitute for a failing local gate (ADR-0010).
"""

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from enum import StrEnum
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

# P58 (issue #130, docs/spikes/2026-11-19-reviewer-reliability.md): machine
# artifacts never enter the DIFF UNDER REVIEW - they are replaced by a digest
# line (path, size, sha256, controlling test). The exclusion list is
# deterministic and documented in the ADR-0010 appendix dated 2026-11-19.
MACHINE_ARTIFACT_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    ("docs/studies/**/execution/**", "make study-p-audit"),
    ("**/audit.json", "make study-p-audit"),
    (
        "src/petrichor/llm/golden/**",
        "tests/test_llm_guard.py::test_no_golden_file_contains_a_verdict",
    ),
    (
        "src/petrichor/study/**/*.pnml",
        "tests/test_study_pilote.py::test_verify_materials_returns_the_pinned_hashes",
    ),
)
EXCLUDED_DIGEST_SECTION_HEADER = "### Machine artifacts excluded from the diff (digests)"
DIFF_FILE_HEADER_PATTERN = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$", re.MULTILINE)

# P59 (issue #131): a bundle too large to review reliably is refused BEFORE the
# session starts, with the semantics of the "preconditions unmet" exit code (3):
# not a verdict, not an attempt. Default 256 KiB: the static part of the bundle
# (role contract + architecture + checklist + all ADRs) measured ~84 KiB on
# 2026-11-19, and the P42 incident bundle reached 164 KiB and died on deadline
# then turn limit (docs/spikes/2026-11-19-reviewer-reliability.md).
BUNDLE_SIZE_LIMIT_VARIABLE = "REVIEWER_BUNDLE_MAX_BYTES"
DEFAULT_BUNDLE_MAX_BYTES = 256 * 1024
DIFF_SECTION_HEADER = "## DIFF UNDER REVIEW"

# P61 (issue #133, docs/spikes/2026-11-19-reviewer-reliability.md): bounded
# retries. Every review invocation appends one line to the attempts journal
# (``.review/review-attempts.jsonl``), keyed by pull request + head SHA, with
# a typed cause. After REVIEWER_MAX_ATTEMPTS failed SESSION attempts (default
# 2 - the P40/P42 incidents each burned several full sessions), the review is
# refused before any new session starts: exit 3 (preconditions unmet - not a
# verdict, not an attempt), the pull request stays open, nothing merges on a
# failed review (fail-closed unchanged), and the human is escalated through
# the existing notify pipeline. A new head SHA starts a fresh attempt count.
MAX_ATTEMPTS_VARIABLE = "REVIEWER_MAX_ATTEMPTS"
DEFAULT_MAX_ATTEMPTS = 2
ATTEMPTS_JOURNAL_FILE_NAME = "review-attempts.jsonl"
NOTIFY_SEVERITY_URGENT = "urgent"
NOTIFY_COMMAND: tuple[str, ...] = ("uv", "run", "notify", "send")


class AttemptPhase(StrEnum):
    """The closed set of review invocation phases journaled per attempt (P61)."""

    PRECONDITIONS = "preconditions"
    BUNDLE = "bundle"
    SESSION = "session"
    CAP = "cap"


class AttemptCause(StrEnum):
    """The closed set of journaled outcome causes (P61): never a bare exit code."""

    CLEAN = "clean"
    BLOCKING = "blocking"
    PRECONDITIONS = "preconditions"
    PROTOCOL = "protocol"
    DEADLINE = "deadline"
    TURN_LIMIT = "turn-limit"


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


def glob_match(*, pattern: str, path: str) -> bool:
    """Match a git-style glob against a repo path, with git ``**`` semantics.

    A ``**`` segment matches zero or more whole path segments, so
    ``docs/studies/**/execution/**`` also matches ``docs/studies/execution/x``
    and ``**/audit.json`` also matches a root-level ``audit.json``.
    """
    return _match_segments(
        pattern_segments=pattern.split("/"),
        path_segments=path.split("/"),
    )


def _match_segments(*, pattern_segments: list[str], path_segments: list[str]) -> bool:
    """Recursive segment matcher: ``**`` consumes zero or more path segments."""
    if not pattern_segments:
        return not path_segments
    head = pattern_segments[0]
    if head == "**":
        return any(
            _match_segments(
                pattern_segments=pattern_segments[1:],
                path_segments=path_segments[offset:],
            )
            for offset in range(len(path_segments) + 1)
        )
    if not path_segments:
        return False
    return fnmatch.fnmatchcase(path_segments[0], head) and _match_segments(
        pattern_segments=pattern_segments[1:],
        path_segments=path_segments[1:],
    )


def path_is_excluded(*, path: str) -> bool:
    """Tell whether a changed path is a machine artifact excluded from the diff (P58)."""
    normalized = path.strip("/")
    return any(
        glob_match(pattern=pattern, path=normalized)
        for pattern, _controller in MACHINE_ARTIFACT_EXCLUSIONS
    )


def controller_for_path(*, path: str) -> str:
    """Return the test or command that controls an excluded machine artifact."""
    normalized = path.strip("/")
    for pattern, controller in MACHINE_ARTIFACT_EXCLUSIONS:
        if glob_match(pattern=pattern, path=normalized):
            return controller
    return "aucun"


def digest_for_head_path(*, path: str) -> tuple[int, str] | None:
    """Return (size in bytes, sha256) of the file at HEAD; None when absent at HEAD."""
    completed = subprocess.run(
        ["git", "cat-file", "blob", f"HEAD:{path}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    payload = completed.stdout
    return len(payload), hashlib.sha256(payload).hexdigest()


def digest_for_path(*, path: str) -> tuple[int, str] | None:
    """Digest of a changed file: HEAD blob first, working-tree file for additions.

    A machine artifact newly added by the PR is absent at HEAD; its digest is
    then computed from the checked-out file. None only when the file is truly
    gone (deleted in the diff).
    """
    head_digest = digest_for_head_path(path=path)
    if head_digest is not None:
        return head_digest
    working_file = REPOSITORY_ROOT / path
    if working_file.is_file():
        payload = working_file.read_bytes()
        return len(payload), hashlib.sha256(payload).hexdigest()
    return None


def split_diff_chunks(*, diff_text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split a unified diff into (preamble, [(path, chunk)]) per ``diff --git`` header."""
    matches = list(DIFF_FILE_HEADER_PATTERN.finditer(diff_text))
    preamble = diff_text[: matches[0].start()] if matches else diff_text
    chunks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(diff_text)
        chunks.append((match.group("new"), diff_text[match.start() : end]))
    return preamble, chunks


def load_bundle_max_bytes() -> int:
    """The bundle size limit in bytes (``REVIEWER_BUNDLE_MAX_BYTES``, P59)."""
    raw_value = os.environ.get(BUNDLE_SIZE_LIMIT_VARIABLE, "").strip()
    if not raw_value:
        return DEFAULT_BUNDLE_MAX_BYTES
    try:
        return int(raw_value)
    except ValueError:
        print(
            f"Error: {BUNDLE_SIZE_LIMIT_VARIABLE} is not an integer: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from None


def bundle_size_refusal(*, bundle_text: str, diff_text: str) -> str | None:
    """Return the refusal reason when the bundle exceeds the size limit; None otherwise.

    Both the total bundle and the diff part are measured and reported, so the
    message stays actionable: split the pull request, or check the P58
    machine-artifact exclusions.
    """
    limit = load_bundle_max_bytes()
    total_bytes = len(bundle_text.encode("utf-8"))
    if total_bytes <= limit:
        return None
    diff_bytes = len(diff_text.encode("utf-8"))
    return (
        f"bundle too large: {total_bytes} bytes total (diff part {diff_bytes} bytes)"
        f" exceeds the limit of {limit} bytes ({BUNDLE_SIZE_LIMIT_VARIABLE})"
        " - split the pull request or check the P58 machine-artifact exclusions"
    )


def load_max_attempts() -> int:
    """The per-head cap on failed review session attempts (``REVIEWER_MAX_ATTEMPTS``, P61).

    Must be a positive integer: 0 or negative would refuse every review
    before any session starts, which is never the intent.
    """
    raw_value = os.environ.get(MAX_ATTEMPTS_VARIABLE, "").strip()
    if not raw_value:
        return DEFAULT_MAX_ATTEMPTS
    try:
        max_attempts = int(raw_value)
    except ValueError:
        print(
            f"Error: {MAX_ATTEMPTS_VARIABLE} is not an integer: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from None
    if max_attempts < 1:
        print(
            f"Error: {MAX_ATTEMPTS_VARIABLE} must be >= 1: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from None
    return max_attempts


def load_attempts(
    *, journal_path: Path, pull_request_number: int, head_sha: str
) -> list[dict[str, object]]:
    """Read the journaled attempts for one pull request head (P61).

    A missing or unreadable journal means zero attempts - the cap never
    blocks a first review because of a corrupt journal.
    """
    if not journal_path.is_file():
        return []
    attempts: list[dict[str, object]] = []
    for raw_line in journal_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("pr_number") != pull_request_number or entry.get("head_sha") != head_sha:
            continue
        attempts.append(entry)
    return attempts


def count_failed_session_attempts(*, attempts: list[dict[str, object]]) -> int:
    """Count the journaled SESSION attempts that failed (P61).

    Only real session runs count toward the cap: precondition and bundle
    refusals never started a session, they are not attempts.
    """
    return sum(
        1
        for entry in attempts
        if entry.get("phase") == AttemptPhase.SESSION.value
        and entry.get("exit_code") != EXIT_CODE_SUCCESS
    )


def measure_bundle_sizes(*, review_directory: Path) -> tuple[int, int]:
    """Return (total bundle bytes, diff-part bytes) of the built bundle (P62).

    The diff part is everything after the ``## DIFF UNDER REVIEW`` header in
    ``bundle.md`` - the sanitized diff the reviewer actually reads. (0, 0)
    when the bundle was never built.
    """
    bundle_path = review_directory / "bundle.md"
    if not bundle_path.is_file():
        return 0, 0
    bundle_text = bundle_path.read_text(encoding="utf-8")
    total_bytes = len(bundle_text.encode("utf-8"))
    marker_index = bundle_text.find(DIFF_SECTION_HEADER)
    diff_part = bundle_text[marker_index + len(DIFF_SECTION_HEADER) :] if marker_index != -1 else ""
    return total_bytes, len(diff_part.encode("utf-8"))


def record_attempt(
    *,
    journal_path: Path,
    pull_request_number: int,
    head_sha: str,
    phase: AttemptPhase,
    exit_code: int,
    cause: AttemptCause,
    escalated: bool,
    bundle_sizes: tuple[int, int] | None = None,
) -> None:
    """Append one attempt line to the journal (P61); best-effort, never masks the verdict.

    Session attempts also carry the bundle sizes as ``(total, diff part)``
    (P62) so the day-report can plot bundle size against failures without
    any hand-entered data.
    """
    entry: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "pr_number": pull_request_number,
        "head_sha": head_sha,
        "phase": phase.value,
        "exit_code": exit_code,
        "cause": cause.value,
        "attempt": phase == AttemptPhase.SESSION,
        "escalated": escalated,
    }
    if bundle_sizes is not None:
        entry["bundle_bytes"], entry["diff_bytes"] = bundle_sizes
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        with journal_path.open("a", encoding="utf-8") as journal_file:
            journal_file.write(json.dumps(entry) + "\n")
    except OSError as journal_error:
        print(
            f"Warning: could not journal the review attempt ({journal_error});"
            " the verdict is unchanged.",
            file=sys.stderr,
        )


def cause_for_exit_code(*, exit_code: int, review_directory: Path) -> AttemptCause:
    """The typed cause of a review outcome (P61): never a bare exit code.

    Exit 0/1 are verdicts (clean / blocking findings). Exit 2 reads the
    session's failure report for the precise cause (deadline / turn-limit /
    protocol); exit 3 is preconditions.
    """
    if exit_code == EXIT_CODE_SUCCESS:
        return AttemptCause.CLEAN
    if exit_code == EXIT_CODE_CRITERIA_UNMET:
        return AttemptCause.BLOCKING
    if exit_code == EXIT_CODE_PRECONDITIONS_UNMET:
        return AttemptCause.PRECONDITIONS
    report_path = review_directory / "review-report.json"
    if report_path.is_file():
        try:
            report: object = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = None
        if isinstance(report, dict):
            failure_cause = report.get("failure_cause")
            if isinstance(failure_cause, str) and failure_cause:
                try:
                    return AttemptCause(failure_cause)
                except ValueError:
                    pass
    return AttemptCause.PROTOCOL


def escalate_to_human(
    *,
    pull_request_number: int,
    head_sha: str,
    failed_attempts: int,
    max_attempts: int,
    last_cause: AttemptCause,
) -> None:
    """Escalate to the human via the existing notify pipeline (P61, ``make notify``).

    Best-effort by contract: a notification failure never masks the review
    verdict and never raises.
    """
    message = (
        f"Revue de la PR #{pull_request_number} (head {head_sha[:8]}) :"
        f" {failed_attempts}/{max_attempts} tentatives de session épuisées sans revue propre"
        f" (dernier échec : {last_cause.value}). Aucune fusion ne sera faite sans revue propre ;"
        " intervention humaine requise."
    )
    try:
        completed = subprocess.run(
            [
                *NOTIFY_COMMAND,
                "--severity",
                NOTIFY_SEVERITY_URGENT,
                "--message",
                message,
                "--issue-identifier",
                f"PR-{pull_request_number}",
            ],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as notify_error:
        print(
            f"Warning: human escalation could not be sent ({notify_error}); the refusal stands.",
            file=sys.stderr,
        )
        return
    if completed.returncode != 0:
        print(
            f"Warning: human escalation failed (exit {completed.returncode}):"
            f" {completed.stderr.strip()}; the refusal stands.",
            file=sys.stderr,
        )


def sanitize_diff(*, diff_text: str) -> str:
    """Remove machine-artifact chunks from a diff, replacing them with digest lines (P58).

    Deterministic: excluded paths are sorted, digests are computed from the
    HEAD blob. The doctrine is unchanged - the bundle is still the entire
    context - it is simply rid of the noise the CI audit already controls
    better than the reviewer (ADR-0010 appendix, 2026-11-19).
    """
    preamble, chunks = split_diff_chunks(diff_text=diff_text)
    kept_chunks: list[str] = []
    digest_lines: list[str] = []
    for path, chunk in chunks:
        if not path_is_excluded(path=path):
            kept_chunks.append(chunk)
            continue
        controller = controller_for_path(path=path)
        digest = digest_for_path(path=path)
        if digest is None:
            digest_lines.append(f"- `{path}` — supprimé dans ce diff — audité par `{controller}`")
        else:
            size, sha256 = digest
            digest_lines.append(
                f"- `{path}` — {size} octets — sha256:{sha256} — audité par `{controller}`"
            )
    sanitized = preamble + "".join(kept_chunks)
    if digest_lines:
        sanitized += (
            f"\n{EXCLUDED_DIGEST_SECTION_HEADER}\n" + "\n".join(sorted(digest_lines)) + "\n"
        )
    return sanitized


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
    diff_text = sanitize_diff(diff_text=diff_text)
    if arguments.description_file is not None:
        description_text = read_text_file(path=Path(arguments.description_file))
    else:
        description_text = NO_DESCRIPTION_TEXT
    bundle_text = render_bundle(
        diff_text=diff_text,
        description_text=description_text,
        decisions=collect_decisions(),
    )
    refusal_reason = bundle_size_refusal(bundle_text=bundle_text, diff_text=diff_text)
    if refusal_reason is not None:
        print(f"Bundle refused: {refusal_reason}.", file=sys.stderr)
        return EXIT_CODE_PRECONDITIONS_UNMET
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
        journal_path = REVIEW_DIRECTORY / ATTEMPTS_JOURNAL_FILE_NAME
        if refusal_reason is not None:
            record_attempt(
                journal_path=journal_path,
                pull_request_number=arguments.pr_number,
                head_sha=head_sha,
                phase=AttemptPhase.PRECONDITIONS,
                exit_code=EXIT_CODE_PRECONDITIONS_UNMET,
                cause=AttemptCause.PRECONDITIONS,
                escalated=False,
            )
            print(f"Review refused: {refusal_reason}.", file=sys.stderr)
            return EXIT_CODE_PRECONDITIONS_UNMET
        # P61: bounded retries - after REVIEWER_MAX_ATTEMPTS failed session
        # attempts on this head, refuse before any new session starts (exit 3:
        # not a verdict, not an attempt). The human was already escalated when
        # the cap was reached; the nightly stops looping here.
        max_attempts = load_max_attempts()
        attempts = load_attempts(
            journal_path=journal_path,
            pull_request_number=arguments.pr_number,
            head_sha=head_sha,
        )
        failed_attempts = count_failed_session_attempts(attempts=attempts)
        if failed_attempts >= max_attempts:
            record_attempt(
                journal_path=journal_path,
                pull_request_number=arguments.pr_number,
                head_sha=head_sha,
                phase=AttemptPhase.CAP,
                exit_code=EXIT_CODE_PRECONDITIONS_UNMET,
                cause=AttemptCause.PRECONDITIONS,
                escalated=False,
            )
            print(
                f"Review refused: {failed_attempts} failed session attempts on head"
                f" {head_sha[:8]} reached the cap of {max_attempts}"
                f" ({MAX_ATTEMPTS_VARIABLE}) - the human was escalated via `make notify`;"
                " fix the cause, push a new head, then re-run `make review`.",
                file=sys.stderr,
            )
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
        if bundle_exit_code == EXIT_CODE_PRECONDITIONS_UNMET:
            # P59: an oversized bundle is refused before the session starts -
            # preconditions unmet, not a verdict, not an attempt.
            record_attempt(
                journal_path=journal_path,
                pull_request_number=arguments.pr_number,
                head_sha=head_sha,
                phase=AttemptPhase.BUNDLE,
                exit_code=EXIT_CODE_PRECONDITIONS_UNMET,
                cause=AttemptCause.PRECONDITIONS,
                escalated=False,
            )
            return EXIT_CODE_PRECONDITIONS_UNMET
        if bundle_exit_code != EXIT_CODE_SUCCESS:
            record_attempt(
                journal_path=journal_path,
                pull_request_number=arguments.pr_number,
                head_sha=head_sha,
                phase=AttemptPhase.BUNDLE,
                exit_code=EXIT_CODE_PROTOCOL_FAILURE,
                cause=AttemptCause.PROTOCOL,
                escalated=False,
            )
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
        # P61: journal the session attempt with its typed cause, and escalate
        # to the human once the cap of failed attempts is reached - the nightly
        # stops looping, the pull request stays open, nothing merges (fail-closed).
        attempt_cause = cause_for_exit_code(
            exit_code=review_exit_code, review_directory=REVIEW_DIRECTORY
        )
        failed_after = failed_attempts + (1 if review_exit_code != EXIT_CODE_SUCCESS else 0)
        escalated = review_exit_code != EXIT_CODE_SUCCESS and failed_after >= max_attempts
        record_attempt(
            journal_path=journal_path,
            pull_request_number=arguments.pr_number,
            head_sha=head_sha,
            phase=AttemptPhase.SESSION,
            exit_code=review_exit_code,
            cause=attempt_cause,
            escalated=escalated,
            bundle_sizes=measure_bundle_sizes(review_directory=REVIEW_DIRECTORY),
        )
        if escalated:
            escalate_to_human(
                pull_request_number=arguments.pr_number,
                head_sha=head_sha,
                failed_attempts=failed_after,
                max_attempts=max_attempts,
                last_cause=attempt_cause,
            )
        post_review_comment(
            http_client=http_client,
            repository_slug=repository_slug,
            pull_request_number=arguments.pr_number,
            review_directory=REVIEW_DIRECTORY,
        )
    return review_exit_code
