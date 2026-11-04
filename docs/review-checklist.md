# Review checklist

Used by the reviewer agent for every pull request, and available to the
developer agent as a self-check before reporting work. **Every item is
binary**: yes or no, with `file:line` evidence for every "no".

## 1. Architecture and imports

- [ ] All dependencies point strictly inward (`models` → nothing,
      `services` → `models`, `infrastructure` → inner layers, `main` → all).
- [ ] `models` contains no I/O, no orchestration.
- [ ] I/O happens only in `infrastructure` adapters, behind a port
      (`services/ports.py`).
- [ ] No adapter is constructed outside the composition root (`main.py`).
- [ ] `import-linter` (`make imports`) passes - no layer was added.

## 2. Style

- [ ] All parameters keyword-only (exceptions: `self`, `cls`, single obvious
      primitive).
- [ ] No abbreviations (allowed: `id`, `url`, `http`).
- [ ] Every literal with meaning is a named `UPPER_CASE` constant.
- [ ] Functions are linear and flat: no arbitrary extraction; extracted
      blocks are reused ≥ 2× or intention-named.
- [ ] Complexity ≤ 10, nesting ≤ 4.
- [ ] Google-style docstrings on public callables.

## 3. Typing

- [ ] Every signature fully annotated; `mypy` strict passes on the diff.
- [ ] Domain primitives (`NewType`) used at boundaries - no bare `str` for
      identifiers, messages, URLs.
- [ ] Closed sets: `StrEnum`/`Literal` with `match` + `assert_never`
      exhaustiveness.
- [ ] Variant data: explicit `Field(discriminator=...)`.
- [ ] No `Any` without an inline justification comment.
- [ ] `TypeIs` guards for narrowing; no bare-boolean narrowing helpers.

## 4. Logging

- [ ] Every logical section narrates a `DEBUG` event (executable comment).
- [ ] All events use named fields, never positional format strings.
- [ ] No secret ever logged; `SecretStr` used for credentials.
- [ ] No `print` outside `main.py` / `scripts/`.
- [ ] No comment that merely repeats the code below it - it should have been
      a log event or a docstring.

## 5. Tests

- [ ] Every new behavior is covered by an assertion that **fails if the
      behavior regresses** (not just "executes without crashing").
- [ ] Narration contract asserted: the service's key log events are pinned
      by tests.
- [ ] Domain logic has property tests (`pytest -m property`).
- [ ] No test weakened or deleted to make CI pass.

## 6. Security and autonomy

- [ ] No new dependency without an approved escalation (ADR-0009, ADR-0011).
- [ ] No constitutional file touched: `AGENTS.md`, `docs/agents/`,
      `docs/decisions/`, `docs/architecture.md`, `docs/branching.md`,
      lint/type/coverage configuration.
- [ ] Pull request under ~500 changed lines, or split proposed.
- [ ] Errors: typed domain errors, `raise ... from ...`, nothing swallowed.
- [ ] Conventional Commit message matching the actual change.

## 7. Web frontend (React/TypeScript, ADR-0018)

- [ ] Dependencies point strictly inward (`lib` → nothing, `hooks` →
      `lib`, `components` → `hooks`/`lib`, `app` → all); `eslint`
      boundaries passes.
- [ ] `tsc` strict-plus passes on the diff; no `any` - `unknown` with
      narrowing instead.
- [ ] Closed sets are union types (never `enum`), with an exhaustive
      `switch` guarded by `never`.
- [ ] Named exports only; no default exports.
- [ ] No `console`, no `alert` anywhere in application code.
- [ ] Components stay presentational: state/effects in `hooks`, logic
      in `lib`; props typed with explicit interfaces.
- [ ] Files kebab-case; `UPPER_CASE` constants at module top; no
      abbreviations (`id`, `url`, `http` excepted).
- [ ] New frontend behavior is covered by a colocated test that
      **fails on regression**; the 90 % coverage floor holds
      (`make web-test`).

## Verdict

Every finding is reported as **BLOCKING** (violates a rule above; no merge),
**TO-FIX** (should be fixed; merge can proceed if trivial and tracked) or
**SUGGESTION** (advisory, never blocks). The reviewer never fixes code, and
never merges - the report ends with a recommendation only.
