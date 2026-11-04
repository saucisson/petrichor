# Architecture Decision Records

An ADR records **why** a choice was made, so it can be revisited later with
the original reasoning in hand - not to freeze it.

## Process (MADR-lite)

Each ADR is one file, `adr-NNNN-short-name.md`, containing:

- **Status**: `accepted` (or `superseded by adr-NNNN`).
- **Date**: the decision date.
- **Context**: the forces at play.
- **Options considered**: with explicit trade-offs, including the rejected
  ones and why.
- **Decision**: the chosen option.
- **Consequences**: including the negatives - every decision has some.

## Rules

- Accepted ADRs are **never edited**. A reversal is a new ADR that
  references and supersedes the old one.
- ADRs are constitutional files: agents may **draft** a new ADR inside a
  pull request, but only the human owner accepts it. Draft status is
  explicit at the top of the file.
- "ADR-worthy" changes are on the strict escalation list
  (`docs/agents/escalation.md`).

## Index

| ADR      | Decision                                                      |
| -------- | ------------------------------------------------------------- |
| [0001](adr-0001-python-3-15-and-uv.md) | Python 3.15 + uv toolchain, committed lockfile |
| [0002](adr-0002-onion-data-first-architecture.md) | Onion data-first architecture |
| [0003](adr-0003-data-oriented-style.md) | Data-oriented style: Pydantic over OOP, functions over classes |
| [0004](adr-0004-readability-first-style.md) | Readability-first style: keyword-only, no abbreviations, linear code |
| [0005](adr-0005-logging-as-executable-comments.md) | structlog logging as executable comments |
| [0006](adr-0006-precise-typing.md) | Precise typing: NewTypes, discriminated unions, exhaustive matches |
| [0007](adr-0007-mypy-reference-checker.md) | mypy as the reference type checker |
| [0008](adr-0008-quality-gates.md) | Quality gates: Ruff, pytest, coverage 90%, import-linter, pre-commit |
| [0009](adr-0009-security-gates.md) | Security gates: Ruff S rules, gitleaks, pip-audit |
| [0010](adr-0010-branch-model.md) | Branch model main/daily/work with codified autonomous merges |
| [0011](adr-0011-agent-protocol.md) | Two-agent protocol with isolated review and budgets |
| [0012](adr-0012-human-contact-channel.md) | Human contact: Discord webhook via the notify app |
| [0013](adr-0013-anti-complacency-tests.md) | Anti-complacency tests: mutation testing and property tests |
| [0014](adr-0014-hatchling-build-backend.md) | hatchling build backend |
| [0015](adr-0015-delegated-maintenance.md) | Delegated maintenance: Renovate + release-please |
| [0016](adr-0016-reviewer-launch-mechanics.md) | Reviewer launch mechanics: CI job, bundle, required status check |
| [0017](adr-0017-tools-directory.md) | A `tools/` directory for long-lived utilities |
| [0018](adr-0018-react-typescript-frontend.md) | React + TypeScript frontend under the same discipline |
| [0019](adr-0019-agent-runtime-permissions.md) | Agent runtime permissions: the `.vibe/` profile |
