# Architecture

This document records the architecture and style rules of the project. It is
**constitutional**: changing it requires a human decision (see
`docs/decisions/README.md`). The reasoning behind every rule is in the ADRs
referenced throughout.

The rules are **language- and tool-agnostic**: they bind any instance of the
template, in whatever language it builds its product. The concrete tools
enforcing them in this repository (Ruff, mypy, structlog, pytest,
import-linter - all for the Python tools shipped here) are the worked
example; an instance in another language applies the same rules with its
community's equivalent tools.

## Repository layout (ADR-0014)

Every directory has exactly one job:

```text
tools/notify/ the agent-to-owner notification tool (a utility, not the product)
scripts/      day-cycle and review orchestration scripts
docs/         constitutional documents and ADRs
```

The template ships **no product code**. An instance's product code is
created per instance, in the language's conventional source layout (for a
Python instance: `src/`), under the same rules as the tools. The rule
itself: **long-lived utilities live in `tools/`, each self-contained;
the product lives in its own directory.** A new top-level directory is an
ADR-worthy decision, not a convenience.

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

### Layer responsibilities

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

No service changes: the port absorbs the difference. That is the point of
the onion.

## Style rules (ADR-0003, ADR-0004)

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

## Typing rules (ADR-0006)

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

## HTTP APIs (ADR-0016)

When an instance exposes an HTTP API: REST over HTTP/JSON, resource-
oriented URLs, standard verb semantics, truthful status codes, typed
error payloads, pagination on list endpoints, standard authentication
schemes, explicit versioning, and a contract document (OpenAPI or
equivalent) kept with the code and reviewed. Full rules in
`docs/decisions/adr-0016-apis-rest-best-practices.md`.

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
- Never log secrets; secrets stay in masked/protected settings types.
- Rendering: JSON in production, console locally - configured **only in
  the composition root**.
- Direct stdout/stderr writes are banned outside entry points and
  `scripts/`.

## Decisions

Every non-obvious choice has an ADR in `docs/decisions/`. The index and the
process are documented in `docs/decisions/README.md`. Numbers 0001-0099
belong to the template; a project instantiated from it numbers its own
ADRs from 0100 upward.
