"""Tests for the review preconditions gate in pr_review.py (ADR-0010, template d6379b4)."""

from typing import Protocol

import httpx
import pytest

from scripts import pr_review

HEAD_SHA = "abc123"
PULL_REQUEST: dict[str, object] = {"head": {"sha": HEAD_SHA}}


def client_with_check_runs(check_runs: list[object]) -> httpx.Client:
    """Build an httpx client returning the given check runs for any request."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"check_runs": check_runs})

    return httpx.Client(transport=httpx.MockTransport(handler))


class GitRunner(Protocol):
    """Signature of the run_git replacement used in tests."""

    def __call__(self, *, arguments: list[str]) -> str: ...


def fake_git_factory(*, head: str, porcelain: str) -> GitRunner:
    """Return a run_git replacement with fixed HEAD and status output."""

    def fake_run_git(*, arguments: list[str]) -> str:
        if arguments[:2] == ["rev-parse", "HEAD"]:
            return head
        return porcelain

    return fake_run_git


def test_extract_head_sha_returns_sha() -> None:
    """The head SHA is extracted from the pull request payload."""
    assert pr_review.extract_head_sha(pull_request=PULL_REQUEST) == HEAD_SHA


def test_extract_head_sha_fails_closed_without_sha() -> None:
    """A pull request without head SHA is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        pr_review.extract_head_sha(pull_request={"head": {}})
    assert excinfo.value.code == pr_review.EXIT_CODE_PROTOCOL_FAILURE


def test_fetch_check_runs_returns_dict_entries() -> None:
    """Only dict entries of check_runs are kept."""
    runs: list[object] = [{"name": "ci", "status": "completed", "conclusion": "success"}, "junk"]
    client = client_with_check_runs(runs)
    assert pr_review.fetch_check_runs(
        http_client=client, repository_slug="o/r", head_sha=HEAD_SHA
    ) == [runs[0]]


def test_fetch_check_runs_empty_on_malformed_payload() -> None:
    """A payload without a check_runs list yields no runs."""
    client = client_with_check_runs([])
    assert (
        pr_review.fetch_check_runs(http_client=client, repository_slug="o/r", head_sha=HEAD_SHA)
        == []
    )


def test_preconditions_pass_on_matching_head_and_green_ci(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching head, clean tree, all checks green: the review may start."""
    monkeypatch.setattr(pr_review, "run_git", fake_git_factory(head=HEAD_SHA + "\n", porcelain=""))
    client = client_with_check_runs(
        [{"name": "quality gate", "status": "completed", "conclusion": "success"}]
    )
    pr_review.enforce_review_preconditions(
        http_client=client, repository_slug="o/r", pull_request=PULL_REQUEST
    )


def test_preconditions_refuse_stale_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR head != local HEAD: exit 3, not a verdict."""
    monkeypatch.setattr(pr_review, "run_git", fake_git_factory(head="other\n", porcelain=""))
    client = client_with_check_runs([])
    with pytest.raises(SystemExit) as excinfo:
        pr_review.enforce_review_preconditions(
            http_client=client, repository_slug="o/r", pull_request=PULL_REQUEST
        )
    assert excinfo.value.code == pr_review.EXIT_CODE_PRECONDITIONS_UNMET


def test_preconditions_refuse_unfinished_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """A check still running: exit 3 - the review is the last gate."""
    monkeypatch.setattr(pr_review, "run_git", fake_git_factory(head=HEAD_SHA + "\n", porcelain=""))
    client = client_with_check_runs(
        [{"name": "quality gate", "status": "in_progress", "conclusion": None}]
    )
    with pytest.raises(SystemExit) as excinfo:
        pr_review.enforce_review_preconditions(
            http_client=client, repository_slug="o/r", pull_request=PULL_REQUEST
        )
    assert excinfo.value.code == pr_review.EXIT_CODE_PRECONDITIONS_UNMET


def test_preconditions_refuse_failed_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    """A completed-but-failed check: exit 3."""
    monkeypatch.setattr(pr_review, "run_git", fake_git_factory(head=HEAD_SHA + "\n", porcelain=""))
    client = client_with_check_runs(
        [{"name": "quality gate", "status": "completed", "conclusion": "failure"}]
    )
    with pytest.raises(SystemExit) as excinfo:
        pr_review.enforce_review_preconditions(
            http_client=client, repository_slug="o/r", pull_request=PULL_REQUEST
        )
    assert excinfo.value.code == pr_review.EXIT_CODE_PRECONDITIONS_UNMET
