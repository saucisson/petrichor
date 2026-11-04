# ADR-0004: Readability-first style - keyword-only, no abbreviations, linear code

- Status: accepted
- Date: 2026-11-02

## Context

Owner's explicit preference: systematic named parameters, named constants,
no abbreviations, readability and comprehensibility above compactness -
"longer but linear functions beat over-decomposed code with no reason".

## Options considered

- **Keyword-only + linear-first** (chosen): call sites read like
  assignments; extraction requires a reason.
- **Full decomposition doctrine** (extract every block > 5 lines):
  punishes linear code, produces a lattice of trivial helpers where names
  - not logic - carry the meaning; rejected: the name is a comment, and
  indirection is a tax.
- **Conventional Python (positional, abbreviated)**: call sites ambiguous,
  `usr_msg`-style names; rejected.

## Decision

Enforced in `docs/architecture.md` and by Ruff: keyword-only parameters
(exceptions: `self`, `cls`, single obvious primitive); no abbreviations
(allowed: `id`, `url`, `http`); `UPPER_CASE` constants; extraction only
when a block is reused ≥ 2× or carries its own intention; complexity ≤ 10;
nesting ≤ 4; Google-style docstrings.

## Consequences

- Code is greppable and self-documenting; diffs review well.
- Longer call sites and files - accepted: vertical space is cheap,
  ambiguity is not.
- Negative: some standard-library and third-party signatures force
  positional style at the boundary; the rule applies to *our* code, with
  `# noqa` the honest exception marker.
