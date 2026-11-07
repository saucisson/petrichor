# ADR-0003: Data-oriented style - data and functions over OOP

- Status: accepted
- Date: 2026-11-02

## Context

The owner prefers exposed, manipulable data and explicit flows over
encapsulated objects: the code must be readable top-down, like a
story, with state visible rather than hidden behind accessors. This
also serves agents: flat functions with typed, validated data at the
boundaries are the shape current models handle most reliably.

## Options considered

- **Validated data records + module functions** (chosen): data is a
  typed, validated, serializable value; behavior lives in explicit
  functions that take named parameters.
- **Behavior-first classes** (data hiding, methods): classic OOP -
  opaque state, indirection to follow, harder logging, and
  inheritance traps.
- **Bare records + manual validation**: lighter, but re-implements
  the validation, coercion and serialization the language's
  standard data library already provides, better tested and faster.

## Decision

Domain concepts are validated, immutable-where-possible data records
(the language's standard validation/serialization library); logic is
free functions in `services`; classes exist only where state is
genuinely necessary (e.g. an adapter holding a connection).

## Consequences

- Every value can be logged, diffed, and round-tripped through a
  serialization format - which message payloads literally require.
- Validation at construction: invalid data cannot enter the system.
- Negative: a function that takes five related arguments should become
  a record rather than grow a parameter list - reviewers must enforce
  this line; it is a style rule, not a type-system one.
