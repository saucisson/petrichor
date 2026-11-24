# Rules and review checklist

This document records the architecture and style rules of the project,
and the binary checklist the reviewer applies to every diff. It is
**constitutional**: changing it requires a human decision (see
`docs/decisions/README.md`). The reasoning behind every rule is in the
ADRs referenced throughout.

The rules are **language- and tool-agnostic**: they bind any instance
of the template, in whatever language it builds its product. The
concrete tools enforcing them in this repository (Ruff, mypy, structlog,
pytest, import-linter - all for the Python tools shipped here) are the
worked example; an instance in another language applies the same rules
with its community's equivalent tools.

## Repository layout

Every directory has exactly one job:

```text
tools/notify/ the agent-to-owner notification tool (a utility, not the product)
scripts/      day-cycle and review orchestration scripts
docs/         constitutional documents and ADRs
```

The template ships **no product code**. An instance's product code is
created per instance, in the language's conventional source layout (for
a Python instance: `src/`), under the same rules as the tools. The rule
itself: **long-lived utilities live in `tools/`, each self-contained;
the product lives in its own directory.** A new top-level directory is
an ADR-worthy decision, not a convenience.

## The onion, data-first

Every application in this repository - the `notify` tool today, an
instance's product code tomorrow - is organized in layers with a
single dependency direction: inward, toward the data. The layers below
are those of the notify tool, the reference application.

```text
┌─────────────────────────────────────────────┐
│ main (composition root)                     │  wires everything, configures logging
│  ┌───────────────────────────────────────┐  │
│  │ infrastructure (adapters)             │  │  console, Discord, future I/O
│  │  ┌─────────────────────────────────┐  │  │
│  │  │ services (pure logic)           │  │  │  orchestration, ports
│  │  │  ┌───────────────────────────┐  │  │  │
│  │  │  │ models (domain language)  │  │  │  │  validated data, errors, settings
│  │  │  └───────────────────────────┘  │  │  │
│  │  └─────────────────────────────────┘  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

The rule, enforced mechanically (for the Python tools: import-linter,
`make imports`):

> **A layer may only import from layers strictly inside it.** `models`
> imports nothing from the application. `services` imports `models`.
> `infrastructure` imports `services` (its ports) and `models`.
> `main` imports everything - and is the only layer that may do so.

```python
# Valid: services depending on models and its own ports.
from notify.models.notification import NotificationRequest
from notify.services.ports import MessageDispatcher

