# ADR-0004: Quality, security and anti-complacency gates

- Status: accepted
- Date: 2026-11-02 (condensed 2026-11-13)
- Merges: former ADR-0007 (quality gates), ADR-0008 (security gates),
  ADR-0012 (anti-complacency tests)

## Context

Autonomous development needs gates that say "no" mechanically. Each
gate targets a distinct failure mode observed in AI-assisted codebases:
style drift, layer rot, untested confidence. Agents add two specific
risks: hallucinated package names that resolve to squatted packages,
and committed secrets when an agent "fixes" a failing integration by
hard-coding a token. Goodhart's law also applies brutally: line
coverage as a target gets gamed - tests that execute code without
asserting anything, assertions that can never fail. Coverage measures
execution, not verification.

## Decision

- **One gate per failure mode** (no multi-tool stacks, no contradictory
  verdicts): one linter/formatter (community standard, broad rule set:
  style, complexity, print bans, annotations, security lint); one test
  runner with unit / integration / property tiers; one layering
  checker for the onion; **coverage floor 90 %** (branch mode); all in
  pre-commit so remote CI verifies rather than discovers. (Ruff,
  pytest, import-linter here.)
- **Security, defense in depth**: security lint rules in the default
  gate; secret scanning in pre-commit and CI (no secret ever lands on
  a branch); dependency audit on the resolved lockfile; new
  dependencies are on the strict escalation list. (Ruff S rules,
  gitleaks, pip-audit here.)
- **Anti-complacency tests**: mutation testing on `daily` at end of
  day - surviving mutants become issues; property-based tests for
  domain invariants; the checklist demands a test that fails if the
  behavior regresses, not one that merely runs it. (mutmut,
  Hypothesis here.)

## Consequences

- "CI green" is a strong, defined statement - merge criterion #1.
- The agent-specific risks (typosquatting, secret leakage) are covered
  by tools that never tire and never get convinced.
- "Covered but unverified" code is surfaced daily, mechanically.
- Negative: the 90 % floor pushes towards test-the-lines behavior -
  the floor is the cheapest signal, the mutation score is the honest
  one; secret scanners occasionally flag test fixtures (obvious fake
  values, documented per-line exceptions only); mutation runs are
  slow, hence end-of-day.
