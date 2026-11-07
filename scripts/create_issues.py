"""Populate the GitHub repository with labels, milestones and issues from docs/backlog.md.

Implements spec §19.2: creation order is labels -> milestones -> issues, the
script is idempotent on issue titles, and ``--dry-run`` (the default) only
prints the plan. ``scripts/create_issues.sh`` is a thin wrapper around this
module so the logic stays under the ruff/mypy/pytest gates.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

BACKLOG_PATH = Path("docs/backlog.md")
EXPECTED_ISSUE_COUNT = 50
MODE_DRY_RUN = "dry-run"
MODE_APPLY = "apply"
EN_DASH = "\u2013"
USAGE = """usage: create_issues.sh [--dry-run|--apply]
  --dry-run (default)  print what would be created, create nothing
  --apply              create missing labels, milestones and issues
"""

LABEL_STYLE: dict[str, tuple[str, str]] = {
    "area:api": ("0075ca", "Area: API"),
    "area:ui": ("0075ca", "Area: UI"),
    "area:llm": ("0075ca", "Area: LLM"),
    "area:engine": ("0075ca", "Area: engines"),
    "area:static": ("0075ca", "Area: static analysis"),
    "area:docs": ("0075ca", "Area: docs"),
    "area:study": ("0075ca", "Area: studies"),
    "kind:feat": ("0e8a16", "Kind: feature"),
    "kind:test": ("1d76db", "Kind: test"),
    "kind:chore": ("ededed", "Kind: chore"),
    "kind:spike": ("fbca04", "Kind: spike"),
    "kind:docs": ("5319e7", "Kind: documentation"),
}

MILESTONE_HEADER_PATTERN = re.compile(r"^## Vague \d+ — Milestone `([^`]+)`")
ISSUE_HEADER_PATTERN = re.compile(r"^### (P\d+) · (.+)$")
LABELS_LINE_PATTERN = re.compile(r"^\*\*Labels\*\* (.+?) · \*\*Dép\.\*\* (.+)$")
DEPENDENCY_RANGE_PATTERN = re.compile(rf"^(P\d+)\s*[{re.escape(EN_DASH)}-]\s*(P\d+)$")
ISSUE_URL_PATTERN = re.compile(r"/issues/(\d+)")


@dataclass(frozen=True)
class IssueSpec:
    """One issue parsed from the backlog document."""

    identifier: str
    title: str
    milestone: str
    labels: tuple[str, ...]
    dependencies: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class BacklogPlan:
    """The full parsed backlog: labels, milestones and issues in order."""

    labels: tuple[str, ...]
    milestones: tuple[str, ...]
    issues: tuple[IssueSpec, ...]


@dataclass(frozen=True)
class PendingPlan:
    """What is missing on the remote compared to the backlog."""

    labels: tuple[str, ...]
    milestones: tuple[str, ...]
    issues: tuple[IssueSpec, ...]


@dataclass
class _IssueAccumulator:
    """Mutable builder for one issue while scanning the backlog lines."""

    identifier: str
    title: str
    milestone: str
    labels: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    body: list[str] = field(default_factory=list)

    def feed(self, line: str) -> None:
        """Consume one line: the labels line is metadata, anything else is body."""
        labels_match = LABELS_LINE_PATTERN.match(line)
        if labels_match:
            self.labels = re.findall(r"`([^`]+)`", labels_match.group(1))
            self.dependencies = expand_dependencies(labels_match.group(2))
        else:
            self.body.append(line)

    def build(self) -> IssueSpec:
        """Assemble the IssueSpec, trimming blank lines at the body edges."""
        body = self.body
        while body and not body[0].strip():
            body.pop(0)
        while body and not body[-1].strip():
            body.pop()
        return IssueSpec(
            identifier=self.identifier,
            title=self.title,
            milestone=self.milestone,
            labels=tuple(self.labels),
            dependencies=tuple(self.dependencies),
            body="\n".join(body),
        )


def expand_dependencies(raw: str) -> list[str]:
    """Expand a dependency cell such as ``P01, P02`` or a ``P11...P17`` range."""
    raw = raw.strip()
    if raw in ("—", "-", ""):
        return []
    identifiers: list[str] = []
    for chunk in raw.split(","):
        stripped = chunk.strip()
        range_match = DEPENDENCY_RANGE_PATTERN.match(stripped)
        if range_match:
            first = int(range_match.group(1)[1:])
            last = int(range_match.group(2)[1:])
            identifiers += [f"P{number:02d}" for number in range(first, last + 1)]
        elif re.match(r"^P\d+$", stripped):
            identifiers.append(stripped)
        else:
            raise ValueError(f"unrecognized dependency: {stripped!r}")
    return identifiers


class _BacklogScanner:
    """Line scanner turning backlog markdown into IssueSpec objects."""

    def __init__(self) -> None:
        self.issues: list[IssueSpec] = []
        self.milestone = ""
        self.accumulator: _IssueAccumulator | None = None

    def scan(self, line: str) -> None:
        """Dispatch one line: milestone header, issue header, section end, or body."""
        if milestone_match := MILESTONE_HEADER_PATTERN.match(line):
            self.milestone = milestone_match.group(1)
        elif issue_match := ISSUE_HEADER_PATTERN.match(line):
            self.close()
            self.accumulator = _IssueAccumulator(
                identifier=issue_match.group(1),
                title=issue_match.group(2).strip(),
                milestone=self.milestone,
            )
        elif line.startswith(("## ", "### ")):
            self.close()
        elif self.accumulator is not None:
            self.accumulator.feed(line)

    def close(self) -> None:
        """Seal the current issue accumulator, if any."""
        if self.accumulator is not None:
            self.issues.append(self.accumulator.build())
            self.accumulator = None

    def finish(self) -> list[IssueSpec]:
        """Seal the last issue and return all parsed issues."""
        self.close()
        return self.issues


def parse_backlog(text: str) -> BacklogPlan:
    """Parse the backlog markdown into a structured plan."""
    scanner = _BacklogScanner()
    for line in text.splitlines():
        scanner.scan(line)
    issues = scanner.finish()
    if len(issues) != EXPECTED_ISSUE_COUNT:
        raise ValueError(
            f"expected {EXPECTED_ISSUE_COUNT} issues in {BACKLOG_PATH}, got {len(issues)}"
        )
    labels = tuple(sorted({label for issue in issues for label in issue.labels}))
    milestones: list[str] = []
    for issue in issues:
        if issue.milestone not in milestones:
            milestones.append(issue.milestone)
    return BacklogPlan(labels=labels, milestones=tuple(milestones), issues=tuple(issues))


def issue_title(issue: IssueSpec) -> str:
    """Return the exact GitHub title used for idempotency checks."""
    return f"{issue.identifier} · {issue.title}"


def compute_pending(
    *,
    plan: BacklogPlan,
    existing_labels: set[str],
    existing_milestones: set[str],
    existing_titles: set[str],
) -> PendingPlan:
    """Return what is missing on the remote; empty when fully populated (idempotent)."""
    return PendingPlan(
        labels=tuple(label for label in plan.labels if label not in existing_labels),
        milestones=tuple(m for m in plan.milestones if m not in existing_milestones),
        issues=tuple(i for i in plan.issues if issue_title(i) not in existing_titles),
    )


def build_issue_body(*, issue: IssueSpec, number_by_identifier: dict[str, int]) -> str:
    """Return the issue body with a blocked-by footer resolved to real issue numbers."""
    resolved = [
        f"#{number_by_identifier[dependency]}"
        for dependency in issue.dependencies
        if dependency in number_by_identifier
    ]
    unresolved = [d for d in issue.dependencies if d not in number_by_identifier]
    if unresolved:
        print(
            f"warning: {issue.identifier} has unresolved dependencies: {', '.join(unresolved)}",
            file=sys.stderr,
        )
    footer = f"blocked-by: {', '.join(resolved)}" if resolved else "aucune."
    return f"{issue.body}\n\n---\n**Dépendances.** {footer}"


def gh(*arguments: str) -> str:
    """Run a gh CLI command and return its stdout, failing loudly on error."""
    completed = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"gh {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout


def fetch_existing(repo: str) -> tuple[set[str], set[str], dict[str, int]]:
    """Return existing label names, milestone titles and the title -> number map of issues."""
    labels = set(gh("label", "list", "--repo", repo, "--json", "name", "--jq", ".[].name").split())
    milestones = set(
        gh("api", f"repos/{repo}/milestones?state=all", "--jq", ".[].title").splitlines()
    )
    title_to_number: dict[str, int] = {}
    issue_rows = gh(
        "api",
        f"repos/{repo}/issues?state=all",
        "--paginate",
        "--jq",
        ".[] | select(.pull_request == null) | [.title, (.number | tostring)] | @tsv",
    )
    for row in issue_rows.splitlines():
        title, number = row.split("\t")
        title_to_number[title] = int(number)
    return labels, milestones, title_to_number


def create_labels(*, repo: str, labels: tuple[str, ...]) -> None:
    """Create the missing labels with their color and description."""
    for label in labels:
        color, description = LABEL_STYLE[label]
        gh("label", "create", label, "--repo", repo, "--color", color, "--description", description)
        print(f"label created: {label}")


def create_milestones(*, repo: str, milestones: tuple[str, ...]) -> dict[str, int]:
    """Create the missing milestones and return the title -> number map."""
    number_by_milestone: dict[str, int] = {}
    for milestone in milestones:
        output = gh("api", "-X", "POST", f"repos/{repo}/milestones", "-f", f"title={milestone}")
        number_by_milestone[milestone] = json.loads(output)["number"]
        print(f"milestone created: {milestone} (#{number_by_milestone[milestone]})")
    listing = gh(
        "api",
        f"repos/{repo}/milestones?state=all",
        "--jq",
        '.[] | .title + " " + (.number | tostring)',
    )
    for row in listing.splitlines():
        title, number = row.rsplit(" ", 1)
        number_by_milestone.setdefault(title, int(number))
    return number_by_milestone


def create_issues(
    *,
    repo: str,
    plan: BacklogPlan,
    pending: PendingPlan,
    title_to_number: dict[str, int],
) -> None:
    """Create the pending issues in backlog order, resolving blocked-by to issue numbers."""
    number_by_identifier = {
        issue.identifier: title_to_number[issue_title(issue)]
        for issue in plan.issues
        if issue_title(issue) in title_to_number
    }
    with tempfile.TemporaryDirectory(prefix="petrichor-issues-") as directory:
        for issue in pending.issues:
            body_file = Path(directory) / f"{issue.identifier}.md"
            body = build_issue_body(issue=issue, number_by_identifier=number_by_identifier)
            body_file.write_text(body, encoding="utf-8")
            arguments = [
                "issue",
                "create",
                "--repo",
                repo,
                "--title",
                issue_title(issue),
                "--body-file",
                str(body_file),
                "--milestone",
                issue.milestone,
            ]
            for label in issue.labels:
                arguments += ["--label", label]
            output = gh(*arguments)
            match = ISSUE_URL_PATTERN.search(output)
            if match is None:
                raise SystemExit(f"could not parse issue number from gh output: {output!r}")
            number = int(match.group(1))
            number_by_identifier[issue.identifier] = number
            print(f"issue created: #{number} {issue_title(issue)} [{issue.milestone}]")


def parse_mode(command_line_arguments: Sequence[str]) -> str:
    """Return the run mode from CLI arguments; dry-run is the safe default."""
    if not command_line_arguments or tuple(command_line_arguments) == ("--dry-run",):
        return MODE_DRY_RUN
    if tuple(command_line_arguments) == ("--apply",):
        return MODE_APPLY
    raise SystemExit(f"unknown arguments: {' '.join(command_line_arguments)}\n{USAGE}")


def main(command_line_arguments: Sequence[str]) -> int:
    """Entry point: parse the backlog, then print the plan or apply it."""
    if tuple(command_line_arguments) in (("-h",), ("--help",)):
        print(USAGE)
        return 0
    mode = parse_mode(command_line_arguments)
    repo = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    plan = parse_backlog(BACKLOG_PATH.read_text(encoding="utf-8"))
    existing_labels, existing_milestones, title_to_number = fetch_existing(repo)
    pending = compute_pending(
        plan=plan,
        existing_labels=existing_labels,
        existing_milestones=existing_milestones,
        existing_titles=set(title_to_number),
    )
    print(
        f"[{mode}] {repo}: {len(pending.labels)} labels, {len(pending.milestones)} milestones, "
        f"{len(pending.issues)} issues to create "
        f"(backlog: {len(plan.labels)}/{len(plan.milestones)}/{len(plan.issues)})"
    )
    for label in pending.labels:
        print(f"  label     {label}")
    for milestone in pending.milestones:
        print(f"  milestone {milestone}")
    for issue in pending.issues:
        print(f"  issue     {issue_title(issue)} [{issue.milestone}]")
    if mode == MODE_DRY_RUN:
        return 0
    create_labels(repo=repo, labels=pending.labels)
    create_milestones(repo=repo, milestones=pending.milestones)
    create_issues(repo=repo, plan=plan, pending=pending, title_to_number=title_to_number)
    print(f"done: {len(pending.issues)} issues created in this run")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
