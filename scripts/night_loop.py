"""Unattended day loop: one issue, one fresh headless session (ADR-0017).

Runs the protocol's day loop overnight. The directive comes in two
flavors:

- explicit: ``make night ISSUES="12,13,14"`` - the human wrote the
  issue list before leaving, and the loop runs one headless session
  per issue, in order;
- auto-discovered: ``make night`` with no ISSUES - the loop derives
  the worklist from the repository itself (open issues, triage gate,
  minus the ``night-claimed`` label), and each session **picks one
  item and claims it first** (``make claim`` or ``make claim-pr``,
  ADR-0017). The claim is the concurrency guard: two sessions never
  work the same item, and a failed attempt stays claimed for the
  morning audit.

In both modes the process boundary IS the context reset of the
interactive flow (orchestration 3): no issue inherits the previous
one's residue, and a session that dies at 3 a.m. costs one issue, not
the night. The loop re-derives the worklist before every session and
stops when no unclaimed item remains, when the session budget is
spent, or when a session claims nothing - the night never spins on
one stuck item.

Nobody answers approvals while this runs: sessions get the project's
runtime profile (``.vibe/``, ADR-0015), allowlisted commands run, and
a denied call is a stop signal the session escalates honestly - never
an obstacle to route around.

The loop itself is honest glue: it fetches the issues and the
recoverable pull requests, applies the triage gate, renders the
versioned prompt, runs the session, records the exit code, and moves
on. The verdicts live in the repository (``WORKLOG.md``, the pull
requests, the issues) for the morning audit.

Output has two channels (ADR-0017): stdout is the short, readable
summary - one line per session and one verdict at the end - while the
verbose trace (full candidate lists, anti-spin decisions, fetch
warnings) lands in ``.night/night-loop.log`` (override it with
``NIGHT_LOG_FILE``). The morning audit reads the terminal; the
post-mortem reads the log.

Usage::

    make night ISSUES=12,13,14
    make night MILESTONES=3,4
    make night
"""

import argparse
import dataclasses
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from collections.abc import Sequence

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_TOKEN_VARIABLE = "GITHUB_TOKEN"  # noqa: S105 - an env var NAME, never a secret
ENVIRONMENT_FILE_NAME = ".env"
DEVELOPER_CLI_COMMAND = "vibe"
PROMPT_TEMPLATE_PATH = ".vibe/prompts/night-issue.md"
AUTO_PROMPT_TEMPLATE_PATH = ".vibe/prompts/night-auto.md"
ISSUE_PLACEHOLDER = "{{issue}}"
WORKLIST_PLACEHOLDER = "{{worklist}}"
NIGHT_MODEL_VARIABLE = "NIGHT_MODEL"
NIGHT_MAX_TURNS_VARIABLE = "NIGHT_MAX_TURNS"
SESSION_TIME_LIMIT_VARIABLE = "NIGHT_TIME_LIMIT_SECONDS"
NIGHT_MAXIMUM_SESSIONS_VARIABLE = "NIGHT_MAX_SESSIONS"
NIGHT_CANDIDATES_VARIABLE = "NIGHT_CANDIDATES"
NIGHT_LOG_FILE_VARIABLE = "NIGHT_LOG_FILE"
DEFAULT_NIGHT_LOG_FILE_NAME = ".night/night-loop.log"
LOG_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_MAXIMUM_SESSION_TURNS = 100
DEFAULT_SESSION_TIME_LIMIT_SECONDS = 3600
DEFAULT_MAXIMUM_SESSIONS = 10
DEFAULT_CANDIDATE_COUNT = 10
OPEN_ISSUES_PAGE_SIZE = 100
OPEN_PULL_REQUESTS_PAGE_SIZE = 100
DAILY_BRANCH_NAME = "daily"
NIGHT_HEAD_BRANCH_PATTERN = re.compile(r"^(?:feat|fix)/(\d+)(?:-.*)?$")
TRIAGE_SKIP_LABELS = ("blocked", "needs-human", "night-claimed")
CLAIM_LABEL_NAME = "night-claimed"

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_ALL_ISSUES_PROCESSED = 0
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
            f"Error: {GITHUB_TOKEN_VARIABLE} is not set - cannot fetch the"
            " issues of the night directive.",
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


