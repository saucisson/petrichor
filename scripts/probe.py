"""Merge probe: replay this branch onto a base and verify every commit (ADR-0008).

Implements the local dry run of N5 (``make probe-merge``, never a PR)
and the per-commit greenness gate of H5: the commits of the current
branch are replayed onto the base inside a throwaway worktree, and
every replayed commit must be green on the fast fronts (lint,
typecheck, imports). What lands on the target branch after a rebase
merge is the branch's own commits (I3) - so the probe verifies those
commits, one by one, not only the tip. Bisectability is the point: a
green tip over a red middle commit poisons ``git bisect`` for every
future agent.

Nothing outside the worktree moves: no branch is touched, no push
happens, ``main`` and ``daily`` are never written. The probe reports
and exits; it never repairs.
"""

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
    run_git,
    try_git,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

DEFAULT_BASE_REF = "origin/daily"
PROBE_BRANCH_PREFIX = "probe-merge/"
COMMIT_SHA_LENGTH = 12
FAILING_FRONT_OUTPUT_LINES = 30

# The fast fronts of H5 - the cheap deterministic oracles that must
# pass on EVERY commit of the branch, not only on its tip. Same argv
# as the make targets (lint, typecheck, imports); `uv run` syncs the
# worktree environment for the tree state being probed, and a lock
# file out of sync with the tree is itself a greenness failure.
FAST_FRONT_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lint", ("uv", "run", "ruff", "check", ".")),
    ("typecheck", ("uv", "run", "mypy", "tools/notify/src", "tools/notify/tests", "scripts")),
    ("imports", ("uv", "run", "lint-imports")),
)


@dataclass(frozen=True)
class WorktreeContext:
    """One probe run's throwaway worktree and temporary branch."""

    directory: Path
    probe_branch: str


@dataclass(frozen=True)
class CommitVerdict:
    """Fast-front greenness of one replayed commit."""

    commit: str
    subject: str
    failing_front: str | None  # None means every front passed
    failure_output: str


