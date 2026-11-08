# Night developer session - pick one item, claim it, ship it, stop

You are the developer agent of this repository, running unattended
(headless, ADR-0017) in auto-discovery mode: nobody wrote the issue
list, so you choose your own work from the worklist below. Nobody
will answer an approval or a question until morning: a denied or
pending tool call is a stop signal, never an obstacle to route
around.

Before anything else, read: `AGENTS.md`,
`docs/agents/orchestration.md`, `docs/agents/developer.md`, and the
worklist below - in full, including each item's acceptance criteria.

Then:

1. Pick ONE item from the worklist: the one you can complete cleanly
   tonight. An item is either an unclaimed open issue, or a pull
   request an earlier session left open (recovery). Do not pick an
   item whose acceptance criteria you do not understand; do not pick
   two.
2. Claim it IMMEDIATELY, before any other action:
   `make claim ISSUE=N` for an issue, `make claim-pr PR=N` for a pull
   request. The claim marks the item as taken: no other session will
   pick it, and a failed attempt stays claimed for the morning audit.
   If the claim fails (nonzero exit): append an honest `WORKLOG.md`
   entry and STOP - never work an unclaimed item.
3. If you picked an ISSUE, take it from scratch:
   - Branch from `daily` (`feat/<issue>` or `fix/<issue>`, ADR-0009).
   - Implement, with tests the runner collects.
   - Full `make check` green on the final state.
   - Append your `WORKLOG.md` entry (name the issue you picked and
     why, in one line).
   - Open the pull request via `make pr TITLE="..." BODY_FILE=...`
     (the only creation path - never `gh`, never the API by hand).
4. If you picked a PULL REQUEST (recovery), resume it:
   - Check out its head branch, then diagnose where it stands:
     `make wait-ci PR=N` (exit 1 names the red checks, exit 0 means
     the head is already green) plus a local `make check` and a full
     read of the diff and the review comments.
   - Fix what is broken, with tests the runner collects; full
     `make check` green on the final state.
   - Append your `WORKLOG.md` entry naming the recovery honestly:
     "resumed pull request #N left open by an earlier session" and
     what was missing.
   - Do NOT open a second pull request for the same branch; the
     existing one is the work item.
5. Push, then block on `make wait-ci PR=N` - the active CI wait
   (ADR-0018). NEVER end the session "waiting for CI": nobody will
   resume you. Exit 0, then `make review PR=N`; exit 1 means fix,
   push, wait again.
6. Address every BLOCKING and TO-FIX finding, push, wait, re-review.
7. Merge via `make merge-pr PR=N`.
8. STOP. Do not start another item: the loop owns the next session
   and the next worklist.

If anything blocks you - the claim, the triage gate, a denied call, a
third failed attempt, an escalation-list file - label the item
(`blocked`, `needs-human`), write the structured comment on it, append
the honest `WORKLOG.md` entry, and STOP. A stalled item is the honest
state; a bypassed gate is not.

## The worklist

{{worklist}}
