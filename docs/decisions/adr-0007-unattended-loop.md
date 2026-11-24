# ADR-0007: The unattended loop - one headless session per item

- Status: accepted
- Date: 2026-11-07 (condensed 2026-11-13)
- Merges: former ADR-0017, including its amendments of 2026-11-07
  (abandoned pull requests are work items too) and 2026-11-08
  (suffixed branch names; the loop speaks to a human)

## Context

The day loop assumes a human checks in at morning and end of day, but
nothing forces the human to watch the day itself. The wanted next
step: the developer agent works through several issues overnight with
nobody at the keyboard. Three properties must hold: approvals cannot
be answered interactively (the runtime profile is the only gate);
context must not accumulate across issues (an outer process cannot
type `/clear`); a failure at 3 a.m. must cost one issue, not the
night.

## Decision

- **One headless session per item, launched by a loop script**
  (`make night`): the process boundary IS the context reset, and an
  individual crash is isolated to its item. The loop owns the sequence;
  no session ever starts a second item.
- **Directive**: explicit (`make night ISSUES="12,13,14"` - the human's
  list, never edited by agents) or auto-discovered (`make night` with
  no ISSUES). In auto-discovery the loop derives the worklist from the
  repository - open issues and abandoned pull requests, triage gate
  applied, minus the `night-claimed` label - and each session picks
  ONE item and **claims it first**: `make claim ISSUE=N` or
  `make claim-pr PR=N`. No claim, no work. A failed attempt stays
  claimed; un-claiming is a human act, never an agent's.
- **Recovery** (auto-discovery only): an abandoned pull request is
  recoverable when it is open, targets `daily`, its head branch is
  `feat/N` or `fix/N` (an optional descriptive suffix after the number
  is tolerated - the number decides the backing issue), its backing
  issue carries `night-claimed`, and it is not claimed by another
  session. The resumption is named honestly on the pull request. A
  human can hand a pull request to the night by labeling its backing
  issue by hand.
- **Anti-spin**: the loop re-derives the worklist before every session
  and stops when no unclaimed item remains, when the session budget
  (`NIGHT_MAX_SESSIONS`) is spent, or when a session claims nothing.
- Pull requests are opened through `make pr` (never `gh`, never raw
  API writes); a denied call is a stop-and-escalate signal; a session
  never rewrites `daily` and never runs `make sync-daily`.
- **Two output channels**: stdout is the short, human-readable summary
  (one line per session, one verdict); the verbose trace lands in
  `.night/night-loop.log` (override: `NIGHT_LOG_FILE`). Session
  prompts are kept under `.night/sessions/<run>/`; a `still running`
  heartbeat logs every `NIGHT_HEARTBEAT_SECONDS` (default 60, `0`
  disables it).
- The morning audit is unchanged: the day's `docs/worklog/` entries
  (via `make day-report`), the pull requests, the `needs-human`
  issues, and every claimed-but-unmerged item tell the night's story.

## Consequences

- Overnight autonomy with a bounded blast radius: one item per session,
  one session per context, the day's container unchanged.
- The morning audit reads the terminal; the post-mortem reads the log;
  a silent session is not a dead session.
- Negative: no cross-issue momentum inside one night (accepted - that
  is the point); the token needs issues read/write (the claim label);
  the machine stays on, the CLI stays signed in, CI must run
  unattended - operational requirements, stated plainly.