def parse_issue_list(*, raw_issue_list: str) -> list[int]:
    """Parse the night directive: comma-separated issue numbers, nothing else."""
    issue_numbers = []
    for raw_number in raw_issue_list.split(","):
        stripped_number = raw_number.strip()
        if not stripped_number:
            continue
        if not stripped_number.isdigit():
            print(f"Error: not an issue number: {stripped_number}.", file=sys.stderr)
            raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
        issue_numbers.append(int(stripped_number))
    if not issue_numbers:
        print(
            "Error: no issues to process - the night directive is the issue"
            ' list, e.g. make night ISSUES="12,13,14".',
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return issue_numbers


def parse_milestone_list(*, raw_milestone_list: str) -> set[int]:
    """Parse the milestone filter: comma-separated milestone numbers.

    An empty list means no filtering; anything else must be digits.
    """
    milestone_numbers: set[int] = set()
    for raw_number in raw_milestone_list.split(","):
        stripped_number = raw_number.strip()
        if not stripped_number:
            continue
        if not stripped_number.isdigit():
            print(f"Error: not a milestone number: {stripped_number}.", file=sys.stderr)
            raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
        milestone_numbers.add(int(stripped_number))
    return milestone_numbers


def item_milestone_number(*, item: dict[str, object]) -> int | None:
    """Return the milestone number of an issue or pull request, if any."""
    milestone = item.get("milestone")
    if not isinstance(milestone, dict):
        return None
    number = milestone.get("number")
    return number if isinstance(number, int) else None


def milestone_matches(*, item: dict[str, object], milestone_numbers: set[int]) -> bool:
    """Tell whether an item passes the milestone filter (empty set = all)."""
    if not milestone_numbers:
        return True
    return item_milestone_number(item=item) in milestone_numbers


def read_integer_setting(*, variable_name: str, default_value: int) -> int:
    """Read an integer environment setting; an invalid value fails closed."""
    raw_value = os.environ.get(variable_name, "").strip()
    if not raw_value:
        return default_value
    try:
        return int(raw_value)
    except ValueError:
        print(
            f"Error: {variable_name} is not an integer: {raw_value}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE) from None


def fetch_issue(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    issue_number: int,
) -> dict[str, object] | None:
    """Fetch the issue, or report and return None if the API refuses it."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{issue_number}"
    )
    if not response.is_success:
        print(f"Warning: could not fetch issue #{issue_number} ({response.status_code}).")
        return None
    issue: object = response.json()
    if not isinstance(issue, dict):
        print(f"Warning: issue #{issue_number} has an unexpected payload.")
        return None
    return issue


def triage_issue(*, issue: dict[str, object]) -> str | None:
    """Apply the triage gate (orchestration 2); a reason means: skip."""
    if "pull_request" in issue:
        return "it is a pull request, not an issue"
    labels = issue.get("labels", [])
    label_names: set[str] = set()
    if isinstance(labels, list):
        label_names = {entry.get("name", "") for entry in labels if isinstance(entry, dict)}
    for label in TRIAGE_SKIP_LABELS:
        if label in label_names:
            return f"it is labeled {label}"
    body = issue.get("body")
    if not isinstance(body, str) or not body.strip():
        return "it has no acceptance criteria (triage gate)"
    return None


def render_issue_section(*, issue: dict[str, object], issue_number: int) -> str:
    """Render the issue (title and body) as the prompt's issue section."""
    title = issue.get("title", "")
    title_text = title if isinstance(title, str) else ""
    body = issue.get("body")
    body_text = body if isinstance(body, str) else ""
    return f"# Issue #{issue_number}: {title_text}\n\n{body_text}"


def fetch_candidate_issues(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    candidate_limit: int,
    milestone_numbers: set[int],
) -> list[tuple[int, dict[str, object]]]:
    """Derive the auto-discovered worklist: open, eligible, unclaimed, in scope."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues",
        params={"state": "open", "per_page": OPEN_ISSUES_PAGE_SIZE},
    )
    if not response.is_success:
        print(f"Warning: could not list open issues ({response.status_code}).")
        return []
    payload = response.json()
    if not isinstance(payload, list):
        print("Warning: the open-issues payload is unexpected.")
        return []
    candidates: list[tuple[int, dict[str, object]]] = []
    for issue in payload:
        if not isinstance(issue, dict):
            continue
        if triage_issue(issue=issue) is not None:
            continue
        if not milestone_matches(item=issue, milestone_numbers=milestone_numbers):
            continue
        issue_number = issue.get("number")
        if not isinstance(issue_number, int):
            continue
        candidates.append((issue_number, issue))
    candidates.sort(key=lambda candidate: candidate[0])
    return candidates[:candidate_limit]


def render_worklist(*, candidates: Sequence[tuple[int, dict[str, object]]]) -> str:
    """Render the candidate issues as the prompt's worklist."""
    sections = [
        render_issue_section(issue=issue, issue_number=issue_number)
        for issue_number, issue in candidates
    ]
    return "\n\n---\n\n".join(sections)


def parse_backing_issue_number(*, head_ref: object) -> int | None:
    """Return the backing issue number of a ``feat/N`` or ``fix/N`` branch.

    A descriptive suffix after the number (``feat/12-canonical-hash``)
    is tolerated: agents name branches that way, and the number alone
    decides the backing issue.
    """
    if not isinstance(head_ref, str):
        return None
    match = NIGHT_HEAD_BRANCH_PATTERN.match(head_ref)
    return int(match.group(1)) if match else None


def item_has_claim_label(*, item: dict[str, object]) -> bool:
    """Tell whether an issue or pull request payload already carries the claim."""
    labels = item.get("labels", [])
    if not isinstance(labels, list):
        return False
    label_names = {entry.get("name", "") for entry in labels if isinstance(entry, dict)}
    return CLAIM_LABEL_NAME in label_names


def fetch_candidate_pull_requests(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    candidate_limit: int,
    milestone_numbers: set[int],
) -> list[tuple[int, dict[str, object]]]:
    """Derive the recoverable pull requests (ADR-0017 recovery path).

    A pull request is recoverable when all of these hold: it is open,
    it targets ``daily``, its head branch is ``feat/N`` or ``fix/N``,
    its backing issue #N carries ``night-claimed`` (the mark of an
    earlier unattended attempt), and the pull request itself is not
    already claimed by another recovery session.
    """
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/pulls",
        params={
            "state": "open",
            "base": DAILY_BRANCH_NAME,
            "per_page": OPEN_PULL_REQUESTS_PAGE_SIZE,
        },
    )
    if not response.is_success:
        print(f"Warning: could not list open pull requests ({response.status_code}).")
        return []
    payload = response.json()
    if not isinstance(payload, list):
        print("Warning: the open-pull-requests payload is unexpected.")
        return []
    candidates: list[tuple[int, dict[str, object]]] = []
    for pull_request in payload:
        if not isinstance(pull_request, dict):
            continue
        pr_number = pull_request.get("number")
        if not isinstance(pr_number, int):
            continue
        if item_has_claim_label(item=pull_request):
            continue
        if not milestone_matches(item=pull_request, milestone_numbers=milestone_numbers):
            continue
        head = pull_request.get("head")
        head_ref = head.get("ref") if isinstance(head, dict) else None
        backing_issue_number = parse_backing_issue_number(head_ref=head_ref)
        if backing_issue_number is None:
            continue
        backing_issue = fetch_issue(
            http_client=http_client,
            repository_slug=repository_slug,
            issue_number=backing_issue_number,
        )
        if backing_issue is None or not item_has_claim_label(item=backing_issue):
            continue
        candidates.append((pr_number, pull_request))
    candidates.sort(key=lambda candidate: candidate[0])
    return candidates[:candidate_limit]


def render_pull_request_section(
    *,
    pull_request: dict[str, object],
    pr_number: int,
) -> str:
    """Render a recoverable pull request as the prompt's worklist section."""
    title = pull_request.get("title", "")
    title_text = title if isinstance(title, str) else ""
    head = pull_request.get("head")
    head_ref = head.get("ref") if isinstance(head, dict) else "unknown"
    backing_issue_number = parse_backing_issue_number(head_ref=head_ref)
    backing_clause = (
        f", backing issue #{backing_issue_number}" if backing_issue_number is not None else ""
    )
    body = pull_request.get("body")
    body_text = body if isinstance(body, str) else ""
    return (
        f"# Pull request #{pr_number}: {title_text}\n\n"
        f"An earlier session left this pull request open (branch `{head_ref}`"
        f"{backing_clause}, targeting `daily`) and never merged it. It is"
        " offered for recovery: check it out, find out where it stands, fix"
        " it, and take it through the review gate.\n\n"
        f"{body_text}"
    )


def render_auto_worklist(
    *,
    issues: Sequence[tuple[int, dict[str, object]]],
    pull_requests: Sequence[tuple[int, dict[str, object]]],
) -> str:
    """Render the combined worklist: unclaimed issues, then recoverable PRs."""
    sections = [
        render_issue_section(issue=issue, issue_number=issue_number)
        for issue_number, issue in issues
    ]
    sections.extend(
        render_pull_request_section(pull_request=pull_request, pr_number=pr_number)
        for pr_number, pull_request in pull_requests
    )
    return "\n\n---\n\n".join(sections)


def auto_candidate_keys(
    *,
    issues: Sequence[tuple[int, dict[str, object]]],
    pull_requests: Sequence[tuple[int, dict[str, object]]],
) -> set[tuple[str, int]]:
    """Build the anti-spin guard: one key per worklist item."""
    issue_keys = {("issue", issue_number) for issue_number, _ in issues}
    pull_request_keys = {("pull request", pr_number) for pr_number, _ in pull_requests}
    return issue_keys | pull_request_keys


def describe_auto_candidates(
    *,
    issues: Sequence[tuple[int, dict[str, object]]],
    pull_requests: Sequence[tuple[int, dict[str, object]]],
) -> str:
    """Render the candidate list for the loop's own trace."""
    descriptions = [f"#{issue_number} (issue)" for issue_number, _ in issues]
    descriptions.extend(f"#{pr_number} (pull request)" for pr_number, _ in pull_requests)
    return ", ".join(descriptions)


def load_prompt_template(*, template_path: str) -> str:
    """Load a versioned night-session prompt (ADR-0017); missing fails closed."""
    resolved_path = REPOSITORY_ROOT / template_path
    if not resolved_path.is_file():
        print(
            f"Error: the night-session prompt is missing: {template_path}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return resolved_path.read_text(encoding="utf-8")


def build_prompt(*, template: str, placeholder: str, replacement: str) -> str:
    """Combine a session template and its replacement text."""
    if placeholder in template:
        return template.replace(placeholder, replacement)
    return f"{template}\n\n{replacement}\n"


def run_developer_session(
    *,
    prompt_path: Path,
    maximum_session_turns: int,
    session_time_limit_seconds: int,
) -> tuple[int, int]:
    """Run one headless developer session; return (exit code, seconds)."""
    command = [
        DEVELOPER_CLI_COMMAND,
        "--prompt-file",
        str(prompt_path),
        "--output",
        "json",
        "--max-turns",
        str(maximum_session_turns),
        "--time-limit",
        str(session_time_limit_seconds),
    ]
    developer_model = os.environ.get(NIGHT_MODEL_VARIABLE, "").strip()
    if developer_model:
        command.extend(["--model", developer_model])
    started_at = time.monotonic()
    completed_process = subprocess.run(command, cwd=REPOSITORY_ROOT, check=False)
    elapsed_seconds = int(time.monotonic() - started_at)
    return completed_process.returncode, elapsed_seconds


def format_duration(*, seconds: int) -> str:
    """Render a duration in minutes and seconds, e.g. 04:31."""
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def resolve_log_file_path() -> Path:
    """Resolve the verbose trace location: stdout stays for the human."""
    configured_path = os.environ.get(NIGHT_LOG_FILE_VARIABLE, "").strip()
    log_file = Path(configured_path) if configured_path else Path(DEFAULT_NIGHT_LOG_FILE_NAME)
    if log_file.is_absolute():
        return log_file
    return REPOSITORY_ROOT / log_file


def write_log_entry(*, log_file: Path, text: str) -> None:
    """Append one timestamped line to the verbose night trace."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as log_stream:
        log_stream.write(f"[{time.strftime(LOG_TIMESTAMP_FORMAT)}] {text}\n")


@dataclasses.dataclass
class NightJournal:
    """The loop's two output channels (ADR-0017).

    ``record`` pairs one short stdout line for the morning audit with
    its verbose log counterpart; ``log`` writes to the trace only.
    """

    log_file: Path
    summary_lines: list[str] = dataclasses.field(default_factory=list)

    def log(self, *, text: str) -> None:
        """Append one entry to the verbose trace only."""
        write_log_entry(log_file=self.log_file, text=text)

    def record(self, *, line: str, text: str) -> None:
        """Add a stdout summary line and its verbose counterpart."""
        self.summary_lines.append(line)
        self.log(text=text)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="night_loop",
        description="Run the unattended day loop, one session per issue (ADR-0017).",
    )
    argument_parser.add_argument(
        "--issues",
        default="",
        help='Comma-separated issue numbers, e.g. "12,13,14". Omit it and each'
        " session picks one unclaimed open issue and claims it (auto-discovery).",
    )
    argument_parser.add_argument(
        "--milestones",
        default="",
        help='Comma-separated milestone numbers, e.g. "3,4". Only issues and pull'
        " requests of these milestones are worked. Omit it for no filtering.",
    )
    argument_parser.add_argument(
        "--max-turns",
        type=int,
        default=read_integer_setting(
            variable_name=NIGHT_MAX_TURNS_VARIABLE,
            default_value=DEFAULT_MAXIMUM_SESSION_TURNS,
        ),
        help="Maximum assistant turns per session.",
    )
    argument_parser.add_argument(
        "--time-limit",
        type=int,
        default=read_integer_setting(
            variable_name=SESSION_TIME_LIMIT_VARIABLE,
            default_value=DEFAULT_SESSION_TIME_LIMIT_SECONDS,
        ),
        help="Time limit per session, in seconds.",
    )
    argument_parser.add_argument(
        "--max-sessions",
        type=int,
        default=read_integer_setting(
            variable_name=NIGHT_MAXIMUM_SESSIONS_VARIABLE,
            default_value=DEFAULT_MAXIMUM_SESSIONS,
        ),
        help="Auto-discovery only: maximum number of sessions (one issue each).",
    )
    argument_parser.add_argument(
        "--candidates",
        type=int,
        default=read_integer_setting(
            variable_name=NIGHT_CANDIDATES_VARIABLE,
            default_value=DEFAULT_CANDIDATE_COUNT,
        ),
        help="Auto-discovery only: issues listed in each session's worklist.",
    )
    return argument_parser.parse_args(command_line_arguments)


def run_explicit_directive(
    *,
    http_client: httpx.Client,
    prompt_directory: Path,
    repository_slug: str,
    issue_numbers: Sequence[int],
    milestone_numbers: set[int],
    prompt_template: str,
    arguments: argparse.Namespace,
    journal: NightJournal,
) -> None:
    """Explicit mode: one session per issue of the human's list."""
    for issue_number in issue_numbers:
        print(f"\n=== Issue #{issue_number} ===")
        journal.log(text=f"explicit session: issue #{issue_number}")
        issue = fetch_issue(
            http_client=http_client,
            repository_slug=repository_slug,
            issue_number=issue_number,
        )
        if issue is None:
            journal.record(
                line=f"Issue #{issue_number}: skipped - could not fetch it",
                text=f"issue #{issue_number}: could not fetch it",
            )
            continue
        skip_reason = triage_issue(issue=issue)
        if skip_reason is None and not milestone_matches(
            item=issue, milestone_numbers=milestone_numbers
        ):
            skip_reason = "it is not in the requested milestones"
        if skip_reason:
            print(f"Skipped: {skip_reason}.")
            journal.record(
                line=f"Issue #{issue_number}: skipped - {skip_reason}",
                text=f"issue #{issue_number} skipped: {skip_reason}",
            )
            continue
        issue_section = render_issue_section(issue=issue, issue_number=issue_number)
        prompt_path = prompt_directory / f"issue-{issue_number}.md"
        prompt_path.write_text(
            build_prompt(
                template=prompt_template,
                placeholder=ISSUE_PLACEHOLDER,
                replacement=issue_section,
            ),
            encoding="utf-8",
        )
        exit_code, elapsed_seconds = run_developer_session(
            prompt_path=prompt_path,
            maximum_session_turns=arguments.max_turns,
            session_time_limit_seconds=arguments.time_limit,
        )
        print(f"Session exited {exit_code} after {format_duration(seconds=elapsed_seconds)}.")
        journal.record(
            line=(
                f"Issue #{issue_number}: session exit {exit_code}"
                f" ({format_duration(seconds=elapsed_seconds)})"
            ),
            text=(
                f"issue #{issue_number}: session exit {exit_code}"
                f" after {format_duration(seconds=elapsed_seconds)}"
            ),
        )


def run_auto_discovered_sessions(
    *,
    http_client: httpx.Client,
    prompt_directory: Path,
    repository_slug: str,
    milestone_numbers: set[int],
    prompt_template: str,
    arguments: argparse.Namespace,
    journal: NightJournal,
) -> None:
    """Auto-discovery: each session picks one item and claims it first."""
    if arguments.max_sessions < 1:
        print(
            f"Error: {NIGHT_MAXIMUM_SESSIONS_VARIABLE} must be at least 1,"
            f" not {arguments.max_sessions}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    if arguments.candidates < 1:
        print(
            f"Error: {NIGHT_CANDIDATES_VARIABLE} must be at least 1, not {arguments.candidates}.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    sessions_started = 0
    while sessions_started < arguments.max_sessions:
        issues = fetch_candidate_issues(
            http_client=http_client,
            repository_slug=repository_slug,
            candidate_limit=arguments.candidates,
            milestone_numbers=milestone_numbers,
        )
        pull_requests = fetch_candidate_pull_requests(
            http_client=http_client,
            repository_slug=repository_slug,
            candidate_limit=arguments.candidates,
            milestone_numbers=milestone_numbers,
        )
        if not issues and not pull_requests:
            print("\nNo unclaimed eligible item left - the night is done.")
            journal.log(text="worklist empty - the night is done")
            break
        candidate_description = describe_auto_candidates(
            issues=issues,
            pull_requests=pull_requests,
        )
        print(
            f"\nNight session {sessions_started + 1} of at most"
            f" {arguments.max_sessions}: {len(issues)} issue(s),"
            f" {len(pull_requests)} pull request(s) in the worklist."
        )
        journal.log(
            text=f"session {sessions_started + 1} candidates: {candidate_description}",
        )
        prompt_path = prompt_directory / f"session-{sessions_started + 1}.md"
        prompt_path.write_text(
            build_prompt(
                template=prompt_template,
                placeholder=WORKLIST_PLACEHOLDER,
                replacement=render_auto_worklist(issues=issues, pull_requests=pull_requests),
            ),
            encoding="utf-8",
        )
        exit_code, elapsed_seconds = run_developer_session(
            prompt_path=prompt_path,
            maximum_session_turns=arguments.max_turns,
            session_time_limit_seconds=arguments.time_limit,
        )
        sessions_started += 1
        print(
            f"Session {sessions_started}: exit {exit_code}"
            f" in {format_duration(seconds=elapsed_seconds)}."
        )
        journal.record(
            line=(
                f"Session {sessions_started}: exit {exit_code}"
                f" in {format_duration(seconds=elapsed_seconds)}"
            ),
            text=(
                f"session {sessions_started}: exit {exit_code}"
                f" after {format_duration(seconds=elapsed_seconds)}"
            ),
        )
        fresh_issues = fetch_candidate_issues(
            http_client=http_client,
            repository_slug=repository_slug,
            candidate_limit=arguments.candidates,
            milestone_numbers=milestone_numbers,
        )
        fresh_pull_requests = fetch_candidate_pull_requests(
            http_client=http_client,
            repository_slug=repository_slug,
            candidate_limit=arguments.candidates,
            milestone_numbers=milestone_numbers,
        )
        candidate_keys = auto_candidate_keys(issues=issues, pull_requests=pull_requests)
        fresh_keys = auto_candidate_keys(issues=fresh_issues, pull_requests=fresh_pull_requests)
        if candidate_keys.issubset(fresh_keys):
            # No item was claimed and none disappeared: the next session
            # would get the very same worklist. Stop instead of spinning
            # on it all night.
            print(
                "No candidate was claimed - stopping the loop instead of"
                " retrying the same worklist."
            )
            journal.record(
                line=(
                    "Loop stopped: no candidate was claimed last session"
                    " - the morning audit decides."
                ),
                text=(
                    f"anti-spin stop: no candidate claimed,"
                    f" worklist unchanged: {candidate_description}"
                ),
            )
            break
        journal.log(
            text=(f"anti-spin pass: before={sorted(candidate_keys)} fresh={sorted(fresh_keys)}"),
        )


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: night directive in (or auto-discovered), report out."""
    load_environment_file()
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    github_token = load_github_token()
    if shutil.which(DEVELOPER_CLI_COMMAND) is None:
        print(
            f"Error: {DEVELOPER_CLI_COMMAND} is not on PATH - the unattended"
            " loop needs a signed-in Vibe Code CLI (ADR-0017).",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
    repository_slug = resolve_repository_slug()
    milestone_numbers = parse_milestone_list(raw_milestone_list=arguments.milestones)
    journal = NightJournal(log_file=resolve_log_file_path())
    print(f"Verbose trace: {journal.log_file}")
    journal.log(
        text=(
            f"night loop started: mode="
            f"{'explicit' if arguments.issues else 'auto-discovered'}"
            f", repository={repository_slug}"
        ),
    )
    with (
        httpx.Client(
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            timeout=30.0,
        ) as http_client,
        tempfile.TemporaryDirectory(prefix="night-loop-") as prompt_directory,
    ):
        if arguments.issues:
            issue_numbers = parse_issue_list(raw_issue_list=arguments.issues)
            prompt_template = load_prompt_template(template_path=PROMPT_TEMPLATE_PATH)
            run_explicit_directive(
                http_client=http_client,
                prompt_directory=Path(prompt_directory),
                repository_slug=repository_slug,
                issue_numbers=issue_numbers,
                milestone_numbers=milestone_numbers,
                prompt_template=prompt_template,
                arguments=arguments,
                journal=journal,
            )
        else:
            prompt_template = load_prompt_template(template_path=AUTO_PROMPT_TEMPLATE_PATH)
            run_auto_discovered_sessions(
                http_client=http_client,
                prompt_directory=Path(prompt_directory),
                repository_slug=repository_slug,
                milestone_numbers=milestone_numbers,
                prompt_template=prompt_template,
                arguments=arguments,
                journal=journal,
            )
    print("\n=== Night summary ===")
    for result in journal.summary_lines:
        print(f"- {result}")
    print("Morning audit: WORKLOG.md, the pull requests, and every issue labeled needs-human.")
    journal.log(
        text=f"night loop finished: {len(journal.summary_lines)} result line(s) on stdout",
    )
    return EXIT_CODE_ALL_ISSUES_PROCESSED


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
