# ADR-0002: Onion, data-first architecture and readable style

- Status: accepted
- Date: 2026-11-02 (condensed 2026-11-13)
- Merges: former ADR-0002 (onion data-first), ADR-0003 (data-oriented
  style), ADR-0004 (readability-first style)

## Context

AI agents develop and review all code autonomously for most of the day.
The architecture must be mechanically checkable (a layering checker
verifies import direction; it cannot verify "clean" abstractions) and
readable by a human who is not an architecture specialist. The owner's
explicit preferences: exposed, manipulable data over encapsulated
objects; systematic named parameters, named constants, no
abbreviations; readability above compactness - "longer but linear
functions beat over-decomposed code with no reason". Flat functions
with typed, validated data at the boundaries are also the shape
current models handle most reliably.

## Decision

- Four layers, one dependency rule, validated data at the center:
  `models` (domain language) → `services` (pure functions behind port
  interfaces) → `infrastructure` (adapters implementing the ports) →
  `main` (the only composition root). Dependencies point strictly
  inward; a mechanical layering check fails the build on violation.
- Domain concepts are validated, immutable-where-possible data records
  (the language's standard validation/serialization library); logic is
  free functions in `services`; classes only where state is genuinely
  necessary (e.g. an adapter holding a connection).
- Style, enforced by the linter wherever the language allows:
  named/keyword-only parameters (self/this and a single obvious
  primitive excepted); no abbreviations (allowed: `id`, `url`, `http`);
  `UPPER_CASE` named constants; extraction only when a block is reused
  at least twice or carries its own intention; complexity ≤ 10,
  nesting ≤ 4; docstrings on public callables.

## Consequences

- Every pull request's architecture conformance is a machine check,
  not a reviewer opinion; every value can be logged, diffed and
  round-tripped through a serialization format.
- Validation at construction: invalid data cannot enter the system.
- Code is greppable and self-documenting; diffs review well.
- Negative: more files and an explicit port per boundary; five related
  arguments should become a record rather than grow a parameter list -
  a style rule reviewers must enforce; some standard-library
  signatures force positional style at the boundary (the rule applies
  to our code, with an explicit suppression as the honest exception).
