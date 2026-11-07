# ADR-0004: Readability-first style - named parameters, no abbreviations, linear code

- Status: accepted
- Date: 2026-11-02

## Context

Owner's explicit preference: systematic named parameters, named
constants, no abbreviations, readability and comprehensibility above
compactness - "longer but linear functions beat over-decomposed code
with no reason".

## Options considered

- **Named parameters + linear-first** (chosen): call sites read like
  assignments; extraction requires a reason.
- **Full decomposition doctrine** (extract every block over a few
  lines): punishes linear code, produces a lattice of trivial helpers
  where names - not logic - carry the meaning; rejected: the name is
  a comment, and indirection is a tax.
- **Conventional positional, abbreviated style**: ambiguous call
  sites, `usr_msg`-style names; rejected.

## Decision

Enforced in `docs/architecture.md` and by the linter wherever the
language allows: named (keyword-only where supported) parameters,
except the obvious self/this exception and a single obvious
primitive; no abbreviations (allowed: `id`, `url`, `http`);
`UPPER_CASE` (or the language's constant convention) named constants;
extraction only when a block is reused at least twice or carries its
own intention; complexity and nesting budgets; docstrings on public
callables.

## Consequences

- Code is greppable and self-documenting; diffs review well.
- Longer call sites and files - accepted: vertical space is cheap,
  ambiguity is not.
- Negative: some standard-library and third-party signatures force
  positional style at the boundary; the rule applies to *our* code,
  with an explicit suppression the honest exception marker.
