# ADR-0015: Delegated maintenance - Renovate + release-please

- Status: accepted
- Date: 2026-11-02

## Context

The project must not rot while agents develop autonomously. Dependency
maintenance and release management are the two chores that are pure
overhead for the human and yet are consequential decisions (ADR-0011
escalation list): the answer is delegation with graded human oversight,
not abandonment or full automation.

## Options considered

- **Renovate (dependencies) + release-please (releases)** (chosen):
  Renovate's grouped, scheduled updates keep PR noise sustainable and
  its automerge policy can encode our severity split; release-please
  derives semver releases from Conventional Commits - the discipline we
  already enforce.
- **Dependabot**: one PR per dependency, no grouping, no schedule
  control - drowns a one-human review capacity.
- **Manual maintenance**: the human becomes the bottleneck for exactly
  the work agents cannot decide alone.
- **Full automerge of everything**: contradicts the escalation list -
  major bumps change behavior, and behavior changes are human decisions.

## Decision

- **Renovate**: weekly schedule, grouped updates; patch automerge when
  CI is green; major bumps open a PR labeled `needs-human` - they are
  escalation items, not chores.
- **release-please**: on `main` only, releases derived from Conventional
  Commit messages; the human merging `daily` → `main` is implicitly the
  release decision.

## Consequences

- The project stays current without human busywork; the human sees
  dependency PRs as a weekly digest, not a firehose.
- Releases are mechanical, honest semver traceable to commits.
- Negative: two platform apps to enable; on the GitLab mirror the same
  policy maps to Renovate's GitLab support and release tooling is a
  documented follow-up, not a template blocker.
