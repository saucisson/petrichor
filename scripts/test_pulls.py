"""Tests for the pull request driver domain scripts/pulls.py (ADR-0017, ADR-0019, ADR-0021)."""

import argparse
import subprocess
from typing import TYPE_CHECKING

import httpx
import pytest

from scripts import _shared, pulls

if TYPE_CHECKING:
    from pathlib import Path


def client_returning(payload: object, status_code: int = 200) -> httpx.Client:
    """Build an httpx client whose every request returns the given JSON payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_open_pull_request_returns_payload_on_success() -> None:
    """A 2xx response yields the pull request payload."""
    client = client_returning({"number": 53, "html_url": "https://example.test/pr/53"})
    pull_request = pulls.open_pull_request(
        http_client=client,
        repository_slug="o/r",
        title="feat: x",
        head_branch="feat/x",
        base_branch="daily",
        body="body",
    )
    assert pull_request["number"] == 53


def test_open_pull_request_422_means_criteria_unmet() -> None:
    """A 422 is a state problem (branch not pushed or PR exists), not a crash."""
    client = client_returning({}, status_code=422)
    with pytest.raises(SystemExit) as excinfo:
        pulls.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == _shared.EXIT_CODE_CRITERIA_UNMET


def test_open_pull_request_api_error_is_protocol_failure() -> None:
    """Any other non-2xx response fails closed as a protocol failure."""
    client = client_returning({}, status_code=500)
    with pytest.raises(SystemExit) as excinfo:
        pulls.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_open_pull_request_malformed_json_is_protocol_failure() -> None:
    """A 2xx with an undecodable body fails closed with exit 2, not a traceback."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        pulls.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_open_pull_request_transport_error_is_protocol_failure() -> None:
    """A network failure must not surface as exit 1 (criteria-unmet)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        pulls.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_read_pull_request_body_from_file(tmp_path: Path) -> None:
    """An explicit body file wins over the repository template."""
    body_file = tmp_path / "body.md"
    body_file.write_text("custom body", encoding="utf-8")
    assert pulls.read_pull_request_body(body_file=str(body_file)) == "custom body"


def test_read_pull_request_body_missing_file_fails_closed(tmp_path: Path) -> None:
    """A missing body file is a protocol failure, never an empty body."""
    with pytest.raises(SystemExit) as excinfo:
        pulls.read_pull_request_body(body_file=str(tmp_path / "nope.md"))
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_absent_is_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in the environment: fail closed with exit code 2."""
    monkeypatch.delenv(_shared.GITHUB_TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _shared.load_github_token()
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_present_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment is returned stripped."""
    monkeypatch.setenv(_shared.GITHUB_TOKEN_VARIABLE, "  token-value  ")
    assert _shared.load_github_token() == "token-value"


def test_register_pr_wires_the_pr_subcommand() -> None:
    """The pr subcommand parses --title/--body-file and defaults base to daily."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    pulls.register(subparsers)
    arguments = parser.parse_args(["pr", "--title", "feat: x", "--body-file", "b.md"])
    assert arguments.title == "feat: x"
    assert arguments.body_file == "b.md"
    assert arguments.base == pulls.DEFAULT_BASE_BRANCH
    assert callable(arguments.runner)


def test_register_update_pr_wires_the_update_pr_subcommand() -> None:
    """The update-pr subcommand requires --pr-number and --body-file."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    pulls.register(subparsers)
    arguments = parser.parse_args(["update-pr", "--pr-number", "59", "--body-file", "b.md"])
    assert arguments.pr_number == 59
    assert arguments.body_file == "b.md"
    assert callable(arguments.runner)


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
    assert _shared.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_parses_https_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTPS origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://github.com/acme/widgets.git"),
    )
    assert _shared.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-GitHub remote fails closed as a protocol failure."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        _shared.resolve_repository_slug()
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def open_pull_request_payload() -> dict[str, object]:
    """The issues-endpoint payload of an OPEN pull request."""
    return {"number": 59, "state": "open", "pull_request": {"url": "..."}}


def test_read_new_body_returns_file_content(tmp_path: Path) -> None:
    """A composed body file is returned verbatim (the driver replaces)."""
    body_file = tmp_path / "body.md"
    body_file.write_text("existing body\n\n## Split proposal\n...", encoding="utf-8")
    assert pulls.read_new_body(body_file=str(body_file)) == (
        "existing body\n\n## Split proposal\n..."
    )


def test_read_new_body_missing_file_is_criteria_unmet(tmp_path: Path) -> None:
    """A missing body file is exit 1, never an empty body."""
    with pytest.raises(SystemExit) as excinfo:
        pulls.read_new_body(body_file=str(tmp_path / "nope.md"))
    assert excinfo.value.code == _shared.EXIT_CODE_CRITERIA_UNMET


def test_read_new_body_empty_file_is_criteria_unmet(tmp_path: Path) -> None:
    """An empty body file is exit 1: an empty PR body is never the intent."""
    body_file = tmp_path / "empty.md"
    body_file.write_text("  \n", encoding="utf-8")
    with pytest.raises(SystemExit) as excinfo:
        pulls.read_new_body(body_file=str(body_file))
    assert excinfo.value.code == _shared.EXIT_CODE_CRITERIA_UNMET


def test_fetch_issue_returns_payload_on_success() -> None:
    """A 2xx response yields the item payload."""
    client = client_returning(open_pull_request_payload())
    item = pulls.fetch_issue(http_client=client, repository_slug="o/r", item_number=59)
    assert item["number"] == 59


def test_fetch_issue_http_error_is_criteria_unmet() -> None:
    """A non-2xx on fetch is exit 1: do not update an unverified item."""
    client = client_returning({}, status_code=404)
    with pytest.raises(SystemExit) as excinfo:
        pulls.fetch_issue(http_client=client, repository_slug="o/r", item_number=59)
    assert excinfo.value.code == _shared.EXIT_CODE_CRITERIA_UNMET


def test_fetch_issue_malformed_payload_is_protocol_failure() -> None:
    """A 2xx with a non-dict payload fails closed with exit 2."""
    client = client_returning(["not", "a", "dict"])
    with pytest.raises(SystemExit) as excinfo:
        pulls.fetch_issue(http_client=client, repository_slug="o/r", item_number=59)
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_fetch_issue_transport_error_is_protocol_failure() -> None:
    """A network failure must not surface as exit 1 (criteria-unmet)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        pulls.fetch_issue(http_client=client, repository_slug="o/r", item_number=59)
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_update_is_allowed_accepts_open_pull_request() -> None:
    """An open pull request passes the guard."""
    pulls.update_is_allowed(issue=open_pull_request_payload(), item_number=59)


def test_update_is_allowed_refuses_plain_issue() -> None:
    """An issue (no pull_request key) is exit 1."""
    with pytest.raises(SystemExit) as excinfo:
        pulls.update_is_allowed(issue={"number": 2, "state": "open"}, item_number=2)
    assert excinfo.value.code == _shared.EXIT_CODE_CRITERIA_UNMET


def test_update_is_allowed_refuses_closed_pull_request() -> None:
    """A closed pull request is exit 1: the morning audit's business."""
    closed = {**open_pull_request_payload(), "state": "closed"}
    with pytest.raises(SystemExit) as excinfo:
        pulls.update_is_allowed(issue=closed, item_number=59)
    assert excinfo.value.code == _shared.EXIT_CODE_CRITERIA_UNMET


def test_write_body_patches_the_pull_request() -> None:
    """A 2xx PATCH completes the update."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"number": 59})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    pulls.write_body(
        http_client=client,
        repository_slug="o/r",
        pull_request_number=59,
        new_body="new body",
    )
    assert requests[0].method == "PATCH"
    assert requests[0].url.path == "/repos/o/r/pulls/59"
    assert b'{"body":"new body"}' in requests[0].content


def test_write_body_api_error_is_protocol_failure() -> None:
    """A non-2xx PATCH fails closed with exit 2 - the body is unchanged."""
    client = client_returning({}, status_code=403)
    with pytest.raises(SystemExit) as excinfo:
        pulls.write_body(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=59,
            new_body="new body",
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_write_body_transport_error_is_protocol_failure() -> None:
    """A network failure on PATCH is exit 2, never a traceback."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        pulls.write_body(
            http_client=client,
            repository_slug="o/r",
            pull_request_number=59,
            new_body="new body",
        )
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE
