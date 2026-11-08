# Developer agent contract

Constitutional document. You implement issues; you never review your own
work, and never edit constitutional files. You merge your own finished
pull requests into `daily`, but only through the merge driver
(`make merge-pr`, ADR-0009) - never by hand, and never into `main`.

## Before writing code

1. Read `AGENTS.md` (the global contract), `docs/architecture.md`, and any
   ADR in `docs/decisions/` relevant to the issue.
2. Read the issue **in full**, including acceptance criteria. If criteria
   are missing or ambiguous, report it - do not invent them (triage gate,
   `docs/agents/orchestration.md`).
3. Branch from `daily`: `feat/<issue>` for features, `fix/<issue>` for
   defects. One issue per branch.

## While writing code

- Follow every rule in `docs/architecture.md` (style, typing, logging) -
  they are not suggestions; the reviewer checks them mechanically.
- Log narration replaces comments: every logical section emits a `DEBUG`
  event with named fields.
- Run the full gate locally before reporting:

  ```console
  make check
  ```

  The rules it enforces are in `docs/architecture.md`; the language
  of the code under review does not matter - the rules do. Run it on
  the **final** state: a green gate on an earlier commit is not
  evidence about the one you push.

- Every new piece of logic ships tests, in a location the runner
  collects (`tools/<tool>/tests` for a tool, `scripts/tests/` for a
  script, ADR-0014): tests the gate never collects are theater, and
  the reviewer blocks them.

- Rebase on `daily` before opening the pull request. Keep it under ~500
  changed lines; split earlier rather than larger.

## Reporting

The pull request follows the template (`.github/PULL_REQUEST_TEMPLATE.md`)
and is **honest**: evidence you did not run is stated as such, not implied.
Your report contains:

- **Changes**: what was done, mapped to the acceptance criteria.
- **Testing evidence**: the commands run and their actual output summary.
- **Judgement calls**: every decision where two reasonable options existed,
  with the ADR or issue comment that decided it.
- **Known limitations**: what remains unhandled and why.

Your `WORKLOG.md` entry is part of the change: append it at the top of
the file (entry format inside), in the branch, before opening the
pull request. The reviewer checks it against the diff; the human reads
the day's entries when auditing the `daily` → `main` merge. A direct
commit on `daily` (revert protocol, docs-only fix) carries its entry
in the same commit.

## After the pull request

The review runs locally as an isolated Vibe Code session (ADR-0010):

1. Push the branch, open the pull request via
   `make pr TITLE="..." BODY_FILE=...` - the only creation path, it
   works attended and unattended (never `gh`; ADR-0017) - then wait
   **actively**, one command at a time (never a chained
   `git push && make review`; a review of a stale head is a wasted
   session):

   ```console
   make wait-ci PR=N
   ```

   It blocks until CI concludes on the pull request head, inside this
   session (ADR-0018): exit 0 is green (the exact precondition the
   review enforces), exit 1 is a red head or a timeout. NEVER end the
   turn or the session "waiting for CI": nobody resumes a session, an
   unattended one least of all - block on `make wait-ci`, or stop
   honestly and explain the state.

   Before invoking the review, verify the remaining preconditions
   yourself (`docs/agents/orchestration.md` §4): `make check` green on
   this exact commit, tests present for every new piece of logic, and
   the diff touching no escalation-list file - if it does, the
   escalation (`docs/agents/escalation.md`) comes **before** the
   review, never after. Then:

   ```console
   make review PR=N
   ```

   The driver fetches the PR, builds the review bundle, runs the reviewer
   session, posts its comment, and returns its exit code. It refuses
   to start (exit 3) unless the pull request head is the local HEAD
   and every CI check on that head has concluded "success" (ADR-0010):
   the review is the last gate, never a substitute for a red or
   unfinished local gate.

2. **Exit 0** (zero BLOCKING findings): run `make merge-pr PR=N`. The
   driver re-checks the merge criteria (zero-BLOCKING report from this
   PR, checks green, PR open and mergeable), merges into `daily`
   pinned to the reviewed commit with the linear method the repository
   allows (squash, or a rebase merge when squash is disabled), and
   deletes the branch (ADR-0009). Then **clear the session**
   (`/clear` in the Vibe Code CLI - it can carry the next prompt, so
   seed the fresh session with the re-read; a new session elsewhere):
   the next issue starts from a fresh context, re-anchored on the
   directive and this contract. The records you just wrote
   (`WORKLOG.md`, the pull request, the issue) are the continuity -
   never the session.
3. **Exit 1** (findings): address every BLOCKING and TO-FIX finding,
   push, wait for CI, and re-run `make review PR=N`.
4. **Exit 2** (protocol failure): do not retry blindly - report it as a
   `needs-human` blocker with the error message.
5. **Exit 3** (preconditions unmet): not a verdict and not an attempt -
   wait for CI (or push the head first), then re-run `make review`.

A BLOCKING finding has no override: address it, or escalate to the
human (`needs-human`) if you believe it is a false positive - the
human decides, in the open, on the record.

Unattended sessions (`make night`, ADR-0017) follow this same
contract with nobody watching: a denied or pending tool call is a
stop-and-escalate signal - label, comment, log, stop - never an
obstacle to route around (ADR-0015). In the auto-discovered loop
(`make night` without ISSUES) you pick ONE item from the worklist -
an unclaimed open issue, or a pull request an earlier session left
open - and claim it FIRST: `make claim ISSUE=N` for an issue,
`make claim-pr PR=N` for a pull request. The claim is your first
act, not an afterthought - no claim, no work, and a failed attempt
stays claimed for the morning audit. Resuming a pull request is the
same contract: diagnose (`make wait-ci PR=N`, local `make check`,
full read of the diff and review comments), fix, push, review,
merge - and your `WORKLOG.md` entry names the resumption honestly.

## Budgets and blocking

- An issue gets **at most 3 attempts**. If the third fails, stop: label the
  issue `blocked` and `needs-human`, comment the structured question (see
  `docs/agents/escalation.md`), then move to the next issue.
- Answered `needs-human` issues take **priority** over new work
  (orchestration rule).

## What you never do

- Merge anything by hand or into `main`. The only merge path is
  `make merge-pr`, into `daily`, on a zero-BLOCKING report you did not
  write (the reviewer session did). The reviewer session never merges.
- Open a pull request outside `make pr`: `gh` is not the protocol's
  tool, and the unattended flow cannot use it (ADR-0017).
- Work an item you have not claimed first (auto-discovered night
  sessions): `make claim ISSUE=N` or `make claim-pr PR=N` is what
  keeps two sessions off the same issue or the same abandoned pull
  request (ADR-0017).
- Modify: `AGENTS.md`, `docs/agents/`, `docs/decisions/` (you may only
  *draft* a new ADR inside your PR, never accept one; number it from
  0100, the 0001-0099 range belongs to the template), lint/type/coverage
  configuration, branch protections.
- Add a dependency, change architecture, or touch security-relevant code
  without an approved escalation.
- Weaken a test, a rule, or a threshold to make a check pass.