def ensure_clean_work_tree() -> None:
    """Refuse to probe over a dirty tree; it would never replay."""
    completed_process = try_git(arguments=["status", "--porcelain"])
    if completed_process.returncode != EXIT_CODE_SUCCESS:
        print("Error: git status failed; cannot probe.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    if completed_process.stdout.strip():
        print(
            "Probe refused: the working tree has uncommitted changes -"
            " commit or stash them first; the probe replays commits,"
            " not the working tree.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def resolve_commit(*, commitish: str) -> str:
    """Resolve a commit-ish; unresolvable is a refusal, not a failure."""
    completed_process = try_git(arguments=["rev-parse", "--verify", f"{commitish}^{{commit}}"])
    if completed_process.returncode != EXIT_CODE_SUCCESS:
        print(
            f"Probe refused: cannot resolve {commitish} - fetch the"
            " base ref first (`git fetch origin`).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    return completed_process.stdout.strip()


def list_commits(*, base_commit: str, head_commit: str) -> list[str]:
    """Return base..head oldest first; the walk order of the probe."""
    output = run_git(arguments=["rev-list", "--reverse", f"{base_commit}..{head_commit}"])
    return [line.strip() for line in output.splitlines() if line.strip()]


def commit_subject(*, commit: str) -> str:
    """Return a commit's subject line - its declared contract (H5)."""
    return run_git(arguments=["show", "-s", "--format=%s", commit]).strip()


def prepare_worktree(*, head_commit: str) -> WorktreeContext:
    """Add a throwaway worktree at the head, on a probe branch."""
    directory = Path(tempfile.mkdtemp(prefix="probe-merge-"))
    probe_branch = f"{PROBE_BRANCH_PREFIX}{os.getpid()}"
    worktree_add = try_git(arguments=["worktree", "add", "--detach", str(directory), head_commit])
    if worktree_add.returncode != EXIT_CODE_SUCCESS:
        print(
            f"Error: git worktree add failed: {worktree_add.stderr.strip()}",
            file=sys.stderr,
        )
        cleanup_worktree(context=WorktreeContext(directory=directory, probe_branch=probe_branch))
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    checkout = try_git(
        arguments=[
            "-C",
            str(directory),
            "checkout",
            "-B",
            probe_branch,
            head_commit,
        ]
    )
    if checkout.returncode != EXIT_CODE_SUCCESS:
        print(f"Error: probe branch checkout failed: {checkout.stderr.strip()}", file=sys.stderr)
        cleanup_worktree(context=WorktreeContext(directory=directory, probe_branch=probe_branch))
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return WorktreeContext(directory=directory, probe_branch=probe_branch)


def replay_onto_base(*, context: WorktreeContext, base_commit: str) -> None:
    """Rebase the probe branch onto the base inside the worktree.

    A replay conflict is a refusal (criteria unmet, never a protocol
    failure): conflicts escalate (I1) - the branch is rebased by hand
    and probed again.
    """
    replay = try_git(arguments=["-C", str(context.directory), "rebase", base_commit])
    if replay.returncode != EXIT_CODE_SUCCESS:
        try_git(arguments=["-C", str(context.directory), "rebase", "--abort"])
        print(
            f"Probe refused: replaying onto {base_commit} conflicts -"
            " a conflict escalates (I1): rebase the branch onto the"
            " base yourself, then probe again.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def replayed_commits(*, context: WorktreeContext, base_commit: str) -> list[str]:
    """Return the replayed commits oldest first (new SHAs, replayed order)."""
    output = run_git(
        arguments=[
            "-C",
            str(context.directory),
            "rev-list",
            "--reverse",
            f"{base_commit}..{context.probe_branch}",
        ]
    )
    return [line.strip() for line in output.splitlines() if line.strip()]


def run_command(*, command: Sequence[str], directory: Path) -> subprocess.CompletedProcess[str]:
    """Run one fast front inside the worktree; the caller checks the exit."""
    return subprocess.run(
        list(command),
        cwd=directory,
        capture_output=True,
        text=True,
        check=False,
    )


def probe_single_commit(*, context: WorktreeContext, commit: str) -> CommitVerdict:
    """Check out one replayed commit and run the fast fronts on it.

    Replayed commits live in the shared object store, so their
    subjects resolve from the main repository. The failing front's
    output is captured for the report; the fronts after it are not
    run (the first red front is the verdict).
    """
    checkout = try_git(arguments=["-C", str(context.directory), "checkout", "--detach", commit])
    if checkout.returncode != EXIT_CODE_SUCCESS:
        print(
            f"Error: cannot check out {commit} in the probe worktree: {checkout.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    subject = commit_subject(commit=commit)
    for front_name, command in FAST_FRONT_COMMANDS:
        completed_process = run_command(command=command, directory=context.directory)
        if completed_process.returncode != EXIT_CODE_SUCCESS:
            failure_output = f"{completed_process.stdout}{completed_process.stderr}".strip()
            return CommitVerdict(
                commit=commit,
                subject=subject,
                failing_front=front_name,
                failure_output=failure_output,
            )
    return CommitVerdict(commit=commit, subject=subject, failing_front=None, failure_output="")


def cleanup_worktree(*, context: WorktreeContext) -> None:
    """Remove the probe worktree and branch; warned, never fatal."""
    removal = try_git(arguments=["worktree", "remove", "--force", str(context.directory)])
    if removal.returncode != EXIT_CODE_SUCCESS:
        print(
            "Warning: could not remove the probe worktree"
            f" {context.directory}: {removal.stderr.strip()}",
        )
        return
    branch_deletion = try_git(arguments=["branch", "-D", context.probe_branch])
    if branch_deletion.returncode != EXIT_CODE_SUCCESS:
        print(
            f"Warning: could not delete the probe branch {context.probe_branch}:"
            f" {branch_deletion.stderr.strip()}",
        )


def print_verdict(*, verdict: CommitVerdict) -> None:
    """Print one commit's verdict line, with the failing front's output."""
    short_commit = verdict.commit[:COMMIT_SHA_LENGTH]
    if verdict.failing_front is None:
        print(f"  {short_commit} {verdict.subject} - PASS")
        return
    print(f"  {short_commit} {verdict.subject} - FAIL ({verdict.failing_front})")
    output_lines = verdict.failure_output.splitlines()[:FAILING_FRONT_OUTPUT_LINES]
    for output_line in output_lines:
        print(f"      {output_line}")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the probe-merge subcommand."""
    subparser = subparsers.add_parser(
        "probe-merge",
        help="Probe every commit of this branch: replay onto a base, run the fast fronts.",
        description=(
            "Local merge probe (ADR-0008 N5, H5): rebases this branch"
            " onto the base inside a throwaway worktree and requires"
            " every replayed commit to be green on the fast fronts"
            " (lint, typecheck, imports). Never opens a PR, never"
            " pushes, never writes main or daily."
        ),
    )
    subparser.add_argument(
        "--base-ref",
        default=DEFAULT_BASE_REF,
        help="Branch or commit to replay onto (default: origin/daily).",
    )
    subparser.set_defaults(runner=run)


def run(*, arguments: argparse.Namespace) -> int:
    """Entry point: replay, probe every commit, report per-commit greenness."""
    ensure_clean_work_tree()
    base_commit = resolve_commit(commitish=arguments.base_ref)
    head_commit = resolve_commit(commitish="HEAD")
    commits = list_commits(base_commit=base_commit, head_commit=head_commit)
    if not commits:
        print(f"Probe refused: HEAD is at {arguments.base_ref}; nothing to probe.")
        return EXIT_CODE_CRITERIA_UNMET
    print(
        f"Probing {len(commits)} commit(s) onto {arguments.base_ref}"
        " (rebase replay in a throwaway worktree)."
    )
    context = prepare_worktree(head_commit=head_commit)
    try:
        replay_onto_base(context=context, base_commit=base_commit)
        replayed = replayed_commits(context=context, base_commit=base_commit)
        verdicts = [probe_single_commit(context=context, commit=commit) for commit in replayed]
    finally:
        cleanup_worktree(context=context)
    for verdict in verdicts:
        print_verdict(verdict=verdict)
    green_commit_count = sum(verdict.failing_front is None for verdict in verdicts)
    if green_commit_count == len(verdicts):
        print(f"Probe verdict: {len(verdicts)}/{len(verdicts)} commits green on the fast fronts.")
        return EXIT_CODE_SUCCESS
    print(
        f"Probe verdict: {green_commit_count}/{len(verdicts)} commits green -"
        " fix the red commit(s), squash in the branch (I3) and probe again."
    )
    return EXIT_CODE_CRITERIA_UNMET
