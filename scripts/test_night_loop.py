"""Tests for the unattended night loop night_loop.py (ADR-0017, template 3463aea)."""

import subprocess
from typing import TYPE_CHECKING

import httpx
import pytest

import night_loop
from night_loop import IssueIdentifier

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx client dispatching to the given handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def issue_payload(
    number: int,
    labels: list[dict[str, str]] | None = None,
    body: str = "acceptance criteria",
    *,
    is_pull_request: bool = False,
) -> dict[str, object]:
    """Build a GitHub issue payload."""
    payload: dict[str, object] = {
        "number": number,
        "title": f"Issue {number}",
        "labels": labels or [],
        "body": body,
    }
    if is_pull_request:
        payload["pull_request"] = {"url": "https://api.github.test/pr"}
    return payload


def test_parse_issue_list_accepts_comma_separated_numbers() -> None:
    """The explicit directive parses to an ordered list of issue numbers."""
    assert night_loop.parse_issue_list(raw_issue_list="12, 13,14") == [
        IssueIdentifier(12),
        IssueIdentifier(13),
        IssueIdentifier(14),
    ]


def test_parse_issue_list_rejects_non_numbers() -> None:
    """Anything but digits is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_issue_list(raw_issue_list="12,abc")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_parse_issue_list_rejects_empty_directive() -> None:
    """An empty directive is a protocol failure, not a silent no-op."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_issue_list(raw_issue_list=" , ")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_read_integer_setting_returns_default_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset variable yields the default."""
    monkeypatch.delenv(night_loop.NIGHT_MAXIMUM_SESSIONS_VARIABLE, raising=False)
    assert (
        night_loop.read_integer_setting(
            variable_name=night_loop.NIGHT_MAXIMUM_SESSIONS_VARIABLE, default_value=10
        )
        == 10
    )


def test_read_integer_setting_rejects_non_integer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-integer value fails closed."""
    monkeypatch.setenv(night_loop.NIGHT_MAXIMUM_SESSIONS_VARIABLE, "ten")
    with pytest.raises(SystemExit) as excinfo:
        night_loop.read_integer_setting(
            variable_name=night_loop.NIGHT_MAXIMUM_SESSIONS_VARIABLE, default_value=10
        )
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_triage_skips_pull_requests() -> None:
    """A pull request is never work: the triage gate skips it."""
    reason = night_loop.triage_issue(issue=issue_payload(1, is_pull_request=True))
    assert reason is not None and "pull request" in reason


def test_triage_skips_blocked_needs_human_and_claimed() -> None:
    """The three skip labels are all honored by the triage gate."""
    for label in night_loop.TRIAGE_SKIP_LABELS:
        reason = night_loop.triage_issue(issue=issue_payload(1, [{"name": label}]))
        assert reason is not None and label in reason


def test_triage_skips_issues_without_acceptance_criteria() -> None:
    """An empty body fails the triage gate."""
    reason = night_loop.triage_issue(issue=issue_payload(1, body="  "))
    assert reason is not None and "acceptance criteria" in reason


def test_triage_accepts_a_plain_open_issue() -> None:
    """An open issue with a body passes the gate."""
    assert night_loop.triage_issue(issue=issue_payload(1)) is None


def test_triage_skips_a_malformed_labels_payload() -> None:
    """A non-list or non-dict labels payload is skipped, never trusted."""
    payload = issue_payload(1)
    payload["labels"] = ["blocked"]
    reason = night_loop.triage_issue(issue=payload)
    assert reason is not None and "malformed" in reason


def test_render_issue_section_includes_title_and_body() -> None:
    """The prompt's issue section carries the issue number, title and body."""
    section = night_loop.render_issue_section(
        issue=issue_payload(7), issue_number=IssueIdentifier(7)
    )
    assert "# Issue #7: Issue 7" in section
    assert "acceptance criteria" in section


def test_fetch_candidate_issues_filters_and_sorts() -> None:
    """The worklist excludes triaged-out issues and sorts by number."""
    payload = [
        issue_payload(3),
        issue_payload(1),
        issue_payload(2, [{"name": "blocked"}]),
        issue_payload(4, is_pull_request=True),
    ]
    client = client_with(lambda request: httpx.Response(200, json=payload))
    candidates = night_loop.fetch_candidate_issues(
        http_client=client, repository_slug="o/r", candidate_limit=10
    )
    assert [number for number, _ in candidates] == [IssueIdentifier(1), IssueIdentifier(3)]


def test_fetch_candidate_issues_api_error_yields_empty_worklist() -> None:
    """A failed listing warns and yields no candidates (loop stops)."""
    client = client_with(lambda request: httpx.Response(500, json={}))
    assert (
        night_loop.fetch_candidate_issues(
            http_client=client, repository_slug="o/r", candidate_limit=10
        )
        == []
    )


def test_render_worklist_joins_issue_sections() -> None:
    """The worklist renders one section per candidate, separated."""
    candidates = [(IssueIdentifier(1), issue_payload(1)), (IssueIdentifier(2), issue_payload(2))]
    worklist = night_loop.render_worklist(candidates=candidates)
    assert "# Issue #1" in worklist and "# Issue #2" in worklist and "---" in worklist


def test_build_prompt_replaces_placeholder() -> None:
    """A known placeholder is replaced in the template."""
    assert (
        night_loop.build_prompt(template="a {{issue}} b", placeholder="{{issue}}", replacement="X")
        == "a X b"
    )


def test_build_prompt_appends_when_placeholder_absent() -> None:
    """An unknown placeholder appends the replacement instead of failing."""
    result = night_loop.build_prompt(template="a", placeholder="{{issue}}", replacement="X")
    assert result == "a\n\nX\n"


def test_format_duration_renders_minutes_and_seconds() -> None:
    """271 seconds renders as 04:31."""
    assert night_loop.format_duration(seconds=271) == "04:31"


def test_load_prompt_template_missing_file_fails_closed(tmp_path: Path) -> None:
    """A missing prompt template is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.load_prompt_template(template_path="no/such/prompt.md")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_request_or_fail_transport_error_is_protocol_failure() -> None:
    """A network failure fails closed with exit 2, never a traceback."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        night_loop.request_or_fail(http_client=client, method="GET", url="https://api.github.test")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def completed_remote(url: str) -> subprocess.CompletedProcess[str]:
    """Build the CompletedProcess returned by a mocked `git remote get-url`."""
    return subprocess.CompletedProcess(
        args=["git", "remote", "get-url", "origin"], returncode=0, stdout=url, stderr=""
    )


def test_resolve_repository_slug_parses_ssh_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An SSH origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("git@github.com:acme/widgets.git\n"),
    )
    assert night_loop.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_parses_https_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTPS origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://github.com/acme/widgets.git"),
    )
    assert night_loop.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-GitHub remote fails closed as a protocol failure."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        night_loop.resolve_repository_slug()
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE
