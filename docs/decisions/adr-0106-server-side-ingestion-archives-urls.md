# ADR-0106: Server-side ingestion — archives and URL import

- Status: proposed (drafted in the P70 PR per ADR-0010; the human accepts it)
- Date: 2026-11-21

## Context

Spec §21.3 (issue P70 / #165): the user picks a local file (PNML or an
archive holding PNML) or pastes a URL. MCC archives often hold dozens of
instances, so a silent multi-import is forbidden. URL import must follow
the security protocol (ADR-0008): https only, versioned domain allowlist,
SSRF rejection of private/local networks.

## Decision

- **`POST /v1/models` accepts archives** (`application/zip`,
  `application/gzip`, `application/x-tar`) in addition to
  `application/pnml+xml`, under an upload quota (`MAX_UPLOAD_BYTES`,
  100 Mo). Extraction reuses the protected `archive_ingest` module from
  ADR-0105: zip-slip members (absolute paths, `..`, drive letters) are
  refused and listed in the typed 422 `unsafe_archive`; corrupt archives
  and malformed PNML members are typed 422 (`invalid_archive` /
  `invalid_pnml`); an archive without PNML is 422 `no_pnml`.
- **The one-PNML-or-candidates rule**: exactly one PNML imports directly
  (201/200 idempotent); several PNML answer **409 `multiple_candidates`**
  with the typed candidate list (name, size, sha256, detected formalism)
  and import only when the client re-POSTs with the `candidate` query
  parameter (404 `unknown_candidate` otherwise). No code path imports
  several models silently — pinned by a guard test.
- **`POST /v1/models/import-url`** takes `{url, candidate?}`. The policy
  lives in the versioned `src/petrichor/url_import.json` (default allowlist:
  `pnrepository.lip6.fr`, `mcc.lip6.fr`, `yanntm.github.io`,
  `raw.githubusercontent.com`). `url_import.py` validates: https scheme,
  allowlisted domain (exact or subdomain), and **every resolved address
  public** (rejects private/loopback/link-local/multicast/reserved/
  unspecified — SSRF gate); redirects are re-validated hop by hop.
  Download honors timeout + quota (413 `quota_exceeded`), then the same
  one-PNML-or-candidates rule applies. Content type is sniffed by URL
  extension then magic bytes.
- **Provenance** (`source`: upload|url, url, archive/candidate sha256) is
  recorded in `model.metadata["provenance"]`, outside the content hash, so
  re-import stays idempotent.
- `openapi.yaml` documents both endpoints (ADR-0016).

## Consequences

- The UI (P71) has exactly three import paths — catalog name, file
  (PNML or archive), URL — all converging on the same candidate rule.
- The allowlist is configuration, not code: adding a trusted mirror is a
  one-line change to a versioned JSON file.
- SSRF defense is at resolution time (all A/AAAA records checked), not just
  at string matching, and redirects cannot bypass it.
