"""Tests for the issue claim driver claim_issue.py (ADR-0017, template 3463aea)."""

import subprocess
from typing import TYPE_CHECKING

import httpx
import pytest

import claim_issue
from claim_issue import ItemIdentifier

if TYPE_CHECKING:
    from collections.abc import Callable


def client_with(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx client dispatching to the given handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


def issue_payload(
    labels: list[dict[str, str]], *, is_pull_request: bool = False
) -> dict[str, object]:
    """Build a GitHub issue payload with the given labels."""
    payload: dict[str, object] = {"number": 12, "labels": labels}
    if is_pull_request:
        payload["pull_request"] = {"url": "https://api.github.test/pr/12"}
    return payload


def test_claim_is_allowed_on_a_plain_open_issue() -> None:
    """An open issue without the claim label can be claimed."""
    claim_issue.claim_is_allowed(
        issue=issue_payload([{"name": "enhancement"}]),
        item_number=ItemIdentifier(12),
        expected_kind="issue",
    )


def test_claim_refuses_a_pull_request() -> None:
    """A pull request is not an issue: refuse with criteria-unmet."""
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.claim_is_allowed(
            issue=issue_payload([], is_pull_request=True),
            item_number=ItemIdentifier(12),
            expected_kind="issue",
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_CRITERIA_UNMET


def test_claim_refuses_an_already_claimed_issue() -> None:
    """The night-claimed label blocks a second session: criteria-unmet."""
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.claim_is_allowed(
            issue=issue_payload([{"name": claim_issue.CLAIM_LABEL_NAME}]),
            item_number=ItemIdentifier(12),
            expected_kind="issue",
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_CRITERIA_UNMET


def test_claim_refuses_a_malformed_labels_payload() -> None:
    """A non-list labels field fails closed: protocol failure, never fail-open."""
    payload = issue_payload([])
    payload["labels"] = "night-claimed"
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.claim_is_allowed(
            issue=payload, item_number=ItemIdentifier(12), expected_kind="issue"
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE


def test_claim_refuses_non_dict_label_entries() -> None:
    """A bare-string label entry fails closed instead of being dropped."""
    payload = issue_payload([])
    payload["labels"] = ["night-claimed"]
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.claim_is_allowed(
            issue=payload, item_number=ItemIdentifier(12), expected_kind="issue"
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE


def test_fetch_issue_returns_payload_on_success() -> None:
    """A 2xx issue fetch returns the issue dict."""
    client = client_with(lambda request: httpx.Response(200, json=issue_payload([])))
    issue = claim_issue.fetch_issue(
        http_client=client, repository_slug="o/r", item_number=ItemIdentifier(12)
    )
    assert issue["number"] == 12


def test_fetch_issue_api_error_is_criteria_unmet() -> None:
    """A failed fetch means no claim, no work: criteria-unmet, not a crash."""
    client = client_with(lambda request: httpx.Response(404, json={}))
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.fetch_issue(
            http_client=client, repository_slug="o/r", item_number=ItemIdentifier(12)
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_CRITERIA_UNMET


def test_fetch_issue_malformed_json_is_protocol_failure() -> None:
    """A 2xx with an undecodable body fails closed with exit 2, not a traceback."""
    client = client_with(lambda request: httpx.Response(200, content=b"not json{"))
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.fetch_issue(
            http_client=client, repository_slug="o/r", item_number=ItemIdentifier(12)
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE


def test_ensure_claim_label_is_idempotent_when_label_exists() -> None:
    """An existing label: only the GET happens, no creation POST."""
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200, json={"name": "night-claimed"})

    claim_issue.ensure_claim_label(http_client=client_with(handler), repository_slug="o/r")
    assert len(requests_seen) == 1
    assert requests_seen[0].method == "GET"


def test_ensure_claim_label_creates_it_once() -> None:
    """A missing label is created with the ADR-0017 color and description."""
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(201, json={"name": "night-claimed"})

    claim_issue.ensure_claim_label(http_client=client_with(handler), repository_slug="o/r")
    creation = requests_seen[-1]
    assert creation.method == "POST"
    assert creation.url.path.endswith("/labels")


def test_ensure_claim_label_failure_is_protocol_failure() -> None:
    """If the label can neither be found nor created: protocol failure."""
    client = client_with(lambda request: httpx.Response(403, json={}))
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.ensure_claim_label(http_client=client, repository_slug="o/r")
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE


def test_add_claim_label_posts_the_label() -> None:
    """The claim is recorded through the labels endpoint."""
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200, json=[{"name": "night-claimed"}])

    claim_issue.add_claim_label(
        http_client=client_with(handler), repository_slug="o/r", item_number=ItemIdentifier(12)
    )
    assert requests_seen[-1].url.path.endswith("/issues/12/labels")


def test_add_claim_label_failure_is_protocol_failure() -> None:
    """A failed label post means no claim, no work: protocol failure."""
    client = client_with(lambda request: httpx.Response(500, json={}))
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.add_claim_label(
            http_client=client, repository_slug="o/r", item_number=ItemIdentifier(12)
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE


def test_parse_arguments_accepts_issue_or_pr() -> None:
    """The script contract: exactly one of --issue / --pr."""
    arguments = claim_issue.parse_arguments(command_line_arguments=["--issue", "12"])
    assert arguments.issue == 12 and arguments.pr is None
    arguments = claim_issue.parse_arguments(command_line_arguments=["--pr", "53"])
    assert arguments.pr == 53 and arguments.issue is None


def test_claim_refuses_kind_mismatch_for_pr() -> None:
    """An issue number passed to --pr fails closed: criteria-unmet."""
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.claim_is_allowed(
            issue=issue_payload([]),
            item_number=ItemIdentifier(12),
            expected_kind="pull_request",
        )
    assert excinfo.value.code == claim_issue.EXIT_CODE_CRITERIA_UNMET


def test_request_or_fail_transport_error_is_protocol_failure() -> None:
    """A network failure fails closed with exit 2, never a traceback."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.request_or_fail(http_client=client, method="GET", url="https://api.github.test")
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE


def test_request_or_fail_passes_json_payload() -> None:
    """A POST with a JSON body reaches the transport unchanged."""
    requests_seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests_seen.append(request)
        return httpx.Response(200, json={})

    claim_issue.request_or_fail(
        http_client=client_with(handler),
        method="POST",
        url="https://api.github.test/x",
        json_payload={"labels": ["night-claimed"]},
    )
    assert requests_seen[-1].method == "POST"
    assert b"night-claimed" in requests_seen[-1].content


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
    assert claim_issue.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_parses_https_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTPS origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://github.com/acme/widgets.git"),
    )
    assert claim_issue.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-GitHub remote fails closed as a protocol failure."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        claim_issue.resolve_repository_slug()
    assert excinfo.value.code == claim_issue.EXIT_CODE_PROTOCOL_FAILURE
