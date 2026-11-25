"""Tests for the merge probe scripts/probe.py (ADR-0008 N5, H5)."""

import argparse
import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts import probe
from scripts._shared import EXIT_CODE_CRITERIA_UNMET, EXIT_CODE_SUCCESS

if TYPE_CHECKING:
    from pathlib import Path


def _passing_front() -> subprocess.CompletedProcess[str]:
    """A fast front that passes."""
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_list_commits_parses_rev_list_oldest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """rev-list output becomes the oldest-first commit list."""
    monkeypatch.setattr(probe, "run_git", lambda *, arguments: "aaa\nbbb\n\n")
    assert probe.list_commits(base_commit="base", head_commit="head") == ["aaa", "bbb"]


def test_ensure_clean_work_tree_refuses_a_dirty_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uncommitted changes never replay; the probe refuses them."""
    dirty_status = subprocess.CompletedProcess(args=[], returncode=0, stdout="M file", stderr="")
    monkeypatch.setattr(probe, "try_git", lambda *, arguments: dirty_status)
    with pytest.raises(SystemExit) as exit_information:
        probe.ensure_clean_work_tree()
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_resolve_commit_refuses_an_unresolvable_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unresolvable base ref is a refusal (criteria unmet)."""
    unresolvable = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="bad ref")
    monkeypatch.setattr(probe, "try_git", lambda *, arguments: unresolvable)
    with pytest.raises(SystemExit) as exit_information:
        probe.resolve_commit(commitish="origin/nowhere")
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_probe_single_commit_passes_when_every_front_is_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every front green: the verdict carries no failing front."""
    checkout_succeeded = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(probe, "try_git", lambda *, arguments: checkout_succeeded)
    monkeypatch.setattr(probe, "commit_subject", lambda *, commit: "docs: the contract")

    def fake_run_command(
        *, command: tuple[str, ...], directory: Path
    ) -> subprocess.CompletedProcess[str]:
        return _passing_front()

    monkeypatch.setattr(probe, "run_command", fake_run_command)
    verdict = probe.probe_single_commit(
        context=probe.WorktreeContext(directory=tmp_path, probe_branch="probe-merge/1"),
        commit="aaa",
    )
    assert verdict.failing_front is None
    assert verdict.subject == "docs: the contract"


def test_probe_single_commit_stops_at_the_first_red_front(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A red front ends the walk and is captured with its output."""
    checkout_succeeded = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
    monkeypatch.setattr(probe, "try_git", lambda *, arguments: checkout_succeeded)
    monkeypatch.setattr(probe, "commit_subject", lambda *, commit: "feat: a thing")
    front_commands: list[tuple[str, ...]] = []

    def fake_run_command(
        *, command: tuple[str, ...], directory: Path
    ) -> subprocess.CompletedProcess[str]:
        front_commands.append(command)
        if "mypy" in command:
            return subprocess.CompletedProcess(
                args=list(command), returncode=1, stdout="type error\n", stderr=""
            )
        return _passing_front()

    monkeypatch.setattr(probe, "run_command", fake_run_command)
    verdict = probe.probe_single_commit(
        context=probe.WorktreeContext(directory=tmp_path, probe_branch="probe-merge/1"),
        commit="aaa",
    )
    assert verdict.failing_front == "typecheck"
    assert "type error" in verdict.failure_output
    assert len(front_commands) == 2


