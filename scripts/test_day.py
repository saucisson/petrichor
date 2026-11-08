"""Tests for the day-cycle drivers scripts/day.py (ADR-0020, template 2aa4d27)."""

import argparse
import subprocess
from typing import TYPE_CHECKING, Protocol

import pytest

from scripts import _shared, day

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


class TryGit(Protocol):
    """The try_git signature: keyword-only arguments in, result out."""

    def __call__(self, *, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        """Run one mocked git command."""
        ...


def completed(
    arguments: list[str], returncode: int = 0, stdout: str = ""
) -> subprocess.CompletedProcess[str]:
    """Build the CompletedProcess a mocked git call returns."""
    return subprocess.CompletedProcess(
        args=["git", *arguments], returncode=returncode, stdout=stdout, stderr=""
    )


def git_router(
    handler: Callable[[list[str]], subprocess.CompletedProcess[str]],
) -> TryGit:
    """Adapt a git-arguments handler to the try_git keyword signature."""

    def fake_try_git(*, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return handler(list(arguments))

    return fake_try_git


def parse_day_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered day subcommands."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    day.register(subparsers)
    return parser.parse_args(command_line_arguments)


def test_register_wires_day_report_and_sync_daily() -> None:
    """Both day-cycle subcommands are registered."""
    arguments = parse_day_arguments(["day-report", "--since-tag", "daily-2026-11-08"])
    assert arguments.since_tag == "daily-2026-11-08"
    arguments = parse_day_arguments(["sync-daily"])
    assert arguments.command == "sync-daily"
    with pytest.raises(SystemExit):
        parse_day_arguments(["day-report"])


def test_render_report_lists_merges_and_counts() -> None:
    """The report contains the baseline, the merges and the counts."""
    report = day.render_report(
        tag_name="daily-2026-11-08",
        merge_commits=["abc123 feat: x"],
        commit_count=3,
        changed_file_count=5,
    )
    assert "Baseline tag: `daily-2026-11-08`" in report
    assert "- abc123 feat: x" in report
    assert "- Commits since baseline: 3" in report
    assert "- Files changed since baseline: 5" in report


def test_render_report_without_merges_says_so() -> None:
    """No merge commits: the report says so explicitly."""
    report = day.render_report(tag_name="t", merge_commits=[], commit_count=0, changed_file_count=0)
    assert "- No merges since the baseline tag." in report


def test_run_day_report_refuses_a_missing_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing baseline tag: exit 1, no report."""
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 1)))
    assert (
        day.run_day_report(arguments=parse_day_arguments(["day-report", "--since-tag", "nope"]))
        == _shared.EXIT_CODE_CRITERIA_UNMET
    )


def test_run_day_report_prints_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An existing tag: the Markdown report is printed, exit 0."""

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["rev-parse", "--quiet"]:
            return completed(arguments, 0)
        if arguments[0] == "log":
            return completed(arguments, 0, "abc123 feat: x\n")
        if arguments[:2] == ["rev-list", "--count"]:
            return completed(arguments, 0, "3\n")
        return completed(arguments, 0, "a.py\nb.py\n")

    monkeypatch.setattr(day, "try_git", git_router(handler))
    monkeypatch.setattr(day, "run_git", lambda *, arguments: handler(arguments).stdout)
    assert (
        day.run_day_report(arguments=parse_day_arguments(["day-report", "--since-tag", "t"]))
        == _shared.EXIT_CODE_SUCCESS
    )
    assert "# Day report" in capsys.readouterr().out


def test_precondition_refuses_a_wrong_branch() -> None:
    """sync-daily runs from daily only: any other branch is refused."""
    assert day.precondition_exit_code(branch="feat/x") == day.EXIT_CODE_REFUSED


def test_precondition_refuses_a_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dirty working tree is refused: no rebase over uncommitted work."""
    monkeypatch.setattr(
        day, "try_git", git_router(lambda arguments: completed(arguments, 0, " M file.py\n"))
    )
    assert day.precondition_exit_code(branch="daily") == day.EXIT_CODE_REFUSED


def test_precondition_allows_a_clean_daily_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clean tree on daily: proceed (None)."""
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 0, "")))
    assert day.precondition_exit_code(branch="daily") is None


