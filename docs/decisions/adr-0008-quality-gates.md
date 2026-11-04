# ADR-0008: Quality gates - Ruff, pytest, 90% coverage, import-linter, pre-commit

- Status: accepted
- Date: 2026-11-02

## Context

Autonomous development needs gates that say "no" mechanically. Each gate
targets a distinct failure mode observed in AI-assisted codebases:
style drift, dead abstractions, layer rot, and untested confidence.

## Options considered

- **The selected gate stack** (chosen): one linter that can actually be
  respected all day, one test runner, one architectural checker, one
  coverage floor, one local enforcer.
- **Multi-linter stacks (flake8 + isort + pylint + black)**: config sprawl
  and contradictory verdicts - agents thrash between tools.
- **No coverage floor**: agents game nothing - and test nothing.
- **Gates as reviewer opinions only**: unscalable, and the reviewer agent
  cannot run a hunch on every line.

## Decision

- **Ruff**: lint + format, broad rule set including `S` (security), `ANN`
  (annotations), `TC` (type-checking imports), `T20` (print ban outside
  `main.py`/`scripts/`), `C901` (complexity).
- **pytest** with function-style tests; markers `integration` and
  `property` to run tiers separately.
- **Coverage floor 90%** (branch mode, `--cov-fail-under=90`): a floor,
  not a target to game (ADR-0013 complements this).
- **import-linter**: the layer contract of ADR-0002 as a hard check.
- **pre-commit**: ruff, gitleaks, mypy, lint-imports run before a commit
  exists, so remote CI verifies rather than discovers.

## Consequences

- "CI green" is a strong, defined statement - it is merge criterion #1
  (`docs/branching.md`).
- Negative: a 90% floor pushes towards test-the-lines behavior; that is
  precisely why mutation testing (ADR-0013) exists - the floor is the
  cheapest signal, mutation score is the honest one.
