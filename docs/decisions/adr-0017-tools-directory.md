# ADR-0017: A `tools/` directory for long-lived utilities

- Status: accepted
- Date: 2026-11-03

## Context

The template shipped its contact-channel application - the `notify`
tool - inside `src/`, the directory the product is
supposed to occupy. That conflated two things: the tool is genuinely
useful (it is the ADR-0012 doorbell the coding agent rings whenever an
issue needs the owner's interaction), but it is **not** the `petrichor`
product the repository exists to build. Leaving it in
`src/` meant the first real issue would start by evicting a working
tool, and every architecture rule narrated in `docs/architecture.md`
would point at example code instead of product code.

## Options considered

1. **Keep the reference app in `src/` and let the product push it
   out.** Rejected: the eviction is busywork, the reviewer bundle then
   reviews product and utility against the same spec, and the
   directory that should start honest and empty starts occupied.
2. **Drop the tool from the template entirely.** Rejected: the
   notification channel is a working part of the agent protocol
   (ADR-0012), and it is the best living example of every style rule.
3. **Move utilities to `tools/`, keep `src/` for the product.**
   Chosen: a clear rule - "the product lives in `src/`, long-lived
   utilities live in `tools/`" - with the tool as the first occupant.

## Decision

- The repository grows a `tools/` directory for **long-lived utilities
  that are maintained like code but are not the product**. The
  notification tool moves to `tools/notify/` (code in
  `tools/notify/src/notify/`, tests in `tools/notify/tests/`,
  image recipe in `tools/notify/Dockerfile`).
- `src/` is reserved for product code and ships **empty** (a README
  stating the rule, no filler code).
- A separate `apps/` directory hosts applications with their own
  toolchain (the web app, ADR-0018); `tools/` stays single-toolchain
  and part of the root project.
- Utilities keep **every** quality gate: lint, strict types, layer
  contract, tests, mutation, review. "It's only a tool" is never an
  argument - the tool notifies the owner, so it is on the escalation
  path of trust.

## Consequences

- The root `pyproject.toml` packages the tool from its `tools/` path;
  `uv sync`, `make check`, import-linter and the console script work
  unchanged from the repository root.
- Path references become longer (`tools/notify/src/notify/`) - the
  price of an honest layout, paid once.
- New utilities follow the same shape: `tools/<name>/` with code,
  tests and a README, announced by an ADR.
- The product starts with an empty `src/` and a first real issue, not
  with example code to delete.
