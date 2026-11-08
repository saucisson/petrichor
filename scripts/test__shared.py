"""Tests for the shared driver plumbing scripts/_shared.py (ADR-0021)."""

import subprocess

import pytest

from scripts import _shared


def test_load_github_token_absent_is_protocol_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token in the environment: fail closed with exit code 2."""
    monkeypatch.delenv(_shared.GITHUB_TOKEN_VARIABLE, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        _shared.load_github_token()
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_load_github_token_present_is_returned_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token in the environment is returned stripped."""
    monkeypatch.setenv(_shared.GITHUB_TOKEN_VARIABLE, "  token-value  ")
    assert _shared.load_github_token() == "token-value"


def completed_remote(url: str) -> subprocess.CompletedProcess[str]:
    """Build the CompletedProcess returned by a mocked git call."""
    return subprocess.CompletedProcess(args=["git"], returncode=0, stdout=url, stderr="")


def test_resolve_repository_slug_parses_ssh_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    """An SSH origin remote parses to owner/repository."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("git@github.com:acme/widgets.git\n"),
    )
    assert _shared.resolve_repository_slug() == "acme/widgets"


def test_resolve_repository_slug_rejects_non_github_remote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-GitHub remote fails closed as a protocol failure."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("https://gitlab.com/acme/widgets.git"),
    )
    with pytest.raises(SystemExit) as excinfo:
        _shared.resolve_repository_slug()
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_run_git_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful git command returns its stdout."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: completed_remote("daily\n"),
    )
    assert _shared.run_git(arguments=["branch", "--show-current"]) == "daily\n"


def test_run_git_fails_closed_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing git command is a protocol failure, never a traceback."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="fatal: nope"
        ),
    )
    with pytest.raises(SystemExit) as excinfo:
        _shared.run_git(arguments=["status"])
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_github_headers_carry_bearer_and_accept() -> None:
    header_value = "test-token"
    """The shared headers carry the bearer token and the v3 media type."""
    headers = _shared.github_headers(token=header_value)
    assert headers["Authorization"] == "Bearer test-token"
    assert headers["Accept"] == "application/vnd.github.v3+json"