# Invalid: models importing a service (outward dependency).
from notify.services.notifications import dispatch_notification  # never
```

| Layer            | Contains                                          | Must never contain             |
| ---------------- | ------------------------------------------------- | ------------------------------ |
| `models`         | Validated data records, enums, domain types, errors, settings | I/O, business orchestration  |
| `services`       | Pure functions, ports, orchestration              | I/O, adapter construction, config |
| `infrastructure` | Adapters implementing ports (console, Discord)    | Business decisions, domain rules |
| `main`           | Composition root, CLI, logging configuration      | Business logic, adapters' logic |

### Adding a new adapter

1. Add the payload/domain records to `models` if the channel has a
   contract.
2. Extend `DeliveryChannel` and any outcome records if needed.
3. Implement the `MessageDispatcher` port in
   `infrastructure/notifications/`.
4. Wire the selection in `main.select_dispatcher` only.

No service changes: the port absorbs the difference. That is the point
of the onion.

## Style rules

- **All development artifacts are in English**: identifiers, comments,
  docstrings, commit messages, pull requests, issues and documents.
  Prose in any other language is a review finding.
- **Named parameters everywhere** (keyword-only where the language
  supports it), except the self/this exception and a single obvious
  primitive. Call sites must read like named assignments.
- **No abbreviations.** Exceptions: `id`, `url`, `http`.
- **Named constants** at module top, in the language's constant
  convention. No magic literals inline.
- **Linear over decomposed.** Prefer a longer, readable, flat function
  over arbitrary extraction. Extract only when the block is reused at
  least twice or carries a name-worthy intention of its own.
- Complexity budget ≤ 10, nesting depth ≤ 4.
- Docstrings on every public callable.
- Conventional Commits, enforced by review and release-please.

## Typing rules

- One **reference type checker**, strict profile, in pre-commit and CI;
  its verdict is the truth. Any dynamic/unchecked escape requires an
  inline comment justifying it.
- Domain primitives are distinct types: `IssueIdentifier`, `MessageText`,
  `WebhookUrl`. A bare string at a boundary is a review finding.
- Closed sets are enumerated types (with behavior) or literal unions
  (bare data); pattern matching with an exhaustiveness guard guarantees
  every case is handled at check time.
- Variant data uses discriminated unions with an explicit discriminator.
- Prefer type-guard functions that narrow, over bare boolean checks.

## HTTP APIs

When an instance exposes an HTTP API: REST over HTTP/JSON, resource-
oriented URLs, standard verb semantics, truthful status codes, typed
error payloads, pagination on list endpoints, standard authentication
schemes, explicit versioning, and a contract document (OpenAPI or
equivalent) kept with the code and reviewed. Full rules in
`docs/decisions/adr-0003-typing-logging-and-apis.md`.

## Logging contract

Logs replace comments: a `DEBUG` event narrates the section it
documents, and that narration is asserted by tests - it cannot rot
like a comment.

| Level     | Meaning                                                     |
| --------- | ----------------------------------------------------------- |
| `DEBUG`   | Quotation of what the code is doing (executable comments)   |
| `INFO`    | Business milestones (delivered, started, completed)         |
| `WARNING` | Recovered fallbacks, rejected payloads                      |
| `ERROR`   | Failures with full structured context                        |

- Structured events with **named fields** only - never positional
  format strings.
- Never log secrets; secrets stay in masked/protected settings types.
- Rendering: JSON in production, console locally - configured **only
  in the composition root**.
- Direct stdout/stderr writes are banned outside entry points and
  `scripts/`.

## Decisions

Every non-obvious choice has an ADR in `docs/decisions/`. The index
and the process are documented in `docs/decisions/README.md`. Numbers
0001-0099 belong to the template; a project instantiated from it
numbers its own ADRs from 0100 upward.

## The review checklist

Used by the reviewer agent for every pull request, and available to the
developer agent as a self-check before reporting work. **Every item is
binary**: yes or no, with `file:line` evidence for every "no".

### 1. Architecture and imports

- [ ] All dependencies point strictly inward (`models` → nothing,
      `services` → `models`, `infrastructure` → inner layers, `main` → all).
- [ ] `models` contains no I/O, no orchestration.
- [ ] I/O happens only in `infrastructure` adapters, behind a port
      (`services/ports.py`).
- [ ] No adapter is constructed outside the composition root (`main.py`).
- [ ] The layering check (`make imports`) passes - no layer was added.

### 2. Style

- [ ] All new prose (comments, docstrings, commit message, documents)
      is in **English**.
- [ ] All parameters named/keyword-only (exceptions: `self`/`this`, single
      obvious primitive).
- [ ] No abbreviations (allowed: `id`, `url`, `http`).
- [ ] Every literal with meaning is a named constant.
- [ ] Functions are linear and flat: no arbitrary extraction; extracted
      blocks are reused ≥ 2× or intention-named.
- [ ] Complexity ≤ 10, nesting ≤ 4.
- [ ] Docstrings on public callables.

### 3. Typing

- [ ] Every signature fully annotated; the reference type checker's
      strict profile passes on the diff.
- [ ] Domain primitives are distinct types at boundaries - no bare
      string/integer for identifiers, messages, URLs.
- [ ] Closed sets: enumerated types or literal unions with an
      exhaustiveness guard on every match/switch.
- [ ] Variant data: discriminated unions with an explicit discriminator.
- [ ] No dynamic/unchecked escape without an inline justification comment.
- [ ] Narrowing uses type guards, not bare-boolean helpers.

### 4. Logging

- [ ] Every logical section narrates a `DEBUG` event (executable comment).
- [ ] All events use named fields, never positional format strings.
- [ ] No secret ever logged; credentials stay in protected settings types.
- [ ] No direct stdout/stderr write outside entry points and `scripts/`.
- [ ] No comment that merely repeats the code below it - it should have
      been a log event or a docstring.

### 5. Tests

- [ ] Every new behavior is covered by an assertion that **fails if the
      behavior regresses** (not just "executes without crashing").
- [ ] Narration contract asserted: the service's key log events are pinned
      by tests.
- [ ] Domain logic has property tests (the suite's `property` tier).
- [ ] No test weakened or deleted to make CI pass.

### 6. HTTP APIs (when the diff exposes or changes one)

- [ ] URLs name resources, never actions; non-CRUD actions are explicit
      sub-resources.
- [ ] Verb semantics standard (GET safe/idempotent, PUT/DELETE idempotent);
      status codes truthful - no "200 with error inside".
- [ ] List endpoints paginate; unbounded responses are a finding.
- [ ] Errors are the documented typed payload, same shape everywhere.
- [ ] Authentication uses standard schemes; no secrets in URLs.
- [ ] The contract document (OpenAPI or equivalent) is updated with the
      code.

### 7. Security and autonomy

- [ ] No new dependency without an approved escalation.
- [ ] The worklog entry (`docs/worklog/<date>-pr-<N>.md`) is present in
      the diff and its title is coherent with the change; for a direct
      commit on `daily`, the entry follows `docs/worklog/_template.md`.
- [ ] No constitutional file touched: `AGENTS.md`, `docs/protocol.md`,
      `docs/rules.md`, `docs/decisions/`, lint/type/coverage
      configuration.
- [ ] Pull request under ~500 changed lines, or split proposed in the
      pull request body via `make update-pr PR=N BODY_FILE=...` (the
      only body-update path).
- [ ] Errors: typed domain errors, causes preserved, nothing swallowed.
- [ ] Conventional Commit message matching the actual change.

### Verdict

Every finding is reported as **BLOCKING** (violates a rule above; no
merge), **TO-FIX** (should be fixed; merge can proceed if trivial and
tracked) or **SUGGESTION** (advisory, never blocks). The reviewer never
fixes code, and never merges - the report ends with a recommendation
only.

## The reviewer agent

The reviewer reviews; it never modifies code, never merges, and its
report is a recommendation - the merge decision belongs to the
codified criteria (`docs/protocol.md`), not to the reviewer.

### Context isolation

The reviewer works with **only**: the pull request specification (the PR
description, whose acceptance-criteria mapping is the spec), the pull
request diff, this document, and the ADRs. It is **forbidden** from
consuming the developer's reasoning: no session transcripts, no design
chat, no "what the author meant". Judging the diff against the rules -
not the author's intent against their own hopes - is what makes the
review independent. For consequential changes, the reviewer should be a
**different model family** than the developer.

### Method: seek counterexamples

Do not rate quality ("looks good"). For each acceptance criterion and
each checklist item, try to construct a failing case:

- What input breaks this function?
- What change would make the project's type checker or linter complain
  but pass here?
- Which assertion would catch a regression of this behavior - is it
  actually present?
- Is a narrating log event asserted anywhere, or can it silently vanish?

A finding you cannot attach to `file:line` evidence is not a finding.

### Report format

Structured, binary, by severity:

- **BLOCKING**: violates a checklist rule, an ADR, or the acceptance
  criteria. No merge until fixed.
- **TO-FIX**: real problem, merge can proceed only if trivial and
  tracked as an issue.
- **SUGGESTION**: advisory; explicitly never blocks.

Each finding: `file:line`, the rule violated, and the counterexample or
evidence. The report ends with the counts per severity and a one-line
recommendation. A checklist item unclear? Ask via `needs-human`, do not
guess. One comment is posted per review run; the latest is authoritative.
Pull request labels never enter the review: report exactly what you
find, at the severity you judge. A BLOCKING finding has no override in
the protocol - if the human overrules one, the human merges, on the
record, outside the agent loop.

### Launch mechanics

The reviewer is launched **locally**, by the developer agent, as a
**headless Vibe Code session**: `make review PR=N` runs the
`agent-review` driver, which starts `vibe --prompt-file ...` in a fresh
empty directory containing only the review bundle (and the two prompt
files). There is no repository around the session, no project
configuration, no session history: the bundle is the entire context,
and isolation is enforced by construction, not by prompt discipline.
Programmatic mode denies any tool call that would need an approval, so
the reviewer answers from the bundle alone.

The review bundle (this document, the ADRs, the pull request
specification, the diff) is built deterministically by
`make review-bundle`. The reviewer answers with a **JSON report**
(`findings` with `severity`, `file`, `line`, `rule`, `evidence`). The
driver script recomputes all counts locally and fails closed: an
unknown severity is recorded as BLOCKING, an invalid answer is a
protocol failure. Exit codes drive the merge:

- `0` - zero BLOCKING findings (the merge driver may proceed),
- `1` - at least one BLOCKING finding (the developer iterates),
- `2` - protocol failure: session could not start or failed, invalid
  answer (fail-closed, never fail-open - never mergeable),
- `3` - preconditions unmet: the review never started (the head was
  not pushed, or CI had not concluded on it); not a verdict, not an
  attempt.

The reviewer re-reviews after fixes on the same budget terms: the
issue's 3-attempt budget applies across the whole cycle, not per review
round. `REVIEWER_MODEL` selects the reviewing session's model - point
it at a **different model family** than the developer agent. To debug
locally, `make review-bundle` prints exactly what the reviewer sees.
