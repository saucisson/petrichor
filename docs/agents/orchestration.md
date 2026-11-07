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

One issue, one branch, one PR at a time. Small pull requests (< 500 lines).
Rebase on `daily` before opening the PR. Full `make check` before the
reviewer sees anything.

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
