# ADR-0001: Python 3.15 + uv toolchain, committed lockfile

- Status: accepted
- Date: 2026-11-02

## Context

The template must start on the latest stable Python (3.15 at design time)
and be reproducible: agents install dependencies dozens of times per day,
and any drift between environments produces phantom failures that cost
review cycles. Tool choice also defines every command agents type daily.

## Options considered

- **uv** (chosen): extremely fast, lockfile-first, `uv sync` gives an
  exact environment, supports dependency groups natively, now the de facto
  standard for new Python projects.
- **Poetry**: mature, but slower and its lock resolution became a liability
  for agent-scale iteration frequency.
- **pip + requirements files**: no lock semantics for the full graph, no
  groups, drifts silently - rejected for reproducibility.

## Decision

Python 3.15, pinned via `.python-version`; uv as the single tool for
sync, run, and lock; `uv.lock` **committed**; `dependency-groups` in
`pyproject.toml` (no legacy setup files).

## Consequences

- Fast, identical environments everywhere; agents never improvise installs
  (`uv sync` only - hallucinated installs are a known agent failure mode).
- 3.15 gives current syntax (PEP 695, PEP 728 closed TypedDicts) without
  back-compat shims.
- Negative: uv is a young tool by Python standards; if it stagnates,
  migration is a `pip-tools`-era operation, not a rewrite.
