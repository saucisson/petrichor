# ADR-0103: VPS deployment — automatic deploy of `daily` to petrichor.linard.fr

- Status: proposed (drafted per ADR-0010; the human accepts it)
- Date: 2026-11-20

## Context

The API is containerized (ADR-0102) and serves both the UI v2 (`/ui/`) and
the REST API (`/v1/`) on one loopback-published port. The product needs to be
reachable on the public internet at `https://petrichor.linard.fr` (DNS already
points at the VPS `vps-89091363.vps.ovh.net`, 145.239.197.11). The machine is
shared with at least `layered-data.linard.fr`: nothing existing may be
modified or interrupted. Every build of `daily` should be deployed
automatically.

## Decision

- **One clone on the VPS**: `/srv/petrichor` is a git clone of this
  repository on branch `daily`. The deploy script
  (`deploy/deploy.sh`) fast-forwards it to `origin/daily` itself, so the
  manual deployment and the CI deployment run the exact same code path.
- **Production compose override**: `deploy/docker-compose.prod.yml` layered
  over the base `docker-compose.yml` only changes `restart: "no"` to
  `restart: unless-stopped`. Loopback-only published ports, healthchecks,
  non-root read-only containers are inherited unchanged (ADR-0102).
- **Default service**: `api` — it serves the UI and the API; the check
  runner is not wired to the engines yet, so engines are opt-in
  (`./deploy/deploy.sh api dummy verifypn its-tools`).
- **Reverse proxy**: the existing reverse proxy on the VPS routes the
  `petrichor.linard.fr` host to `127.0.0.1:8080` (the API's published
  loopback port). The site config lives in `deploy/nginx/` and is installed
  alongside the existing `layered-data.linard.fr` config, never over it.
  TLS is terminated by the proxy with a Let's Encrypt certificate.
- **CI**: `.github/workflows/deploy.yml` triggers on push to `daily` and
  runs `deploy/deploy.sh` on the VPS over SSH, using the repository secrets
  `SSH_PRIVATE_KEY` and `VPS_HOST` (user `ubuntu`, overridable via the
  `VPS_USER` variable). Secrets are never committed (ADR-0008); the API key
  lives only in the git-ignored `.env` on the VPS.

## Consequences

- A push to `daily` is live on `https://petrichor.linard.fr` within minutes;
  the deploy is idempotent and fails loudly (non-zero exit) if a healthcheck
  does not pass, leaving the previous containers' state inspectable.
- The shared machine is untouched outside the petrichor site config and the
  `/srv/petrichor` clone.
- `main` is never deployed by this path; only `daily`.
