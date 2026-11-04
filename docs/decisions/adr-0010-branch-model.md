# ADR-0010: Branch model - main/daily/work with codified autonomous merges

- Status: accepted
- Date: 2026-11-02

## Context

Requirement: agents must process and merge issues **without human
intervention during a working day**, while the human keeps meaningful
control. Pure trunk-based development removes the control point; classic
gitflow never lets agents merge anything. The blast radius of agent
autonomy needs a bounded container.

## Options considered

- **main / daily / feat|fix per issue** (chosen): a day is the autonomy
  unit; the human guards the only exit.
- **Trunk-based with feature flags**: agents merge straight to main -
  control becomes retroactive, rejected.
- **Per-PR human approval**: contradicts the autonomy requirement.
- **Long-lived develop branch**: the unit of autonomy becomes unbounded -
  exactly what a day branch bounds.

## Decision

`main` (human merges only, after human tests) / `daily` (the day's
container, tagged each morning as a restore point) / `feat|fix/<issue>`
(one issue per branch). Auto-merge into `daily` fires only on the three
codified criteria: CI green, zero BLOCKING reviewer findings, not on the
escalation list. Incident protocol: **revert first, debug second**, with a
3-attempt budget per issue. Details in `docs/branching.md`.

## Consequences

- Agents work autonomously with a bounded blast radius (one day); the
  human's control is structural, not per-PR.
- The merge decision is a machine-checkable expression, not agent
  judgement - the core of honest autonomy.
- Negative: one merge boundary per day means a red morning can cost a
  day; the revert-first protocol and the daily tag exist precisely to
  make that recovery mechanical.
