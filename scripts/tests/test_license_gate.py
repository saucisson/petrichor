"""Tests for the license gate scripts/license_gate.py (ADR-0008 N2)."""

import argparse
from typing import TYPE_CHECKING

from scripts import license_gate
from scripts._shared import (
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_SUCCESS,
)

if TYPE_CHECKING:
    import pytest


def test_expression_or_passes_when_one_operand_is_allowed() -> None:
    """Dual licensing: one allowed operand is enough."""
    assert license_gate.expression_is_allowed("MIT OR Apache-2.0")


def test_expression_or_fails_when_no_operand_is_allowed() -> None:
    """Every operand off-list fails the expression."""
    assert not license_gate.expression_is_allowed("GPL-3.0-only OR AGPL-3.0-only")


def test_expression_and_fails_when_one_operand_is_off_list() -> None:
    """AND multiplies: one off-list operand fails the whole branch."""
    assert not license_gate.expression_is_allowed("MIT AND GPL-3.0-only")


def test_expression_parentheses_are_honored() -> None:
    """Parentheses bind before OR and AND apply."""
    assert license_gate.expression_is_allowed("GPL-3.0-only OR (MIT AND Apache-2.0)")
    assert not license_gate.expression_is_allowed("(MIT OR Apache-2.0) AND GPL-3.0-only")


def test_expression_with_exception_binds_to_the_operand() -> None:
    """A WITH exception binds to the operand before it."""
    assert license_gate.expression_is_allowed("Apache-2.0 WITH LLVM-exception")


def test_expression_is_case_insensitive() -> None:
    """SPDX identifiers compare casefolded."""
    assert license_gate.expression_is_allowed("mit")


def test_license_violation_expression_decides_first() -> None:
    """A present expression decides, classifiers stay unread."""
    assert (
        license_gate.license_violation(
            expression="GPL-3.0-only", license_classifiers=("MIT License",), legacy_license=""
        )
        == "GPL-3.0-only"
    )
    assert (
        license_gate.license_violation(
            expression="MIT", license_classifiers=("GPL License",), legacy_license=""
        )
        is None
    )


def test_license_violation_classifiers_pass_on_any_allowed_name() -> None:
    """M
    ultiple classifier names pass when one is allowed."""
    assert (
        license_gate.license_violation(
            expression="", license_classifiers=("MIT License", "GPL License"), legacy_license=""
        )
        is None
    )


def test_license_violation_fails_closed_without_any_metadata() -> None:
    """No expression, no classifier, no field: fail closed."""
    assert (
        license_gate.license_violation(expression="", license_classifiers=(), legacy_license="")
        == "no license metadata at all"
    )


def test_license_violation_legacy_field_passes_on_a_known_name() -> None:
    """The legacy License field passes when it matches the set."""
    assert (
        license_gate.license_violation(expression="", license_classifiers=(), legacy_license="MIT")
        is None
    )
    assert (
        license_gate.license_violation(
            expression="", license_classifiers=(), legacy_license="Some custom EULA text"
        )
        == "Some custom EULA text"
    )


def test_run_fails_when_one_distribution_is_off_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """One offender line per distribution; the verdict is fail."""
    monkeypatch.setattr(license_gate, "scan_offenders", lambda: ["fake-pkg 1.0: GPL-3.0-only"])
    assert license_gate.run(arguments=argparse.Namespace()) == EXIT_CODE_CRITERIA_UNMET


def test_run_succeeds_when_every_distribution_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """No offender: success."""
    monkeypatch.setattr(license_gate, "scan_offenders", lambda: [])
    assert license_gate.run(arguments=argparse.Namespace()) == EXIT_CODE_SUCCESS
