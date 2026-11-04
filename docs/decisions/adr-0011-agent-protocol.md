# ADR-0011: Agent protocol - two roles, isolated review, budgets

- Status: accepted
- Date: 2026-11-02

## Context

One agent developing and reviewing itself degenerates into correlated
judgment: the reviewer inherits the author's blind spots and rationalizes
them. The mitigation pattern from agentic-development practice is role
separation with context isolation and, where possible, model diversity.

## Options considered

- **Two roles - developer and reviewer - with isolated contexts and a
  structured report** (chosen).
- **Single agent with a self-review step**: same context, same blind spots -
  theater, not review.
- **N-agent voting panels**: better diversity but triple the token spend
  for marginal gains at this project's scale; can be adopted later for
  consequential changes.

## Decision

- **Developer agent** implements (contract: `docs/agents/developer.md`).
- **Reviewer agent** reviews with **isolated context** (spec + diff +
  rules only, never the author's reasoning), a **counterexample-seeking
  method**, and a structured report (BLOCKING / TO-FIX / SUGGESTION with
  `file:line` evidence). A different model than the developer is preferred
  for consequential changes.
- The reviewer **never merges**; merging is a platform action on codified
  criteria (ADR-0010).
- **3-attempt budget** per issue across the whole develop-review loop;
  exhaustion → `blocked` + escalation.
- A **strict escalation list** (security, secrets, dependencies,
  architecture, migrations, constitutional files) keeps the truly
  consequential decisions with the human.

## Consequences

- Correlated judgment is structurally reduced: the reviewer can only see
  what the rules and the diff allow.
- The severity-tagged report makes "zero BLOCKING findings" a mergeable,
  machine-readable statement.
- Negative: isolated context costs the reviewer some author intent - by
  design. If the code needs the author's explanation to be correct, that
  is a finding in itself.
