# Branch model and the autonomous day

Constitutional document (ADR-0009). Changing it requires a human decision.

## Branches

| Branch             | Purpose                             | Who merges                                    |
| ------------------ | ----------------------------------- | --------------------------------------------- |
| `main`             | The tested, releasable product      | **Human only**, after human tests             |
| `daily`            | One working day of agent work       | Developer agent, via `make merge-pr` (ADR-0009), on the criteria below |
| `feat/...`, `fix/...` | One issue each, short-lived      | Same driver, same criteria, into `daily`      |

`main` and `daily` are both protected branches.

## The day cycle

1. **Morning directive** - the human selects and prioritizes the day's
   issues, and runs `make day-start` (tags `daily-YYYY-MM-DD` as a restore
   point). If `origin/main` has commits `daily` lacks (template sync,
   hotfix, release), the day session then runs `make sync-daily`
   (ADR-0020) before any work starts.
2. **Triage gate** - each issue must carry acceptance criteria (template
   `.github/ISSUE_TEMPLATE/task.md`). No criteria → the issue is labeled
   `blocked` and skipped.
3. **Work** - one issue at a time, branch `feat/<issue>` or `fix/<issue>`
   from `daily`, small pull requests (target < 500 changed lines), rebase
   on `daily` before opening.
4. **Review and merge into `daily`** - once CI is green on the pushed
   head, the developer agent runs
   `make review PR=N` (local reviewer session, ADR-0010), then merges
   via `make merge-pr PR=N` (ADR-0009). The driver merges only when
   **all three** hold:
   - CI is green on the PR head,
   - the review report contains **zero BLOCKING findings**
     (ADR-0010),
   - the change is not on the escalation list (`docs/agents/escalation.md`).
5. **End of day** - freeze merges, `make day-report`, `make smoke-test`,
   then the human tests, audits `WORKLOG.md`, and merges `daily` into
   `main`.
6. **Mutation testing** runs on `daily` at EOD (`make mutate`, workflow
   `mutation.yml`) - surviving mutants become issues (ADR-0012).

## Integrating main into daily (ADR-0020)

`main` moves without `daily` when the human syncs the template, hotfixes,
or releases. The day session reconciles the branches itself, early in the
day (right after `make day-start`, before opening pull requests):

- `make sync-daily` rebases `daily` onto `origin/main` and pushes the
  rewritten `daily` with `--force-with-lease` (ADR-0020). A strictly
  behind `daily` is fast-forwarded instead - no rewrite, no force.
- A rebase conflict aborts and escalates (`needs-human`): resolving a
  conflict between accepted `main` content and accepted `daily` work
  is a human decision, never an unattended merge.
- `main` is never rewritten. Night sessions never run the sync: the
  unattended loop derives its worklist from `daily` and nobody answers
  a conflict at 3 a.m. (ADR-0017, ADR-0020).

## Protections to enable

**GitHub** (`main` and `daily`):

- Require status checks: the `check` and `security` jobs from
  `ci.yml`. There is no `reviewer` status check: the review runs
  locally (ADR-0010) and the merge driver enforces its verdict
  (ADR-0009).
- Require linear history (the merge driver squash-merges, or
  rebase-merges when squash is disabled).
- Allow force pushes on `daily` only (the sync driver pushes
  `--force-with-lease`, ADR-0020); keep them forbidden on `main`.
- Delete head branches on merge (the driver also deletes them).
- `CODEOWNERS` reserves `docs/decisions/` and tool configuration for the
  human owner.

**GitLab**: merge checks = pipelines must succeed; "merge when pipeline
succeeds" plays the merge role (the merge driver targets the GitHub
API); protected branches for `main` and `daily` with no agent allowed
to merge directly.

## Incident protocol

When `daily` is red: **revert first, debug second**.

1. Revert the offending merge commit (never pile a fix on a red branch).
2. Reopen the original issue with the failure evidence.
3. The issue keeps its attempt budget: after **3 failed attempts**, it is
   labeled `blocked` and escalated (`needs-human`).

An incident that cannot be resolved by revert is an **urgent** escalation:
all merges stop until the human answers (see `docs/agents/escalation.md`).

## Why this shape

`daily` exists so that agent autonomy has a blast radius of exactly one
day. `main` exists so that a human always stands between "the agents
believe it works" and "the product is updated". The merge criteria are
codified - not vibes - so both agents can check them mechanically.
The one sanctioned rewrite of `daily` (`make sync-daily`, ADR-0020)
integrates already-accepted `main` content early in the day; it never
touches `main` itself, so the human merge stays the only path in.
