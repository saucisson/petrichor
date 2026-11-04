# ADR-0007: mypy as the reference type checker

- Status: accepted
- Date: 2026-11-02

## Context

One checker must be *the* reference - the one whose verdict the agents
and CI treat as truth. Candidates as of late 2026: mypy (1.x), Pyright,
Astral's `ty`, and Meta's Pyrefly (stable since May 2026).

## Options considered

- **mypy** (chosen): the longest-maintained checker, the reference
  semantics most LLMs trained on (agent familiarity matters when agents
  must fix its errors without docs), broad plugin ecosystem
  (pydantic-mypy), stable strictness flags.
- **Pyright**: excellent speed and inference, but a different error
  dialect; its semantics live in a TypeScript codebase less legible to
  Python-fluent agents.
- **ty**: extremely fast, but still early (0.0.x era) - promising future
  replacement, premature as the single source of truth.
- **Pyrefly**: strong performance claims, newest of all - too recent to
  pin a constitutional choice on.

## Decision

mypy, strict-plus (ADR-0006), with pydantic-mypy plugin, in pre-commit
and CI as the reference verdict.

## Consequences

- Errors agents see match the dialect they know; fewer loops to fix them.
- Negative: mypy is the slowest of the four. Mitigated by incremental
  caching and the fact that agents run it per-PR, not continuously.
- Migration-ready: the typing rules (ADR-0006) are checker-agnostic; when
  `ty` or Pyrefly matures, swapping is a new ADR, not a codebase change.
