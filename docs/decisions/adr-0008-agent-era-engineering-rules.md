# ADR-0008: Agent-era engineering rules and the night-acceptance loop

- Status: accepted
- Date: 2026-11-25

## Context

Code is now read and rewritten by agents on every session far more
often than by humans, while writing, migrating and refactoring became
cheap. The existing documents say what the code must look like
(`docs/rules.md`, ADR-0002 to ADR-0004) and how the agent team works
(`docs/protocol.md`, ADR-0005 to ADR-0007); none of them states which
practices that economics rewards. The night loop also lacks a
structured channel for human acceptance: its verdicts land at the
owner as diffs, while what the owner actually verifies is the
product, by using it.

## Options considered

1. Amend `docs/rules.md` and `docs/protocol.md` in place. Rejected:
   the two documents serve different readers (instance code rules;
   agent working protocol); one merged text blurs the reviewer bundle
   and inflates every session's context load.
2. One new constitutional document - `docs/engineering-rules.md` -
   recording the economics, the derived core rules, the per-language
   profiles, review checklist additions and the night-acceptance
   loop, cross-referenced from both existing documents. Chosen:
   self-contained, quotable by the reviewer, minimal edits elsewhere.
3. Wait and codify rule by rule as each is debated. Rejected: the
   practices already exist in the instances; unrecorded, they drift.

## Decision

- Adopt `docs/engineering-rules.md` as a constitutional document. It
  governs wherever it overlaps `docs/rules.md` or
  `docs/protocol.md`.
- Its Part IV night-acceptance rules take effect as written, with two
  explicit exceptions: the automated daily-to-main merge option (N5)
  is **not** activated by this ADR - it amends the human-only merge of
  ADR-0005 and stays a separate, explicit acceptance; and the shared
  tooling it names (N2's allowlist file, N4's commit-msg hook, N3's
  staging deploys, N8's per-app consoles) are follow-up work items,
  not merged with this document.
- The escalation list of `AGENTS.md` is amended accordingly: a
  dependency on the versioned allowlist does not escalate, and
  `docs/engineering-rules.md` joins the constitutional files.
- The review checklist's dependency item in `docs/rules.md` follows
  the same allowlist.

## Consequences

- Instances inherit the document and its checklist additions through
  template sync; their ADR-0XXX numbering space is untouched.
- The reviewer bundle grows by one document; the review-bundle driver
  must include it. Bundle wiring is a follow-up work item.
- Negative: some rules (reals over mocks, mutation audits, scenario
  per user-visible PR) impose rework on instances that violate them;
  that lands through normal review, not retroactively.
- Negative: until the follow-up items land, N2 falls back to full
  dependency escalation, N8's scenarios fall back to the morning
  report, and the allowlist, hook and consoles named in Part IV are
  obligations on paper only. The obligations are stated plainly
  rather than hidden.
