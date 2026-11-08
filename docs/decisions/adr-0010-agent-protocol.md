# ADR-0010: Agent protocol - two roles, isolated local review, budgets

- Status: accepted
- Date: 2026-11-06

## Context

One agent developing and reviewing itself degenerates into correlated
judgment: the reviewer inherits the author's blind spots and
rationalizes them. The mitigation pattern from agentic-development
practice is role separation with context isolation and, where
possible, model diversity. The reviewer must also be cheap enough to
run on every pull request: a pay-per-token API reviewer re-sends the
whole rule corpus per review, and its spend grows with the number of
decisions and the volume of pull requests.

Meanwhile the project's coding agent is Vibe Code, running on the
local machine with the plan's INCLUDED monthly usage. The same
review, run as a headless local session, costs no API tokens at all.

## Options considered

- **Two roles - developer and reviewer - with isolated contexts and a
  structured report** (chosen).
- **Single agent with a self-review step**: same context, same blind
  spots - theater, not review.
- **N-agent voting panels**: better diversity but triple the spend
  for marginal gains at this scale; can be adopted later for
  consequential changes.
- **Keep the review as a pay-per-token API call** (the former
  mechanics): spend grows with the corpus and the PR volume; rejected
  when included local usage exists anyway.
- **Replace the reviewer with a deterministic local rule-checker**:
  zero tokens, but zero judgment - everything it catches, the linter,
  the type checker and the tests already catch; the
  counterexample-seeking review is the core of the protocol, a
  rule-checker is a second linter. Rejected.

## Decision

- **Developer agent** implements (contract: `docs/agents/developer.md`).
- **Reviewer agent** reviews with **isolated context** (spec + diff +
  rules only, never the author's reasoning), a **counterexample-seeking
  method**, and a structured report (BLOCKING / TO-FIX / SUGGESTION
  with `file:line` evidence). A different model family than the
  developer is preferred for consequential changes.
- The reviewer runs as a **headless Vibe Code session**
  (`vibe --prompt-file ... --system-prompt-file ... --output json
  --max-turns 8 --time-limit 600`), launched by
  the `agent-review` subcommand in a fresh **empty temporary directory**
  whose only content is the review bundle (role contract, architecture
  rules, review checklist, the ADRs, the pull request specification,
  the diff) and the two prompt files. Isolation is enforced by
  construction: no repository, no project configuration, no session
  history - nothing else to read. Programmatic mode denies any tool
  call that would need an approval, so the review answers from the
  bundle alone. `make review PR=N` fetches
  the pull request, builds the bundle, runs the session and posts the
  comment - and refuses to start (exit code 3, not a verdict, not an
  attempt) unless the pull request head is the local HEAD and every
  CI check on it has concluded "success": the review is the last
  gate, never a substitute for a failing local one.
- The report contract: JSON findings with severity, file, line, rule,
  evidence; counts recomputed locally by the driver; fail-closed on
  any unknown severity, fenced or malformed answer, or failed session;
  exit codes 0 (clean) / 1 (blocking findings) / 2 (protocol failure,
  never mergeable).
- The session spends the plan's **included** Vibe Code usage, not
  pay-per-token API calls. Budgets: `--max-turns 8`, `--time-limit`
  (overridable), optional `--max-tokens`; `REVIEWER_MODEL` maps to the
  session's `--model` flag (the model-diversity lever).
- The reviewer **never merges**; the developer agent merges through
  the criteria-checked driver (ADR-0009).
- **3-attempt budget** per issue across the whole develop-review
  loop; exhaustion → `blocked` + escalation.
- A **strict escalation list** (security, secrets, dependencies,
  architecture, migrations, constitutional files) keeps the truly
  consequential decisions with the human.

## Consequences

- Correlated judgment is structurally reduced: the reviewer can only
  see what the rules and the diff allow - and the review keeps its
  intelligence at zero API spend, with no secret to manage beyond the
  platform token.
- Every review is a fresh session: no drift, no accumulated context.
- The review no longer fires automatically on every push: the
  developer agent runs it, which suits both usage modes (web patch
  applied by Vibe Code, or direct Vibe Code work).
- Negative: isolated context costs the reviewer some author intent -
  by design. If the code needs the author's explanation to be
  correct, that is a finding in itself. The working machine needs the
  Vibe CLI installed and signed in; a session that cannot start fails
  closed (protocol failure), never fail-open.
