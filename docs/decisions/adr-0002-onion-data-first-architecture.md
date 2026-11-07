# ADR-0002: Onion, data-first architecture

- Status: accepted
- Date: 2026-11-02

## Context

AI agents develop and review all code autonomously for most of the
day. The architecture must be mechanically checkable (a layering
checker can verify import direction; it cannot verify "clean"
abstractions), and readable by a human who is not an architecture
specialist - the owner audits at day boundary.

## Options considered

- **Onion, data-first** (chosen): four layers (`models` → `services` →
  `infrastructure` → `main`), one dependency rule, validated data at
  the center.
- **Hexagonal / Clean Architecture**: ports-and-adapters with
  entity/use-case/interface layers and heavy inversion machinery -
  more abstract vocabulary, harder for a non-expert human to audit,
  harder for a checker to pin down precisely.
- **Flat package**: no rule to check, drifts immediately under agent
  autonomy - rejected.

## Decision

The onion: `models` is the domain language, `services` are pure
functions behind port interfaces, `infrastructure` holds adapters
implementing those ports, `main` is the only composition root.
Dependencies point strictly inward; a mechanical layering check fails
the build on violation (for the Python tools in this template:
import-linter).

## Consequences

- Every pull request's architecture conformance is a machine check,
  not a reviewer opinion.
- Data-first means state lives in inspectable, serializable data
  objects - easy to log, diff, and test - instead of hidden behind
  behavior-first classes.
- Negative: more files and an explicit port for each boundary; for a
  very small application this is overhead. Accepted because the
  template's product *is* the discipline.
