"""URL import with an https-only domain allowlist and SSRF rejection (spec §21.3, ADR-0008).

The allowlist lives in the versioned ``url_import.json`` next to this module.
Every URL is validated before any connection: https scheme, allowlisted
domain, and every resolved address must be public (no private, loopback,
link-local, multicast, reserved or unspecified network). Redirects are
re-validated one hop at a time.
"""

import http.client
import ipaddress
import json
import socket
import ssl
import urllib.parse
from pathlib import Path
from typing import NewType

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


ImportUrl = NewType("ImportUrl", str)
"""A validated URL primitive (ADR-0006): no bare string at the boundary."""


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


HTTP_OK = 200
HTTP_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5
"""Maximum number of redirect hops, each re-validated and re-pinned."""


def _resolve_public_address(*, host: str) -> str:
    """Resolve the host and return one public address; refuse any non-public answer."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as error:
        raise PrivateNetworkError(f"Cannot resolve {host!r}: {error}") from error
    addresses = [str(info[4][0]) for info in infos]
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise PrivateNetworkError(
                f"{host!r} resolves to a private or local address ({raw}) - SSRF refused."
            )
    return addresses[0]


def validate_url(*, url: ImportUrl, config: UrlImportConfig) -> None:
    """Validate scheme and allowlist; raise typed errors."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https":
        raise UrlNotAllowedError(f"Only https URLs are accepted, got scheme {parsed.scheme!r}.")
    host = parsed.hostname
    if host is None or not _host_is_allowlisted(host=host, config=config):
        raise UrlNotAllowedError(f"Domain of {url!r} is not in the allowlist.")


def _fetch_once(*, url: str, config: UrlImportConfig) -> tuple[bytes, str | None]:
    """One pinned HTTPS GET: the connection goes to the validated address only.

    The socket is opened on the address returned by _resolve_public_address
    (validated in the same call), with the original host as SNI/Host, so a
    rebinding DNS answer cannot redirect the connection to a private network.
    Returns the body and the Location header when the server answers a redirect.
    """
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if host is None:
        raise UrlNotAllowedError(f"The URL {url!r} has no host.")
    address = _resolve_public_address(host=host)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    raw_socket = socket.create_connection((address, 443), timeout=config.timeout_seconds)
    context = ssl.create_default_context()
    tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
    try:
        connection = http.client.HTTPSConnection(host, timeout=config.timeout_seconds)
        connection.sock = tls_socket
        connection.request("GET", path, headers={"User-Agent": config.user_agent, "Host": host})
        response = connection.getresponse()
        if response.status in HTTP_REDIRECT_STATUSES:
            location = response.getheader("Location")
            response.read()
            return b"", location
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
        if response.status != HTTP_OK:
            raise DownloadFailedError(f"GET {url} failed: HTTP {response.status}.")
        return b"".join(chunks), None
    finally:
        tls_socket.close()


def download(*, url: ImportUrl, config: UrlImportConfig) -> bytes:
    """Validate then download a URL with timeout and size quota.

    Every redirect hop is re-validated (scheme, allowlist, public address)
    and re-pinned to a freshly validated address.
    """
    current = str(url)
    for _ in range(MAX_REDIRECTS):
        validate_url(url=ImportUrl(current), config=config)
        body, location = _fetch_once(url=current, config=config)
        if location is None:
            return body
        current = urllib.parse.urljoin(current, location)
    raise DownloadFailedError(f"Too many redirects for {url!r} (max {MAX_REDIRECTS}).")
