#!/usr/bin/env bash
# Idempotent deployment of the Petrichor stack on the VPS (ADR-0103).
#
# Runs ON the VPS, from the root of the repository clone (default
# /srv/petrichor). The same script is used by the manual deployment and by
# the CI workflow (.github/workflows/deploy.yml), so both paths are
# identical by construction.
#
#   ./deploy/deploy.sh              # deploy the default services (api)
#   ./deploy/deploy.sh api dummy    # deploy an explicit service list
#   DEPLOY_REF=feat/x ./deploy/deploy.sh   # deploy another ref (manual runs)
#
# Prerequisites on the VPS:
#   - a git clone of this repository at /srv/petrichor
#   - a `.env` file next to docker-compose.yml with PETRICHOR_API_KEY set
#     (git-ignored, never committed - ADR-0008)
#   - docker with the compose plugin (sudo as needed)
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPOSITORY_ROOT"

DEPLOY_REF="${DEPLOY_REF:-daily}"
COMPOSE_FILES=(-f docker-compose.yml -f deploy/docker-compose.prod.yml)
SERVICES=("$@")
if [ "${#SERVICES[@]}" -eq 0 ]; then
  # The API serves the UI under /ui/ and the REST API under /v1/; the check
  # runner is not wired to the engines yet (503 runner_not_configured), so
  # the API alone is the deployable unit today. Engines can be added
  # explicitly: ./deploy/deploy.sh api dummy verifypn its-tools
  SERVICES=(api)
fi

if [ ! -f .env ] || ! grep -q '^PETRICHOR_API_KEY=' .env; then
  echo "deploy: .env with PETRICHOR_API_KEY is required at $REPOSITORY_ROOT/.env" >&2
  exit 1
fi

echo "deploy: updating the clone to origin/$DEPLOY_REF"
git fetch --quiet origin "$DEPLOY_REF"
git checkout --quiet --force "$DEPLOY_REF"
git reset --quiet --hard "origin/$DEPLOY_REF"

echo "deploy: building and starting services: ${SERVICES[*]}"
docker compose "${COMPOSE_FILES[@]}" up -d --build "${SERVICES[@]}"

echo "deploy: waiting for the healthchecks"
DEADLINE=$((SECONDS + 120))
for service in "${SERVICES[@]}"; do
  while [ "$SECONDS" -lt "$DEADLINE" ]; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$(docker compose "${COMPOSE_FILES[@]}" ps -q "$service")" 2>/dev/null || true)"
    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      echo "deploy: $service is $status"
      break
    fi
    sleep 2
  done
  if [ "$SECONDS" -ge "$DEADLINE" ]; then
    echo "deploy: $service did not become healthy in time (last status: ${status:-unknown})" >&2
    docker compose "${COMPOSE_FILES[@]}" ps >&2 || true
    exit 1
  fi
done

echo "deploy: done"
docker compose "${COMPOSE_FILES[@]}" ps
