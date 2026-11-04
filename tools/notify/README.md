# notify - the agent-to-owner doorbell

`notify` is a utility of the repository, not part of the `petrichor`
product (ADR-0017): it is the tool your coding agent - Vibe Code or
any other - calls to tell you that **something on an issue needs your
interaction**. It is the implementation of the ADR-0012 contact channel
and the escalation severity contract.

Incidentally, it is also a small, complete Python project
demonstrating every rule of `docs/architecture.md` - onion layers,
data-first style, precise typing, logging as executable comments.

## What it does

- `notify send --severity <question|urgent|info> --message ...
  [--issue-identifier ...]` - delivers a notification to the owner
  (Discord webhook, console fallback), with the severity contract of
  `docs/agents/escalation.md`: `question` parks the work, `urgent`
  stops all merges.
- `notify smoke` - proves the notification pipeline is alive.

## Layout

```text
tools/notify/src/notify/
    models/            domain language: Pydantic, NewTypes, closed sets
    services/          pure functions behind Protocol ports
    infrastructure/    adapters: console, Discord webhook (httpx)
    main.py            composition root - the ONLY place logging is configured
tools/notify/tests/    unit / integration / property tests
```

The tool is part of the root project: `uv sync` installs it, `make check`
gates it, `tools/notify/Dockerfile` builds it, and the `notify` service
of `docker-compose.yml` runs it.
