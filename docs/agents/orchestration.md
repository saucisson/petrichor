# Orchestration of the autonomous day

Constitutional document. This is the loop the agent team follows between
two human check-ins - one working day. It operationalizes ADR-0009 and
ADR-0010.

## The day loop

```text
morning:  human directive → make day-start → tag daily-YYYY-MM-DD → make sync-daily (if main moved, ADR-0020)
loop:     pick next issue → develop → make review → make merge-pr into daily
EOD:      freeze merges → make day-report → make smoke-test → human tests → human merges daily → main
```

## 1. Morning directive

The human selects and prioritizes the day's issues. Agents **never edit
the directive**: priorities change only by human comment. Re-read the
directive at the start of every session - this is the goal-drift guard:
every session re-anchors to what the human actually asked for.

If `origin/main` has commits `daily` lacks (template sync, hotfix,
release), the day session runs `make sync-daily` right after
`make day-start` and before any work: the driver rebases `daily` onto
`origin/main` and pushes with `--force-with-lease`; a conflict aborts
and escalates (`needs-human`) - resolving accepted `main` content
against accepted `daily` work is a human decision (ADR-0020).

## 2. Triage gate

An issue enters the loop only with:

- acceptance criteria (the `task` template enforces them),
- an assignable branch name,
- no unresolved dependency on a `blocked` issue.

Otherwise: label `blocked`, comment why, move on.

## 3. Sequential work

One issue, one branch, one PR, one context at a time. Small pull
requests (< 500 lines). Rebase on `daily` before opening the PR. Full
`make check` before the reviewer sees anything.

The developer session is cleared after every merged pull request
(`/clear` in the Vibe Code CLI, a new session elsewhere): a context
that carries the previous issue's residue rationalizes the next one.
Nothing is lost - the day's memory lives in the repository
(`WORKLOG.md`, the pull requests, the issues), and the fresh session
re-anchors by re-reading the directive and `AGENTS.md` (§1), the
goal-drift guard.

## 4. Review and merge criteria (codified - no judgement calls)

The review is the **last** gate, never a lint pass: every finding a
local gate or a re-read of the rules could have caught is a wasted
review session. The developer agent runs `make review PR=N` (local
reviewer session, ADR-0010) only once the preconditions hold - the
first two are enforced mechanically by the review driver (`make
review`), the rest are the developer's honesty rules:

1. the pull request head is the local HEAD: work committed and pushed,
   one command at a time - never a chained `git push && make review`,
   which reviews a stale head;
2. every CI check run on that head has concluded "success" - red or
   pending CI means the review waits, it never substitutes for a
   failing local gate;
3. `make check` is green on the exact commit reviewed, not on an
   earlier one;
4. every new piece of logic ships tests the runner collects;
5. a re-read of the escalation list (`docs/agents/escalation.md`)
   against the diff shows no constitutional file touched without an
   approved escalation.

On a clean verdict the agent merges via `make merge-pr PR=N`
(ADR-0009). The driver merges only when **all three** hold:

1. CI is green on the PR head,
2. the review report contains **zero BLOCKING findings**,
3. the change is not on the escalation list (`docs/agents/escalation.md`).

The reviewer session never merges: it writes the report; the merge
driver reads it. If any criterion is unmet, the PR waits: stalled PRs
are the honest state, silent rule-bending is not.

"Pending CI means the review waits" is served by an **active** wait,
never by ending the session on a wait: `make wait-ci PR=N` (ADR-0018)
blocks until CI concludes on the head
- exit 0 green (the precondition above), exit 1 red or timed out. A
session that stops "waiting for CI" is a session nobody resumes.

## 5. Priority rule

Answered `needs-human` issues are picked up **before** any new work. A
decision the human already made is worth more than progress on a question
they have not seen.

## 6. Urgent = full stop

On an `urgent` escalation, **all merges stop** - not just the affected
issue - until the human answers. See `docs/agents/escalation.md` for the
severity contract.

## 7. End of day

1. Freeze: no new merges into `daily`.
2. `make day-report` (from the morning tag) - the human's test brief.
3. `make smoke-test` - proof the pipeline still runs.
4. Mutation workflow runs on `daily` (`make mutate`); surviving mutants
   become new issues (ADR-0012).
