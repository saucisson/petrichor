# ADR-0009: Security gates - Ruff S rules, gitleaks, pip-audit

- Status: accepted
- Date: 2026-11-02

## Context

AI agents introduce a specific supply-chain risk: hallucinated package
names that resolve to attacker-squatted packages, plus the classic risk of
committed secrets when an agent "fixes" a failing integration by
hard-coding a token. Both are documented failure modes of agentic
development, and both are mechanically detectable.

## Options considered

- **Defense in depth: lint rules + secret scan + dependency audit** in both
  CI and pre-commit (chosen).
- **Perimeter tooling only (CI, not local)**: an agent can make 50 commits
  before CI sees the first one; secrets leak at commit time, not at push
  time - local gates are the leak's first line.
- **Trust the review agent**: reviewers miss what scanners cannot - a
  secret is invisible in a diff to a model that has seen thousands.

## Decision

- Ruff `S` (bandit-equivalent security) rules in the default gate.
- **gitleaks** in pre-commit and CI: no secret ever lands on a branch.
- **pip-audit** on the resolved `uv.lock` in CI: known CVEs in the exact
  installed graph, not the declared one.
- New dependencies are on the strict escalation list (ADR-0011): an agent
  adding a package without a human decision is a constitutional violation,
  regardless of scan results.

## Consequences

- The two agent-specific risks (typosquatting, secret leakage) are
  covered by tools that never tire and never get convinced.
- Negative: false positives from secret scanners occasionally flag test
  fixtures; the template uses obvious fake values and documented
  per-line `nosecret` exceptions only with a justification comment.
