"""Tests for create_issues.py (P01 backlog population, spec §19.2).

Lives next to the script (ADR-0014 keeps tools/ self-contained). Run with
``uv run pytest scripts`` — the constitutional testpaths only covers the
notify tool, so this file is collected explicitly, not by ``make test``.
Assertions go through ``check`` because S101 (assert) is not exempted in
scripts/ and the lint configuration is constitutional.
"""

import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

import create_issues
from create_issues import (
    build_issue_body,
    compute_pending,
    expand_dependencies,
    issue_title,
    parse_backlog,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IDENTIFIER_PATTERN = re.compile(r"P\d+")
EXPECTED_MILESTONES = (
    "P0 — socle",
    "P1 — MVP élicitation",
    "Outillage — moteurs & transformations",
    "P2 — chaîne MC",
    "P3 — pilote",
    "P4 — études & publication",
)


def check(condition: bool, message: str) -> None:
    """Raise AssertionError when the condition fails (S101-safe assert)."""
    if not condition:
        raise AssertionError(message)


def backlog_text() -> str:
    """Return the real backlog document shipped in the repository."""
    return (REPOSITORY_ROOT / "docs" / "backlog.md").read_text(encoding="utf-8")


def test_expand_dependencies_empty() -> None:
    """Em-dash, hyphen and empty cells all mean no dependencies."""
    check(expand_dependencies("—") == [], "em-dash should expand to nothing")
    check(expand_dependencies("") == [], "empty cell should expand to nothing")


def test_expand_dependencies_list() -> None:
    """A comma-separated cell expands to its identifiers in order."""
    check(expand_dependencies("P01, P02") == ["P01", "P02"], "list expansion failed")


def test_expand_dependencies_range() -> None:
    """An en-dash range expands to every identifier it covers."""
    en_dash = "\u2013"
    expected = [f"P{number:02d}" for number in range(11, 18)]
    check(expand_dependencies(f"P11{en_dash}P17") == expected, "range expansion failed")


def test_expand_dependencies_rejects_garbage() -> None:
    """An unrecognized cell raises ValueError instead of passing silently."""
    with pytest.raises(ValueError, match="unrecognized dependency"):
        expand_dependencies("whatever")


@pytest.mark.property
@given(st.text())
def test_expand_dependencies_output_is_always_well_formed(text: str) -> None:
    """Property: any accepted input expands to well-formed P-identifiers only."""
    try:
        result = expand_dependencies(text)
    except ValueError:
        return
    check(
        all(IDENTIFIER_PATTERN.fullmatch(item) for item in result),
        f"malformed identifier in {result!r}",
    )


def test_parse_real_backlog() -> None:
    """The shipped backlog parses to 50 issues across the 6 expected milestones."""
    plan = parse_backlog(backlog_text())
    check(len(plan.issues) == create_issues.EXPECTED_ISSUE_COUNT, "wrong issue count")
    expected = [f"P{number:02d}" for number in range(1, 51)]
    check([issue.identifier for issue in plan.issues] == expected, "identifiers not P01..P50")
    check(plan.milestones == EXPECTED_MILESTONES, "milestone order mismatch")
    for issue in plan.issues:
        check(len(issue.labels) > 0, f"{issue.identifier} has no labels")
        check(issue.milestone in plan.milestones, f"{issue.identifier} milestone unknown")
        check(len(issue.body) > 0, f"{issue.identifier} has an empty body")


def test_compute_pending_is_idempotent_when_everything_exists() -> None:
    """When the remote already has everything, the pending plan is empty."""
    plan = parse_backlog(backlog_text())
    existing_titles = {issue_title(issue) for issue in plan.issues}
    pending = compute_pending(
        plan=plan,
        existing_labels=set(plan.labels),
        existing_milestones=set(plan.milestones),
        existing_titles=existing_titles,
    )
    check(pending.labels == (), "labels should all exist")
    check(pending.milestones == (), "milestones should all exist")
    check(pending.issues == (), "issues should all exist")


def test_compute_pending_reports_only_what_is_missing() -> None:
    """Only the missing issue is reported as pending."""
    plan = parse_backlog(backlog_text())
    existing_titles = {issue_title(issue) for issue in plan.issues[:-1]}
    pending = compute_pending(
        plan=plan,
        existing_labels=set(),
        existing_milestones=set(),
        existing_titles=existing_titles,
    )
    check([issue.identifier for issue in pending.issues] == ["P50"], "only P50 should be pending")
    check(len(pending.labels) == len(plan.labels), "all labels should be pending")


def test_build_issue_body_resolves_blocked_by_to_issue_numbers() -> None:
    """The blocked-by footer uses the real issue numbers."""
    plan = parse_backlog(backlog_text())
    issue = next(item for item in plan.issues if item.identifier == "P03")
    body = build_issue_body(issue=issue, number_by_identifier={"P01": 1, "P02": 2})
    check("blocked-by: #1, #2" in body, "blocked-by footer missing or wrong")
    check("Critères d'acceptation" in body, "acceptance criteria missing from body")


def test_build_issue_body_without_dependencies() -> None:
    """An issue without dependencies gets the explicit 'aucune.' footer."""
    plan = parse_backlog(backlog_text())
    issue = next(item for item in plan.issues if item.identifier == "P01")
    body = build_issue_body(issue=issue, number_by_identifier={})
    check("**Dépendances.** aucune." in body, "no-dependency footer missing")


def test_module_exposes_expected_entry_point() -> None:
    """The module exposes a callable main entry point."""
    check(callable(create_issues.main), "main is not callable")
