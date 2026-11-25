"""Tests for the claim driver scripts/claims.py (ADR-0007)."""

import pytest

from scripts import claims
from scripts._shared import (
    CLAIM_LABEL_NAME,
    EXIT_CODE_CRITERIA_UNMET,
)


def test_claim_is_allowed_accepts_a_clean_issue() -> None:
    """An unclaimed issue passes the guard."""
    claims.claim_is_allowed(
        issue={"state": "open"},
        item_number=7,
        expected_kind="issue",
    )


def test_claim_is_allowed_accepts_a_clean_pull_request() -> None:
    """An unclaimed pull request passes the guard."""
    claims.claim_is_allowed(
        issue={"state": "open", "pull_request": {"url": "https://example"}},
        item_number=7,
        expected_kind="pull_request",
    )


def test_claim_is_allowed_refuses_an_issue_claimed_as_pull_request() -> None:
    """An issue number passed to --pr fails closed."""
    with pytest.raises(SystemExit) as exit_information:
        claims.claim_is_allowed(
            issue={"state": "open"},
            item_number=7,
            expected_kind="pull_request",
        )
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_claim_is_allowed_refuses_a_pull_request_claimed_as_issue() -> None:
    """A pull request number passed to --issue fails closed."""
    with pytest.raises(SystemExit) as exit_information:
        claims.claim_is_allowed(
            issue={"state": "open", "pull_request": {"url": "https://example"}},
            item_number=7,
            expected_kind="issue",
        )
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_claim_is_allowed_refuses_an_already_claimed_item() -> None:
    """A night-claimed item is never claimed twice."""
    with pytest.raises(SystemExit) as exit_information:
        claims.claim_is_allowed(
            issue={
                "state": "open",
                "labels": [{"name": CLAIM_LABEL_NAME}],
            },
            item_number=7,
            expected_kind="issue",
        )
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET
