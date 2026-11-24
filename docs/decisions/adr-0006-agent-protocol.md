# ADR-0006: Agent protocol - two roles, isolated local review

- Status: accepted
- Date: 2026-11-06 (condensed 2026-11-13)
- Merges: former ADR-0010 (agent protocol), ADR-0011 (human contact
  channel), ADR-0015 (agent runtime permissions), ADR-0018 (active CI
  wait)

## Context

One agent developing and reviewing itself degenerates into correlated
judgment: the reviewer inherits the author's blind spots and
rationalizes them. A pay-per-token API reviewer re-sends the whole
rule corpus per review; the project's coding agent (Vibe Code) runs
locally on the plan's included usage - the same review as a headless
local session costs no API tokens. Agents must also reach the owner
with a notification that actually pushes, with a severity contract.
In practice, approval prompts froze the working day: the CLI asked
before every shell command. And a session that ends "the review will
happen once CI finishes" is attended thinking - a headless session
never resumes.

## Decision

- **Two roles**: the developer agent implements; the reviewer agent
  reviews with **isolated context** (spec + diff + rules only, never
  the author's reasoning), a **counterexample-seeking method**, and a
  structured report (BLOCKING / TO-FIX / SUGGESTION with `file:line`
  evidence). A different model family is preferred for consequential
  changes.
- The reviewer runs as a **headless local Vibe Code session** in a
  fresh empty directory whose only content is the review bundle; the
  driver recomputes the counts, fails closed, and exits 0 (clean) /
  1 (blocking) / 2 (protocol failure, never mergeable) / 3
  (preconditions unmet - not a verdict). The reviewer never merges.
- **3-attempt budget** per issue across the whole loop; a strict
  escalation list keeps the consequential decisions with the human.
- **Contact channel**: `notify send --severity <question|urgent|info>
  --message ... [--issue-identifier ...]` posts to a Discord webhook
  (`DISCORD_WEBHOOK_URL`), console fallback when unconfigured;
  `urgent` requires the webhook and stops all merges until answered.
  The comment on the issue is the durable record; the webhook is the
  doorbell.
- **Runtime profile** (`.vibe/config.toml`, version-controlled,
  escalation-listed): `smart-approve`, `outside_workdir = "deny"`
  (reads/writes/commands outside the repository are refused, not
  asked), `[tools.bash]` in `ask` mode with an explicit allowlist
  (read-only classics, git including push, the toolchain, make) and a
  minimal denylist. Trust the folder once, after reviewing the file.
  On WSL2, clone inside the Linux filesystem, never `/mnt/c`.
- **Active CI wait**: `make wait-ci PR=N` blocks until CI concludes on
  the head (exit 0 green, 1 red or timeout, 2 protocol failure),
  following the CURRENT head on re-push. Ending a session "waiting for
  CI" is forbidden; budgets compose (`WAIT_CI_TIMEOUT_SECONDS` <
  `NIGHT_TIME_LIMIT_SECONDS`).

## Consequences

- Correlated judgment is structurally reduced; every review is a fresh
  session; the review keeps its intelligence at zero API spend.
- The autonomous day no longer stalls on routine commands; the human is
  asked only for calls the classifier judges risky; the hard boundaries
  remain branch protections, WSL mounts and CI - never the classifier.
- The session is autonomous end to end: push, wait, review, merge.
- Negative: isolated context costs the reviewer author intent - by
  design; the working machine needs the Vibe CLI installed and signed
  in (a session that cannot start fails closed); the token now needs
  issues read/write; a misjudged call can still run (smart-approve
  reduces prompts, it does not limit access); new tool commands prompt
  until the profile is extended - an escalation, by design.
