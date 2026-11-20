"""Unit tests of the URL-import policy: allowlist, SSRF rejection, quota (issue #165, ADR-0008)."""

import io
import socket
import urllib.error
import urllib.request

import pytest

from petrichor.url_import import (
    DEFAULT_CONFIG_PATH,
    DownloadFailedError,
    PrivateNetworkError,
    QuotaExceededError,
    UrlImportConfig,
    UrlNotAllowedError,
    download,
    load_url_import_config,
    validate_url,
)

CONFIG = UrlImportConfig()


class TestValidateUrl:
    """Scheme and domain allowlist."""

    def test_allowlisted_domain_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )
        validate_url(url="https://mcc.lip6.fr/2026/x.tgz", config=CONFIG)

    def test_subdomain_of_allowlisted_domain_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )
        validate_url(url="https://raw.githubusercontent.com/x/y/main/m.pnml", config=CONFIG)

    def test_http_scheme_is_refused(self) -> None:
        with pytest.raises(UrlNotAllowedError):
            validate_url(url="http://mcc.lip6.fr/x", config=CONFIG)

    def test_off_allowlist_domain_is_refused(self) -> None:
        with pytest.raises(UrlNotAllowedError):
            validate_url(url="https://evil.example.com/x", config=CONFIG)

    def test_lookalike_domain_is_refused(self) -> None:
        with pytest.raises(UrlNotAllowedError):
            validate_url(url="https://mcc.lip6.fr.evil.com/x", config=CONFIG)


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
            validate_url(url="https://mcc.lip6.fr/x", config=CONFIG)

    def test_unresolvable_host_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_getaddrinfo(*args: object, **kwargs: object) -> list[tuple]:
            raise OSError("no DNS")

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        with pytest.raises(PrivateNetworkError):
            validate_url(url="https://mcc.lip6.fr/x", config=CONFIG)


class TestDownload:
    """Quota and failure modes of the download itself."""

    def test_quota_is_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeResponse(io.BytesIO):
            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *args: object) -> None:
                self.close()

        def fake_open(self: object, request: object, timeout: float) -> FakeResponse:
            return FakeResponse(b"x" * 100)

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )
        monkeypatch.setattr(
            urllib.request, "build_opener", lambda *a, **k: type("O", (), {"open": fake_open})()
        )
        with pytest.raises(QuotaExceededError):
            download(url="https://mcc.lip6.fr/big.tgz", config=UrlImportConfig(max_bytes=10))

    def test_http_error_is_a_typed_download_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
        )

        def fake_open(self: object, request: object, timeout: float) -> object:
            raise urllib.error.HTTPError(
                url="https://mcc.lip6.fr/x", code=404, msg="nf", hdrs=None, fp=io.BytesIO(b"")
            )

        monkeypatch.setattr(
            urllib.request, "build_opener", lambda *a, **k: type("O", (), {"open": fake_open})()
        )
        with pytest.raises(DownloadFailedError):
            download(url="https://mcc.lip6.fr/x", config=CONFIG)


def test_default_config_file_loads() -> None:
    config = load_url_import_config(path=DEFAULT_CONFIG_PATH)
    assert "mcc.lip6.fr" in config.allowed_domains
    assert config.max_bytes > 0
