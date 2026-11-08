# ADR-0100: Petrichor product code lives in `src/petrichor/`, tests in `tests/`

- Status: proposed (drafted in PR #59 per ADR-0010; the human accepts it)
- Date: 2026-11-08

## Context

ADR-0014 fixes the repository layout: `tools/` holds long-lived
self-contained utilities, `scripts/` holds day-cycle and review
orchestration scripts, and **the product lives in its own directory** -
for a Python instance, the conventional `src/` layout. The first
product module, the canonical `petri+json` v1.0 schema and content
hashing (`petri_canonical.py`, issue #2 / spec §13), was initially placed
in `scripts/` because the lint/type/test configuration only covered
`tools/notify` and `scripts`. The review of PR #59 flagged this as a
BLOCKING architecture finding: `scripts/` is not a product home, and
"the config does not cover it yet" is not an architecture argument.

## Options considered

- **Keep the product module in `scripts/`** (rejected): violates
  ADR-0014's one-job-per-directory rule; `scripts/` would silently
  become the product directory by accretion.
- **Product package in `src/petrichor/`, tests in `tests/`** (chosen):
  the conventional Python source layout named by ADR-0014; the quality
  gates (ruff, mypy strict, pytest + coverage floor) are extended to
  cover it in the same commit, so the module is linted, type-checked and
  measured exactly like the tools.
- **Product package under `tools/`** (rejected): `tools/` is for
  self-contained utilities, not the product (ADR-0014).

## Decision

- The Petrichor product code lives in **`src/petrichor/`** (a package,
  importable as `petrichor.*` via pytest's `pythonpath = ["src"]`).
- Its tests live in **`tests/`**, collected by the same pytest gate.
- The quality configuration covers both: ruff `src` includes `src`,
  mypy strict runs on `src` (Makefile `typecheck` and the pre-commit
  hook), pytest `testpaths` includes `tests`, and coverage measures
  `notify` and `petrichor` against the same 90% floor.
- Any future top-level directory remains an ADR-worthy decision
  (ADR-0014); this ADR covers the product source layout only.

## Consequences

- `scripts/` returns to its single job: orchestration scripts.
- The constitutional files touched to enable this (pyproject.toml,
  Makefile, .pre-commit-config.yaml) change under explicit human
  directive in the session that resolved the PR #59 review finding.
