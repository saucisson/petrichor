# Orchestration of the autonomous day

Constitutional document. This is the loop the agent team follows between
two human check-ins - one working day. It operationalizes ADR-0009 and
ADR-0010.

## The day loop

```text
morning:  human directive → make day-start → tag daily-YYYY-MM-DD
loop:     pick next issue → develop → make review → make merge-pr into daily
EOD:      freeze merges → make day-report → make smoke-test → human tests → human merges daily → main
```

## 1. Morning directive

The human selects and prioritizes the day's issues. Agents **never edit
the directive**: priorities change only by human comment. Re-read the
directive at the start of every session - this is the goal-drift guard:
every session re-anchors to what the human actually asked for.

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
first two are enforced mechanically by `scripts/pr_review.py`, the
rest are the developer's honesty rules:

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
comes in two flavors, and `scripts/night_loop.py` runs **one fresh
headless session per issue** in both (ADR-0017) - the process boundary
replaces `/clear` (§3): no issue inherits the previous one's residue,
and a session that dies at 3 a.m. costs one issue, not the night.

- **Explicit directive**: `make night ISSUES=12,13,14`. The human
  writes the issue list before leaving (the morning-directive rule:
  agents never edit it), and the loop runs one session per issue, in
  order.
- **Auto-discovery**: `make night` with no ISSUES. The loop derives
  each worklist from the repository itself - open issues, triage gate
  (§2), minus the `night-claimed` label - and each session **picks
  one issue and claims it first**: `make claim ISSUE=N`
  (`scripts/claim_issue.py`) adds the `night-claimed` label before any
  other action. The claim is the concurrency guard and the honesty
  record: two sessions never work the same issue, and a failed
  attempt stays claimed - the morning audit sees what was tried, and
  the human, never an agent, decides the retry.

- The loop applies the triage gate (§2) before each session: issues
  labeled `blocked`, `needs-human`, or `night-claimed`, pull
  requests, and issues without acceptance criteria are skipped and
  reported.
- Each session follows the full developer contract, opens its pull
  request through `make pr`, and **stops after one issue** - the loop,
  never the session, owns the next one and the next worklist.
- In auto-discovery the loop re-derives the worklist before every
  session and stops when no unclaimed issue remains, when the session
  budget (`NIGHT_MAX_SESSIONS`) is spent, or when a session claims
  nothing - the night never spins on one stuck issue.
- Nobody answers approvals: allowlisted commands run (ADR-0015), and
  a denied call is a stop-and-escalate signal, never an obstacle to
  route around.
- The morning audit is unchanged and is the whole point: `WORKLOG.md`,
  the pull requests, every `needs-human` issue, and every
  `night-claimed` issue tell the night's story (§7).
