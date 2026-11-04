# ADR-0003: Data-oriented style - Pydantic over OOP, functions over classes

- Status: accepted
- Date: 2026-11-02

## Context

The owner prefers exposed, manipulable data and explicit flows over
encapsulated objects: the code must be readable top-down, like a story,
with state visible rather than hidden behind accessors. This also serves
agents: flat functions with typed data at the boundaries are the shape
current models handle most reliably.

## Options considered

- **Pydantic models + module functions** (chosen): data is a typed,
  validated, serializable value; behavior lives in explicit functions
  that take keyword-only arguments.
- **Behavior-first classes** (data hiding, methods): classic OOP - opaque
  state, indirection to follow, harder logging, and inheritance traps.
- **dataclasses + manual validation**: lighter, but re-implements
  validation, coercion and JSON shaping that Pydantic v2 already gives
  with better performance.

## Decision

Domain concepts are Pydantic models (frozen where possible); logic is
free functions in `services`; classes exist only where state is genuinely
necessary (adapters holding a connection, e.g. `DiscordWebhookDispatcher`).

## Consequences

- Every value can be logged, diffed, and round-tripped through JSON -
  which the notification payloads literally require.
- Validation at construction: invalid data cannot enter the system.
- Negative: a function that takes five related arguments should become a
  model rather than grow a parameter list - reviewers must enforce this
  line; it is a style rule, not a type-system one.
