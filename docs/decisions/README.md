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
  references and supersedes the old one. Stale driver path references
  are the one owner-set exception (ADR-0021): they follow the code so
  projects instantiated from the template never inherit dead paths;
  the decisions themselves never change.
- Superseded ADRs are removed from the repository; ADR numbers are
  never reused.
- **Numbering**: ADR numbers `0001`-`0099` are reserved for the
  template's own decisions (currently 0001-0021). A project
  instantiated from this template numbers its own, project-specific
  ADRs from `0100` upward, so a template update can add new template
  ADRs without ever colliding with the project's.
- ADRs are constitutional files: agents may **draft** a new ADR inside
  a pull request, but only the human owner accepts it. Draft status is
  explicit at the top of the file.
- "ADR-worthy" changes are on the strict escalation list
  (`docs/agents/escalation.md`).
- The ADRs are **language- and tool-agnostic**: they record the ideas.
  The concrete tools of an instance (its language, linter, type
  checker, test runner) apply the ideas; the template's own Python
  tools are the worked example.

## Index

| ADR      | Decision                                                      |
| -------- | ------------------------------------------------------------- |
| [0001](adr-0001-toolchain.md) | Toolchain: latest stable versions, community-standard tools, committed lockfile |
| [0002](adr-0002-onion-data-first-architecture.md) | Onion data-first architecture |
| [0003](adr-0003-data-oriented-style.md) | Data-oriented style: validated data and functions over OOP |
| [0004](adr-0004-readability-first-style.md) | Readability-first style: named parameters, no abbreviations, linear code |
| [0005](adr-0005-logging-as-executable-comments.md) | Structured logging as executable comments |
| [0006](adr-0006-precise-typing.md) | Precise typing with one reference type checker |
| [0007](adr-0007-quality-gates.md) | Quality gates: one gate per failure mode, 90% coverage floor, pre-commit |
| [0008](adr-0008-security-gates.md) | Security gates: secret scan, dependency audit, security lint |
| [0009](adr-0009-branch-model.md) | Branch model main/daily/work; the agent merges into daily through the criteria-checked driver |
| [0010](adr-0010-agent-protocol.md) | Two-agent protocol with isolated local reviewer session and budgets |
| [0011](adr-0011-human-contact-channel.md) | Human contact: Discord webhook via the notify tool |
| [0012](adr-0012-anti-complacency-tests.md) | Anti-complacency tests: mutation testing and property tests |
| [0013](adr-0013-delegated-maintenance.md) | Delegated maintenance: Renovate + release-please |
| [0014](adr-0014-tools-directory.md) | A `tools/` directory - the template ships tools, not product code |
| [0015](adr-0015-agent-runtime-permissions.md) | Agent runtime permissions: the `.vibe/` profile |
| [0016](adr-0016-apis-rest-best-practices.md) | APIs: REST over HTTP/JSON with best practices |
| [0017](adr-0017-unattended-loop.md) | Unattended loop: one fresh headless session per issue, claims first |
| [0018](adr-0018-active-ci-wait.md) | Active CI wait inside the session |
| [0019](adr-0019-pull-request-body-updates.md) | Pull request body updates go through a driver |
| [0020](adr-0020-daily-integration-sync.md) | daily integrates main through a sync driver |
| [0021](adr-0021-scripts-cli-package.md) | Driver commands: one CLI package with subcommands, make stays the agent surface |