def test_fetch_origin_fails_closed_on_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed fetch is a protocol failure: never sync on stale refs."""
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 128)))
    assert day.fetch_origin() == day.EXIT_CODE_PROTOCOL_FAILURE


def test_remote_main_exists_checks_the_remote_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """The remote main ref decides whether there is anything to sync."""
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 0)))
    assert day.remote_main_exists() is True
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 1)))
    assert day.remote_main_exists() is False


def test_is_ancestor_uses_merge_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """merge-base --is-ancestor decides ancestry."""
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 0)))
    assert day.is_ancestor(ancestor="origin/main", descendant="HEAD") is True
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 1)))
    assert day.is_ancestor(ancestor="origin/main", descendant="HEAD") is False


def test_daily_only_commit_count_parses_the_rev_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The daily-only commit count comes from rev-list origin/main..HEAD."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(arguments, 0, "7\n")

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert day.daily_only_commit_count() == 7
    assert seen == [["rev-list", "--count", "origin/main..HEAD"]]


def test_push_daily_force_uses_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diverged path pushes with --force-with-lease."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(arguments, 0)

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert day.push_daily(force=True, previous_head="abc") == _shared.EXIT_CODE_SUCCESS
    assert seen == [["push", "--force-with-lease", "origin", "daily"]]


def test_push_daily_refusal_restores_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused push restores the previous head and fails the protocol."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(arguments, 1 if arguments[0] == "push" else 0)

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert day.push_daily(force=False, previous_head="abc") == day.EXIT_CODE_PROTOCOL_FAILURE
    assert ["reset", "--hard", "abc"] in seen


def test_fast_forward_pushes_without_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strictly behind: ff-only merge, then a normal push."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(arguments, 0)

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert day.fast_forward_daily(previous_head="abc") == _shared.EXIT_CODE_SUCCESS
    assert ["merge", "--ff-only", "origin/main"] in seen
    assert ["push", "origin", "daily"] in seen
    assert ["push", "--force-with-lease", "origin", "daily"] not in seen


def test_fast_forward_failure_restores_daily(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed ff-only merge restores daily, nothing is pushed."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(arguments, 1 if arguments[0] == "merge" else 0)

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert day.fast_forward_daily(previous_head="abc") == day.EXIT_CODE_PROTOCOL_FAILURE
    assert ["reset", "--hard", "abc"] in seen
    assert not any(arguments[0] == "push" for arguments in seen)


def test_rebase_conflict_aborts_and_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebase conflict: abort, refuse with exit 1, daily untouched."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(
            arguments, 1 if arguments[:1] == ["rebase"] and "--abort" not in arguments else 0
        )

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert (
        day.rebase_daily_onto_main(previous_head="abc", daily_only_before=3)
        == day.EXIT_CODE_REFUSED
    )
    assert ["rebase", "--abort"] in seen
    assert not any(arguments[0] == "push" for arguments in seen)


def test_rebase_replay_must_contain_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """A replayed daily that lost origin/main is restored, exit 2."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        if arguments[:2] == ["merge-base", "--is-ancestor"]:
            return completed(arguments, 1)
        return completed(arguments, 0)

    monkeypatch.setattr(day, "try_git", git_router(handler))
    assert (
        day.rebase_daily_onto_main(previous_head="abc", daily_only_before=3)
        == day.EXIT_CODE_PROTOCOL_FAILURE
    )
    assert ["reset", "--hard", "abc"] in seen


def test_rebase_replay_must_keep_the_commit_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """A changed daily-only commit count means a rewritten history: restore, exit 2."""
    monkeypatch.setattr(day, "try_git", git_router(lambda arguments: completed(arguments, 0)))
    monkeypatch.setattr(day, "is_ancestor", lambda *, ancestor, descendant: True)
    monkeypatch.setattr(day, "daily_only_commit_count", lambda: 4)
    assert (
        day.rebase_daily_onto_main(previous_head="abc", daily_only_before=3)
        == day.EXIT_CODE_PROTOCOL_FAILURE
    )


