# ADR-0018: The active CI wait - a session never ends "waiting for CI"

- Status: accepted
- Date: 2026-11-07

## Context

Production observation, first night out: a developer session opened its
pull request and ended with "the rest of the protocol (review, merge)
will happen once CI finishes". That is attended thinking - the agent
treats the human as the scheduler who will prompt it again. A headless
session has no such scheduler: it never resumes, nobody types the next
prompt at 3 a.m., and the issue dies half-done with an open, unreviewed
pull request.

Even attended, the habit serializes the day on the human's presence:
the human becomes the CI poller.

## Options considered

- **End the session, let someone resume it later.** Rejected: an
  unattended session is never resumed (ADR-0017) - the loop runs one
  session per issue and moves on; the state left behind is a pull
  request nobody reviews.
- **Make the review driver wait internally.** Rejected:
  The `review` driver's preconditions must stay instant and mechanical
  (ADR-0010 - exit 3 means "not a verdict, not an attempt"); folding a
  poll loop into it couples the review budget to the CI budget and
  blurs what "preconditions unmet" means.
- **A dedicated blocking driver** (chosen): `make wait-ci PR=N` blocks
  until CI concludes on the pull request head, inside the session, and
  hands back a verdict the session acts on.

## Decision

- `make wait-ci PR=N` polls the head's CI check
  runs every `WAIT_CI_INTERVAL_SECONDS` (default 20) up to
  `WAIT_CI_TIMEOUT_SECONDS` (default 1800). It judges the CURRENT
  head: a re-push while waiting switches the watch to the new head.
- Exit 0: every check run concluded "success" - exactly the
  precondition the `review` subcommand enforces, so the session chains
  `make review PR=N` right after. Exit 1: CI failed on the head, or
  did not conclude within the timeout - the honest stop (fix, push,
  wait again; a timeout is an escalation, never an excuse to review
  anyway). Exit 2: protocol failure (no token, pull request
  unreachable at the first poll).
- Ending a session (or a turn) "waiting for CI" is forbidden in the
  developer contract and in both night prompts: the session blocks on
  `make wait-ci`, or it stops honestly with the state explained.
- The budgets compose: the session's time limit
  (`NIGHT_TIME_LIMIT_SECONDS`) must exceed the CI wait budget, or the
  session dies mid-wait - which is still honest, but wasteful.

## Consequences

- The session is autonomous end to end: push, wait, review, merge,
  without a human in the loop, without pretending someone will resume
  it.
- The attended flow gets the same benefit: nobody polls GitHub by
  hand or babysits a green check.
- Negative: a session spends wall-clock blocked in the wait - bounded
  by `WAIT_CI_TIMEOUT_SECONDS`, and cheaper than a dead session.
- Negative: two budgets to keep consistent
  (`WAIT_CI_TIMEOUT_SECONDS` < `NIGHT_TIME_LIMIT_SECONDS`); the night
  loop's defaults (1800 < 3600) already compose.
