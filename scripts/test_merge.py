"""Tests for the merge-method negotiation in merge.py (ADR-0009, template d6379b4)."""

import httpx
import pytest

from scripts import _shared, merge


def client_returning(payload: object, status_code: int = 200) -> httpx.Client:
    """Build an httpx client whose every request returns the given JSON payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_resolve_merge_method_prefers_squash() -> None:
    """Squash is chosen when the repository allows it."""
    client = client_returning({"allow_squash_merge": True, "allow_rebase_merge": True})
    assert merge.resolve_merge_method(http_client=client, repository_slug="o/r") == "squash"


def test_resolve_merge_method_falls_back_to_rebase() -> None:
    """Rebase is the fallback when squash is disabled (our repository)."""
    client = client_returning({"allow_squash_merge": False, "allow_rebase_merge": True})
    assert merge.resolve_merge_method(http_client=client, repository_slug="o/r") == "rebase"


def test_resolve_merge_method_refuses_when_neither_allowed() -> None:
    """Neither squash nor rebase allowed: protocol failure, human decision."""
    client = client_returning({"allow_squash_merge": False, "allow_rebase_merge": False})
    with pytest.raises(SystemExit) as excinfo:
        merge.resolve_merge_method(http_client=client, repository_slug="o/r")
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE


def test_resolve_merge_method_fails_closed_on_api_error() -> None:
    """A non-2xx settings response is a protocol failure, never a guess."""
    client = client_returning({}, status_code=500)
    with pytest.raises(SystemExit) as excinfo:
        merge.resolve_merge_method(http_client=client, repository_slug="o/r")
    assert excinfo.value.code == _shared.EXIT_CODE_PROTOCOL_FAILURE
