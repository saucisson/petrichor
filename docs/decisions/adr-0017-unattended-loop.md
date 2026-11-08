# ADR-0017: Unattended loop - one headless session per issue

- Status: accepted
- Date: 2026-11-07

## Context

The day loop (ADR-0009) assumes a human checks in at morning and end
of day, but nothing forces the human to watch the day itself. The
wanted next step is to let the developer agent work through several
issues overnight, with nobody at the keyboard.

Three properties must hold:

- approvals cannot be answered interactively - the runtime profile
  (ADR-0015) is the only gate;
- context must not accumulate across issues (orchestration §3: one
  session per pull request - and `/clear` is an interactive gesture
  nobody can type at 3 a.m.);
- a failure in the middle of the night must cost one issue, not the
  night.

## Options considered

- **One long session working issue after issue.** Rejected: the
  context grows unboundedly - exactly the drift the per-PR reset
  exists to prevent - and a crash loses the whole night.
- **A supervisor typing `/clear` between issues.** Rejected: `/clear`
  is an operator command inside an interactive session; an outer
  process cannot drive the prompt, and a session cannot clear itself.
- **One headless session per issue, launched by a loop script**
  (chosen): programmatic mode (`vibe --prompt-file --output json
  --max-turns --time-limit`) gives a fresh context per issue by
  construction - the process boundary IS the context reset - and an
  individual crash is isolated to its issue.

A second axis: who picks the issue. Letting each session pick freely
with no guard is the retry spiral - two sessions can pick the same
issue, and a session that fails without a trace makes the next one
pick it again, all night. Chosen instead: **the session picks, and
claims before working**. The claim (a `night-claimed` label, set by
`make claim`) is the concurrency guard and the honesty record.

## Decision

- The night directive is explicit or auto-discovered. Explicit:
  `make night ISSUES="12,13,14"` - the human writes the issue list
  before leaving; agents never edit it (the morning-directive rule,
  unchanged). Auto-discovered: `make night` with no ISSUES - the loop
  lists the open, eligible, unclaimed issues, and each session picks
  one from the worklist.
- A session's FIRST act in auto-discovery is the claim:
  `make claim ISSUE=N` (`scripts/claim_issue.py`) adds the
  `night-claimed` label through the GitHub API before any work. The
  loop filters claimed issues out of every worklist, and the driver
  refuses to claim twice: two sessions never work the same issue. A
  failed attempt stays claimed - the human, never an agent, decides
  the retry in the morning.
- `scripts/night_loop.py` fetches the issues, applies the triage gate
  (skip pull requests, `blocked`, `needs-human`, `night-claimed`, and
  issues without acceptance criteria), renders the versioned prompt
  (`.vibe/prompts/night-issue.md` or `night-auto.md`), and runs ONE
  headless session per issue; it records the exit code and moves on.
  In auto-discovery it re-derives the worklist before every session
  and stops when no unclaimed issue remains, when the session budget
  (`NIGHT_MAX_SESSIONS`) is spent, or when a session claims nothing -
  the night never spins on one stuck issue. The
  loop owns the sequence; no session ever starts a second issue.
- Pull requests are opened through `scripts/open_pr.py`
  (`make pr TITLE=... BODY_FILE=...`) - the GitHub API driver, with
  the same token as the review and merge drivers - so the unattended
  flow needs no `gh` and no allowlist widening (ADR-0015).
- A denied or pending tool call is a stop signal the session escalates
  (`blocked`/`needs-human` labels, structured comment, worklog entry)
  - never an obstacle to route around. Unattended honesty is the same
  honesty, with nobody watching.
- The morning audit is unchanged: `WORKLOG.md`, the pull requests, the
  `needs-human` issues, and the claimed-but-unmerged issues tell the
  night's story (orchestration §7).

## Consequences

- Overnight autonomy with a bounded blast radius: one issue per
  session, one session per context, the day's container unchanged.
- A session that hits its turn or time budget stops honestly; the
  morning audit sees it in the loop's summary and the worklog.
- Negative: no cross-issue momentum inside one night - accepted,
  that is the point; the repository's records carry what matters.
- Negative: the token now needs issues read/write (the claim label),
  on top of pull requests read/write.
- Negative: a claimed-but-failed issue waits for a human decision -
  the label is removed by the human, never by an agent; an agent
  un-claiming its own failure is the retry spiral in disguise.
- Negative: the machine stays on, the CLI stays signed in, and CI
  must run unattended - operational requirements, stated plainly.

## Amendment 2026-11-07 - abandoned pull requests are work items too

A failed night session often leaves a pull request open: CI red,
findings unaddressed, or the turn budget spent right after the push.
Until now the auto-discovered worklist only offered unclaimed
issues, so those pull requests waited for the human even though the
night had sessions to spare.

The universe of candidates is widened: an auto-discovered session
may pick **either an unclaimed open issue or an abandoned pull
request**. A pull request is recoverable when it is open, targets
`daily`, its head branch is `feat/N` or `fix/N`, its backing issue
#N carries `night-claimed` (the mark of the earlier attempt), and
the pull request itself is not claimed by another recovery session.
The claim is uniform over both kinds: `make claim-pr PR=N`
(`scripts/claim_issue.py --pr`) labels the pull request itself before
any work, with the same guards (kind must match, no double claim).
The session then resumes it: diagnose with `make wait-ci PR=N`, a
local `make check`, and a full read of the diff and review comments;
fix; push; review; merge - the recovery contract is the developer
contract, with a `WORKLOG.md` entry naming the resumption.

Consequences of the amendment:

- the anti-spin guard now keys on items, not numbers: issues and
  pull requests share one key space, and a session that merges a
  pull request or claims any item shrinks the worklist - progress;
  a session that changes nothing stops the loop, as before;
- the explicit directive (`ISSUES=...`) is untouched: the human's
  list is the human's list - recovery is an auto-discovery concern;
- a human can hand a specific pull request to the night by labeling
  its backing issue `night-claimed` by hand - the hand-off path is
  the label, not a new protocol;
- un-claiming stays a human act, for issues and pull requests alike.
