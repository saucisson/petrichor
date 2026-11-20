# ADR-0102: API containerization — the Petrichor API as a docker compose service

- Status: proposed (drafted per ADR-0010; the human accepts it)
- Date: 2026-11-20

## Context

Until now the API ran only as a local Python process
(`PYTHONPATH=src python -m petrichor.main`), while the engines were already
containerized (spec §19.4: one engine = one compose service, GPL engines
built locally, non-privileged). Deploying on a server requires the API itself
to be a container: same image everywhere, no host Python dependency, and a
single `docker compose up` brings up the whole stack.

## Decision

- **Multi-stage `Dockerfile.api`**: a Node stage builds the UI v2 SPA
  (`npm ci && npm run build` → `static/`), then a `python:3.15-slim` stage
  installs the locked production dependencies with `uv sync --locked
  --no-dev --no-install-project`, copies `src/petrichor/`, and serves with
  `python -m petrichor.main`.
- **Compose service `api`** in `docker-compose.yml`: image
  `petrichor-api:2.0.0`, non-root user (65534), read-only root filesystem,
  `no-new-privileges`, all caps dropped, healthcheck on `/ui/`, published on
  `127.0.0.1:${API_HOST_PORT:-8080}:8080`. A named volume `petrichor-data` is
  mounted at `/app/.petrichor` for the persistent stores (sessions,
  arbitrations, witnesses, …).
- **Host binding**: `PETRICHOR_HOST` environment variable (default
  `127.0.0.1` locally, `0.0.0.0` in the container via the Dockerfile) so the
  published port can reach the WSGI server inside the container.
- **API key**: `PETRICHOR_API_KEY` is required (compose interpolation fails
  with `:?` if unset) — a misconfigured server never starts open.

## Consequences

- Local development is unchanged: `python -m petrichor.main` still works and
  listens on loopback.
- Server deployment: `PETRICHOR_API_KEY=... docker compose up -d` brings up
  the API plus any engines.
- The `.env` file (git-ignored) holds local secrets; never committed.
