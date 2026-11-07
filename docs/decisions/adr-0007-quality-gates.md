# ADR-0007: Quality gates - one gate per failure mode

- Status: accepted
- Date: 2026-11-02

## Context

Autonomous development needs gates that say "no" mechanically. Each
gate targets a distinct failure mode observed in AI-assisted
codebases: style drift, dead abstractions, layer rot, and untested
confidence.

## Options considered

- **One gate per failure mode** (chosen): one linter/formatter that
  can actually be respected all day, one test runner, one
  architectural layering checker, one coverage floor, one local
  enforcer.
- **Multi-tool stacks**: config sprawl and contradictory verdicts -
  agents thrash between tools.
- **No coverage floor**: agents game nothing - and test nothing.
- **Gates as reviewer opinions only**: unscalable, and the reviewer
  agent cannot run a hunch on every line.

## Decision

- **One linter/formatter** (the community standard), broad rule set:
  style, complexity, print bans outside entry points, annotations.
- **One test runner**; markers to run tiers (unit / integration /
  property) separately.
- **Coverage floor 90 %** (branch mode): a floor, not a target to
  game (ADR-0012 complements this).
- **One layering checker** enforcing the onion contract (ADR-0002).
- **Pre-commit hooks** so the gates run before a commit exists:
  remote CI verifies rather than discovers.
- For the Python tools in this template: Ruff, pytest, import-linter.

## Consequences

- "CI green" is a strong, defined statement - it is merge criterion #1
  (`docs/branching.md`).
- Negative: a 90 % floor pushes towards test-the-lines behavior; that
  is precisely why mutation testing (ADR-0012) exists - the floor is
  the cheapest signal, mutation score is the honest one.
