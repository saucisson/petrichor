"""Day-cycle drivers: the end-of-day report and the daily integration sync.

``day-report`` prints the end-of-day Markdown report for the daily
branch, measured from the morning tag. Stdlib only, on purpose: the
report must run in the bareest possible environment, because it is
executed exactly when the working day is being closed.

``sync-daily`` reconciles ``daily`` with ``origin/main`` (ADR-0020).
The merge flow is one-directional (ADR-0009): work lands in ``daily``
through reviewed pull requests, and the human merges ``daily`` into
``main`` at the end of the day. But ``main`` can also move WITHOUT
passing through ``daily``: a template sync the human commits on
``main``, a hotfix, a release. The branches then diverge, and merging
``main`` into ``daily`` would write a merge commit (linear history
forbids it), while a raw rebase rewrites a protected branch. The sync
driver is the one sanctioned path (ADR-0020):

- run it from a clean ``daily`` checkout, early in the day (right
  after ``make day-start``, before opening pull requests);
- ``daily`` already contains ``origin/main`` -> exit 0, nothing
  written;
- ``daily`` is strictly behind -> fast-forward, a normal push, no
  rewrite at all;
- diverged -> rebase ``daily`` onto ``origin/main``, verify the
  replayed ``daily`` contains ``origin/main`` and the same number of
  daily-only commits, then push with ``--force-with-lease``;
- a rebase conflict aborts the rebase and refuses (exit 1): resolving
  accepted ``main`` content against accepted ``daily`` work is a
  human decision, never an unattended merge;
- ``main`` is never rewritten, and unattended (night) sessions never
  run this driver (ADR-0020).

Exit codes: 0 synced (or nothing to do), 1 refused (wrong branch,
dirty tree, conflict - nothing was written), 2 protocol failure
(daily restored, remote untouched).
"""

import sys
from datetime import date
from typing import TYPE_CHECKING

from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_SUCCESS,
    run_git,
    try_git,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence

MERGE_COMMIT_FORMAT = "%h %s"
DAILY_BRANCH_NAME = "daily"
MAIN_REMOTE_REFERENCE = "origin/main"
WORKLOG_PATH = "docs/worklog"
EXIT_CODE_FAILURE = EXIT_CODE_CRITERIA_UNMET  # local alias: the report never started
EXIT_CODE_REFUSED = 1
EXIT_CODE_PROTOCOL_FAILURE = 2


def tag_exists(*, tag_name: str) -> bool:
    """Whether the given tag is present in the repository."""
    verification = try_git(arguments=["rev-parse", "--quiet", "--verify", f"refs/tags/{tag_name}"])
    return verification.returncode == EXIT_CODE_SUCCESS


def merges_since(*, tag_name: str) -> list[str]:
    """List merge commits reachable from the daily branch since the tag."""
    revision_range = f"{tag_name}..HEAD"
    git_output = run_git(
        arguments=["log", "--merges", f"--pretty={MERGE_COMMIT_FORMAT}", revision_range]
    )
    return [line for line in git_output.splitlines() if line.strip()]


def commits_since(*, tag_name: str) -> int:
    """Count all commits (merges included) since the tag."""
    git_output = run_git(arguments=["rev-list", "--count", f"{tag_name}..HEAD"])
    return int(git_output.strip())


def changed_files_since(*, tag_name: str) -> int:
    """Count files changed since the tag, merges included."""
    git_output = run_git(arguments=["diff", "--name-only", f"{tag_name}..HEAD"])
    changed_paths = [line for line in git_output.splitlines() if line.strip()]
    return len(changed_paths)


def worklog_entries_since(*, tag_name: str) -> list[str]:
    """List the worklog entries changed since the tag (ADR-0022)."""
    git_output = run_git(arguments=["diff", "--name-only", f"{tag_name}..HEAD", "--", WORKLOG_PATH])
    return [line for line in git_output.splitlines() if line.strip()]


def render_report(
    *,
    tag_name: str,
    merge_commits: Sequence[str],
    commit_count: int,
    changed_file_count: int,
    worklog_entries: Sequence[str],
) -> str:
    """Assemble the Markdown day report."""
    today = date.today().isoformat()
    report_sections: list[str] = []
    report_sections.append(f"# Day report - {today}")
    report_sections.append("")
    report_sections.append(f"Baseline tag: `{tag_name}`")
    report_sections.append("")
    report_sections.append("## Merges of the day")
    report_sections.append("")
    if merge_commits:
        for merge_commit in merge_commits:
            report_sections.append(f"- {merge_commit}")
    else:
        report_sections.append("- No merges since the baseline tag.")
    report_sections.append("")
    report_sections.append("## Worklog entries of the day")
    report_sections.append("")
    if worklog_entries:
        for entry in worklog_entries:
            report_sections.append(f"- `{entry}`")
    else:
        report_sections.append("- No worklog entries since the baseline tag.")
    report_sections.append("")
    report_sections.append("## Activity summary")
    report_sections.append("")
    report_sections.append(f"- Commits since baseline: {commit_count}")
    report_sections.append(f"- Files changed since baseline: {changed_file_count}")
    report_sections.append("")
    report_sections.append("## Reminder - needs-human issues")
    report_sections.append("")
    report_sections.append(
        "- Check open issues labeled `needs-human`: answer them before"
        " approving the merge of `daily` into `main`."
    )
    report_sections.append(
        "- Audit the worklog entries listed above in `docs/worklog/` -"
        " every entry should match a merge above, and no merge above"
        " without an entry."
    )
    report_sections.append("")
    return "\n".join(report_sections)


