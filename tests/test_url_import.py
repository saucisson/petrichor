"""Unit tests of the URL-import policy: allowlist, SSRF rejection, quota (issue #165, ADR-0008)."""

import socket
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from petrichor import url_import

if TYPE_CHECKING:
    import ipaddress
from petrichor.url_import import (
    DEFAULT_CONFIG_PATH,
    DownloadFailedError,
    ImportUrl,
    PrivateNetworkError,
    QuotaExceededError,
    UrlImportConfig,
    UrlNotAllowedError,
    download,
    load_url_import_config,
    validate_url,
)

CONFIG = UrlImportConfig()
PUBLIC_ADDRESS = "93.184.216.34"


def fake_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every host resolve to one public address."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_ADDRESS, 443))],
    )


class TestValidateUrl:
    """Scheme and domain allowlist."""

    def test_allowlisted_domain_passes(self) -> None:
        validate_url(url=ImportUrl("https://mcc.lip6.fr/2026/x.tgz"), config=CONFIG)

    def test_subdomain_of_allowlisted_domain_passes(self) -> None:
        validate_url(
            url=ImportUrl("https://raw.githubusercontent.com/x/y/main/m.pnml"), config=CONFIG
        )

    def test_http_scheme_is_refused(self) -> None:
        with pytest.raises(UrlNotAllowedError):
            validate_url(url=ImportUrl("http://mcc.lip6.fr/x"), config=CONFIG)

    def test_off_allowlist_domain_is_refused(self) -> None:
        with pytest.raises(UrlNotAllowedError):
            validate_url(url=ImportUrl("https://evil.example.com/x"), config=CONFIG)

    def test_lookalike_domain_is_refused(self) -> None:
        with pytest.raises(UrlNotAllowedError):
            validate_url(url=ImportUrl("https://mcc.lip6.fr.evil.com/x"), config=CONFIG)


class TestSsrfRejection:
    """Private/local/reserved resolutions are refused (ADR-0008)."""

    @pytest.mark.parametrize(
        "address",
        ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.0.1", "::1", "0.0.0.0"],  # noqa: S104 - test literals, not a bind
    )
    def test_non_public_address_is_refused(
        self, monkeypatch: pytest.MonkeyPatch, address: str
    ) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
        )
        with pytest.raises(PrivateNetworkError):
            url_import._resolve_public_address(host="mcc.lip6.fr")

    def test_unresolvable_host_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
            raise OSError("no DNS")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(PrivateNetworkError):
            url_import._resolve_public_address(host="mcc.lip6.fr")

    @pytest.mark.property
    @given(st.ip_addresses())
    def test_any_non_public_resolved_address_is_refused(
        self, address: ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> None:
        """Property tier (ADR-0012): the SSRF gate holds for every address, not just samples."""
        with mock.patch.object(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (str(address), 443))],
        ):
            is_public = not (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_multicast
                or address.is_reserved
                or address.is_unspecified
            )
            if is_public:
                assert url_import._resolve_public_address(host="mcc.lip6.fr") == str(address)
            else:
                with pytest.raises(PrivateNetworkError):
                    url_import._resolve_public_address(host="mcc.lip6.fr")


class _FakeResponse:
    def __init__(self, *, status: int, chunks: list[bytes], location: str | None = None) -> None:
        self.status = status
        self._chunks = chunks
        self._location = location

    def read(self, size: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def getheader(self, name: str) -> str | None:
        return self._location if name.lower() == "location" else None


class _FakeSocket:
    def close(self) -> None:
        pass


class _FakeConnection:
    response: _FakeResponse

    def __init__(self, host: str, timeout: float) -> None:
        self.sock = None

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        pass

    def getresponse(self) -> _FakeResponse:
        return self.response


def _patch_transport(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    """Pin the transport: public DNS, dummy socket/TLS, one fake HTTPS response."""
    fake_public_dns(monkeypatch)
    monkeypatch.setattr(url_import.socket, "create_connection", lambda *a, **k: _FakeSocket())

    class _FakeContext:
        def wrap_socket(self, sock: object, server_hostname: str) -> object:
            return sock

    monkeypatch.setattr(url_import.ssl, "create_default_context", _FakeContext)
    connection = _FakeConnection("mcc.lip6.fr", 1)
    connection.response = response
    monkeypatch.setattr(url_import.http.client, "HTTPSConnection", lambda *a, **k: connection)


class TestDownload:
    """Quota, HTTP errors and redirect re-validation of the pinned download."""

    def test_quota_is_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_transport(monkeypatch, _FakeResponse(status=200, chunks=[b"x" * 100]))
        with pytest.raises(QuotaExceededError):
            download(
                url=ImportUrl("https://mcc.lip6.fr/big.tgz"),
                config=UrlImportConfig(max_bytes=10),
            )

    def test_http_error_is_a_typed_download_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_transport(monkeypatch, _FakeResponse(status=404, chunks=[b""]))
        with pytest.raises(DownloadFailedError):
            download(url=ImportUrl("https://mcc.lip6.fr/x"), config=CONFIG)

    def test_successful_download_returns_the_body(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_transport(monkeypatch, _FakeResponse(status=200, chunks=[b"hello", b""]))
        assert download(url=ImportUrl("https://mcc.lip6.fr/x"), config=CONFIG) == b"hello"

    def test_redirect_to_a_non_allowlisted_domain_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_public_dns(monkeypatch)
        monkeypatch.setattr(
            url_import,
            "_fetch_once",
            lambda *, url, config: (b"", "https://evil.example.com/x"),
        )
        with pytest.raises(UrlNotAllowedError):
            download(url=ImportUrl("https://mcc.lip6.fr/x"), config=CONFIG)

    def test_redirect_loop_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_public_dns(monkeypatch)
        monkeypatch.setattr(
            url_import,
            "_fetch_once",
            lambda *, url, config: (b"", "https://mcc.lip6.fr/again"),
        )
        with pytest.raises(DownloadFailedError):
            download(url=ImportUrl("https://mcc.lip6.fr/x"), config=CONFIG)


def test_default_config_file_loads() -> None:
    config = load_url_import_config(path=DEFAULT_CONFIG_PATH)
    assert "mcc.lip6.fr" in config.allowed_domains
    assert config.max_bytes > 0
