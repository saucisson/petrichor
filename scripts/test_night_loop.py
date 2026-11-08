"""Tests for the unattended night loop night_loop.py (ADR-0017, template 3463aea)."""

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from hypothesis import given, strategies

from scripts import night_loop
from scripts.night_loop import IssueIdentifier, PullRequestIdentifier

if TYPE_CHECKING:
    from collections.abc import Callable


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx client dispatching to the given handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def issue_payload(
    *,
    number: int,
    labels: list[dict[str, str]] | None = None,
    body: str = "acceptance criteria",
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
    reason = night_loop.triage_issue(issue=issue_payload(number=1, is_pull_request=True))
    assert reason is not None and "pull request" in reason


def test_triage_skips_blocked_needs_human_and_claimed() -> None:
    """The three skip labels are all honored by the triage gate."""
    for label in night_loop.TRIAGE_SKIP_LABELS:
        reason = night_loop.triage_issue(issue=issue_payload(number=1, labels=[{"name": label}]))
        assert reason is not None and label in reason


def test_triage_skips_issues_without_acceptance_criteria() -> None:
    """An empty body fails the triage gate."""
    reason = night_loop.triage_issue(issue=issue_payload(number=1, body="  "))
    assert reason is not None and "acceptance criteria" in reason


def test_triage_accepts_a_plain_open_issue() -> None:
    """An open issue with a body passes the gate."""
    assert night_loop.triage_issue(issue=issue_payload(number=1)) is None


def test_triage_skips_a_malformed_labels_payload() -> None:
    """A non-list or non-dict labels payload is skipped, never trusted."""
    payload = issue_payload(number=1)
    payload["labels"] = ["blocked"]
    reason = night_loop.triage_issue(issue=payload)
    assert reason is not None and "malformed" in reason


def test_render_issue_section_includes_title_and_body() -> None:
    """The prompt's issue section carries the issue number, title and body."""
    section = night_loop.render_issue_section(
        issue=issue_payload(number=7), issue_number=IssueIdentifier(7)
    )
    assert "# Issue #7: Issue 7" in section
    assert "acceptance criteria" in section


def test_fetch_candidate_issues_filters_and_sorts() -> None:
    """The worklist excludes triaged-out issues and sorts by number."""
    payload = [
        issue_payload(number=3),
        issue_payload(number=1),
        issue_payload(number=2, labels=[{"name": "blocked"}]),
        issue_payload(number=4, is_pull_request=True),
    ]
    client = client_with(lambda request: httpx.Response(200, json=payload))
    candidates = night_loop.fetch_candidate_issues(
        http_client=client,
        repository_slug="o/r",
        candidate_limit=10,
        milestone_numbers=set(),
    )
    assert [number for number, _ in candidates] == [IssueIdentifier(1), IssueIdentifier(3)]


def test_fetch_candidate_issues_api_error_yields_empty_worklist() -> None:
    """A failed listing warns and yields no candidates (loop stops)."""
    client = client_with(lambda request: httpx.Response(500, json={}))
    assert (
        night_loop.fetch_candidate_issues(
            http_client=client,
            repository_slug="o/r",
            candidate_limit=10,
            milestone_numbers=set(),
        )
        == []
    )


def test_render_worklist_joins_issue_sections() -> None:
    """The worklist renders one section per candidate, separated."""
    candidates = [
        (IssueIdentifier(1), issue_payload(number=1)),
        (IssueIdentifier(2), issue_payload(number=2)),
    ]
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
    client = client_with(lambda request: httpx.Response(200, json=issue_payload(number=12)))
    arguments = night_loop.parse_arguments(command_line_arguments=[])
    journal = night_loop.NightJournal(log_file=tmp_path / "night.log")
    night_loop.run_explicit_directive(
        http_client=client,
        prompt_directory=tmp_path,
        repository_slug="o/r",
        issue_numbers=[IssueIdentifier(12), IssueIdentifier(13)],
        milestone_numbers=set(),
        prompt_template="T {{issue}}",
        arguments=arguments,
        journal=journal,
    )
    assert len(sessions) == 2
    assert "# Issue #12" in sessions[0].read_text(encoding="utf-8")
    assert any("Issue #13" in result for result in journal.summary_lines)


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
        lambda request: httpx.Response(
            200, json=issue_payload(number=12, labels=[{"name": "blocked"}])
        )
    )
    arguments = night_loop.parse_arguments(command_line_arguments=[])
    journal = night_loop.NightJournal(log_file=tmp_path / "night.log")
    night_loop.run_explicit_directive(
        http_client=client,
        prompt_directory=tmp_path,
        repository_slug="o/r",
        issue_numbers=[IssueIdentifier(12)],
        milestone_numbers=set(),
        prompt_template="T {{issue}}",
        arguments=arguments,
        journal=journal,
    )
    assert sessions == []
    assert any("skipped" in result for result in journal.summary_lines)


