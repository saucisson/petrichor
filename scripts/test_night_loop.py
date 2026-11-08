"""Tests for the unattended night loop night_loop.py (ADR-0017, template 3463aea)."""

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

import night_loop
from night_loop import IssueIdentifier

if TYPE_CHECKING:
    from collections.abc import Callable


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


def test_run_explicit_directive_runs_one_session_per_eligible_issue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit mode: one headless session per issue of the directive, in order."""
    sessions: list[Path] = []

    def fake_session(**kwargs: object) -> tuple[int, int]:
        prompt_path = kwargs["prompt_path"]
        assert isinstance(prompt_path, Path)
        sessions.append(prompt_path)
        return 0, 5

    monkeypatch.setattr(night_loop, "run_developer_session", fake_session)
    client = client_with(lambda request: httpx.Response(200, json=issue_payload(12)))
    arguments = night_loop.parse_arguments(command_line_arguments=[])
    results: list[str] = []
    night_loop.run_explicit_directive(
        http_client=client,
        prompt_directory=tmp_path,
        repository_slug="o/r",
        issue_numbers=[IssueIdentifier(12), IssueIdentifier(13)],
        prompt_template="T {{issue}}",
        arguments=arguments,
        results=results,
    )
    assert len(sessions) == 2
    assert "# Issue #12" in sessions[0].read_text(encoding="utf-8")
    assert any("Issue #13" in result for result in results)


def test_run_explicit_directive_skips_triaged_issues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A blocked issue in the directive gets no session, only a skip record."""
    sessions: list[Path] = []

    def fake_session(**kwargs: object) -> tuple[int, int]:
        prompt_path = kwargs["prompt_path"]
        assert isinstance(prompt_path, Path)
        sessions.append(prompt_path)
        return 0, 1

    monkeypatch.setattr(night_loop, "run_developer_session", fake_session)
    client = client_with(
        lambda request: httpx.Response(200, json=issue_payload(12, [{"name": "blocked"}]))
    )
    arguments = night_loop.parse_arguments(command_line_arguments=[])
    results: list[str] = []
    night_loop.run_explicit_directive(
        http_client=client,
        prompt_directory=tmp_path,
        repository_slug="o/r",
        issue_numbers=[IssueIdentifier(12)],
        prompt_template="T {{issue}}",
        arguments=arguments,
        results=results,
    )
    assert sessions == []
    assert any("skipped" in result for result in results)


def test_run_auto_discovered_sessions_stops_when_nothing_is_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If a session claims nothing, the loop stops instead of spinning."""
    derivations = 0

    def fake_candidates(**kwargs: object) -> list[tuple[IssueIdentifier, dict[str, object]]]:
        nonlocal derivations
        derivations += 1
        return [(IssueIdentifier(1), issue_payload(1))]

    monkeypatch.setattr(night_loop, "fetch_candidate_issues", fake_candidates)
    monkeypatch.setattr(night_loop, "run_developer_session", lambda **kwargs: (0, 3))
    arguments = night_loop.parse_arguments(command_line_arguments=["--max-sessions", "5"])
    results: list[str] = []
    night_loop.run_auto_discovered_sessions(
        http_client=client_with(lambda request: httpx.Response(200, json=[])),
        prompt_directory=tmp_path,
        repository_slug="o/r",
        prompt_template="W {{worklist}}",
        arguments=arguments,
        results=results,
    )
    assert derivations == 2
    assert any("no candidate was claimed" in result.lower() for result in results)


def test_run_auto_discovered_sessions_stops_when_worklist_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No unclaimed eligible issue left: the night ends cleanly."""
    monkeypatch.setattr(night_loop, "fetch_candidate_issues", lambda **kwargs: [])
    sessions: list[Path] = []

    def fake_session(**kwargs: object) -> tuple[int, int]:
        prompt_path = kwargs["prompt_path"]
        assert isinstance(prompt_path, Path)
        sessions.append(prompt_path)
        return 0, 1

    monkeypatch.setattr(night_loop, "run_developer_session", fake_session)
    arguments = night_loop.parse_arguments(command_line_arguments=["--max-sessions", "5"])
    results: list[str] = []
    night_loop.run_auto_discovered_sessions(
        http_client=client_with(lambda request: httpx.Response(200, json=[])),
        prompt_directory=tmp_path,
        repository_slug="o/r",
        prompt_template="W {{worklist}}",
        arguments=arguments,
        results=results,
    )
    assert sessions == []
    assert results == []
