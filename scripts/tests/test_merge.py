"""Tests for the merge driver scripts/merge.py (ADR-0005, A1)."""

from typing import TYPE_CHECKING

import pytest

from scripts import merge
from scripts._shared import EXIT_CODE_CRITERIA_UNMET, EXIT_CODE_PROTOCOL_FAILURE

if TYPE_CHECKING:
    from pathlib import Path


def _clean_pull_request() -> dict[str, object]:
    """A pull request that satisfies every criterion except the checks."""
    return {"state": "open", "draft": False, "mergeable": True, "base": {"ref": "daily"}}


def _clean_report() -> dict[str, object]:
    """A review report with zero BLOCKING findings."""
    return {"blocking_count": 0}


def _green_check_runs() -> list[dict[str, object]]:
    """Every check run concluded success on the head."""
    return [{"name": "quality gate", "status": "completed", "conclusion": "success"}]


def test_criteria_accept_a_clean_daily_pull_request() -> None:
    """Review clean, CI green, mergeable, base daily: criteria met."""
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=_clean_pull_request(),
        check_runs=_green_check_runs(),
    )
    assert accepted
    assert "criteria satisfied" in reason


def test_criteria_refuse_a_main_base() -> None:
    """A pull request targeting main is refused, whatever else passes."""
    pull_request = _clean_pull_request()
    pull_request["base"] = {"ref": "main"}
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=pull_request,
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "targets `main`" in reason


def test_criteria_refuse_an_unknown_base() -> None:
    """A pull request without a base branch name fails closed."""
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request={"state": "open", "draft": False, "mergeable": True},
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "unknown branch" in reason


def test_criteria_refuse_a_blocking_report() -> None:
    """A nonzero blocking count is a refusal."""
    accepted, reason = merge.evaluate_merge_criteria(
        report={"blocking_count": 2},
        pull_request=_clean_pull_request(),
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "2 BLOCKING" in reason


def test_criteria_refuse_a_report_without_blocking_count() -> None:
    """A report without a blocking count is not a merge ticket."""
    accepted, reason = merge.evaluate_merge_criteria(
        report={},
        pull_request=_clean_pull_request(),
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "no blocking_count" in reason


def test_criteria_refuse_a_closed_pull_request() -> None:
    """Merging a closed pull request is refused."""
    pull_request = _clean_pull_request()
    pull_request["state"] = "closed"
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=pull_request,
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "not open" in reason


def test_criteria_refuse_a_draft_pull_request() -> None:
    """A draft is never merged."""
    pull_request = _clean_pull_request()
    pull_request["draft"] = True
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=pull_request,
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "draft" in reason


def test_criteria_refuse_an_unmergeable_pull_request() -> None:
    """A pull request that is not cleanly mergeable is refused."""
    pull_request = _clean_pull_request()
    pull_request["mergeable"] = False
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=pull_request,
        check_runs=_green_check_runs(),
    )
    assert not accepted
    assert "not cleanly mergeable" in reason


def test_criteria_refuse_incomplete_check_runs() -> None:
    """A check run that did not conclude is a refusal."""
    check_runs: list[dict[str, object]] = [
        {"name": "quality gate", "status": "in_progress", "conclusion": None},
    ]
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=_clean_pull_request(),
        check_runs=check_runs,
    )
    assert not accepted
    assert "still running" in reason


def test_criteria_refuse_failed_check_runs() -> None:
    """A completed check run without success is a refusal."""
    check_runs: list[dict[str, object]] = [
        {"name": "quality gate", "status": "completed", "conclusion": "failure"},
    ]
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=_clean_pull_request(),
        check_runs=check_runs,
    )
    assert not accepted
    assert "did not succeed" in reason


def test_criteria_refuse_absent_check_runs() -> None:
    """No check run on the head at all is a refusal."""
    accepted, reason = merge.evaluate_merge_criteria(
        report=_clean_report(),
        pull_request=_clean_pull_request(),
        check_runs=[],
    )
    assert not accepted
    assert "no CI check runs" in reason


def test_review_context_accepts_this_pull_request_and_head() -> None:
    """The context matches the pull request and the current head."""
    merge.check_review_context(
        context={"pr_number": 7, "head_sha": "abc123"},
        pull_request_number=7,
        head_sha="abc123",
    )


def test_review_context_refuses_another_pull_request() -> None:
    """A report written for another pull request is stale."""
    with pytest.raises(SystemExit) as exit_information:
        merge.check_review_context(
            context={"pr_number": 8, "head_sha": "abc123"},
            pull_request_number=7,
            head_sha="abc123",
        )
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_review_context_refuses_a_moved_head() -> None:
    """Commits pushed after the review invalidate it."""
    with pytest.raises(SystemExit) as exit_information:
        merge.check_review_context(
            context={"pr_number": 7, "head_sha": "def456"},
            pull_request_number=7,
            head_sha="abc123",
        )
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_review_context_refuses_a_missing_head_sha() -> None:
    """A context without a head SHA is not a merge ticket."""
    with pytest.raises(SystemExit) as exit_information:
        merge.check_review_context(
            context={"pr_number": 7, "head_sha": ""},
            pull_request_number=7,
            head_sha="abc123",
        )
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_load_review_report_refuses_a_missing_file(tmp_path: Path) -> None:
    """No report file: criteria unmet, never a silent merge."""
    with pytest.raises(SystemExit) as exit_information:
        merge.load_review_report(report_file=str(tmp_path / "absent.json"))
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_load_review_report_refuses_unreadable_json(tmp_path: Path) -> None:
    """An unreadable report is a protocol failure."""
    report_path = tmp_path / "report.json"
    report_path.write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_information:
        merge.load_review_report(report_file=str(report_path))
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_load_review_report_refuses_a_non_object(tmp_path: Path) -> None:
    """A report that is not a JSON object is a protocol failure."""
    report_path = tmp_path / "report.json"
    report_path.write_text("[1]", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_information:
        merge.load_review_report(report_file=str(report_path))
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_load_review_report_returns_the_validated_object(tmp_path: Path) -> None:
    """A valid report loads as a dictionary."""
    report_path = tmp_path / "report.json"
    report_path.write_text('{"blocking_count": 0}', encoding="utf-8")
    report = merge.load_review_report(report_file=str(report_path))
    assert report == {"blocking_count": 0}


def test_load_review_context_refuses_a_missing_file(tmp_path: Path) -> None:
    """No context file: criteria unmet."""
    with pytest.raises(SystemExit) as exit_information:
        merge.load_review_context(context_file=str(tmp_path / "absent.json"))
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET


def test_load_review_context_refuses_a_non_object(tmp_path: Path) -> None:
    """A context that is not a JSON object is a protocol failure."""
    context_path = tmp_path / "context.json"
    context_path.write_text("[1]", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_information:
        merge.load_review_context(context_file=str(context_path))
    assert exit_information.value.code == EXIT_CODE_PROTOCOL_FAILURE
