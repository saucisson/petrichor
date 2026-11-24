"""Tests for the day-cycle drivers scripts/day.py (ADR-0005)."""

from typing import TYPE_CHECKING

from scripts import day

if TYPE_CHECKING:
    import pytest


def test_render_report_lists_merges_worklog_and_counts() -> None:
    """The report contains the baseline, the merges, the worklog entries and the counts."""
    report = day.render_report(
        tag_name="daily-2026-11-08",
        merge_commits=["abc123 feat: x"],
        commit_count=3,
        changed_file_count=5,
        worklog_entries=["docs/worklog/2026-11-12-pr-91.md"],
    )
    assert "Baseline tag: `daily-2026-11-08`" in report
    assert "- abc123 feat: x" in report
    assert "- `docs/worklog/2026-11-12-pr-91.md`" in report
    assert "- Commits since baseline: 3" in report
    assert "- Files changed since baseline: 5" in report


def test_render_report_without_merges_says_so() -> None:
    """No merge commits and no worklog entries: the report says so explicitly."""
    report = day.render_report(
        tag_name="t",
        merge_commits=[],
        commit_count=0,
        changed_file_count=0,
        worklog_entries=[],
    )
    assert "- No merges since the baseline tag." in report
    assert "- No worklog entries since the baseline tag." in report


def test_worklog_entries_since_filters_docs_worklog(monkeypatch: pytest.MonkeyPatch) -> None:
    """worklog_entries_since returns the docs/worklog/ paths from the git diff."""

    def fake_run_git(*, arguments: list[str]) -> str:
        assert "--" in arguments
        assert "docs/worklog" in arguments
        return "docs/worklog/2026-11-12-pr-94.md\ndocs/worklog/2026-11-12-direct-fix.md\n"

    monkeypatch.setattr(day, "run_git", fake_run_git)
    entries = day.worklog_entries_since(tag_name="daily-2026-11-12")
    assert entries == [
        "docs/worklog/2026-11-12-pr-94.md",
        "docs/worklog/2026-11-12-direct-fix.md",
    ]