def test_run_auto_discovered_sessions_stops_when_nothing_is_claimed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If a session claims nothing, the loop stops instead of spinning."""
    derivations = 0

    def fake_candidates(**kwargs: object) -> list[tuple[IssueIdentifier, dict[str, object]]]:
        nonlocal derivations
        derivations += 1
        return [(IssueIdentifier(1), issue_payload(number=1))]

    monkeypatch.setattr(night_loop, "fetch_candidate_issues", fake_candidates)
    monkeypatch.setattr(night_loop, "run_developer_session", lambda **kwargs: (0, 3))
    arguments = night_loop.parse_arguments(command_line_arguments=["--max-sessions", "5"])
    journal = night_loop.NightJournal(log_file=tmp_path / "night.log")
    night_loop.run_auto_discovered_sessions(
        http_client=client_with(lambda request: httpx.Response(200, json=[])),
        prompt_directory=tmp_path,
        repository_slug="o/r",
        milestone_numbers=set(),
        prompt_template="W {{worklist}}",
        arguments=arguments,
        journal=journal,
    )
    assert derivations == 2
    assert any("no candidate was claimed" in result.lower() for result in journal.summary_lines)


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
    journal = night_loop.NightJournal(log_file=tmp_path / "night.log")
    night_loop.run_auto_discovered_sessions(
        http_client=client_with(lambda request: httpx.Response(200, json=[])),
        prompt_directory=tmp_path,
        repository_slug="o/r",
        milestone_numbers=set(),
        prompt_template="W {{worklist}}",
        arguments=arguments,
        journal=journal,
    )
    assert sessions == []
    assert journal.summary_lines == []


def test_parse_issue_list_rejects_unicode_digits(monkeypatch: pytest.MonkeyPatch) -> None:
    """'²'.isdigit() is True but int('²') raises: the guard must fail closed."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_issue_list(raw_issue_list="²")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_absent_is_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in the environment: fail closed with exit 2."""
    monkeypatch.delenv(night_loop.GITHUB_TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        night_loop.load_github_token()
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_present_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment is returned stripped."""
    monkeypatch.setenv(night_loop.GITHUB_TOKEN_VARIABLE, "  token-value  ")
    assert night_loop.load_github_token() == "token-value"


def test_load_environment_file_sets_unset_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """KEY=VALUE pairs from .env land in the environment when unset."""
    environment_file = tmp_path / ".env"
    environment_file.write_text("NIGHT_TEST_VARIABLE=from-dotenv\n# comment\n\n", encoding="utf-8")
    monkeypatch.setattr(night_loop, "REPOSITORY_ROOT", tmp_path)
    monkeypatch.delenv("NIGHT_TEST_VARIABLE", raising=False)
    night_loop.load_environment_file()
    assert os.environ["NIGHT_TEST_VARIABLE"] == "from-dotenv"


def pull_request_payload(
    *,
    number: int,
    head_ref: str = "feat/12",
    labels: list[dict[str, str]] | None = None,
    milestone: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a GitHub pull request payload."""
    payload: dict[str, object] = {
        "number": number,
        "title": f"Pull request {number}",
        "labels": labels or [],
        "body": "pr body",
        "head": {"ref": head_ref, "sha": "abc123"},
    }
    if milestone is not None:
        payload["milestone"] = milestone
    return payload


def test_parse_milestone_list_accepts_comma_separated_numbers() -> None:
    """The milestone filter parses to a set of milestone numbers."""
    assert night_loop.parse_milestone_list(raw_milestone_list="3, 4,4") == {3, 4}


def test_parse_milestone_list_empty_means_no_filtering() -> None:
    """An empty milestone list is the empty set: no filtering."""
    assert night_loop.parse_milestone_list(raw_milestone_list=" , ") == set()


def test_parse_milestone_list_rejects_non_numbers() -> None:
    """Anything but digits is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_milestone_list(raw_milestone_list="3,abc")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_parse_milestone_list_rejects_unicode_digits() -> None:
    """'²'.isdigit() is True but int('²') raises: the guard must fail closed."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_milestone_list(raw_milestone_list="²")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_item_milestone_number_reads_the_milestone() -> None:
    """The milestone number is read from the milestone object."""
    item: dict[str, object] = {"milestone": {"number": 7}}
    assert night_loop.item_milestone_number(item=item) == 7


def test_item_milestone_number_handles_missing_or_malformed() -> None:
    """A missing, non-dict or non-int milestone yields None, never a crash."""
    assert night_loop.item_milestone_number(item={}) is None
    assert night_loop.item_milestone_number(item={"milestone": None}) is None
    assert night_loop.item_milestone_number(item={"milestone": {"number": "7"}}) is None


def test_milestone_matches_empty_set_accepts_everything() -> None:
    """No milestone filter: every item passes, even without a milestone."""
    assert night_loop.milestone_matches(item={}, milestone_numbers=set()) is True


def test_milestone_matches_filters_by_number() -> None:
    """With a filter, only items of those milestones pass."""
    item: dict[str, object] = {"milestone": {"number": 3}}
    assert night_loop.milestone_matches(item=item, milestone_numbers={3, 4}) is True
    assert night_loop.milestone_matches(item=item, milestone_numbers={4}) is False
    assert night_loop.milestone_matches(item={}, milestone_numbers={4}) is False


def test_fetch_candidate_issues_applies_the_milestone_filter() -> None:
    """Issues outside the requested milestones are excluded from the worklist."""
    payload = [
        issue_payload(number=1),
        {**issue_payload(number=2), "milestone": {"number": 3}},
        {**issue_payload(number=3), "milestone": {"number": 4}},
    ]
    client = client_with(lambda request: httpx.Response(200, json=payload))
    candidates = night_loop.fetch_candidate_issues(
        http_client=client,
        repository_slug="o/r",
        candidate_limit=10,
        milestone_numbers={4},
    )
    assert [number for number, _ in candidates] == [IssueIdentifier(3)]


def test_parse_backing_issue_number_reads_feat_and_fix_branches() -> None:
    """feat/N and fix/N head branches map to their backing issue number."""
    assert night_loop.parse_backing_issue_number(head_ref="feat/12") == 12
    assert night_loop.parse_backing_issue_number(head_ref="fix/34") == 34
    assert night_loop.parse_backing_issue_number(head_ref="daily") is None
    assert night_loop.parse_backing_issue_number(head_ref="feat/abc") is None
    assert night_loop.parse_backing_issue_number(head_ref=None) is None


def test_parse_backing_issue_number_tolerates_descriptive_suffix() -> None:
    """feat/2-canonical-petri-json backs issue #2: the number alone decides."""
    assert night_loop.parse_backing_issue_number(head_ref="feat/2-canonical-petri-json") == 2
    assert night_loop.parse_backing_issue_number(head_ref="fix/34-hotfix") == 34
    assert night_loop.parse_backing_issue_number(head_ref="feat/12abc") is None
    assert night_loop.parse_backing_issue_number(head_ref="feat/١٢") is None


def test_resolve_log_file_path_defaults_to_the_night_trace() -> None:
    """Without NIGHT_LOG_FILE, the verbose trace lands in .night/night-loop.log."""
    assert night_loop.resolve_log_file_path() == (
        night_loop.REPOSITORY_ROOT / night_loop.DEFAULT_NIGHT_LOG_FILE_NAME
    )


def test_resolve_log_file_path_honors_the_environment_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """NIGHT_LOG_FILE overrides the trace location; absolute paths pass through."""
    monkeypatch.setenv(night_loop.NIGHT_LOG_FILE_VARIABLE, "logs/custom.log")
    assert night_loop.resolve_log_file_path() == night_loop.REPOSITORY_ROOT / "logs/custom.log"
    absolute = tmp_path / "trace.log"
    monkeypatch.setenv(night_loop.NIGHT_LOG_FILE_VARIABLE, str(absolute))
    assert night_loop.resolve_log_file_path() == absolute


def test_night_journal_record_pairs_stdout_line_with_log_entry(tmp_path: Path) -> None:
    """record() appends the summary line and writes the verbose trace entry."""
    log_file = tmp_path / "night.log"
    journal = night_loop.NightJournal(log_file=log_file)
    journal.record(line="Issue #12: session exit 0 (00:05)", text="issue #12: exit 0")
    journal.log(text="verbose only")
    assert journal.summary_lines == ["Issue #12: session exit 0 (00:05)"]
    trace = log_file.read_text(encoding="utf-8")
    assert "issue #12: exit 0" in trace
    assert "verbose only" in trace


def test_item_has_claim_label_detects_the_claim() -> None:
    """The claim label is detected; a malformed labels payload is not trusted."""
    claimed = pull_request_payload(number=1, labels=[{"name": "night-claimed"}])
    assert night_loop.item_has_claim_label(item=claimed) is True
    assert night_loop.item_has_claim_label(item=pull_request_payload(number=1)) is False
    malformed: dict[str, object] = {"labels": ["night-claimed"]}
    assert night_loop.item_has_claim_label(item=malformed) is False


def test_fetch_candidate_pull_requests_recovers_claimed_backing_issues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PR on feat/N whose backing issue is night-claimed is recoverable."""
    backing_issue = issue_payload(number=12, labels=[{"name": "night-claimed"}])

    def handler(request: httpx.Request) -> httpx.Response:
        if "/pulls" in request.url.path:
            return httpx.Response(200, json=[pull_request_payload(number=53)])
        if "/issues/12" in request.url.path:
            return httpx.Response(200, json=backing_issue)
        return httpx.Response(404, json={})

    monkeypatch.setattr(night_loop, "fetch_issue", lambda **kwargs: backing_issue)
    candidates = night_loop.fetch_candidate_pull_requests(
        http_client=client_with(handler),
        repository_slug="o/r",
        candidate_limit=10,
        milestone_numbers=set(),
    )
    assert [number for number, _ in candidates] == [PullRequestIdentifier(53)]


def test_fetch_candidate_pull_requests_skips_unrecoverable_prs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Claimed PRs, non-feat heads and unclaimed backing issues are skipped."""
    backing_issue = issue_payload(number=12, labels=[{"name": "night-claimed"}])
    unclaimed_issue = issue_payload(number=13)
    monkeypatch.setattr(
        night_loop,
        "fetch_issue",
        lambda **kwargs: backing_issue if kwargs["issue_number"] == 12 else unclaimed_issue,
    )
    payload = [
        pull_request_payload(number=1, labels=[{"name": "night-claimed"}]),
        pull_request_payload(number=2, head_ref="daily"),
        pull_request_payload(number=3, head_ref="feat/13"),
        pull_request_payload(number=4, head_ref="feat/12"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/pulls" in request.url.path:
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    candidates = night_loop.fetch_candidate_pull_requests(
        http_client=client_with(handler),
        repository_slug="o/r",
        candidate_limit=10,
        milestone_numbers=set(),
    )
    assert [number for number, _ in candidates] == [PullRequestIdentifier(4)]


def test_fetch_candidate_pull_requests_applies_the_milestone_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRs outside the requested milestones are not recoverable."""
    backing_issue = issue_payload(number=12, labels=[{"name": "night-claimed"}])
    monkeypatch.setattr(night_loop, "fetch_issue", lambda **kwargs: backing_issue)
    payload = [
        {**pull_request_payload(number=1), "milestone": {"number": 3}},
        {**pull_request_payload(number=2), "milestone": {"number": 4}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/pulls" in request.url.path:
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    candidates = night_loop.fetch_candidate_pull_requests(
        http_client=client_with(handler),
        repository_slug="o/r",
        candidate_limit=10,
        milestone_numbers={4},
    )
    assert [number for number, _ in candidates] == [PullRequestIdentifier(2)]


def test_fetch_candidate_pull_requests_api_error_yields_empty_worklist() -> None:
    """A failed listing warns and yields no candidates."""
    client = client_with(lambda request: httpx.Response(500, json={}))
    assert (
        night_loop.fetch_candidate_pull_requests(
            http_client=client,
            repository_slug="o/r",
            candidate_limit=10,
            milestone_numbers=set(),
        )
        == []
    )


def test_render_pull_request_section_mentions_the_backing_issue() -> None:
    """The recovery section names the PR, its branch and its backing issue."""
    section = night_loop.render_pull_request_section(
        pull_request=pull_request_payload(number=53, head_ref="feat/12"),
        pull_request_number=PullRequestIdentifier(53),
    )
    assert "# Pull request #53" in section
    assert "`feat/12`" in section
    assert "backing issue #12" in section


def test_render_auto_worklist_combines_issues_and_pull_requests() -> None:
    """The auto worklist renders issues first, then recoverable PRs."""
    worklist = night_loop.render_auto_worklist(
        issues=[(IssueIdentifier(1), issue_payload(number=1))],
        pull_requests=[(PullRequestIdentifier(53), pull_request_payload(number=53))],
    )
    assert "# Issue #1" in worklist
    assert "# Pull request #53" in worklist
    assert worklist.index("# Issue #1") < worklist.index("# Pull request #53")


def test_auto_candidate_keys_covers_both_kinds() -> None:
    """The anti-spin guard keys issues and pull requests distinctly."""
    keys = night_loop.auto_candidate_keys(
        issues=[(IssueIdentifier(1), issue_payload(number=1))],
        pull_requests=[(PullRequestIdentifier(53), pull_request_payload(number=53))],
    )
    assert keys == {("issue", 1), ("pull request", 53)}


def test_describe_auto_candidates_lists_both_kinds() -> None:
    """The trace line describes each candidate with its kind."""
    description = night_loop.describe_auto_candidates(
        issues=[(IssueIdentifier(1), issue_payload(number=1))],
        pull_requests=[(PullRequestIdentifier(53), pull_request_payload(number=53))],
    )
    assert description == "#1 (issue), #53 (pull request)"


def test_parse_milestone_list_rejects_unicode_decimal_digits() -> None:
    """'٣' is a decimal digit int() accepts: the ASCII guard must fail closed."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_milestone_list(raw_milestone_list="٣")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_parse_issue_list_rejects_unicode_decimal_digits() -> None:
    """'١٢' parses as 12 with int() alone: the ASCII guard must fail closed."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_issue_list(raw_issue_list="١٢")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


@pytest.mark.property
@given(strategies.text())
def test_parse_milestone_list_property_round_trips(raw_milestone_list: str) -> None:
    """Property: an accepted milestone list is a set of ASCII-parsed ints."""
    try:
        milestone_numbers = night_loop.parse_milestone_list(raw_milestone_list=raw_milestone_list)
    except SystemExit:
        return
    assert all(isinstance(number, int) for number in milestone_numbers)
    rejoined = ",".join(str(number) for number in sorted(milestone_numbers))
    assert night_loop.parse_milestone_list(raw_milestone_list=rejoined) == milestone_numbers


@pytest.mark.property
@given(strategies.text())
def test_parse_backing_issue_number_property(head_ref: str) -> None:
    """Property: a parsed backing issue number is always a non-negative int."""
    backing_issue_number = night_loop.parse_backing_issue_number(head_ref=head_ref)
    assert backing_issue_number is None or backing_issue_number >= 0
    if head_ref.startswith(("feat/", "fix/")) and head_ref.split("/", 1)[1].isascii():
        digits = head_ref.split("/", 1)[1]
        if digits.isdigit():
            assert backing_issue_number == int(digits)


def test_parse_milestone_list_rejects_int_accepted_non_digits() -> None:
    """'+3' and '1_2' are accepted by int() alone: the digit guard fails closed."""
    for raw in ("+3", "-3", "1_2"):
        with pytest.raises(SystemExit) as excinfo:
            night_loop.parse_milestone_list(raw_milestone_list=raw)
        assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_parse_issue_list_rejects_int_accepted_non_digits() -> None:
    """'1_2' would claim issue 12 with int() alone: the digit guard fails closed."""
    with pytest.raises(SystemExit) as excinfo:
        night_loop.parse_issue_list(raw_issue_list="1_2")
    assert excinfo.value.code == night_loop.EXIT_CODE_PROTOCOL_FAILURE


def test_fetch_candidate_pull_requests_skips_prs_with_unreachable_backing_issue() -> None:
    """A deleted/invisible backing issue (404) skips the PR, never aborts the loop."""
    payload = [pull_request_payload(number=9, head_ref="feat/999")]

    def handler(request: httpx.Request) -> httpx.Response:
        if "/pulls" in request.url.path:
            return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    candidates = night_loop.fetch_candidate_pull_requests(
        http_client=client_with(handler),
        repository_slug="o/r",
        candidate_limit=10,
        milestone_numbers=set(),
    )
    assert candidates == []


def test_run_explicit_directive_skips_issues_outside_the_milestones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit mode honors the milestone filter: out-of-milestone issues get no session."""
    sessions: list[Path] = []

    def fake_session(**kwargs: object) -> tuple[int, int]:
        prompt_path = kwargs["prompt_path"]
        assert isinstance(prompt_path, Path)
        sessions.append(prompt_path)
        return 0, 1

    monkeypatch.setattr(night_loop, "run_developer_session", fake_session)
    in_milestone = {**issue_payload(number=12), "milestone": {"number": 4}}
    out_of_milestone = {**issue_payload(number=13), "milestone": {"number": 3}}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/issues/12" in request.url.path:
            return httpx.Response(200, json=in_milestone)
        return httpx.Response(200, json=out_of_milestone)

    arguments = night_loop.parse_arguments(command_line_arguments=[])
    journal = night_loop.NightJournal(log_file=tmp_path / "night.log")
    night_loop.run_explicit_directive(
        http_client=client_with(handler),
        prompt_directory=tmp_path,
        repository_slug="o/r",
        issue_numbers=[IssueIdentifier(12), IssueIdentifier(13)],
        milestone_numbers={4},
        prompt_template="T {{issue}}",
        arguments=arguments,
        journal=journal,
    )
    assert len(sessions) == 1
    assert any("not in the requested milestones" in result for result in journal.summary_lines)
