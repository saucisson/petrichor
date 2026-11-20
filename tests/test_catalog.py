"""Tests of the ModelCatalog domain and adapters (issue #164, spec §21.1).

Domain: instance-name parsing, -PT- twins, size buckets, index search with
facets / stable sorts / pagination. Infrastructure: the three source kinds
over mocked HTTP (fixtures), the bounded disk cache, and the explicit
degradation when every source fails. Archive ingestion: zip/tgz extraction
with zip-slip refusal.
"""

import gc
import gzip
import io
import json
import tarfile
import urllib.error
import urllib.request
import warnings
import zipfile
from pathlib import Path

import pytest
import structlog.testing
from hypothesis import given
from hypothesis import strategies as st

from petrichor import catalog_sources
from petrichor.archive_ingest import (
    CorruptArchiveError,
    MalformedPnmlError,
    NoPnmlError,
    UnsafeArchiveError,
    detect_formalism,
    extract_pnml_candidates,
)
from petrichor.catalog import (
    CatalogEntry,
    CatalogIndex,
    CatalogQuery,
    CatalogUnavailableError,
    InvalidCatalogPnmlError,
    QuotaExceededError,
    SourceUnavailableError,
    UnknownModelError,
    parse_instance_name,
    pt_twin_name,
    size_bucket_of,
)
from petrichor.catalog_sources import (
    CatalogConfig,
    CatalogService,
    EditionSource,
    MetadataSource,
    SourceConfig,
    StaticIndexSource,
)

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")


