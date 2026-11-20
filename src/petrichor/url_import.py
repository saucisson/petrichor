"""URL import with an https-only domain allowlist and SSRF rejection (spec §21.3, ADR-0008).

The allowlist lives in the versioned ``url_import.json`` next to this module.
Every URL is validated before any connection: https scheme, allowlisted
domain, and every resolved address must be public (no private, loopback,
link-local, multicast, reserved or unspecified network). Redirects are
re-validated one hop at a time.
"""

import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path(__file__).parent / "url_import.json"

DEFAULT_ALLOWED_DOMAINS: tuple[str, ...] = (
    "pnrepository.lip6.fr",
    "mcc.lip6.fr",
    "yanntm.github.io",
    "raw.githubusercontent.com",
)
"""The default domain allowlist of the versioned url_import.json (spec §21.3)."""

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_BYTES = 100 << 20
"""Default download quota: 100 Mo, same order as the catalog archive quota."""

HTTP_CHUNK_BYTES = 64 << 10


class UrlImportError(Exception):
    """Base class of the typed URL-import errors."""


class UrlNotAllowedError(UrlImportError):
    """The URL is not https or its domain is not allowlisted."""


class PrivateNetworkError(UrlImportError):
    """The URL resolves to a private/local/reserved address (SSRF, ADR-0008)."""


class DownloadFailedError(UrlImportError):
    """The download failed (network, timeout, HTTP error)."""


class QuotaExceededError(UrlImportError):
    """The download exceeded the configured size quota."""


class UrlImportConfig(BaseModel):
    """The versioned URL-import configuration."""

    allowed_domains: tuple[str, ...] = DEFAULT_ALLOWED_DOMAINS
    timeout_seconds: float = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    max_bytes: int = Field(default=DEFAULT_MAX_BYTES, gt=0)
    user_agent: str = "petrichor-url-import/1.0"


def load_url_import_config(*, path: Path) -> UrlImportConfig:
    """Load and validate the versioned URL-import configuration."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return UrlImportConfig.model_validate(payload)


def _host_is_allowlisted(*, host: str, config: UrlImportConfig) -> bool:
    """Exact domain match or a subdomain of an allowlisted domain."""
    normalized = host.lower().rstrip(".")
    return any(
        normalized == domain or normalized.endswith(f".{domain}")
        for domain in config.allowed_domains
    )


def _resolved_addresses_are_public(*, host: str) -> bool:
    """Every address the host resolves to must be public (SSRF gate)."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise PrivateNetworkError(f"Cannot resolve {host!r}: {error}") from error
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return False
    return True


def validate_url(*, url: str, config: UrlImportConfig) -> None:
    """Validate scheme, allowlist and resolved addresses; raise typed errors."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise UrlNotAllowedError(f"Only https URLs are accepted, got scheme {parsed.scheme!r}.")
    host = parsed.hostname
    if host is None or not _host_is_allowlisted(host=host, config=config):
        raise UrlNotAllowedError(f"Domain of {url!r} is not in the allowlist.")
    if not _resolved_addresses_are_public(host=host):
        raise PrivateNetworkError(
            f"{host!r} resolves to a private or local address (SSRF refused)."
        )


class _RevalidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate every redirect target against the same policy."""

    def __init__(self, *, config: UrlImportConfig) -> None:
        self._config = config

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        validate_url(url=newurl, config=self._config)
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


def download(*, url: str, config: UrlImportConfig) -> bytes:
    """Validate then download a URL with timeout and size quota."""
    validate_url(url=url, config=config)
    request = urllib.request.Request(url, headers={"User-Agent": config.user_agent})  # noqa: S310 - URL validated above (https + allowlist + public IPs)
    opener = urllib.request.build_opener(_RevalidatingRedirectHandler(config=config))
    try:
        with opener.open(request, timeout=config.timeout_seconds) as response:
            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = response.read(HTTP_CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > config.max_bytes:
                    raise QuotaExceededError(
                        f"Download of {url} exceeds the quota of {config.max_bytes} bytes."
                    )
                chunks.append(chunk)
    except QuotaExceededError:
        raise
    except urllib.error.HTTPError as error:
        error.close()  # HTTPError is a response object: close it, else ResourceWarning
        raise DownloadFailedError(f"GET {url} failed: {error}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DownloadFailedError(f"GET {url} failed: {error}") from error
    return b"".join(chunks)
