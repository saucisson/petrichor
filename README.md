# Project template for AI-agent-driven development

An opinionated, **language-agnostic** project template for
**AI-agent-driven development**: two agents (developer + reviewer)
work autonomously all day inside a bounded blast radius (`daily`), a
human decides at the day boundary (`main`).

The template ships the **protocol** (constitution, decisions, review
and merge drivers) and the **tools**, no product code: your coding
agent (Vibe Code or any other) creates the product code per instance,
in the language of your choice, under the same rules. Alongside the
protocol ships the **`notify` tool** (`tools/notify/`): the contact
channel of the agent protocol, and the worked example of every rule.
Your coding agent calls it to tell you that something on an issue
needs your interaction, through a Discord webhook (or console
fallback), with a severity contract (`question` parks, `urgent` stops
all merges).

## Quick start

```console
uv sync                 # exact environment from the committed lockfile
make check              # the full quality gate (the Python tools)
uv run notify smoke    # prove the notification pipeline is alive
```

To receive Discord notifications:

```console
cp .env.example .env
# edit .env: DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
uv run notify send --severity question --message "Need a decision on X." --issue-identifier "PR-42"
```

## What is in the box

| Area              | Where                                                     |
| ----------------- | --------------------------------------------------------- |
| Architecture      | Onion, data-first, language-agnostic - `docs/architecture.md` (ADR-0002) |
| Toolchain         | latest stable, community-standard tools, committed lockfile - ADR-0001 |
| Style and typing  | readability first, precise typing - ADR-0004, ADR-0006 |
| Quality gates     | one gate per failure mode, 90% coverage - ADR-0007 |
| Security gates    | secret scan, dependency audit - ADR-0008 |
| HTTP APIs         | REST with best practices - ADR-0016 |
| Agent protocol    | `AGENTS.md` + `docs/agents/` (ADR-0010) |
| Reviewer launch   | local headless Vibe Code session - ADR-0010 |
| Merge into daily  | `make review` then `make merge-pr` - ADR-0009 |
| Branch model      | `docs/branching.md` (ADR-0009) |
| Agent runtime     | `.vibe/` permissions profile - ADR-0015 |
| Human contact     | the `notify` command in `tools/notify/` - ADR-0011, ADR-0014 |
| Decisions         | `docs/decisions/` - 16 ADRs (0001-0099); project ADRs start at 0100 |

## The repository layout

```text
tools/notify/
    src/notify/          the notification tool (reference application)
        models/          domain language: validated data, closed sets
        services/        pure functions behind ports
        infrastructure/  adapters: console, Discord webhook
        main.py          composition root - the ONLY place logging is configured
    tests/               unit / integration / property tests
scripts/                 day-cycle, review and merge orchestration
docs/                    constitutional documents and ADRs
```

An instance's product code is created per instance, in the language's
conventional layout (for a Python instance: `src/`), under the same
architecture and style rules (ADR-0014).

## Setting up a repository from this template

1. Clone **inside the WSL/Linux filesystem** (`~/...`), never on
   `/mnt/c` (also an I/O question). On WSL, mount Windows drives
   read-only or not at all: `/etc/wsl.conf` → `[automount]`.
2. `uv sync` - the environment comes from the committed `uv.lock`
   (ADR-0001). To change it: `uv lock`, then commit the updated
   lockfile.
3. At the first Vibe Code session, review `.vibe/config.toml` and
   **trust the folder** - the runtime permissions profile is then
   loaded (ADR-0015). Keep the CLI current before widening it.
4. Create the `daily` branch from `main`
   (`git branch daily && git push -u origin daily`), then enable
   branch protections per `docs/branching.md` (`main` and
   `daily`, required `check` and `security` status checks, linear
   history; no auto-merge, no reviewer check - ADR-0010, ADR-0009).
5. `uv run pre-commit install`.
6. Enable Renovate and release-please on the repository.
7. Replace `YOUR_GITHUB_USERNAME` in `.github/CODEOWNERS`.
8. Set `DISCORD_WEBHOOK_URL` in the CI/CD secrets or `.env`.
9. Install the Vibe CLI on the working machine and sign in
   (ADR-0010): `make review` runs the reviewer as a headless local
   session on the plan's included usage, no API key needed. Create a
   `GITHUB_TOKEN` (fine-grained PAT: pull requests read/write,
   contents read/write) in `.env` for `make review` and
   `make merge-pr`. `REVIEWER_MODEL` and `REVIEWER_MAX_TOKENS` in
   `.env` tune the session.

## License

MIT - see `LICENSE`.
