# ADR-0009: Branch model - main/daily/work with agent-executed merges

- Status: accepted
- Date: 2026-11-06

## Context

Requirement: agents must process and merge issues **without human
intervention during a working day**, while the human keeps meaningful
control. Pure trunk-based development removes the control point;
classic gitflow never lets agents merge anything. The blast radius of
agent autonomy needs a bounded container. The whole loop - develop,
review, merge - runs on the machine of a signed-in coding agent whose
plan includes the work; waiting for platform auto-merge polling would
add hours to a step the agent has everything needed to perform. The
human boundary of the protocol is the day (`daily` into `main`), not
the pull request: the human tests at the end of day, on `daily`.

## Options considered

- **main / daily / feat|fix per issue, the agent merges through a
  criteria-checked driver** (chosen): a day is the autonomy unit; the
  human guards the only exit; the merge is a scripted, criteria-gated
  action instead of a judgement call.
- **Trunk-based with feature flags**: agents merge straight to main -
  control becomes retroactive, rejected.
- **Per-PR human approval**: contradicts the autonomy requirement.
- **Long-lived develop branch**: the unit of autonomy becomes
  unbounded - exactly what a day branch bounds.
- **Platform auto-merge gated only on CI**: the review verdict would
  be enforced by nothing; silent rule-bending becomes possible -
  rejected.
- **The human merges every pull request**: the day loop stalls on the
  human for hours per PR, for a decision the criteria already encode -
  rejected: the human's leverage is the end-of-day merge into `main`.

## Decision

- `main` (human merges only, after human tests) / `daily` (the day's
  container, tagged each morning as a restore point) /
  `feat|fix/<issue>` (one issue per branch).
- After a clean review and green CI, the developer agent merges each
  pull request into `daily` through the merge driver
  (`make merge-pr`, `python -m scripts merge-pr`). This is the merge path;
  there is no other agent path, and no platform auto-merge.
- The driver refuses to merge unless ALL hold: the review report
  exists with zero BLOCKING findings, it was written for THIS pull
  request and its CURRENT head (the review context: PR number and
  head SHA must both match, so a stale report is never a merge
  ticket), the pull request is open and cleanly mergeable, and every
  CI check on its head has concluded "success". It then merges
  through the platform API (`sha` pinned to the reviewed head) with
  the linear method the repository allows - squash preferred, a
  rebase merge as the fallback when squash is disabled; a merge
  commit is never issued - and deletes the branch. Linear history on
  `daily` is preserved.
- Incident protocol: **revert first, debug second**, with a 3-attempt
  budget per issue. Details in `docs/branching.md`.
- Branch protections on `daily` stay as the platform backstop; the
  script's criteria sit in front of the platform's own. On the
  GitLab mirror, "merge when pipeline succeeds" plays the merge role.

## Consequences

- Agents work autonomously with a bounded blast radius (one day); the
  human's control is structural, not per-PR.
- The merge decision is a machine-checkable expression, not agent
  judgement - the core of honest autonomy. The day loop integrates
  each pull request minutes after its review instead of hours.
- The platform no longer machine-checks the verdict pre-merge: the
  enforcement is the driver script plus the honesty rules. A push
  bypassing the script is possible in principle, exactly as any push
  is; it would be a constitutional violation, visible in the history,
  and the end-of-day human review of `daily` is the backstop.
- The merge is auditable: the review comment on the pull request and
  the squash commit carry the trace of what was reviewed before
  merging.
- Negative: one merge boundary per day means a red morning can cost a
  day; the revert-first protocol and the daily tag exist precisely to
  make that recovery mechanical. The criteria include one soft item
  the script cannot check - "not on the escalation list" - that check
  stays with the agent and the honesty rules.
