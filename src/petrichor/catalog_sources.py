"""Infrastructure adapters of the ``ModelCatalog`` port (spec §21.1, issue #164).

Three source kinds, all driven by the versioned configuration
(``catalog_sources.json``) - adding a source is a config change, never a
code change (annexe A §6):

- ``metadata``     - the official pnrepository (``models/all/metadata.json``
  index, per-model archive URLs);
- ``edition``      - the official MCC site per edition (``models.php`` family
  archives; fetch by instance through its family archive);
- ``static-index`` - the GitHub Pages mirrors (``index.html`` tree, one
  ``.tgz`` per instance).

HTTP goes through the stdlib ``urllib`` (no new dependency) with an explicit
User-Agent, a timeout and a size quota. Downloads happen in a background
thread at startup with a bounded disk cache (directory, quota, TTL, sha256);
any failure degrades explicitly (``CatalogUnavailableError``), never a crash.
"""

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from petrichor.archive_ingest import (
    ArchiveError,
    PnmlCandidate,
    extract_pnml_candidates,
)
from petrichor.catalog import (
    CatalogEntry,
    CatalogFetch,
    CatalogIndex,
    CatalogPage,
    CatalogQuery,
    CatalogUnavailableError,
    InvalidCatalogPnmlError,
    QuotaExceededError,
    SourceUnavailableError,
    UnknownModelError,
    parse_instance_name,
    size_bucket_of,
)

logger = structlog.get_logger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent / "catalog_sources.json"
"""The versioned catalog configuration shipped with the code."""

DEFAULT_USER_AGENT = "petrichor-catalog/1.0 (https://petrichor.linard.fr)"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_ARCHIVE_BYTES = 100 << 20  # 100 Mo compressed quota (annexe A §1)
DEFAULT_CACHE_QUOTA_BYTES = 512 << 20
DEFAULT_CACHE_TTL_SECONDS = 24 * 3600
HTTP_CHUNK_BYTES = 64 * 1024

type SourceKind = Literal["metadata", "edition", "static-index"]


class SourceConfig(BaseModel):
    """One configured source: a kind, a name, a base URL, an optional edition."""

    kind: SourceKind
    name: str
    base_url: str
    edition: str | None = None


class CacheConfig(BaseModel):
    """The bounded disk cache of downloaded archives."""

    directory: str = ".petrichor/catalog-cache"
    quota_bytes: int = Field(default=DEFAULT_CACHE_QUOTA_BYTES, gt=0)
    ttl_seconds: int = Field(default=DEFAULT_CACHE_TTL_SECONDS, gt=0)


class CatalogConfig(BaseModel):
    """The versioned catalog configuration."""

    version: int = 1
    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT_SECONDS, gt=0)
    max_archive_bytes: int = Field(default=DEFAULT_MAX_ARCHIVE_BYTES, gt=0)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    sources: tuple[SourceConfig, ...]


def load_catalog_config(*, path: Path) -> CatalogConfig:
    """Load and validate the versioned catalog configuration."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return CatalogConfig.model_validate(payload)


def http_get_bytes(*, url: str, timeout_seconds: int, max_bytes: int, user_agent: str) -> bytes:
    """GET a URL with an explicit User-Agent, a timeout and a size quota."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})  # noqa: S310 - URLs come from the versioned config / catalog index, https-only
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - see above
            chunks: list[bytes] = []
            received = 0
            while True:
                chunk = response.read(HTTP_CHUNK_BYTES)
                if not chunk:
                    break
                received += len(chunk)
                if received > max_bytes:
                    raise QuotaExceededError(
                        f"Download of {url} exceeds the quota of {max_bytes} bytes."
                    )
                chunks.append(chunk)
    except QuotaExceededError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SourceUnavailableError(f"GET {url} failed: {error}") from error
    return b"".join(chunks)


def http_get_text(*, url: str, timeout_seconds: int, max_bytes: int, user_agent: str) -> str:
    """GET a text document (index) with the same protections."""
    return http_get_bytes(
        url=url, timeout_seconds=timeout_seconds, max_bytes=max_bytes, user_agent=user_agent
    ).decode("utf-8")


_FAMILY_ARCHIVE_PATTERN = re.compile(
    r'href="(?:\./)?archives/(?P<family>[A-Za-z0-9]+)-pnml\.tar\.gz"'
)
_STATIC_INDEX_PATTERN = re.compile(r'href="\./INPUTS/(?P<name>[A-Za-z0-9.\-]+\.tgz)"')


MAX_INDEX_BYTES = 4 << 20
"""Cap on the size of a fetched index page (models.php / index.html / metadata.json)."""