def register_day_report(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the day-report subcommand."""
    subparser = subparsers.add_parser(
        "day-report",
        help="Print the end-of-day Markdown report for the daily branch.",
        description="Print the end-of-day Markdown report for the daily branch.",
    )
    subparser.add_argument(
        "--since-tag",
        required=True,
        help="The day-start tag to compare against, e.g. daily-2026-11-01.",
    )
    subparser.set_defaults(runner=run_day_report)


def run_day_report(*, arguments: argparse.Namespace) -> int:
    """Entry point: verify the tag, gather statistics, print the report."""
    tag_name = arguments.since_tag
    if not tag_exists(tag_name=tag_name):
        print(
            f"Error: tag '{tag_name}' does not exist."
            " Run `make day-start` first, or pass the correct --since-tag.",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    markdown_report = render_report(
        tag_name=tag_name,
        merge_commits=merges_since(tag_name=tag_name),
        commit_count=commits_since(tag_name=tag_name),
        changed_file_count=changed_files_since(tag_name=tag_name),
        worklog_entries=worklog_entries_since(tag_name=tag_name),
    )
    print(markdown_report)
    return EXIT_CODE_SUCCESS


def current_branch() -> str:
    """Return the checked-out branch name (or the empty detached state)."""
    return try_git(arguments=["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def working_tree_is_clean() -> bool:
    """Whether the working tree has no uncommitted or untracked change."""
    return try_git(arguments=["status", "--porcelain"]).stdout.strip() == ""


def remote_main_exists() -> bool:
    """Whether the remote main branch was fetched at all."""
    verification = try_git(
        arguments=["rev-parse", "--quiet", "--verify", f"refs/remotes/{MAIN_REMOTE_REFERENCE}"]
    )
    return verification.returncode == EXIT_CODE_SUCCESS


def is_ancestor(*, ancestor: str, descendant: str) -> bool:
    """Whether ancestor is reachable from descendant."""
    return (
        try_git(arguments=["merge-base", "--is-ancestor", ancestor, descendant]).returncode
        == EXIT_CODE_SUCCESS
    )


def daily_only_commit_count() -> int:
    """Count the commits daily has and origin/main lacks."""
    count_output = try_git(
        arguments=["rev-list", "--count", f"{MAIN_REMOTE_REFERENCE}..HEAD"]
    ).stdout.strip()
    return int(count_output)


def restore_daily(*, expected_head: str) -> None:
    """Put daily back where it was before the failed attempt."""
    try_git(arguments=["reset", "--hard", expected_head])


def precondition_exit_code(*, branch: str) -> int | None:
    """Refuse anything but a clean daily checkout; None means: proceed."""
    if branch != DAILY_BRANCH_NAME:
        print(
            f"Error: sync-daily runs from the {DAILY_BRANCH_NAME} checkout,"
            f" not from {branch or 'a detached head'} - it rewrites"
            f" {DAILY_BRANCH_NAME} only (ADR-0020).",
            file=sys.stderr,
        )
        return EXIT_CODE_REFUSED
    if not working_tree_is_clean():
        print(
            "Error: the working tree is not clean - commit or stash first:"
            " a rebase over a dirty tree is not honest glue (ADR-0020).",
            file=sys.stderr,
        )
        return EXIT_CODE_REFUSED
    return None


def fetch_origin() -> int:
    """Fetch the remote; stale refs are never a base for a rewrite."""
    fetch = try_git(arguments=["fetch", "origin"])
    if fetch.returncode == EXIT_CODE_SUCCESS:
        return EXIT_CODE_SUCCESS
    print(
        f"Error: could not fetch origin ({fetch.returncode}) - do not sync on stale references.",
        file=sys.stderr,
    )
    return EXIT_CODE_PROTOCOL_FAILURE


def push_daily(*, force: bool, previous_head: str) -> int:
    """Push daily; a refused push restores daily and fails the protocol."""
    arguments = (
        ["push", "--force-with-lease", "origin", DAILY_BRANCH_NAME]
        if force
        else ["push", "origin", DAILY_BRANCH_NAME]
    )
    pushed = try_git(arguments=arguments)
    if pushed.returncode == EXIT_CODE_SUCCESS:
        return EXIT_CODE_SUCCESS
    restore_daily(expected_head=previous_head)
    print(
        f"Error: the push of {DAILY_BRANCH_NAME} was refused"
        f" ({pushed.returncode}) - daily is restored locally, the remote"
        " was not touched.",
        file=sys.stderr,
    )
    return EXIT_CODE_PROTOCOL_FAILURE


def fast_forward_daily(*, previous_head: str) -> int:
    """Strictly behind: move daily to origin/main without any rewrite."""
    merged = try_git(arguments=["merge", "--ff-only", MAIN_REMOTE_REFERENCE])
    if merged.returncode != EXIT_CODE_SUCCESS:
        restore_daily(expected_head=previous_head)
        print(
            "Error: the fast-forward onto origin/main failed - daily is"
            " restored, nothing was pushed.",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
    push_exit_code = push_daily(force=False, previous_head=previous_head)
    if push_exit_code == EXIT_CODE_SUCCESS:
        print(
            f"{DAILY_BRANCH_NAME} was strictly behind: fast-forwarded onto"
            f" {MAIN_REMOTE_REFERENCE} and pushed - no commit was"
            " rewritten (ADR-0020)."
        )
    return push_exit_code


def rebase_daily_onto_main(*, previous_head: str, daily_only_before: int) -> int:
    """Diverged: replay daily onto origin/main, verify, lease-push."""
    rebased = try_git(arguments=["rebase", MAIN_REMOTE_REFERENCE])
    if rebased.returncode != EXIT_CODE_SUCCESS:
        try_git(arguments=["rebase", "--abort"])
        print(
            "Error: rebasing daily onto origin/main hit a conflict - the"
            " rebase is aborted and daily is untouched. Resolving accepted"
            " main content against accepted daily work is a human decision:"
            " escalate it (needs-human), never merge it by hand (ADR-0020).",
            file=sys.stderr,
        )
        return EXIT_CODE_REFUSED
    if not is_ancestor(ancestor=MAIN_REMOTE_REFERENCE, descendant="HEAD"):
        restore_daily(expected_head=previous_head)
        print(
            "Error: the replayed daily does not contain origin/main -"
            " daily is restored, nothing was pushed.",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
    daily_only_after = daily_only_commit_count()
    if daily_only_after != daily_only_before:
        restore_daily(expected_head=previous_head)
        print(
            f"Error: the replay changed the daily-only commit count"
            f" ({daily_only_before} before, {daily_only_after} after) -"
            " daily is restored, nothing was pushed.",
            file=sys.stderr,
        )
        return EXIT_CODE_PROTOCOL_FAILURE
    push_exit_code = push_daily(force=True, previous_head=previous_head)
    if push_exit_code == EXIT_CODE_SUCCESS:
        print(
            f"{DAILY_BRANCH_NAME} rebased onto {MAIN_REMOTE_REFERENCE}:"
            f" {daily_only_before} commit(s) replayed, pushed with"
            " --force-with-lease - main was not touched (ADR-0020)."
            " Local branches based on the old daily need a rebase before"
            " their pull request."
        )
    return push_exit_code


def register_sync_daily(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the sync-daily subcommand."""
    subparser = subparsers.add_parser(
        "sync-daily",
        help="Rebase daily onto origin/main, early in the day (ADR-0020).",
        description="Rebase daily onto origin/main, early in the day (ADR-0020).",
    )
    subparser.set_defaults(runner=run_sync_daily)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register both day-cycle subcommands."""
    register_day_report(subparsers)
    register_sync_daily(subparsers)


def run_sync_daily(*, arguments: argparse.Namespace) -> int:
    """Entry point: clean daily checkout in, synced (or refused) out."""
    refusal_exit_code = precondition_exit_code(branch=current_branch())
    if refusal_exit_code is not None:
        return refusal_exit_code
    fetch_exit_code = fetch_origin()
    if fetch_exit_code != EXIT_CODE_SUCCESS:
        return fetch_exit_code
    if not remote_main_exists():
        print(f"Nothing to sync: {MAIN_REMOTE_REFERENCE} does not exist on the remote yet.")
        return EXIT_CODE_SUCCESS
    if is_ancestor(ancestor=MAIN_REMOTE_REFERENCE, descendant="HEAD"):
        print(f"{DAILY_BRANCH_NAME} already contains {MAIN_REMOTE_REFERENCE} - nothing to sync.")
        return EXIT_CODE_SUCCESS
    previous_head = try_git(arguments=["rev-parse", "HEAD"]).stdout.strip()
    if is_ancestor(ancestor="HEAD", descendant=MAIN_REMOTE_REFERENCE):
        return fast_forward_daily(previous_head=previous_head)
    return rebase_daily_onto_main(
        previous_head=previous_head,
        daily_only_before=daily_only_commit_count(),
    )