def test_rebase_happy_path_pushes_with_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    """Diverged and clean: rebase, verify, push --force-with-lease, exit 0."""
    seen: list[list[str]] = []

    def handler(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        seen.append(arguments)
        return completed(arguments, 0)

    monkeypatch.setattr(day, "try_git", git_router(handler))
    monkeypatch.setattr(day, "is_ancestor", lambda *, ancestor, descendant: True)
    monkeypatch.setattr(day, "daily_only_commit_count", lambda: 3)
    assert (
        day.rebase_daily_onto_main(previous_head="abc", daily_only_before=3)
        == _shared.EXIT_CODE_SUCCESS
    )
    assert ["rebase", "origin/main"] in seen
    assert ["push", "--force-with-lease", "origin", "daily"] in seen


def test_run_sync_daily_refuses_a_wrong_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not on daily: refused before any fetch or rewrite."""
    monkeypatch.setattr(day, "current_branch", lambda: "feat/x")
    called = False

    def forbidden_fetch() -> int:
        nonlocal called
        called = True
        return _shared.EXIT_CODE_SUCCESS

    monkeypatch.setattr(day, "fetch_origin", forbidden_fetch)
    assert (
        day.run_sync_daily(arguments=parse_day_arguments(["sync-daily"])) == day.EXIT_CODE_REFUSED
    )
    assert called is False


def test_run_sync_daily_nothing_to_do_when_main_is_contained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daily already contains origin/main: exit 0, nothing written."""
    monkeypatch.setattr(day, "current_branch", lambda: "daily")
    monkeypatch.setattr(day, "working_tree_is_clean", lambda: True)
    monkeypatch.setattr(day, "fetch_origin", lambda: _shared.EXIT_CODE_SUCCESS)
    monkeypatch.setattr(day, "remote_main_exists", lambda: True)
    monkeypatch.setattr(day, "is_ancestor", lambda *, ancestor, descendant: True)
    assert (
        day.run_sync_daily(arguments=parse_day_arguments(["sync-daily"]))
        == _shared.EXIT_CODE_SUCCESS
    )


def test_run_sync_daily_fast_forwards_when_strictly_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daily strictly behind origin/main: the fast-forward path runs."""
    monkeypatch.setattr(day, "current_branch", lambda: "daily")
    monkeypatch.setattr(day, "working_tree_is_clean", lambda: True)
    monkeypatch.setattr(day, "fetch_origin", lambda: _shared.EXIT_CODE_SUCCESS)
    monkeypatch.setattr(day, "remote_main_exists", lambda: True)

    def fake_is_ancestor(*, ancestor: str, descendant: str) -> bool:
        return ancestor == "HEAD" and descendant == "origin/main"

    monkeypatch.setattr(day, "is_ancestor", fake_is_ancestor)
    monkeypatch.setattr(
        day, "try_git", git_router(lambda arguments: completed(arguments, 0, "abc\n"))
    )
    monkeypatch.setattr(
        day, "fast_forward_daily", lambda *, previous_head: _shared.EXIT_CODE_SUCCESS
    )
    assert (
        day.run_sync_daily(arguments=parse_day_arguments(["sync-daily"]))
        == _shared.EXIT_CODE_SUCCESS
    )


def test_run_sync_daily_rebases_when_diverged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Diverged branches: the rebase path runs with the commit count."""
    monkeypatch.setattr(day, "current_branch", lambda: "daily")
    monkeypatch.setattr(day, "working_tree_is_clean", lambda: True)
    monkeypatch.setattr(day, "fetch_origin", lambda: _shared.EXIT_CODE_SUCCESS)
    monkeypatch.setattr(day, "remote_main_exists", lambda: True)
    monkeypatch.setattr(day, "is_ancestor", lambda *, ancestor, descendant: False)
    monkeypatch.setattr(
        day, "try_git", git_router(lambda arguments: completed(arguments, 0, "abc\n"))
    )
    monkeypatch.setattr(day, "daily_only_commit_count", lambda: 3)
    seen: list[tuple[str, int]] = []

    def fake_rebase(*, previous_head: str, daily_only_before: int) -> int:
        seen.append((previous_head, daily_only_before))
        return _shared.EXIT_CODE_SUCCESS

    monkeypatch.setattr(day, "rebase_daily_onto_main", fake_rebase)
    assert (
        day.run_sync_daily(arguments=parse_day_arguments(["sync-daily"]))
        == _shared.EXIT_CODE_SUCCESS
    )
    assert seen == [("abc", 3)]
