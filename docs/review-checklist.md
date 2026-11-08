# Review checklist

Used by the reviewer agent for every pull request, and available to the
developer agent as a self-check before reporting work. **Every item is
binary**: yes or no, with `file:line` evidence for every "no". The rules
are language-agnostic (ADR-0004 to ADR-0008); the Python tool names below
are this repository's enforcement - an instance in another language
applies its equivalents.

## 1. Architecture and imports

- [ ] All dependencies point strictly inward (`models` → nothing,
      `services` → `models`, `infrastructure` → inner layers, `main` → all).
- [ ] `models` contains no I/O, no orchestration.
- [ ] I/O happens only in `infrastructure` adapters, behind a port
      (`services/ports.py`).
- [ ] No adapter is constructed outside the composition root (`main.py`).
- [ ] The layering check (`make imports`) passes - no layer was added.

## 2. Style

- [ ] All parameters named/keyword-only (exceptions: `self`/`this`, single
      obvious primitive).
- [ ] No abbreviations (allowed: `id`, `url`, `http`).
- [ ] Every literal with meaning is a named constant.
- [ ] Functions are linear and flat: no arbitrary extraction; extracted
      blocks are reused ≥ 2× or intention-named.
- [ ] Complexity ≤ 10, nesting ≤ 4.
- [ ] Docstrings on public callables.

## 3. Typing

- [ ] Every signature fully annotated; the reference type checker's
      strict profile passes on the diff.
- [ ] Domain primitives are distinct types at boundaries - no bare
      string/integer for identifiers, messages, URLs.
- [ ] Closed sets: enumerated types or literal unions with an
      exhaustiveness guard on every match/switch.
- [ ] Variant data: discriminated unions with an explicit discriminator.
- [ ] No dynamic/unchecked escape without an inline justification comment.
- [ ] Narrowing uses type guards, not bare-boolean helpers.

## 4. Logging

- [ ] Every logical section narrates a `DEBUG` event (executable comment).
- [ ] All events use named fields, never positional format strings.
- [ ] No secret ever logged; credentials stay in protected settings types.
- [ ] No direct stdout/stderr write outside entry points and `scripts/`.
- [ ] No comment that merely repeats the code below it - it should have been
      a log event or a docstring.

## 5. Tests

- [ ] Every new behavior is covered by an assertion that **fails if the
      behavior regresses** (not just "executes without crashing").
- [ ] Narration contract asserted: the service's key log events are pinned
      by tests.
- [ ] Domain logic has property tests (the suite's `property` tier).
- [ ] No test weakened or deleted to make CI pass.

## 6. HTTP APIs (when the diff exposes or changes one, ADR-0016)

- [ ] URLs name resources, never actions; non-CRUD actions are explicit
      sub-resources.
- [ ] Verb semantics standard (GET safe/idempotent, PUT/DELETE idempotent);
      status codes truthful - no "200 with error inside".
- [ ] List endpoints paginate; unbounded responses are a finding.
- [ ] Errors are the documented typed payload, same shape everywhere.
- [ ] Authentication uses standard schemes; no secrets in URLs.
- [ ] The contract document (OpenAPI or equivalent) is updated with the
      code.

## 7. Security and autonomy

- [ ] No new dependency without an approved escalation (ADR-0008, ADR-0010).
- [ ] The `WORKLOG.md` entry is present, follows the file's format, and
      matches the change - including what was **not** run.
- [ ] No constitutional file touched: `AGENTS.md`, `docs/agents/`,
      `docs/decisions/`, `docs/architecture.md`, `docs/branching.md`,
      lint/type/coverage configuration.
- [ ] Pull request under ~500 changed lines, or split proposed in the
      pull request body via `make update-pr PR=N BODY_FILE=...`
      (ADR-0019 - the only body-update path).
- [ ] Errors: typed domain errors, causes preserved, nothing swallowed.
- [ ] Conventional Commit message matching the actual change.

## Verdict

Every finding is reported as **BLOCKING** (violates a rule above; no merge),
**TO-FIX** (should be fixed; merge can proceed if trivial and tracked) or
**SUGGESTION** (advisory, never blocks). The reviewer never fixes code, and
never merges - the report ends with a recommendation only.
