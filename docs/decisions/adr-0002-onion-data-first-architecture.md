# ADR-0002: Onion data-first architecture

- Status: accepted
- Date: 2026-11-02

## Context

AI agents develop and review all code autonomously for most of the day.
The architecture must be mechanically checkable (import-linter can verify
layering; it cannot verify "clean" abstractions), and readable by a human
who is not an architecture specialist - the owner audits at day boundary.

## Options considered

- **Onion, data-first** (chosen): four layers (`models` → `services` →
  `infrastructure` → `main`), one dependency rule, data (Pydantic models)
  at the center.
- **Hexagonal / Clean Architecture**: ports-and-adapters with entity/use
  case/interface layers and heavy inversion machinery - more abstract
  vocabulary, harder for a non-expert human to audit, harder for
  import-linter to pin down precisely.
- **Flat package**: no rule to check, drifts immediately under agent
  autonomy - rejected.

## Decision

The onion in `docs/architecture.md`: `models` is the domain language,
`services` are pure functions behind `Protocol` ports, `infrastructure`
holds adapters, `main` is the only composition root. Dependencies point
strictly inward; `import-linter` fails the build on violation.

## Consequences

- Every PR's architecture conformance is a machine check, not a reviewer
  opinion.
- Data-first means state is inspectable Pydantic objects - easy to log,
  serialize, and test - instead of hidden behind behavior-first classes.
- Negative: more files and an explicit port for each boundary; for a very
  small app this is overhead. Accepted because the template's product
  *is* the discipline.