class _BaseSource:
    """Common behavior of the source adapters: config + HTTP helpers."""

    def __init__(self, *, config: SourceConfig, catalog_config: CatalogConfig) -> None:
        """Bind the source to its configuration."""
        self.config = config
        self.catalog_config = catalog_config

    def _get_text(self, *, url: str) -> str:
        return http_get_text(
            url=url,
            timeout_seconds=self.catalog_config.timeout_seconds,
            max_bytes=MAX_INDEX_BYTES,
            user_agent=self.catalog_config.user_agent,
        )

    def _get_archive(self, *, url: str) -> bytes:
        return http_get_bytes(
            url=url,
            timeout_seconds=self.catalog_config.timeout_seconds,
            max_bytes=self.catalog_config.max_archive_bytes,
            user_agent=self.catalog_config.user_agent,
        )

    def index_entries(self) -> list[CatalogEntry]:
        """Load the source index; raise SourceUnavailableError on failure."""
        raise NotImplementedError

    def fetch_archive(self, *, entry: CatalogEntry) -> bytes:
        """Download the archive holding the entry's PNML."""
        raise NotImplementedError


class MetadataSource(_BaseSource):
    """The official pnrepository: ``metadata`` kind (annexe A §2)."""

    def index_entries(self) -> list[CatalogEntry]:
        """Parse ``models/all/metadata.json`` tolerantly (fields unverifiable offline)."""
        url = self.config.base_url.rstrip("/") + "/models/all/metadata.json"
        document = json.loads(self._get_text(url=url))
        raw_entries = document if isinstance(document, list) else document.get("models", [])
        entries: list[CatalogEntry] = []
        for raw in raw_entries:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name") or raw.get("id") or raw.get("title")
            links = raw.get("links") if isinstance(raw.get("links"), list) else []
            archive_url = raw.get("href") or raw.get("url")
            if links and isinstance(links[0], dict):
                archive_url = links[0].get("href", archive_url)
            if not isinstance(name, str) or not isinstance(archive_url, str):
                continue
            parsed = parse_instance_name(name=name)
            family = raw.get("family") or (parsed.family if parsed else name)
            size = raw.get("size") or raw.get("compressed_size")
            entries.append(
                CatalogEntry(
                    name=name,
                    family=str(family),
                    formalism=parsed.formalism if parsed else None,
                    edition=self.config.edition or _string_or_none(raw.get("edition")),
                    source=self.config.name,
                    size_bucket=size_bucket_of(
                        compressed_bytes=int(size) if isinstance(size, int) else None
                    ),
                    url=archive_url,
                )
            )
        return entries

    def fetch_archive(self, *, entry: CatalogEntry) -> bytes:
        """Download the per-model archive listed in the metadata."""
        return self._get_archive(url=entry.url)


class EditionSource(_BaseSource):
    """The official MCC site per edition: ``edition`` kind (annexe A §1).

    ``models.php`` is human-oriented: the index lists the per-family PNML
    archives; instance-level listing comes from the other source kinds, and
    fetch-by-name goes through the instance's family archive.
    """

    def index_entries(self) -> list[CatalogEntry]:
        """Parse the family archives linked from ``models.php``."""
        url = self.config.base_url.rstrip("/") + "/models.php"
        page = self._get_text(url=url)
        entries: list[CatalogEntry] = []
        for match in _FAMILY_ARCHIVE_PATTERN.finditer(page):
            family = match.group("family")
            entries.append(
                CatalogEntry(
                    name=family,
                    family=family,
                    formalism=None,
                    edition=self.config.edition,
                    source=self.config.name,
                    size_bucket=None,
                    url=f"{self.config.base_url.rstrip('/')}/archives/{family}-pnml.tar.gz",
                )
            )
        return entries

    def fetch_archive(self, *, entry: CatalogEntry) -> bytes:
        """Download the family archive of the requested instance."""
        parsed = parse_instance_name(name=entry.name)
        family = parsed.family if parsed else entry.family
        url = f"{self.config.base_url.rstrip('/')}/archives/{family}-pnml.tar.gz"
        return self._get_archive(url=url)


class StaticIndexSource(_BaseSource):
    """A GitHub Pages mirror: ``static-index`` kind (annexe A §3)."""

    def index_entries(self) -> list[CatalogEntry]:
        """Parse the ``index.html`` directory tree for per-instance ``.tgz``."""
        url = self.config.base_url.rstrip("/") + "/index.html"
        page = self._get_text(url=url)
        entries: list[CatalogEntry] = []
        for match in _STATIC_INDEX_PATTERN.finditer(page):
            file_name = match.group("name")
            name = file_name[: -len(".tgz")]
            parsed = parse_instance_name(name=name)
            entries.append(
                CatalogEntry(
                    name=name,
                    family=parsed.family if parsed else name,
                    formalism=parsed.formalism if parsed else None,
                    edition=self.config.edition,
                    source=self.config.name,
                    size_bucket=None,
                    url=f"{self.config.base_url.rstrip('/')}/INPUTS/{file_name}",
                )
            )
        return entries

    def fetch_archive(self, *, entry: CatalogEntry) -> bytes:
        """Download the per-instance ``.tgz``."""
        return self._get_archive(url=entry.url)


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


