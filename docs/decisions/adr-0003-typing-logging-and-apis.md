# ADR-0003: Precise typing, structured logging, REST APIs

- Status: accepted
- Date: 2026-11-02 (condensed 2026-11-13)
- Merges: former ADR-0005 (logging as executable comments), ADR-0006
  (precise typing), ADR-0016 (REST with best practices)

## Context

Precise types are the agents' primary defense: many failures become
static errors instead of review findings. One type checker must be the
reference whose verdict CI treats as truth. The owner prefers logs to
comments: a log comments *and* serves debugging, and structured events
are machine-parsable in production and testable in development.
Instances will expose HTTP APIs sooner or later; left unspecified,
agents improvise verbs, error shapes and versioning.

## Decision

- **Typing**: domain primitives are distinct types (`IssueIdentifier`,
  `MessageText`, `WebhookUrl`) - a bare string at a boundary is a review
  finding; closed sets are enumerated types (with behavior) or literal
  unions (bare data) with exhaustiveness guards; variant data uses
  discriminated unions with an explicit discriminator; dynamic escapes
  need an inline justification comment. One reference type checker,
  strict profile, in pre-commit and CI (mypy strict for the Python
  tools here).
- **Logging**: structured events with named fields only - never
  positional format strings. Level contract: `DEBUG` = quotation of
  what the code is doing (executable comments), `INFO` = milestones,
  `WARNING` = fallbacks, `ERROR` = failure with full context.
  Rendering: JSON in production, human-readable locally, configured
  only in the composition root. No secret is ever logged. (structlog
  for the Python tools here.)
- **HTTP APIs**: REST over HTTP/JSON; resource-oriented URLs (never
  verbs; non-CRUD actions are explicit sub-resources); standard verb
  semantics and truthful status codes (no "200 with error inside");
  pagination on list endpoints; typed, stable error payloads;
  standard authentication schemes; explicit versioning; the contract
  document (OpenAPI or equivalent) is kept with the code and reviewed.

## Consequences

- Argument swaps, missed cases and wrong variant assumptions become
  static errors; the reviewer checklist verifies typing mechanically.
- Narrating `DEBUG` events replace section comments, are asserted by
  tests, and cannot rot; machine-parsable logs speed the day-end
  triage.
- Any client can consume an API from its contract alone; caches and
  proxies behave correctly by default.
- Negative: the strict profile occasionally demands visibly justified
  escapes; event names are pinned by tests, so renames are deliberate;
  REST's uniformity occasionally forces awkward modeling - the
  sub-resource convention is the escape hatch.
