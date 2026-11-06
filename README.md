# Petrichor

Petrichor is an AI-agent-driven development project: two agents (developer + reviewer) work autonomously all
day inside a bounded blast radius (`daily`), a human decides at the day
boundary (`main`).

The repository **is** the `petrichor` project. Alongside it ships the **`notify` tool**
(`tools/notify/`): the contact channel of the agent protocol. Your
coding agent (Vibe Code or any other) calls it to tell you that
something on an issue needs your interaction, through a Discord webhook
(or console fallback), with a severity contract (`question` parks,
`urgent` stops all merges).

## Quick start

```console
uv sync                 # exact environment from the committed lockfile
make check              # the full quality gate (Python + web)
uv run notify smoke    # prove the notification pipeline is alive
```

Web frontend and shared runtime:

```console
make web-install        # npm install in apps/web - writes package-lock.json
make compose-up         # build and start notify + web (docker compose)
# then open http://localhost:3000
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
| Architecture      | Onion, data-first - `docs/architecture.md` (ADR-0002)      |
| Toolchain         | Python 3.15, uv, hatchling - ADR-0001, ADR-0014            |
| Web frontend      | React + strict TypeScript, layer boundaries - ADR-0018    |
| Shared runtime    | docker compose: notify + web - `make compose-up`           |
| Quality gates     | Ruff, mypy strict-plus, pytest 90%, import-linter - ADR-0008 |
| Security gates    | gitleaks, pip-audit, Ruff S - ADR-0009                    |
| Agent protocol     | `AGENTS.md` + `docs/agents/` (ADR-0011)                   |
| Agent runtime      | `.vibe/` permissions profile - ADR-0019                   |
| Branch model      | `docs/branching.md` (ADR-0010)                            |
| Human contact      | the `notify` command in `tools/notify/` - ADR-0012, 0017 |
| Reviewer launch    | CI job, bundle, required status check - ADR-0016          |
| Human override     | the `human-approved` PR label waives any review block - ADR-0020 |
| Decisions          | `docs/decisions/` - 20 ADRs with rejected alternatives    |

## The repository layout

```text
src/                     the PRODUCT's Python code (starts empty, ADR-0017)
apps/web/                web frontend: Vite + React + strict TypeScript
tools/notify/
    src/notify/          the notification tool (reference application)
        models/          domain language: Pydantic, NewTypes, closed sets
        services/        pure functions behind Protocol ports
        infrastructure/  adapters: console, Discord webhook (httpx)
        main.py          composition root - the ONLY place logging is configured
    tests/               unit / integration / property tests
scripts/                 day-cycle and review orchestration
docs/                    constitutional documents and ADRs
```

## Setting up this repository

1. Clone **inside the WSL/Linux filesystem** (`~/...`), never on
   `/mnt/c` (also an I/O question). On WSL, mount Windows drives
   read-only or not at all: `/etc/wsl.conf` → `[automount]`.
2. `uv sync` to generate `uv.lock` (commit it - ADR-0001), then
   `make web-install` in `apps/web` to generate `package-lock.json`
   (commit it too - ADR-0018).
3. At the first Vibe Code session, review `.vibe/config.toml` and
   **trust the folder** - the runtime permissions profile is then
   loaded (ADR-0019). Keep the CLI current before widening it.
4. Enable branch protections per `docs/branching.md` (`main` and
   `daily`, required status checks, auto-merge).
5. `uv run pre-commit install`.
6. Enable Renovate and release-please on the repository.
7. Replace `YOUR_GITHUB_USERNAME` in `.github/CODEOWNERS`.
8. Set `DISCORD_WEBHOOK_URL` in the CI/CD secrets or `.env`.
9. Add the `MISTRAL_API_KEY` repository secret and (optionally) the
   `REVIEWER_MODEL` repository variable - the reviewer CI job needs
   them (ADR-0016). Mark the `reviewer` status check as required next
   to the `check` and `web` jobs.
10. Optional: create a `human-approved` label on the repository. Pose
    it manually on a pull request to waive every BLOCKING review
    finding (ADR-0020) - the reviewer re-runs when the label changes.

## License

MIT - see `LICENSE`.
