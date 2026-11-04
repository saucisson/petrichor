# `src/` - your product's Python code

This directory is where the **product** - the `petrichor` project
itself - lives: the onion architecture, the quality gates and the
agent protocol all apply to it from day one (`docs/architecture.md`).

The `notify` tool that ships with the repository is **not** product
code. It lives in `tools/notify/` (ADR-0017): it is the doorbell the
coding agent rings when an issue needs the owner's interaction.

Until the first issue lands here, the honest state is an empty
`src/` - the template does not ship filler code.
