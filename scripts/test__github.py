"""Tests for the shared GitHub API helpers scripts/_github.py (ADR-0021)."""

import httpx
import pytest

from scripts import _github
from scripts._shared import (
    EXIT_CODE_PROTOCOL_FAILURE,
    PullRequestIdentifier,
    RepositorySlug,
)


def client_returning(payload: object, status_code: int = 200) -> httpx.Client:
    """Build an httpx client whose every request returns the given JSON payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_open_github_client_uses_the_shared_timeout() -> None:
    header_value = "test-token"
    """The shared client applies the named HTTP timeout."""
    client = _github.open_github_client(token=header_value)
    assert client.timeout == httpx.Timeout(_github.HTTP_TIMEOUT_SECONDS)
    assert client.headers["Authorization"] == "Bearer test-token"


def test_fetch_pull_request_returns_payload_on_success() -> None:
    """A 2xx response yields the pull request payload."""
    client = client_returning({"number": 59, "state": "open"})
    pull_request = _github.fetch_pull_request(
        http_client=client,
        repository_slug=RepositorySlug("o/r"),
        pull_request_number=PullRequestIdentifier(59),
    )
    assert pull_request["number"] == 59


def test_fetch_pull_request_api_error_is_protocol_failure() -> None:
    """A non-2xx response fails closed with exit 2."""
    client = client_returning({}, status_code=404)
    with pytest.raises(SystemExit) as excinfo:
        _github.fetch_pull_request(
            http_client=client,
            repository_slug=RepositorySlug("o/r"),
            pull_request_number=PullRequestIdentifier(59),
        )
    assert excinfo.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_fetch_check_runs_returns_dict_entries_only() -> None:
    """Only dict entries of check_runs are kept."""
    client = client_returning({"check_runs": [{"id": 1}, "junk", {"id": 2}]})
    runs = _github.fetch_check_runs(
        http_client=client,
        repository_slug=RepositorySlug("o/r"),
        head_sha=_github.HeadSha("abc123"),
    )
    assert runs == [{"id": 1}, {"id": 2}]


def test_fetch_check_runs_api_error_is_protocol_failure() -> None:
    """A non-2xx response on check runs fails closed with exit 2."""
    client = client_returning({}, status_code=500)
    with pytest.raises(SystemExit) as excinfo:
        _github.fetch_check_runs(
            http_client=client,
            repository_slug=RepositorySlug("o/r"),
            head_sha=_github.HeadSha("abc123"),
        )
    assert excinfo.value.code == EXIT_CODE_PROTOCOL_FAILURE


def test_extract_head_sha_returns_the_sha() -> None:
    """The head SHA is extracted from the pull request payload."""
    pull_request: dict[str, object] = {"head": {"sha": "abc123"}}
    assert _github.extract_head_sha(pull_request=pull_request) == "abc123"


def test_extract_head_sha_fails_closed_without_sha() -> None:
    """A pull request without a head SHA is a protocol failure."""
    with pytest.raises(SystemExit) as excinfo:
        _github.extract_head_sha(pull_request={"head": {}})
    assert excinfo.value.code == EXIT_CODE_PROTOCOL_FAILURE
