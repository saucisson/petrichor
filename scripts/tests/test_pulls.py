"""Tests for the pull request driver scripts/pulls.py (ADR-0007, ADR-0005)."""

import argparse
import subprocess
from typing import TYPE_CHECKING

from scripts import pulls
from scripts._shared import REPOSITORY_ROOT

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_extract_issue_number_finds_first_reference() -> None:
    """The first #N in the body is the issue reference."""
    assert pulls.extract_issue_number(body="Closes #24\n\nSee also #99") == "24"


def test_extract_issue_number_returns_none_without_reference() -> None:
    """No #N in the body: no issue line."""
    assert pulls.extract_issue_number(body="No issue here") is None


def test_write_worklog_entry_creates_index_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worklog entry is written with the PR number, title, branch, issue, URL."""
    monkeypatch.setattr(pulls, "REPOSITORY_ROOT", tmp_path)
    entry_path = pulls.write_worklog_entry(
        pull_request_number=91,
        title="feat(P24): certified reductions",
        head_branch="feat/24-reductions",
        body="Closes #24",
        repository_slug="saucisson/project-template",
    )
    content = entry_path.read_text(encoding="utf-8")
    assert "# PR #91: feat(P24): certified reductions" in content
    assert "- Branch: feat/24-reductions" in content
    assert "- Issue: #24" in content
    assert "https://github.com/saucisson/project-template/pull/91" in content
    assert entry_path.name.endswith("-pr-91.md")


def test_write_worklog_entry_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second call for the same PR does not overwrite the existing file."""
    monkeypatch.setattr(pulls, "REPOSITORY_ROOT", tmp_path)
    first = pulls.write_worklog_entry(
        pull_request_number=91,
        title="first title",
        head_branch="b",
        body="",
        repository_slug="o/r",
    )
    first.write_text("custom content", encoding="utf-8")
    second = pulls.write_worklog_entry(
        pull_request_number=91,
        title="second title",
        head_branch="b",
        body="",
        repository_slug="o/r",
    )
    assert second == first
    assert second.read_text(encoding="utf-8") == "custom content"


def test_commit_and_push_worklog_entry_commits_and_pushes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh entry is added, committed with 'worklog: PR #N', and pushed."""
    calls: list[list[str]] = []

    def fake_run_git(*, arguments: list[str]) -> str:
        calls.append(arguments)
        return ""

    def fake_try_git(*, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        # diff --cached --quiet exits 1 when something IS staged.
        returncode = 1 if arguments[:3] == ["diff", "--cached", "--quiet"] else 0
        return subprocess.CompletedProcess(
            args=arguments, returncode=returncode, stdout="", stderr=""
        )

    monkeypatch.setattr(pulls, "run_git", fake_run_git)
    monkeypatch.setattr(pulls, "try_git", fake_try_git)
    entry = tmp_path / "docs/worklog/2026-11-12-pr-94.md"
    entry.parent.mkdir(parents=True)
    entry.write_text("card", encoding="utf-8")
    result = pulls.commit_and_push_worklog_entry(entry_path=entry, pull_request_number=94)
    assert result is True
    assert ["add", str(entry)] in calls
    assert ["commit", "-m", "worklog: PR #94"] in calls
    assert ["push"] in calls


def test_commit_and_push_worklog_entry_idempotent_when_nothing_to_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When nothing is staged (diff --cached --quiet exits 0), no commit/push."""
    calls: list[list[str]] = []

    def fake_run_git(*, arguments: list[str]) -> str:
        calls.append(arguments)
        return ""

    def fake_try_git(*, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        returncode = 0 if arguments[:3] == ["diff", "--cached", "--quiet"] else 1
        return subprocess.CompletedProcess(
            args=arguments, returncode=returncode, stdout="", stderr=""
        )

    monkeypatch.setattr(pulls, "run_git", fake_run_git)
    monkeypatch.setattr(pulls, "try_git", fake_try_git)
    entry = tmp_path / "entry.md"
    entry.write_text("card", encoding="utf-8")
    result = pulls.commit_and_push_worklog_entry(entry_path=entry, pull_request_number=94)
    assert result is False
    assert ["push"] not in calls
    assert not any(call[:1] == ["commit"] for call in calls)


def test_run_pr_wires_worklog_entry_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_pr calls write_worklog_entry and commit_and_push_worklog_entry.

    Deleting the wiring from run_pr must fail this test.
    """
    calls: list[str] = []

    def fake_write_worklog_entry(**kwargs: object) -> Path:
        calls.append("write_worklog_entry")
        return REPOSITORY_ROOT / "docs/worklog/2026-11-12-pr-94.md"

    def fake_commit_and_push(*, entry_path: Path, pull_request_number: int) -> bool:
        calls.append("commit_and_push_worklog_entry")
        return True

    monkeypatch.setattr(pulls, "write_worklog_entry", fake_write_worklog_entry)
    monkeypatch.setattr(pulls, "commit_and_push_worklog_entry", fake_commit_and_push)
    monkeypatch.setattr(pulls, "load_github_token", lambda: "token")
    monkeypatch.setattr(pulls, "resolve_repository_slug", lambda: "o/r")
    monkeypatch.setattr(pulls, "current_branch", lambda: "feat/x")
    monkeypatch.setattr(pulls, "read_pull_request_body", lambda *, body_file: "body")

    def fake_open_pull_request(**kwargs: object) -> dict[str, object]:
        return {"number": 94, "html_url": "https://example.com/pr/94"}

    monkeypatch.setattr(pulls, "open_pull_request", fake_open_pull_request)

    class _FakeClient:
        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(pulls, "open_github_client", lambda token: _FakeClient())

    arguments = argparse.Namespace(title="t", body_file="", head="", base="daily")
    exit_code = pulls.run_pr(arguments=arguments)
    assert exit_code == 0
    assert "write_worklog_entry" in calls
    assert "commit_and_push_worklog_entry" in calls