def test_run_returns_success_when_every_commit_is_green(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A green branch probes to success and cleans the worktree up."""

    calls: list[str] = []

    def fake_ensure_clean_work_tree() -> None:
        return None

    def fake_prepare_worktree(*, head_commit: str) -> probe.WorktreeContext:
        calls.append("prepare")
        return probe.WorktreeContext(directory=tmp_path, probe_branch="probe-merge/test")

    def fake_replay_onto_base(*, context: probe.WorktreeContext, base_commit: str) -> None:
        calls.append("replay")

    def fake_replayed_commits(*, context: probe.WorktreeContext, base_commit: str) -> list[str]:
        return ["xxx"]

    def fake_probe_single_commit(
        *, context: probe.WorktreeContext, commit: str
    ) -> probe.CommitVerdict:
        return probe.CommitVerdict(
            commit=commit, subject="docs: a thing", failing_front=None, failure_output=""
        )

    def fake_cleanup_worktree(*, context: probe.WorktreeContext) -> None:
        calls.append("cleanup")

    monkeypatch.setattr(probe, "ensure_clean_work_tree", fake_ensure_clean_work_tree)
    monkeypatch.setattr(probe, "resolve_commit", lambda *, commitish: "sha")
    monkeypatch.setattr(probe, "list_commits", lambda *, base_commit, head_commit: ["aaa"])
    monkeypatch.setattr(probe, "prepare_worktree", fake_prepare_worktree)
    monkeypatch.setattr(probe, "replay_onto_base", fake_replay_onto_base)
    monkeypatch.setattr(probe, "replayed_commits", fake_replayed_commits)
    monkeypatch.setattr(probe, "probe_single_commit", fake_probe_single_commit)
    monkeypatch.setattr(probe, "cleanup_worktree", fake_cleanup_worktree)
    arguments = argparse.Namespace(base_ref="origin/daily")
    assert probe.run(arguments=arguments) == EXIT_CODE_SUCCESS
    assert calls == ["prepare", "replay", "cleanup"]


def test_run_refuses_when_a_commit_is_red(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One red commit makes the whole probe verdict unmet."""

    def fake_prepare_worktree(*, head_commit: str) -> probe.WorktreeContext:
        return probe.WorktreeContext(directory=tmp_path, probe_branch="probe-merge/test")

    def fake_replayed_commits(*, context: probe.WorktreeContext, base_commit: str) -> list[str]:
        return ["xxx"]

    def fake_probe_single_commit(
        *, context: probe.WorktreeContext, commit: str
    ) -> probe.CommitVerdict:
        return probe.CommitVerdict(
            commit="xxx", subject="feat: a thing", failing_front="typecheck", failure_output="e"
        )

    monkeypatch.setattr(probe, "ensure_clean_work_tree", lambda: None)
    monkeypatch.setattr(probe, "resolve_commit", lambda *, commitish: "sha")
    monkeypatch.setattr(probe, "list_commits", lambda *, base_commit, head_commit: ["aaa"])
    monkeypatch.setattr(probe, "prepare_worktree", fake_prepare_worktree)
    monkeypatch.setattr(probe, "replay_onto_base", lambda *, context, base_commit: None)
    monkeypatch.setattr(probe, "replayed_commits", fake_replayed_commits)
    monkeypatch.setattr(probe, "probe_single_commit", fake_probe_single_commit)
    monkeypatch.setattr(probe, "cleanup_worktree", lambda *, context: None)
    arguments = argparse.Namespace(base_ref="origin/daily")
    assert probe.run(arguments=arguments) == EXIT_CODE_CRITERIA_UNMET


def test_run_refuses_a_head_at_the_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """HEAD at the base has nothing to probe: refusal, no worktree."""
    calls: list[str] = []

    def fake_prepare_worktree(*, head_commit: str) -> probe.WorktreeContext:
        calls.append("prepare")
        raise AssertionError("prepare_worktree must not run on an empty range")

    monkeypatch.setattr(probe, "ensure_clean_work_tree", lambda: None)
    monkeypatch.setattr(probe, "resolve_commit", lambda *, commitish: "sha")
    monkeypatch.setattr(probe, "list_commits", lambda *, base_commit, head_commit: [])
    monkeypatch.setattr(probe, "prepare_worktree", fake_prepare_worktree)
    arguments = argparse.Namespace(base_ref="origin/daily")
    assert probe.run(arguments=arguments) == EXIT_CODE_CRITERIA_UNMET
    assert calls == []