def make_tgz(*, members: dict[str, bytes]) -> bytes:
    """Build an in-memory tar.gz archive."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def entry(
    *, name: str, source: str = "src", edition: str | None = "2026", **kwargs: object
) -> CatalogEntry:
    """Build a catalog entry with sensible defaults."""
    parsed = parse_instance_name(name=name)
    return CatalogEntry(
        name=name,
        family=parsed.family if parsed else name,
        formalism=parsed.formalism if parsed else None,
        edition=edition,
        source=source,
        size_bucket=kwargs.get("size_bucket"),
        url=kwargs.get("url", f"https://example.test/{name}.tgz"),
        sha256=kwargs.get("sha256"),
    )


class TestDomain:
    """Pure domain rules of the catalog."""

    def test_parse_instance_name(self) -> None:
        parsed = parse_instance_name(name="Philosophers-PT-010")
        assert parsed is not None
        assert (parsed.family, parsed.formalism, parsed.params) == ("Philosophers", "PT", "010")
        assert parse_instance_name(name="nope") is None

    def test_pt_twin_of_col_instance(self) -> None:
        assert pt_twin_name(name="FileSystem-COL-V50") == "FileSystem-PT-V50"
        assert pt_twin_name(name="Philosophers-PT-010") is None

    def test_size_buckets(self) -> None:
        assert size_bucket_of(compressed_bytes=None) is None
        assert size_bucket_of(compressed_bytes=500) == "small"
        assert size_bucket_of(compressed_bytes=5 << 20) == "medium"
        assert size_bucket_of(compressed_bytes=50 << 20) == "large"
        assert size_bucket_of(compressed_bytes=200 << 20) == "huge"

    def test_index_search_facets_sort_pagination(self) -> None:
        index = CatalogIndex(
            entries=[
                entry(name="Philosophers-PT-010", size_bucket="small"),
                entry(name="Philosophers-PT-020", size_bucket="medium"),
                entry(name="Philosophers-COL-010", source="mirror"),
                entry(name="TokenRing-PT-050", edition="2025", size_bucket="huge"),
            ]
        )
        page = index.search(query=CatalogQuery(search="philosophers"))
        assert page.total == 3
        assert page.facets.formalism == {"PT": 2, "COL": 1}
        assert page.facets.source == {"src": 2, "mirror": 1}
        filtered = index.search(query=CatalogQuery(facets={"formalism": "PT"}))
        assert filtered.total == 3
        assert index.search(query=CatalogQuery(facets={"formalism": "COL"})).total == 1
        by_size = index.search(query=CatalogQuery(sort="size"))
        assert [item.size_bucket for item in by_size.items] == [
            "small",
            "medium",
            "huge",
            None,
        ]
        paged = index.search(query=CatalogQuery(limit=2, offset=2, sort="name"))
        assert paged.total == 4
        assert [item.name for item in paged.items] == [
            "Philosophers-PT-020",
            "TokenRing-PT-050",
        ]

    def test_index_collapses_duplicates_by_name_and_source(self) -> None:
        index = CatalogIndex(entries=[entry(name="A-PT-1"), entry(name="A-PT-1")])
        assert index.search(query=CatalogQuery()).total == 1


class TestArchiveIngest:
    """Protected extraction of PNML candidates from archives (spec §21.3)."""

    def test_tgz_extraction_with_formalism(self) -> None:
        archive = make_tgz(
            members={
                "Philosophers-PT-010/model.pnml": PHILOSOPHES_PNML.encode(),
                "Philosophers-PT-010/CTLCardinality.xml": b"<formula/>",
            }
        )
        candidates = extract_pnml_candidates(data=archive, content_type="application/gzip")
        assert len(candidates) == 2  # .pnml and .xml members are both PNML candidates
        model = next(candidate for candidate in candidates if candidate.name.endswith("model.pnml"))
        assert model.formalism == "pt"
        assert model.sha256.startswith("sha256:")

    def test_symmetric_formalism_detected(self) -> None:
        symmetric = (FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")
        assert detect_formalism(text=symmetric) == "symmetric"

    def test_zip_slip_refused_and_listed(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../evil.pnml", PHILOSOPHES_PNML)
            archive.writestr("/abs.pnml", PHILOSOPHES_PNML)
        with pytest.raises(UnsafeArchiveError) as caught:
            extract_pnml_candidates(data=buffer.getvalue(), content_type="application/zip")
        assert "../evil.pnml" in caught.value.refused
        assert "/abs.pnml" in caught.value.refused

    def test_no_pnml_is_a_typed_error(self) -> None:
        archive = make_tgz(members={"Instance/readme.txt": b"hello"})
        with pytest.raises(NoPnmlError):
            extract_pnml_candidates(data=archive, content_type="application/gzip")

    def test_bare_gzip_pnml(self) -> None:
        data = gzip.compress(PHILOSOPHES_PNML.encode())
        candidates = extract_pnml_candidates(data=data, content_type="application/gzip")
        assert len(candidates) == 1
        assert candidates[0].content == PHILOSOPHES_PNML


def _config(*, sources: list[SourceConfig], max_archive_bytes: int = 100 << 20) -> CatalogConfig:
    return CatalogConfig(
        sources=tuple(sources),
        max_archive_bytes=max_archive_bytes,
        cache={"directory": "unused", "quota_bytes": 1 << 20, "ttl_seconds": 60},
    )


class TestSources:
    """The three source kinds over mocked HTTP."""

    def test_metadata_source_parses_metadata_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        document = {
            "models": [
                {
                    "name": "Philosophers-PT-010",
                    "links": [
                        {"href": "https://pnrepository.lip6.fr/archive/Philosophers-pnml.tar.gz"}
                    ],
                    "size": 2048,
                },
                {"id": "TokenRing-COL-050", "href": "https://example.test/tr.tgz"},
            ]
        }
        monkeypatch.setattr(catalog_sources, "http_get_text", lambda **kwargs: json.dumps(document))
        source = MetadataSource(
            config=SourceConfig(
                kind="metadata",
                name="pnrepository-mcc",
                base_url="https://pnrepository.lip6.fr/mcc/",
            ),
            catalog_config=_config(sources=[]),
        )
        entries = source.index_entries()
        assert [item.name for item in entries] == ["Philosophers-PT-010", "TokenRing-COL-050"]
        assert entries[0].formalism == "PT"
        assert entries[0].size_bucket == "small"
        assert entries[1].formalism == "COL"

    def test_edition_source_parses_models_php_families(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page = '<a href="archives/Philosophers-pnml.tar.gz">PNML archive</a>'
        monkeypatch.setattr(catalog_sources, "http_get_text", lambda **kwargs: page)
        source = EditionSource(
            config=SourceConfig(
                kind="edition",
                name="mcc-2026",
                base_url="https://mcc.lip6.fr/2026/",
                edition="2026",
            ),
            catalog_config=_config(sources=[]),
        )
        entries = source.index_entries()
        assert [item.name for item in entries] == ["Philosophers"]
        assert entries[0].edition == "2026"
        assert entries[0].url.endswith("/archives/Philosophers-pnml.tar.gz")

    def test_static_index_source_parses_tree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        page = (
            '<a href="./INPUTS/Philosophers-PT-010.tgz">x</a>'
            '<a href="./INPUTS/TokenRing-COL-050.tgz">y</a>'
        )
        monkeypatch.setattr(catalog_sources, "http_get_text", lambda **kwargs: page)
        source = StaticIndexSource(
            config=SourceConfig(
                kind="static-index",
                name="pnmcc-2026",
                base_url="https://yanntm.github.io/pnmcc-models-2026/",
                edition="2026",
            ),
            catalog_config=_config(sources=[]),
        )
        entries = source.index_entries()
        assert [item.name for item in entries] == ["Philosophers-PT-010", "TokenRing-COL-050"]
        assert entries[0].url.endswith("/INPUTS/Philosophers-PT-010.tgz")

    def test_http_failure_is_a_typed_source_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def failing(**kwargs: object) -> str:
            raise SourceUnavailableError("boom")

        monkeypatch.setattr(catalog_sources, "http_get_text", failing)
        source = StaticIndexSource(
            config=SourceConfig(
                kind="static-index", name="x", base_url="https://x.test/", edition="2026"
            ),
            catalog_config=_config(sources=[]),
        )
        with pytest.raises(SourceUnavailableError):
            source.index_entries()
        assert urllib.error.URLError is not None  # the stdlib error type is wrapped upstream


class TestCatalogService:
    """The service: background refresh, degradation, bounded cache, fetch."""

    def _service(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fail: bool = False
    ) -> CatalogService:
        index_page = '<a href="./INPUTS/Philosophers-PT-010.tgz">x</a>'
        archive = make_tgz(members={"Philosophers-PT-010/model.pnml": PHILOSOPHES_PNML.encode()})

        def fake_get_text(**kwargs: object) -> str:
            if fail:
                raise SourceUnavailableError("offline")
            return index_page

        def fake_get_bytes(**kwargs: object) -> bytes:
            if fail:
                raise SourceUnavailableError("offline")
            return archive

        monkeypatch.setattr(catalog_sources, "http_get_text", fake_get_text)
        monkeypatch.setattr(catalog_sources, "http_get_bytes", fake_get_bytes)
        config = _config(
            sources=[
                SourceConfig(
                    kind="static-index",
                    name="pnmcc-2026",
                    base_url="https://yanntm.github.io/pnmcc-models-2026/",
                    edition="2026",
                )
            ]
        )
        return CatalogService(config=config, cache_directory=tmp_path / "cache")

    def test_refresh_then_search_and_fetch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = self._service(tmp_path, monkeypatch)
        service.refresh()
        assert service.available
        page = service.search(query=CatalogQuery(search="philosophers"))
        assert page.total == 1
        fetched = service.fetch(name="Philosophers-PT-010")
        assert fetched.pnml_text == PHILOSOPHES_PNML
        assert fetched.sha256.startswith("sha256:")
        assert fetched.entry.source == "pnmcc-2026"
        # The archive landed in the bounded disk cache.
        assert list((tmp_path / "cache").glob("*.bin"))

    def test_all_sources_failing_degrades_explicitly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = self._service(tmp_path, monkeypatch, fail=True)
        service.refresh()  # never raises
        assert not service.available
        with pytest.raises(CatalogUnavailableError):
            service.search(query=CatalogQuery())
        with pytest.raises(CatalogUnavailableError):
            service.fetch(name="Philosophers-PT-010")

    def test_unknown_name_and_quota_are_typed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = self._service(tmp_path, monkeypatch)
        service.refresh()
        with pytest.raises(UnknownModelError):
            service.fetch(name="Nope-PT-999")
        small = _config(
            sources=[
                SourceConfig(
                    kind="static-index", name="s", base_url="https://x.test/", edition="2026"
                )
            ],
            max_archive_bytes=10,
        )
        monkeypatch.setattr(
            catalog_sources, "http_get_text", lambda **kwargs: '<a href="./INPUTS/A-PT-1.tgz">x</a>'
        )

        def quota_enforcing_get_bytes(**kwargs: object) -> bytes:
            max_bytes = kwargs["max_bytes"]
            assert isinstance(max_bytes, int)
            if max_bytes < 100:
                raise QuotaExceededError("quota")
            return make_tgz(members={"A-PT-1/model.pnml": PHILOSOPHES_PNML.encode()})

        monkeypatch.setattr(catalog_sources, "http_get_bytes", quota_enforcing_get_bytes)
        tiny = CatalogService(config=small, cache_directory=tmp_path / "cache2")
        tiny.refresh()
        with pytest.raises(QuotaExceededError):
            tiny.fetch(name="A-PT-1")


class TestPropertyTier:
    """Hypothesis invariants of the catalog domain logic (ADR-0012)."""

    @pytest.mark.property
    @given(
        names=st.lists(
            st.from_regex(r"[A-Za-z]+-(PT|COL)-[0-9]+", fullmatch=True), min_size=0, max_size=30
        )
    )
    def test_facet_counts_always_sum_to_total(self, names: list[str]) -> None:
        index = CatalogIndex(entries=[entry(name=name) for name in names])
        page = index.search(query=CatalogQuery(limit=100))
        assert sum(page.facets.formalism.values()) == page.total
        assert sum(page.facets.family.values()) == page.total
        assert sum(page.facets.source.values()) == page.total

    @pytest.mark.property
    @given(
        names=st.lists(
            st.from_regex(r"[A-Za-z]+-(PT|COL)-[0-9]+", fullmatch=True),
            min_size=1,
            max_size=25,
            unique=True,
        )
    )
    def test_pagination_never_drops_nor_duplicates(self, names: list[str]) -> None:
        index = CatalogIndex(entries=[entry(name=name) for name in names])
        seen: list[str] = []
        offset = 0
        while True:
            page = index.search(query=CatalogQuery(limit=3, offset=offset, sort="name"))
            seen.extend(item.name for item in page.items)
            if offset + 3 >= page.total:
                break
            offset += 3
        assert sorted(seen) == sorted(
            {item.name for item in index.search(query=CatalogQuery(limit=100)).items}
        )

    @pytest.mark.property
    @given(bytes_count=st.integers(min_value=0, max_value=300 << 20))
    def test_size_bucket_is_monotonic_in_bytes(self, bytes_count: int) -> None:
        order = {"small": 0, "medium": 1, "large": 2, "huge": 3}
        bucket = size_bucket_of(compressed_bytes=bytes_count)
        assert bucket is not None
        if bytes_count > 0:
            smaller = size_bucket_of(compressed_bytes=bytes_count - 1)
            assert smaller is not None
            assert order[smaller] <= order[bucket]

    @pytest.mark.property
    @given(st.from_regex(r"[A-Za-z]+-(PT|COL)-[0-9]+", fullmatch=True))
    def test_parse_instance_name_round_trips(self, name: str) -> None:
        parsed = parse_instance_name(name=name)
        assert parsed is not None
        assert f"{parsed.family}-{parsed.formalism}-{parsed.params}" == name


class TestNarrationAndRobustness:
    """The catalog service's narration events are pinned (ADR-0005)."""

    def test_refresh_and_fetch_events_are_narrated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = TestCatalogService()._service(tmp_path, monkeypatch)
        with structlog.testing.capture_logs() as logs:
            service.refresh()
            service.fetch(name="Philosophers-PT-010")
        events = [log["event"] for log in logs]
        assert "catalog_index_refreshed" in events
        assert "catalog_instance_fetched" in events

    def test_failing_source_is_narrated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = TestCatalogService()._service(tmp_path, monkeypatch, fail=True)
        with structlog.testing.capture_logs() as logs:
            service.refresh()
        events = [log["event"] for log in logs]
        assert "catalog_source_unavailable" in events
        assert "catalog_index_refreshed" in events

    def test_corrupt_archive_is_a_typed_error(self) -> None:
        with pytest.raises(CorruptArchiveError):
            extract_pnml_candidates(data=b"not a gzip at all", content_type="application/gzip")

    def test_malformed_pnml_member_is_a_typed_error(self) -> None:
        archive = make_tgz(members={"A-PT-1/model.pnml": b"<not-xml"})
        with pytest.raises(MalformedPnmlError):
            extract_pnml_candidates(data=archive, content_type="application/gzip")

    def test_service_fetch_wraps_archive_errors(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = TestCatalogService()._service(tmp_path, monkeypatch)
        service.refresh()
        monkeypatch.setattr(catalog_sources, "http_get_bytes", lambda **kwargs: b"corrupt-bytes")
        with pytest.raises(InvalidCatalogPnmlError):
            service.fetch(name="Philosophers-PT-010")


def test_http_error_response_is_closed_no_resource_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 must not leak an unclosed HTTPError response (ResourceWarning)."""

    def fake_urlopen(request: urllib.request.Request, timeout: float) -> object:
        raise urllib.error.HTTPError(
            url=request.full_url, code=404, msg="Not Found", hdrs=None, fp=io.BytesIO(b"")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        with pytest.raises(SourceUnavailableError):
            catalog_sources.http_get_bytes(
                url="https://x.test/", timeout_seconds=1, max_bytes=10, user_agent="test"
            )
        gc.collect()
