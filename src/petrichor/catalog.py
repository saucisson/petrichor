"""Domain port ``ModelCatalog`` and the pure catalog index (spec §21.1, issue #164).

The port is the seam of the onion (ADR-0002): no business code depends on a
concrete source. The index search (filter, facets, stable sort, pagination)
is pure and unit-tested; the adapters live in ``petrichor.catalog_sources``.
"""

import re
from typing import Literal, Protocol

from pydantic import BaseModel, Field

type FormalismTag = Literal["PT", "COL"]
"""The formalism facet of a catalog instance (PT net / colored net)."""

type SizeBucket = Literal["small", "medium", "large", "huge"]
"""Derived size facet of a compressed archive (annexe A §5)."""

SIZE_BUCKET_BOUNDS: tuple[tuple[int, SizeBucket], ...] = (
    (1 << 20, "small"),
    (10 << 20, "medium"),
    (100 << 20, "large"),
)
"""Compressed-size bounds per bucket; anything above is ``huge``."""

INSTANCE_NAME_PATTERN = re.compile(
    r"^(?P<family>[A-Za-z0-9]+)-(?P<formalism>PT|COL)-(?P<params>.+)$"
)
"""MCC instance naming: ``<Famille>-<PT|COL>-<Paramètres|none>`` (annexe A §5)."""


class CatalogError(Exception):
    """Base class of the typed catalog errors."""


class CatalogUnavailableError(CatalogError):
    """Every configured source failed to load: explicit degradation, never a crash."""


class UnknownModelError(CatalogError):
    """The requested name is not in the catalog index."""


class SourceUnavailableError(CatalogError):
    """The source holding the requested instance cannot be reached."""


class QuotaExceededError(CatalogError):
    """The instance exceeds the configured compressed-size quota."""


class InvalidCatalogPnmlError(CatalogError):
    """The PNML extracted from the source is invalid."""


class InstanceName(BaseModel):
    """The parsed parts of an MCC instance name."""

    family: str
    formalism: FormalismTag
    params: str


def parse_instance_name(*, name: str) -> InstanceName | None:
    """Parse ``<Famille>-<PT|COL>-<params>``; None when the name does not match."""
    match = INSTANCE_NAME_PATTERN.fullmatch(name)
    if match is None:
        return None
    formalism: FormalismTag = "PT" if match.group("formalism") == "PT" else "COL"
    return InstanceName(
        family=match.group("family"),
        formalism=formalism,
        params=match.group("params"),
    )


def pt_twin_name(*, name: str) -> str | None:
    """The ``-PT-`` twin of a ``-COL-`` instance name, when the naming allows it."""
    parsed = parse_instance_name(name=name)
    if parsed is None or parsed.formalism != "COL":
        return None
    return f"{parsed.family}-PT-{parsed.params}"


def size_bucket_of(*, compressed_bytes: int | None) -> SizeBucket | None:
    """Derive the size bucket of a compressed archive; None when unknown."""
    if compressed_bytes is None:
        return None
    for bound, bucket in SIZE_BUCKET_BOUNDS:
        if compressed_bytes < bound:
            return bucket
    return "huge"


class CatalogEntry(BaseModel):
    """One catalog entry: an instance (or a family, for edition sources)."""

    name: str
    family: str
    formalism: FormalismTag | None
    edition: str | None
    source: str
    size_bucket: SizeBucket | None
    url: str
    sha256: str | None = None


class CatalogFacets(BaseModel):
    """The facet counts of a search result (family, formalism, edition, source, size)."""

    family: dict[str, int] = Field(default_factory=dict)
    formalism: dict[str, int] = Field(default_factory=dict)
    edition: dict[str, int] = Field(default_factory=dict)
    source: dict[str, int] = Field(default_factory=dict)
    size: dict[str, int] = Field(default_factory=dict)


class CatalogPage(BaseModel):
    """One paginated page of catalog entries with its facet counts (ADR-0016)."""

    items: tuple[CatalogEntry, ...]
    total: int
    limit: int
    offset: int
    facets: CatalogFacets


type CatalogSort = Literal["name", "family", "edition", "size"]


