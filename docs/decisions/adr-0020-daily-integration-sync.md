# ADR-0020: daily integrates main through a sync driver

- Status: accepted
- Date: 2026-11-08

## Context

The merge flow is one-directional (ADR-0009): work lands in `daily`
through reviewed pull requests, and the human merges `daily` into
`main` at the end of the day. But `main` can also move WITHOUT passing
through `daily`: a template sync the human commits directly on
`main`, a hotfix, a release. The branches then diverge, and the
protocol had no sanctioned way to reconcile them:

- merging `main` into `daily` writes a merge commit, which required
  linear history forbids on `daily` - and GitHub would refuse it
  anyway; the conflict resolution would live inside that merge commit,
  invisible to the review gate;
- rebasing `daily` onto `main` rewrites a protected branch: a force
  push, admin-only under the protections, correctly outside what an
  agent session may do by hand.

The first real case (an instance repository) blocked a whole day: a
constitutional template sync landed on `main`, `daily` could not take
it, and recovery work targeting a stale `daily` stalled until the
human performed the rebase by hand.

## Options considered

- **Merge `main` into `daily`.** Rejected: the merge commit violates
  required linear history on `daily`; worse, any conflict resolution
  would hide inside that commit, away from the review that every
  other change to accepted work goes through.
- **The human performs every sync.** Rejected: every `main`-only
  commit (template sync, hotfix, release) would block all agent work
  on a day session until the human is available - while `main`'s
  content is, by definition, already human-accepted.
- **An agent rebase through a fail-closed driver** (chosen):
  `make sync-daily` makes the rebase the one
  sanctioned rewrite of `daily`, the way `make update-pr` is the one
  body-update path (ADR-0019): mechanical guards first, verify the
  result, then push with a lease.

## Decision

- A **day session** (attended) may rebase `daily` onto `origin/main`
  via `make sync-daily`, early in the day - right after
  `make day-start`, before opening pull requests.
- The driver refuses anything but a clean `daily` checkout; exits 0
  without writing anything when `daily` already contains
  `origin/main`; fast-forwards (a normal push, no rewrite) when
  `daily` is strictly behind; otherwise rebases onto `origin/main`,
  verifies that the replayed `daily` contains `origin/main` and the
  same number of daily-only commits, and pushes with
  `--force-with-lease`.
- A rebase conflict aborts the rebase and refuses (exit 1):
  resolving accepted `main` content against accepted `daily` work is
  a human decision - escalate it (`needs-human`), never merge it by
  hand.
- `main` is never rewritten. Unattended (night) sessions never run
  the sync: nobody answers a conflict at 3 a.m., and rewriting the
  branch the loop derives its worklist from mid-flight is out.

## Consequences

- **Positive**: `main`-only commits stop blocking the day; the sync is
  one idempotent command with mechanical guards; `--force-with-lease`
  makes a concurrent merge lose the race loudly instead of silently
  overwriting it.
- **Positive**: the audit direction is unchanged - nothing lands in
  `main` except through the human's end-of-day merge; the sync only
  integrates already-accepted content into `daily`.
- **Negative**: when the sync rewrites `daily`, its commit SHAs
  change, so the end-of-day report since the morning tag also lists
  the replayed `main` commits - the human audits the counts - and
  local branches based on the old `daily` need a rebase before their
  pull request.
- **Protection change** (human, by hand): force pushes become allowed
  on `daily` - and stay forbidden on `main`. Required linear history
  stays on both.
