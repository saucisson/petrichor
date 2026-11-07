# ADR-0005: Structured logging as executable comments

- Status: accepted
- Date: 2026-11-02

## Context

Owner's position: prefer logs to comments - a comment has one use, a
log comments *and* serves debugging. Structured-events logging (events
carry named fields rather than formatted prose) is the state of the
art: it is machine-parsable in production and testable in development.

## Options considered

- **A structured logging library with named fields** (chosen):
  key-value events, a rendering switch between human (console) and
  machine (JSON), and log capture in tests so the narration itself is
  asserted.
- **Print-style / format-string logging**: prose that neither machines
  nor tests can rely on; rejected.
- **No logging discipline (scattered ad-hoc events)**: fields drift
  per module, triage at day end becomes archaeology; rejected.

## Decision

- Structured events with **named fields only** - never positional
  format strings.
- Level contract: `DEBUG` = quotation of what the code is doing
  (executable comments), `INFO` = milestones, `WARNING` = fallbacks,
  `ERROR` = failure with full context.
- Rendering: JSON in production, human-readable locally - configured
  **only in the composition root**.
- No secret is ever logged.
- For the Python tools in this template: structlog over the standard
  root logger.

## Consequences

- Narrating `DEBUG` events replace section comments and are asserted
  by tests: they cannot rot like comments.
- Machine-parsable logs in production help the human triage the day
  quickly.
- Negative: more verbose than prose comments; a discipline is needed
  to keep event names stable (tests pin them, so renames are
  deliberate).
