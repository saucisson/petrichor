# Project template for AI-agent-driven development

An opinionated, **language-agnostic** project template for
**AI-agent-driven development**: two agents (developer + reviewer)
work autonomously all day inside a bounded blast radius (`daily`), a
human decides at the day boundary (`main`).

The template ships the **protocol** (constitution, decisions, review
and merge drivers) and the **tools**, no product code: your coding
agent (Vibe Code or any other) creates the product code per instance,
in the language of your choice, under the same rules. All development
artifacts are **in English**: code, comments, documents, commit
messages. Alongside the protocol ships the **`notify` tool**
(`tools/notify/`): the contact channel of the agent protocol, and the
worked example of every rule.

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

| Area              | Where                                                        |
| ----------------- | ------------------------------------------------------------ |
| Constitution      | `AGENTS.md` - the short contract every session re-reads      |
| Working protocol  | `docs/protocol.md` - day/night cycles, merge gates, escalation |
| Code rules        | `docs/rules.md` - architecture, style, review checklist, reviewer contract |
| Decisions         | `docs/decisions/` - 7 ADRs (0001-0099); project ADRs start at 0100 |
| Day audit         | `docs/worklog/` - one driver-generated file per record        |
| Agent runtime     | `.vibe/` permissions profile - ADR-0006                      |
| Human contact     | the `notify` tool in `tools/notify/` - ADR-0006               |
| Drivers           | `scripts/` - one CLI package, one subcommand per make target - ADR-0001 |

## The repository layout

```text
AGENTS.md                the constitution (re-read every session)
docs/protocol.md         day cycle, branch model, developer and escalation contracts
docs/rules.md            architecture and style rules, review checklist, reviewer contract
docs/decisions/          the ADRs and their process
docs/worklog/            the human's audit trail (agents never read it)
scripts/                 the drivers: pr, review, merge-pr, claim, night, ...
tools/notify/            the notification tool (reference application)
.vibe/                   the agent runtime profile and the night prompts
```

An instance's product code is created per instance, in the language's
conventional layout (for a Python instance: `src/`), under the same
architecture and style rules (ADR-0001).

## Setting up a repository from this template

1. Clone **inside the WSL/Linux filesystem** (`~/...`), never on
   `/mnt/c` (also an I/O question). On WSL, mount Windows drives
   read-only or not at all: `/etc/wsl.conf` → `[automount]`.
2. `uv sync` - the environment comes from the committed `uv.lock`
   (ADR-0001). To change it: `uv lock`, then commit the updated
   lockfile.
3. At the first Vibe Code session, review `.vibe/config.toml` and
   **trust the folder** - the runtime permissions profile is then
   loaded (ADR-0006). Keep the CLI current before widening it.
4. Create the `daily` branch from `main`
   (`git branch daily && git push -u origin daily`), then enable
   branch protections per `docs/protocol.md` (`main` and `daily`,
   required `check` and `security` status checks, linear history,
   force pushes allowed on `daily` only - the sync driver, ADR-0005;
   no auto-merge, no reviewer check - ADR-0006).
5. `uv run pre-commit install`.
6. Enable Renovate and release-please on the repository (ADR-0001).
7. Replace `YOUR_GITHUB_USERNAME` in `.github/CODEOWNERS`.
8. Set `DISCORD_WEBHOOK_URL` in the CI/CD secrets or `.env`.
9. Install the Vibe CLI on the working machine and sign in
   (ADR-0006): `make review` runs the reviewer as a headless local
   session on the plan's included usage, no API key needed. Create a
   `GITHUB_TOKEN` (fine-grained PAT: pull requests read/write,
   issues read/write, contents read/write) in `.env` for `make review`
   and `make merge-pr`. `REVIEWER_MODEL` and `REVIEWER_MAX_TOKENS` in
   `.env` tune the session.

## License

MIT - see `LICENSE`.
