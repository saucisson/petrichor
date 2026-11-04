# Branch model and the autonomous day

Constitutional document (ADR-0010). Changing it requires a human decision.

## Branches

| Branch             | Purpose                             | Who merges                                    |
| ------------------ | ----------------------------------- | --------------------------------------------- |
| `main`             | The tested, releasable product      | **Human only**, after human tests             |
| `daily`            | One working day of agent work       | Platform auto-merge, on the criteria below    |
| `feat/...`, `fix/...` | One issue each, short-lived      | Platform auto-merge into `daily`, same criteria |

`main` and `daily` are both protected branches.

## The day cycle

1. **Morning directive** - the human selects and prioritizes the day's
   issues, and runs `make day-start` (tags `daily-YYYY-MM-DD` as a restore
   point).
2. **Triage gate** - each issue must carry acceptance criteria (template
   `.github/ISSUE_TEMPLATE/task.md`). No criteria → the issue is labeled
   `blocked` and skipped.
3. **Work** - one issue at a time, branch `feat/<issue>` or `fix/<issue>`
   from `daily`, small pull requests (target < 500 changed lines), rebase
   on `daily` before opening.
4. **Merge into `daily`** - platform auto-merge only when **all three**
   hold:
   - CI is green on the PR head,
   - the `reviewer` status check is green: the report contains **zero
     BLOCKING findings** (ADR-0016),
   - the change is not on the escalation list (`docs/agents/escalation.md`).
5. **End of day** - freeze merges, `make day-report`, `make smoke-test`,
   then the human tests and merges `daily` into `main`.
6. **Mutation testing** runs on `daily` at EOD (`make mutate`, workflow
   `mutation.yml`) - surviving mutants become issues (ADR-0013).

## Protections to enable

**GitHub** (`main` and `daily`):

- Require status checks: the `check` and `web` jobs from `ci.yml`
  **and** the `reviewer` job from `review.yml` (ADR-0016, ADR-0018).
- Require linear history.
- Allow auto-merge (branch settings), delete head branches on merge.
- `CODEOWNERS` reserves `docs/decisions/` and tool configuration for the
  human owner.

**GitLab**: merge checks = pipelines must succeed (the `reviewer` job is
part of merge request pipelines); "merge when pipeline succeeds" plays
the auto-merge role; protected branches for `main` and `daily` with no
agent allowed to merge directly.

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