class CatalogService:
    """The ``ModelCatalog`` implementation: adapters + index + bounded disk cache."""

    def __init__(self, *, config: CatalogConfig, cache_directory: Path) -> None:
        """Build the adapters from the config; the index starts empty and unavailable."""
        self._config = config
        self._cache_directory = cache_directory
        self._sources: list[_BaseSource] = []
        for source_config in config.sources:
            if source_config.kind == "metadata":
                self._sources.append(MetadataSource(config=source_config, catalog_config=config))
            elif source_config.kind == "edition":
                self._sources.append(EditionSource(config=source_config, catalog_config=config))
            else:
                self._sources.append(StaticIndexSource(config=source_config, catalog_config=config))
        self._index = CatalogIndex(entries=[])
        self._available = False
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """True once at least one source index loaded."""
        return self._available

    def start_background_refresh(self) -> threading.Thread:
        """Refresh the index in a daemon thread at startup; never blocks a request."""
        thread = threading.Thread(target=self.refresh, name="catalog-refresh", daemon=True)
        thread.start()
        return thread

    def refresh(self) -> None:
        """Load every source index; any failure is logged and degrades explicitly."""
        entries: list[CatalogEntry] = []
        loaded = 0
        for source in self._sources:
            try:
                source_entries = source.index_entries()
            except Exception as error:  # noqa: BLE001 - a source must never kill the refresh thread
                logger.warning(
                    "catalog_source_unavailable", source=source.config.name, error=str(error)
                )
                continue
            loaded += 1
            entries.extend(source_entries)
            logger.debug(
                "catalog_source_loaded", source=source.config.name, entries=len(source_entries)
            )
        with self._lock:
            self._index = CatalogIndex(entries=entries)
            self._available = loaded > 0
        logger.info(
            "catalog_index_refreshed",
            sources_loaded=loaded,
            sources_total=len(self._sources),
            entries=len(entries),
            available=self._available,
        )

    def search(self, *, query: CatalogQuery) -> CatalogPage:
        """Search the index; CatalogUnavailableError when every source failed."""
        if not self._available:
            raise CatalogUnavailableError("Catalogue indisponible : aucune source joignable.")
        with self._lock:
            return self._index.search(query=query)

    def _cached_archive(self, *, url: str) -> bytes:
        """Return the archive bytes, from the bounded disk cache when fresh."""
        self._cache_directory.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cached = self._cache_directory / f"{key}.bin"
        now = time.time()
        if cached.exists() and now - cached.stat().st_mtime < self._config.cache.ttl_seconds:
            logger.debug("catalog_cache_hit", url=url)
            return cached.read_bytes()
        data = http_get_bytes(
            url=url,
            timeout_seconds=self._config.timeout_seconds,
            max_bytes=self._config.max_archive_bytes,
            user_agent=self._config.user_agent,
        )
        cached.write_bytes(data)
        self._enforce_quota()
        return data

    def _enforce_quota(self) -> None:
        """Evict the oldest cache files until the quota is respected."""
        files = sorted(
            (path for path in self._cache_directory.glob("*.bin")),
            key=lambda path: path.stat().st_mtime,
        )
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self._config.cache.quota_bytes:
                break
            total -= path.stat().st_size
            path.unlink()
            logger.debug("catalog_cache_evicted", path=path.name)

    def fetch(self, *, name: str) -> CatalogFetch:
        """Fetch one instance by name: typed errors, bounded cache, sha256 provenance."""
        if not self._available:
            raise CatalogUnavailableError("Catalogue indisponible : aucune source joignable.")
        with self._lock:
            entry = self._index.find(name=name)
        if entry is None:
            raise UnknownModelError(f"Instance inconnue du catalogue : {name!r}.")
        try:
            archive = self._cached_archive(url=entry.url)
        except QuotaExceededError as error:
            raise QuotaExceededError(str(error)) from error
        except SourceUnavailableError as error:
            raise SourceUnavailableError(str(error)) from error
        sha256 = f"sha256:{hashlib.sha256(archive).hexdigest()}"
        try:
            candidates = extract_pnml_candidates(data=archive, content_type="application/gzip")
        except ArchiveError as error:
            raise InvalidCatalogPnmlError(f"Archive de {name!r} illisible : {error}") from error
        candidate = _pick_instance_candidate(candidates=candidates, name=name)
        if candidate is None:
            raise InvalidCatalogPnmlError(f"Aucun model.pnml pour {name!r} dans l'archive.")
        logger.info(
            "catalog_instance_fetched",
            name=name,
            source=entry.source,
            sha256=sha256,
            size_bytes=candidate.size_bytes,
        )
        return CatalogFetch(
            name=name, url=entry.url, sha256=sha256, pnml_text=candidate.content, entry=entry
        )


def _pick_instance_candidate(*, candidates: list[PnmlCandidate], name: str) -> PnmlCandidate | None:
    """Pick the candidate of the requested instance (``<Instance>/model.pnml`` first)."""
    for candidate in candidates:
        if candidate.name == f"{name}/model.pnml":
            return candidate
    for candidate in candidates:
        if candidate.name.endswith("model.pnml") and candidate.name.startswith(name):
            return candidate
    return candidates[0] if len(candidates) == 1 else None
