# ADR-0107: UI Amorçage — three import paths, catalog search, candidate choice

- Status: proposed (drafted in the P71 PR per ADR-0010; the human accepts it)
- Date: 2026-11-21

## Context

Spec §21 (issue P71 / #166): the Amorçage step must offer three import
paths — catalog name, local file (PNML **or archive**, per ADR-0106), and
URL (per ADR-0106) — on top of the P68 session-creation flow, which stays
unchanged. The catalog listing comes from `GET /v1/catalog/models`
(ADR-0105) with free-text search and facets (family, formalism, edition,
source, size). COL entries must carry the "symmetric non déplié" warning
with a `-PT-` twin proposal. A 409 `multiple_candidates` must surface a
chooser, and every import ends with a shared confirmation (model id +
provenance). No verdict is ever computed client-side (spec §9, §19.4).

## Decision

- **Typed client additions** (`api.ts`): `searchCatalog(params)`,
  `importFromCatalog(name)`, `importFromUrl(url, candidate?)`,
  `postModel(body, contentType, candidate?)`. Import methods return the raw
  typed statuses (409 → candidate list, 201/200 → model, else the problem
  `detail`) so the component branches on server truth and shows server
  errors verbatim in `role="alert"`.
- **One component, three paths** (`Amorcage.tsx`): a catalog panel
  (`<details>`, search input + 5 facet selects re-querying on change,
  results list, per-entry import button, COL warning + `-PT- twin` button
  via `name.replace("-COL-", "-PT-")`), a by-name field, a URL field, and
  the existing file zone extended to `.zip/.tgz/.tar.gz/.tar` with the
  content-type mapped by extension. The P68 ids (`#pnml-file`,
  `#import-model`) and the session-creation flow are unchanged.
- **409 candidate chooser**: the pending import body is kept client-side
  and re-POSTed with `?candidate=` once the user picks a radio; the
  candidate list renders name, size, formalism and sha256 (never the PNML
  content, per ADR-0106).
- **Shared confirmation**: model id (imported vs idempotent), session id,
  and a provenance line (source, name, sha256) read from
  `model.metadata.provenance` — server-recorded, never reconstructed
  client-side.
- **i18n**: all chrome strings under `amorcage.*` in fr.json/en.json with
  100% key parity (enforced by `i18n.test.tsx`); server-produced values
  stay verbatim and untranslated.
- **Tests**: component tests (`tests/amorcage-catalog.test.tsx`, mocked
  fetch: success by name/URL/file, unknown name, refused URL, catalog
  unavailable, 409 multi-candidate archive with candidate re-POST) and a
  Playwright spec (`e2e/amorcage-import.spec.ts`) driving the three paths
  with `page.route`-mocked `/v1/**` endpoints.

## Consequences

- The Amorçage step covers the full spec §21 surface without touching the
  P68 session flow; e2e for the import paths no longer needs the compose
  engine (mocked endpoints), only the served UI.
- Archive uploads from the browser inherit the ADR-0106 server-side
  guards (quota, zip-slip, one-PNML-or-candidates).
