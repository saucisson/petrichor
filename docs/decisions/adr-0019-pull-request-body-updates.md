# ADR-0019: Pull request body updates go through a driver

- Status: accepted
- Date: 2026-11-08

## Context

The review checklist resolves an over-budget pull request (over ~500
changed lines) with a split proposal written into the pull request
body. The protocol demands that write but provides no tool for it:
`make pr` creates a pull request and never updates one, the review
driver posts comments, and the worklog lives in the repository.

An unattended night session in an instance repository hit exactly
this gap: the code was green, the only remaining BLOCKING was the
size budget, and the sanctioned resolution - a split proposal in the
pull request body - required a raw GitHub API write. The runtime
profile denied it, correctly: the allowlist is the only gate
(ADR-0015), and a denied call is a stop signal, never an obstacle to
route around. The session escalated honestly and the item stalled
for a human decision. The protocol's own required resolution path
had no allowlisted tool: every big pull request would stall at the
same place, every night.

## Options considered

- **Allow raw API writes on pull requests unattended.** Rejected:
  widening the allowlist to arbitrary HTTP is the opposite of the
  runtime-profile decision (ADR-0015) - the gate that stopped the
  session is the gate working.
- **Let the human write every split proposal.** Rejected: it makes
  the unattended loop structurally unable to finish any over-budget
  pull request, and the checklist demands the proposal before the
  merge, not after a human wake-up.
- **A dedicated driver, the only body-update path** (chosen):
  `make update-pr PR=N BODY_FILE=...` (`scripts/update_pr.py`)
  mirrors `make pr` for updates: same token as the other drivers,
  fail-closed guards, one purpose.

## Decision

- `scripts/update_pr.py` (`make update-pr PR=N BODY_FILE=...`) is the
  only path that updates a pull request body, attended or
  unattended. It REPLACES the whole body with the file's content -
  it never appends - so the caller composes the new body (the
  existing body plus the change, e.g. the split proposal) into the
  file first.
- Guards, fail-closed: the item must be an OPEN pull request
  (refusing an issue or a closed pull request is criteria unmet,
  exit 1); the body file must exist and be non-empty; a failed
  write leaves the body unchanged and reports a protocol failure
  (exit 2).

## Consequences

- The size-budget BLOCKING now has an end-to-end unattended
  resolution: fix, review, propose the split in the body, re-review,
  merge.
- The allowlist needs no raw HTTP: the driver is a fixed make
  target, like `make pr` and `make claim-pr`.
- Negative: another driver to maintain, and the replace semantics
  put the composition burden on the caller - a session that forgets
  to include the existing body loses it; the driver says so in its
  success message.
- The escalation path stays open: a denied `make update-pr` call
  remains a stop-and-escalate signal, like any other.
