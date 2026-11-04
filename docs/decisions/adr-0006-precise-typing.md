# ADR-0006: Precise typing - NewTypes, discriminated unions, exhaustive matches

- Status: accepted
- Date: 2026-11-02

## Context

Owner's requirement: types as precise as possible, function contracts
clearly typed, yet readable. For agents, precise types are the primary
defense: many failures become static errors instead of review findings.

## Options considered

- **Strict "strict-plus" mypy profile with a rich type vocabulary**
  (chosen): `disallow_any_generics`, `disallow_any_explicit`,
  `warn_return_any`, `warn_unreachable`, plus targeted patterns below.
- **Basic annotations only**: half the value - unions get narrowed by
  hope, not by guards.
- **Full dependent-typing aspirations (protocols everywhere, phantom
  types)**: readability cost beyond the owner's "readable" bar.

## Decision

- `NewType` domain primitives (`IssueIdentifier`, `MessageText`,
  `WebhookUrl`) at boundaries; bare `str` there is a review finding.
- Closed sets: `StrEnum` when behavior attaches, `Literal` for bare data;
  `match` + `assert_never` for compile-time exhaustiveness.
- Variant data: Pydantic discriminated unions with explicit
  `Field(discriminator=...)`.
- Narrowing: `TypeIs` guards; data-carrying **kwargs: `Unpack[TypedDict]`.
- Any `Any` needs an inline justification comment; per-module
  documented overrides are the pressure valve.

## Consequences

- Big classes of agent errors (argument swaps, missed enum cases, wrong
  variant assumptions) become static errors.
- The reviewer checklist can verify typing mechanically.
- Negative: `disallow_any_explicit` is the strictest stance; occasional
  justified escapes must be visibly justified, and reviewers must resist
  the temptation to silence rather than argue.