CATALOG_SORTS: tuple[CatalogSort, ...] = ("name", "family", "edition", "size")


def parse_sort(*, value: str) -> CatalogSort | None:
    """Narrow a raw sort parameter to the closed set, or None."""
    for sort in CATALOG_SORTS:
        if value == sort:
            return sort
    return None


class CatalogQuery(BaseModel):
    """A listing query: free-text search on name/family, facet filters, sort, pagination."""

    search: str = ""
    facets: dict[str, str] = Field(default_factory=dict)
    sort: CatalogSort = "name"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class CatalogFetch(BaseModel):
    """The PNML of one instance fetched from its source, with provenance."""

    name: str
    url: str
    sha256: str
    pnml_text: str
    entry: CatalogEntry


class ModelCatalog(Protocol):
    """The domain port behind which the source adapters live (ADR-0002)."""

    @property
    def available(self) -> bool:
        """False when every source failed: callers degrade explicitly."""
        ...

    def search(self, *, query: CatalogQuery) -> CatalogPage:
        """Search the index; raises CatalogUnavailableError when unavailable."""
        ...

    def fetch(self, *, name: str) -> CatalogFetch:
        """Fetch the PNML of one instance by name (typed errors, never a crash)."""
        ...


_SIZE_BUCKET_ORDER: dict[SizeBucket, int] = {"small": 0, "medium": 1, "large": 2, "huge": 3}


def _size_sort_key(entry: CatalogEntry) -> tuple[int, str]:
    bucket = entry.size_bucket
    order = _SIZE_BUCKET_ORDER[bucket] if bucket else len(_SIZE_BUCKET_ORDER)
    return (order, entry.name)


_SORT_KEYS = {
    "name": lambda entry: entry.name,
    "family": lambda entry: (entry.family, entry.name),
    "edition": lambda entry: (entry.edition or "", entry.name),
    "size": _size_sort_key,
}
"""The stable sort keys of the listing (ties broken by name)."""


class CatalogIndex:
    """The pure in-memory index: filter, facet counts, stable sort, pagination."""

    def __init__(self, *, entries: list[CatalogEntry]) -> None:
        """Index the entries; duplicates by (name, source) are collapsed."""
        seen: set[tuple[str, str]] = set()
        unique: list[CatalogEntry] = []
        for entry in entries:
            key = (entry.name, entry.source)
            if key not in seen:
                seen.add(key)
                unique.append(entry)
        self._entries = unique

    def find(self, *, name: str) -> CatalogEntry | None:
        """The exact-name entry, or None."""
        for entry in self._entries:
            if entry.name == name:
                return entry
        return None

    def _matches(self, *, entry: CatalogEntry, query: CatalogQuery) -> bool:
        if query.search:
            needle = query.search.lower()
            if needle not in entry.name.lower() and needle not in entry.family.lower():
                return False
        for facet, value in query.facets.items():
            actual = getattr(entry, facet, None)
            if facet == "size":
                actual = entry.size_bucket
            if actual != value:
                return False
        return True

    def search(self, *, query: CatalogQuery) -> CatalogPage:
        """Apply the query: filter, facet counts over the filtered set, sort, paginate."""
        filtered = [entry for entry in self._entries if self._matches(entry=entry, query=query)]
        facets = CatalogFacets()
        for entry in filtered:
            facets.family[entry.family] = facets.family.get(entry.family, 0) + 1
            if entry.formalism is not None:
                facets.formalism[entry.formalism] = facets.formalism.get(entry.formalism, 0) + 1
            if entry.edition is not None:
                facets.edition[entry.edition] = facets.edition.get(entry.edition, 0) + 1
            facets.source[entry.source] = facets.source.get(entry.source, 0) + 1
            if entry.size_bucket is not None:
                facets.size[entry.size_bucket] = facets.size.get(entry.size_bucket, 0) + 1
        ordered = sorted(filtered, key=_SORT_KEYS[query.sort])
        page = ordered[query.offset : query.offset + query.limit]
        return CatalogPage(
            items=tuple(page),
            total=len(filtered),
            limit=query.limit,
            offset=query.offset,
            facets=facets,
        )
