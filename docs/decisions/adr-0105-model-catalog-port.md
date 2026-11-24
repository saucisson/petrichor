# ADR-0105: Model catalog — port, sources, cache, degradation

- Status: proposed (drafted in the P69 PR per ADR-0010; the human accepts it)
- Date: 2026-11-21

## Context

Spec §21.2 (issue P69 / #164) asks for a server-side catalog of the MCC
model-checking competition models so the UI can search and import them by
name instead of uploading files. The spike
(`docs/spikes/2026-11-20-catalogues-mcc.md`) identified three reachable
source kinds:

- **metadata** — `pnrepository.lip6.fr` per-model `metadata.json`. Its exact
  field layout could **not** be verified from this environment (DNS blocked);
  the adapter parses it tolerantly and degrades to the other sources.
- **edition** — `mcc.lip6.fr/<year>/models.php` links per-family
  `archives/<Famille>-pnml.tar.gz`; verified for editions 2021–2026 (no
  archives for ≤ 2020).
- **static-index** — `yanntm.github.io/pnmcc-models-<year>/index.html`
  listing `./INPUTS/<Instance>.tgz`; verified for 2017–2026.

The API must degrade explicitly (typed 503) when no source answers, never
crash the server, and must never silently import the wrong net.

## Decision

- **A domain port `ModelCatalog`** (`src/petrichor/catalog.py`, Protocol:
  `available`, `search`, `fetch`) behind which the source adapters live,
  following ADR-0002. The API depends only on the port; tests use a
  `FakeCatalog`.
- **Three adapter kinds** (`src/petrichor/catalog_sources.py`), configured in
  the versioned `src/petrichor/catalog_sources.json`: 1 metadata source
  (pnrepository-mcc), 6 edition sources (mcc-2021..2026), 10 static-index
  sources (pnmcc-2017..2026). Adding an edition is a one-line config change.
- **Background refresh**: `CatalogService` refreshes all sources from a
  daemon thread at startup and periodically; a failed source never raises
  into the server. `available` is false only when *every* source failed, in
  which case the API answers 503 `catalog_unavailable`.
- **Bounded disk cache** (`.petrichor/catalog-cache`, default 512 Mo, 24 h
  TTL, LRU eviction by quota): fetched archives are cached by sha256-of-URL
  key so repeated imports of the same instance do not re-download. A
  per-download quota (default 100 Mo) raises `QuotaExceededError` → 413.
- **Safe archive extraction** (`src/petrichor/archive_ingest.py`): zip/tgz/
  tar extraction refuses path traversal (zip-slip), absolute paths and
  symlink escapes (`UnsafeArchiveError`); PNML candidates are detected
  namespace-safely (`tag.split("}")[-1] == "net"`), so a COL net inside a
  PT-named archive is detected by content, not by name.
- **Provenance in model metadata**: `POST /v1/catalog/import` records
  `{catalog, source, edition, name, url, sha256}` in
  `model.metadata["provenance"]` (metadata is outside the content hash, so
  re-import stays idempotent: 201 new / 200 existing, like `POST /v1/models`).
- **API surface** (ADR-0016, documented in `openapi.yaml`):
  `GET /v1/catalog/models` (search, facets family/formalism/edition/source/
  size, closed sort set name|family|edition|size with semantic size order,
  limit/offset pagination) and `POST /v1/catalog/import` `{name}`.

## Consequences

- The UI (P71) only needs the two endpoints; no source-specific logic leaks
  into the API or the UI.
- The pnrepository metadata fields being unverifiable is an accepted,
  documented limitation: that source is parsed tolerantly and the edition +
  static-index sources carry the load when it disagrees or is unreachable.
- The cache directory is runtime state, gitignored, and bounded; the quota
  protects the VPS from a hostile or huge archive.
