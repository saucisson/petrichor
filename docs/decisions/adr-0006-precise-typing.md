# ADR-0006: Precise typing, one reference type checker

- Status: accepted
- Date: 2026-11-02

## Context

Owner's requirement: types as precise as possible, function contracts
clearly typed, yet readable. For agents, precise types are the primary
defense: many failures become static errors instead of review
findings. One checker must also be *the* reference - the one whose
verdict the agents and CI treat as truth; agent familiarity with its
error dialect matters when agents must fix errors without docs.

## Options considered

- **The strictest practical profile of the community's reference type
  checker, with a rich type vocabulary** (chosen).
- **Basic annotations only**: half the value - unions get narrowed by
  hope, not by guards.
- **Full dependent-typing aspirations everywhere**: readability cost
  beyond the owner's "readable" bar.
- **Multiple checkers in parallel**: contradictory verdicts - agents
  thrash between tools; rejected.

## Decision

- Domain primitives are distinct types (`IssueIdentifier`,
  `MessageText`, `WebhookUrl`); a bare string at a boundary is a review
  finding.
- Closed sets are enumerated types when behavior attaches, literal
  unions for bare data; pattern matching with an exhaustiveness guard
  guarantees all cases are handled at compile time.
- Variant data uses discriminated unions with an explicit
  discriminator.
- Dynamic/unchecked types need an inline justification comment;
  per-module documented overrides are the pressure valve.
- **One reference type checker**, strict profile, in pre-commit and
  CI as the reference verdict. For the Python tools in this template:
  mypy strict-plus (the longest-maintained checker, the reference
  semantics most models trained on).

## Consequences

- Big classes of agent errors (argument swaps, missed cases, wrong
  variant assumptions) become static errors.
- The reviewer checklist can verify typing mechanically.
- Negative: the strictest profile occasionally demands visibly
  justified escapes, and reviewers must resist the temptation to
  silence rather than argue. The typing rules themselves are
  checker-agnostic; swapping the reference checker is a new ADR, not
  a codebase change.
