"""Tests for the pull request driver open_pr.py (ADR-0017, template 3463aea)."""

import subprocess
from typing import TYPE_CHECKING

import httpx
import pytest

from scripts import open_pr

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
    pull_request = open_pr.open_pull_request(
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
        open_pr.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == open_pr.EXIT_CODE_CRITERIA_UNMET


def test_open_pull_request_api_error_is_protocol_failure() -> None:
    """Any other non-2xx response fails closed as a protocol failure."""
    client = client_returning({}, status_code=500)
    with pytest.raises(SystemExit) as excinfo:
        open_pr.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == open_pr.EXIT_CODE_PROTOCOL_FAILURE


def test_open_pull_request_malformed_json_is_protocol_failure() -> None:
    """A 2xx with an undecodable body fails closed with exit 2, not a traceback."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not json{")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        open_pr.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == open_pr.EXIT_CODE_PROTOCOL_FAILURE


def test_open_pull_request_transport_error_is_protocol_failure() -> None:
    """A network failure must not surface as exit 1 (criteria-unmet)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(SystemExit) as excinfo:
        open_pr.open_pull_request(
            http_client=client,
            repository_slug="o/r",
            title="feat: x",
            head_branch="feat/x",
            base_branch="daily",
            body="body",
        )
    assert excinfo.value.code == open_pr.EXIT_CODE_PROTOCOL_FAILURE


def test_read_pull_request_body_from_file(tmp_path: Path) -> None:
    """An explicit body file wins over the repository template."""
    body_file = tmp_path / "body.md"
    body_file.write_text("custom body", encoding="utf-8")
    assert open_pr.read_pull_request_body(body_file=str(body_file)) == "custom body"


def test_read_pull_request_body_missing_file_fails_closed(tmp_path: Path) -> None:
    """A missing body file is a protocol failure, never an empty body."""
    with pytest.raises(SystemExit) as excinfo:
        open_pr.read_pull_request_body(body_file=str(tmp_path / "nope.md"))
    assert excinfo.value.code == open_pr.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_absent_is_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in the environment: fail closed with exit code 2."""
    monkeypatch.delenv(open_pr.GITHUB_TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        open_pr.load_github_token()
    assert excinfo.value.code == open_pr.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_present_is_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment is returned stripped."""
    monkeypatch.setenv(open_pr.GITHUB_TOKEN_VARIABLE, "  token-value  ")
    assert open_pr.load_github_token() == "token-value"


def test_parse_arguments_contract() -> None:
    """The script contract: --title required, --base defaults to daily."""
    arguments = open_pr.parse_arguments(
        command_line_arguments=["--title", "feat: x", "--body-file", "b.md"]
    )
    assert arguments.title == "feat: x"
    assert arguments.body_file == "b.md"
    assert arguments.base == open_pr.DEFAULT_BASE_BRANCH


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
    assert open_pr.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_parses_https_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An HTTPS origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://github.com/acme/widgets.git"),
    )
    assert open_pr.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-GitHub remote fails closed as a protocol failure."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        open_pr.resolve_repository_slug()
    assert excinfo.value.code == open_pr.EXIT_CODE_PROTOCOL_FAILURE
