# ADR-0001: Toolchain - latest stable versions, community-standard tools

- Status: accepted
- Date: 2026-11-02

## Context

Each instance of this template picks its own language; the template
itself must not hard-code one. What it does mandate: reproducible
environments (agents install dependencies dozens of times per day, and
any drift between environments produces phantom failures that cost
review cycles), current tooling (old versions carry known bugs and
missing safety checks), and tool choices that any practitioner of the
language recognizes on sight.

## Options considered

- **Latest stable language version + the community's standard package
  manager and build tool, with a committed lockfile** (chosen): the
  standard tools have the best documentation, the best agent
  familiarity, and the ecosystem's full support; the lockfile makes
  every environment identical.
- **Bleeding-edge (pre-release) versions**: newest features, but
  agents hallucinate against unstable APIs and the ecosystem lags -
  rejected: stability beats novelty.
- **Whatever is preinstalled / legacy tooling**: zero setup, but
  unversioned environments drift silently - rejected for
  reproducibility.
- **Exotic or hand-rolled build tooling**: rejected: every future
  reader (human or agent) pays an unfamiliarity tax for no gain.

## Decision

- The instance uses the **latest stable version** of its language and
  of every tool in the chain.
- Package management, running, and building go through **the tools
  the language's community recommends as the de facto standard** for
  new projects; one tool per job, no parallel mechanisms.
- The dependency lockfile is **committed**; environments are installed
  only through the lockfile (agents never improvise installs -
  hallucinated installs are a known agent failure mode).
- Dependency groups live in the language's standard manifest, not in
  legacy side files.
- For the Python code shipped with this template (the `notify` tool,
  the `scripts/`), the standard choices are uv for sync/run/lock and
  hatchling for the build backend - the concrete application of this
  ADR, swappable per instance.

## Consequences

- Fast, identical environments everywhere; every command an agent types
  daily is documented by the community, not by this repository.
- Tool migrations are rare, deliberate, and prompted by the community
  shifting its standard - never by fashion.
- Negative: the community's standard changes over time (it has
  before); accepting that is cheaper than betting on a niche tool that
  may never mature.
