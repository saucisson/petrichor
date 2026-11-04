"""Generate the end-of-day Markdown report for the daily branch.

Stdlib only: this script must run in the bareest possible environment,
because it is executed exactly when the working day is being closed.

Usage::

    python scripts/day_report.py --since-tag daily-2026-11-01
"""

import argparse
import subprocess
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
MERGE_COMMIT_FORMAT = "%h %s"
EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1


def run_git(*, arguments: Sequence[str]) -> str:
    """Run one git command in the repository and return its stdout."""
    completed_process = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed_process.stdout


def tag_exists(*, tag_name: str) -> bool:
    """Whether the given tag is present in the repository."""
    completed_process = subprocess.run(
        ["git", "rev-parse", "--quiet", "--verify", f"refs/tags/{tag_name}"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed_process.returncode == EXIT_CODE_SUCCESS


def merges_since(*, tag_name: str) -> list[str]:
    """List merge commits reachable from the daily branch since the tag."""
    revision_range = f"{tag_name}..HEAD"
    git_output = run_git(
        arguments=["log", "--merges", f"--pretty={MERGE_COMMIT_FORMAT}", revision_range]
    )
    merge_lines = [line for line in git_output.splitlines() if line.strip()]
    return merge_lines


def commits_since(*, tag_name: str) -> int:
    """Count all commits (merges included) since the tag."""
    revision_range = f"{tag_name}..HEAD"
    git_output = run_git(arguments=["rev-list", "--count", revision_range])
    return int(git_output.strip())


def changed_files_since(*, tag_name: str) -> int:
    """Count files changed since the tag, merges included."""
    revision_range = f"{tag_name}..HEAD"
    git_output = run_git(arguments=["diff", "--name-only", revision_range])
    changed_paths = [line for line in git_output.splitlines() if line.strip()]
    return len(changed_paths)


def render_report(
    *,
    tag_name: str,
    merge_commits: Sequence[str],
    commit_count: int,
    changed_file_count: int,
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
    report_sections.append("")
    return "\n".join(report_sections)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract."""
    argument_parser = argparse.ArgumentParser(
        prog="day_report",
        description="Print the end-of-day Markdown report for the daily branch.",
    )
    argument_parser.add_argument(
        "--since-tag",
        required=True,
        help="The day-start tag to compare against, e.g. daily-2026-11-01.",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: verify the tag, gather statistics, print the report."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    tag_name = arguments.since_tag
    if not tag_exists(tag_name=tag_name):
        print(
            f"Error: tag '{tag_name}' does not exist."
            " Run `make day-start` first, or pass the correct --since-tag.",
            file=sys.stderr,
        )
        return EXIT_CODE_FAILURE
    merge_commits = merges_since(tag_name=tag_name)
    commit_count = commits_since(tag_name=tag_name)
    changed_file_count = changed_files_since(tag_name=tag_name)
    markdown_report = render_report(
        tag_name=tag_name,
        merge_commits=merge_commits,
        commit_count=commit_count,
        changed_file_count=changed_file_count,
    )
    print(markdown_report)
    return EXIT_CODE_SUCCESS


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