5. Human tests, audits `WORKLOG.md` (the day's agent-written audit
   trail - one entry per commit or pull request, none missing), then
   **merges `daily` into `main` themselves**.

A red `daily` at EOD triggers the incident protocol: revert first
(`docs/branching.md`), and if the revert does not settle it, `urgent`
escalation with merges stopped.

## 8. The unattended loop (night)

`make night` runs the day loop while nobody answers. The directive
comes in two flavors, and the night loop driver (`make night`) runs
**one fresh headless session per issue** in both (ADR-0017) - the process
boundary replaces `/clear` (§3): no issue inherits the previous one's residue,
and a session that dies at 3 a.m. costs one issue, not the night.

- **Explicit directive**: `make night ISSUES=12,13,14`. The human
  writes the issue list before leaving (the morning-directive rule:
  agents never edit it), and the loop runs one session per issue, in
  order.
- **Auto-discovery**: `make night` with no ISSUES. The loop derives
  each worklist from the repository itself - open issues, triage gate
  (§2), minus the `night-claimed` label - and each session **picks
  one item and claims it first**: `make claim ISSUE=N` for an issue,
  `make claim-pr PR=N` for a pull request (the claim driver adds the
  `night-claimed` label before any other action). The claim
  is the concurrency guard and the honesty record: two sessions never
  work the same item, and a failed attempt stays claimed - the
  morning audit sees what was tried, and the human, never an agent,
  decides the retry.
- **Recovery** (auto-discovery only): a session may pick, or be
  handed, a pull request an earlier session left open. A pull request
  is recoverable when it is open, targets `daily`, its head branch is
  `feat/N` or `fix/N` (a descriptive suffix after the number, e.g.
  `feat/2-canonical-petri-json`, is tolerated - the number decides
  the backing issue), its backing issue #N carries `night-claimed`
  (the mark of the earlier attempt), and the pull request itself is
  not claimed by another recovery session. The session claims it
  (`make claim-pr PR=N`) before touching it, diagnoses it with
  `make wait-ci PR=N` plus a local `make check` and a full read of
  the diff and review comments, fixes it, and walks the same
  review-and-merge gate as a fresh issue - the recovery contract is
  the developer contract, with an honest `WORKLOG.md` entry naming
  the resumption. A human can also hand a pull request to the night
  by labeling its backing issue `night-claimed` by hand.

- The loop applies the triage gate (§2) before each session: issues
  labeled `blocked`, `needs-human`, or `night-claimed`, pull
  requests, and issues without acceptance criteria are skipped and
  reported.
- Each session follows the full developer contract, opens its pull
  request through `make pr` (fresh issue) or resumes the existing one
  (recovery), and **stops after one item** - the loop, never the
  session, owns the next one and the next worklist.
- In auto-discovery the loop re-derives the worklist before every
  session and stops when no unclaimed item remains, when the session
  budget (`NIGHT_MAX_SESSIONS`) is spent, or when a session claims
  nothing - the night never spins on one stuck item.
- Nobody answers approvals: allowlisted commands run (ADR-0015), and
  a denied call is a stop-and-escalate signal, never an obstacle to
  route around.
- A session never ends on a wait: CI is waited for **actively**, with
  `make wait-ci PR=N` (ADR-0018), inside the session's time budget -
  "I will continue once CI finishes" is the attended reflex of a
  session nobody will resume.
- A night session never rewrites `daily`: `make sync-daily` is a
  day-session action (ADR-0020), and rewriting the branch the loop
  derives its worklist from mid-flight, with nobody to answer a
  conflict, is out of the unattended flow entirely.
- A size-budget BLOCKING is resolved by a split proposal written into
  the pull request body with `make update-pr PR=N BODY_FILE=...`
  (ADR-0019 - the only body-update path; it replaces the whole body,
  so the session composes the existing body plus the proposal first).
  Raw API writes are denied unattended, and that is the allowlist
  working - the driver is the answer, not a bypass.
- The loop speaks to a human on stdout: one line per session and one
  verdict at the end. The verbose trace - full candidate lists,
  anti-spin decisions, fetch warnings - lands in
  `.night/night-loop.log` (override: `NIGHT_LOG_FILE`, ADR-0017).
- The morning audit is unchanged and is the whole point: `WORKLOG.md`,
  the pull requests, every `needs-human` issue, and every
  `night-claimed` issue tell the night's story (§7).
