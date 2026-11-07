"""Tests for scripts/create_issues.py (P01 backlog population, spec §19.2)."""

import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.create_issues import (  # noqa: E402
    build_issue_body,
    compute_pending,
    expand_dependencies,
    issue_title,
    parse_backlog,
)


def backlog_text() -> str:
    """Return the real backlog document shipped in the repository."""
    return (REPOSITORY_ROOT / "docs" / "backlog.md").read_text(encoding="utf-8")


def test_expand_dependencies_empty() -> None:
    assert expand_dependencies("—") == []
    assert expand_dependencies("") == []


def test_expand_dependencies_list() -> None:
    assert expand_dependencies("P01, P02") == ["P01", "P02"]


def test_expand_dependencies_range() -> None:
    en_dash = "\u2013"
    assert expand_dependencies(f"P11{en_dash}P17") == [f"P{number:02d}" for number in range(11, 18)]


def test_expand_dependencies_rejects_garbage() -> None:
    with pytest.raises(ValueError, match="unrecognized dependency"):
        expand_dependencies("whatever")


def test_parse_real_backlog() -> None:
    plan = parse_backlog(backlog_text())
    assert len(plan.issues) == 50
    expected = [f"P{number:02d}" for number in range(1, 51)]
    assert [issue.identifier for issue in plan.issues] == expected
    assert plan.milestones == (
        "P0 — socle",
        "P1 — MVP élicitation",
        "Outillage — moteurs & transformations",
        "P2 — chaîne MC",
        "P3 — pilote",
        "P4 — études & publication",
    )
    for issue in plan.issues:
        assert issue.labels
        assert issue.milestone in plan.milestones
        assert issue.body


def test_compute_pending_is_idempotent_when_everything_exists() -> None:
    plan = parse_backlog(backlog_text())
    existing_titles = {issue_title(issue) for issue in plan.issues}
    pending = compute_pending(plan, set(plan.labels), set(plan.milestones), existing_titles)
    assert pending.labels == ()
    assert pending.milestones == ()
    assert pending.issues == ()


def test_compute_pending_reports_only_what_is_missing() -> None:
    plan = parse_backlog(backlog_text())
    existing_titles = {issue_title(issue) for issue in plan.issues[:-1]}
    pending = compute_pending(plan, set(), set(), existing_titles)
    assert [issue.identifier for issue in pending.issues] == ["P50"]
    assert len(pending.labels) == len(plan.labels)


def test_build_issue_body_resolves_blocked_by_to_issue_numbers() -> None:
    plan = parse_backlog(backlog_text())
    issue = next(item for item in plan.issues if item.identifier == "P03")
    body = build_issue_body(issue, {"P01": 1, "P02": 2})
    assert "blocked-by: #1, #2" in body
    assert "Critères d'acceptation" in body


def test_build_issue_body_without_dependencies() -> None:
    plan = parse_backlog(backlog_text())
    issue = next(item for item in plan.issues if item.identifier == "P01")
    body = build_issue_body(issue, {})
    assert "**Dépendances.** aucune." in body
