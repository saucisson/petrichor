# ADR-0005: structlog - logging as executable comments

- Status: accepted
- Date: 2026-11-02

## Context

Owner's position: prefer logs to comments - a comment has one use, a log
comments *and* serves debugging. State of the art surveyed (Octez/tezos
logging practice, Spinellis on code commentary, the Python structured
logging ecosystem) supports a structured-events approach where events
carry named fields rather than formatted prose.

## Options considered

- **structlog with stdlib integration** (chosen): structured key-value
  events, typed processors, drop-in rendering switch console ↔ JSON,
  and `capture_logs` makes narration testable.
- **loguru**: pleasant API but its global-mutation style and non-stdlib
  plumbing fight the "configure once in main" rule.
- **stdlib logging alone**: possible, but every structuring (renderers,
  context, testing capture) must be hand-rolled and drifts per module.

## Decision

structlog over the stdlib root logger, configured **only** in
`main.configure_logging`; events with named fields (never positional
format strings); level contract: `DEBUG` = quotation/executable comments,
`INFO` = milestones, `WARNING` = fallbacks, `ERROR` = failure with context;
JSON rendering in production, console locally; no secret ever logged.

## Consequences

- Narrating `DEBUG` events replace section comments and are **asserted by
  tests** (`capture_logs`): they cannot rot like comments.
- Machine-parsable logs in prod help humans triage the day quickly.
- Negative: more verbose than prose comments; a discipline is needed to
  keep event names stable (tests pin them, so renames are deliberate).
