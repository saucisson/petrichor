# Escalation and human contact protocol

Constitutional document. How agents ask the human owner for a decision,
and when they must stop everything. This operationalizes ADR-0012.

## The escalation list (strict)

These changes are **never** decided by agents, at any severity - they are
`question` escalations at minimum, before any code is written:

- anything security-relevant (auth, secrets handling, permissions),
- a new dependency, or a major version bump of an existing one,
- an architecture change (layer boundaries, new layers, ports reshuffle),
- a schema or data migration,
- any constitutional file: `AGENTS.md`, `docs/agents/`, `docs/decisions/`,
  `docs/architecture.md`, `docs/branching.md`, lint/type/coverage config,
  the `.vibe/` runtime profile (ADR-0019), branch protections,
- any decision worth an ADR (rule of thumb: would a future agent ask "why?"
  → draft an ADR in the PR and label it `needs-human`).

## Making a request

1. Comment on the relevant issue (or open one if the question is generic).
   The comment is **structured**:
   - **Context**: what work is blocked, with links.
   - **Question**: one precise, answerable question.
   - **Options**: each with its trade-offs; a recommendation is welcome,
     the decision is not.
   - **Blocked**: what stops, and what continues meanwhile.
   - **Impact**: what happens if unanswered at end of day.
2. Apply the label `needs-human`, remove any `in-progress` label.
3. Send the notification:

   ```console
   make notify severity=question message="..." issue-identifier="..."
   ```

   For urgent matters: `severity=urgent` - this requires
   `DISCORD_WEBHOOK_URL` and pushes to the owner's channel. The
   `notify` tool in `tools/notify/` (ADR-0017) is the
   implementation of this channel.

## Severity contract

| Severity   | Meaning                      | Behavior                          |
| ---------- | ---------------------------- | --------------------------------- |
| `question` | Parkable decision            | Park the issue, continue others  |
| `urgent`   | Day-level blocker or danger  | **All merges stop** until answer  |
| `info`     | FYI, no decision needed      | No workflow impact                |

Examples of `urgent`: `daily` red beyond the revert protocol, a secret
exposed anywhere, every open issue blocked by the same question.

## Answering and resuming

The human answers by commenting the issue. Agents check for answered
`needs-human` issues **first** in every cycle (orchestration §5). An
answered issue is un-labeled, taken with priority, and its answer is the
authority - if the answer conflicts with an ADR, the ADR change (drafted,
human-accepted) comes first.
