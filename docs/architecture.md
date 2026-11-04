# Architecture

This document records the architecture and style rules of the project. It is
**constitutional**: changing it requires a human decision (see
`docs/decisions/README.md`). The reasoning behind every rule is in the ADRs
referenced throughout.

## Repository layout (ADR-0017, ADR-0018)

Every directory has exactly one job:

```text
src/          the PRODUCT's Python code (starts empty - no filler code)
apps/web/     the web frontend: Vite + React + strict TypeScript
tools/notify/ the agent-to-owner notification tool (a utility, not the product)
scripts/      day-cycle and review orchestration scripts
docs/         constitutional documents and ADRs
```

The rule: **the product lives in `src/`, long-lived utilities in
`tools/`, applications with their own toolchain in `apps/`.** A new
directory is an ADR-worthy decision, not a convenience.

## The onion, data-first

Every Python application in this repository - the product in `src/`,
the notify tool in `tools/notify/src/` - is organized in layers with a
single dependency direction: inward, toward the data. The layers below
are those of the notify tool, the reference application.

```text
┌─────────────────────────────────────────────┐
│ main (composition root)                     │  wires everything, configures logging
│  ┌───────────────────────────────────────┐  │
│  │ infrastructure (adapters)             │  │  console, Discord, future I/O
│  │  ┌─────────────────────────────────┐  │  │
│  │  │ services (pure logic)           │  │  │  orchestration, ports (Protocols)
│  │  │  ┌───────────────────────────┐  │  │  │
│  │  │  │ models (domain language)  │  │  │  │  Pydantic types, errors, settings
│  │  │  └───────────────────────────┘  │  │  │
│  │  └─────────────────────────────────┘  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

The rule, enforced by `import-linter` (`make imports`):

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

### Layer responsibilities

| Layer            | Contains                                          | Must never contain             |
| ---------------- | ------------------------------------------------- | ------------------------------ |
| `models`         | Pydantic models, enums, NewTypes, errors, settings | I/O, business orchestration  |
| `services`       | Pure functions, ports (Protocols), orchestration  | I/O, adapter construction, config |
| `infrastructure` | Adapters implementing ports (console, Discord)    | Business decisions, domain rules |
| `main`           | Composition root, CLI, logging configuration      | Business logic, adapters' logic |

### Adding a new adapter

1. Add the payload/domain models to `models` if the channel has a contract.
2. Extend `DeliveryChannel` and any outcome models if needed.
3. Implement the `MessageDispatcher` protocol in
   `infrastructure/notifications/`.
4. Wire the selection in `main.select_dispatcher` only.

No service changes: the port absorbs the difference. That is the point of
the onion.

## Style rules (ADR-0003, ADR-0004)

- **Keyword-only parameters everywhere**, except `self`, `cls`, and a single
  obvious primitive. Call sites must read like named assignments.
- **No abbreviations.** Exceptions: `id`, `url`, `http`.
- **Constants** in `UPPER_CASE` at module top. No magic literals inline.
- **Linear over decomposed.** Prefer a longer, readable, flat function over
  arbitrary extraction. Extract only when the block is reused at least
  twice or carries a name-worthy intention of its own.
- Complexity budget: ≤ 10 (Ruff `C901`), nesting depth ≤ 4.
- Google-convention docstrings on every public callable.
- Conventional Commits, enforced by review and release-please.

## Typing rules (ADR-0006, ADR-0007)

- `mypy` strict **plus** `disallow_any_generics`, `disallow_any_explicit`,
  `warn_return_any`, `warn_unreachable`. Any `Any` requires an inline
  comment justifying it.
- Domain primitives are `NewType`s: `IssueIdentifier`, `MessageText`,
  `WebhookUrl`. A bare `str` at a boundary is a review finding.
- Closed sets are `StrEnum` (with behavior) or `Literal` (bare data).
  `match` + `assert_never` guarantees exhaustiveness.
- Variant data uses Pydantic discriminated unions with an explicit
  `Field(discriminator=...)`.
- Narrowing uses `TypeIs` guards, not bare boolean functions.
- Keyword arguments as data use `Unpack[TypedDict]`.

## Web frontend: React + TypeScript (ADR-0018)

`apps/web/` lives under the **same discipline** as the Python code -
the rules are mechanical, not conventions. The layer contract mirrors
the onion, enforced by `eslint-plugin-boundaries` instead of
import-linter:

```text
app (src/app.tsx, src/main.tsx)   composition root: wires hooks and components
└─ components (src/components/)  presentation, typed props interfaces
   └─ hooks (src/hooks/)         React primitives over lib data
      └─ lib (src/lib/)          pure logic: no React, no I/O, no imports
```

> **A layer may only import from layers strictly inside it.**
> `lib` imports nothing internal. `hooks` imports `lib`.
> `components` imports `hooks` and `lib`. `app` imports everything -
> and is the only layer that may do so.

### Frontend rules

- **Strict-plus `tsc`**: `strict` + `noUncheckedIndexedAccess`,
  `exactOptionalPropertyTypes`, `verbatimModuleSyntax`, and friends -
  every flag is in `apps/web/tsconfig.json`.
- **No `any`** (`no-explicit-any` is an error): use `unknown` and
  narrow - the equivalent of `object` + isinstance.
- **Closed sets are union types, never enums** (enums are banned by
  lint); exhaustive `switch` with a `never` guard is the
  `assert_never` equivalent.
- **Named exports only**; imports sorted; no `console`, no `alert`
  (the `T20` parity).
- **Files kebab-case** (`day-phase.ts`), exported symbols PascalCase.
- Style parity with ADR-0004: no abbreviations, `UPPER_CASE` constants
  at module top, linear functions, options objects instead of long
  positional parameter lists.
- Components are **pure presentation**: props in, markup out; state
  and effects live in `hooks`, logic in `lib`.
- Testing parity: vitest + Testing Library, colocated tests
  (`src/**/*.test.tsx`), **90 % coverage floor** on lines, branches,
  functions and statements.

The web quality gate is `make web-check` (part of `make check`); CI
runs it as the `web` job (GitHub) / `web-quality-gate` job (GitLab).
The shared local runtime is `make compose-up` (notify + web).

## Logging contract (ADR-0005)

Logs replace comments: a `DEBUG` event narrates the section it documents,
and that narration is asserted by tests - it cannot rot like a comment.

| Level     | Meaning                                                     |
| --------- | ----------------------------------------------------------- |
| `DEBUG`   | Quotation of what the code is doing (executable comments)   |
| `INFO`    | Business milestones (delivered, started, completed)         |
| `WARNING` | Recovered fallbacks, rejected payloads                      |
| `ERROR`   | Failures with full structured context                       |

- Structured events with **named fields** only - never positional format
  strings.
- Never log secrets (`SecretStr` everywhere in settings).
- Rendering: JSON in production, console locally - configured **only in
  `main.configure_logging`**.
- `print` is banned (`T20`) except `main.py` and `scripts/`.

## Decisions

Every non-obvious choice has an ADR in `docs/decisions/`. The index and the
process are documented in `docs/decisions/README.md`.
