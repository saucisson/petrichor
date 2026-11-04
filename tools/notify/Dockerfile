# Multi-stage build (ADR-0001): uv resolves and installs in the builder,
# the runtime image is slim and carries only the virtual environment.
# Build context is the REPOSITORY ROOT (see docker-compose.yml) because
# the root pyproject.toml owns the tool's packaging (ADR-0017).

FROM ghcr.io/astral-sh/uv:python3.15-bookworm-slim AS builder

WORKDIR /app

# Sync dependencies first so source changes do not invalidate the layer.
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN --mount=type=cache,target=/root/.cache/uv \
	uv sync --frozen --locked --no-dev --no-install-project

# Then install the tool itself (packaged from tools/notify/src).
COPY tools/notify/src ./tools/notify/src
RUN --mount=type=cache,target=/root/.cache/uv \
	uv sync --frozen --locked --no-dev

FROM python:3.15-slim-bookworm AS runtime

# Non-root user: the container never needs write access to its code.
RUN groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1
USER app

# Smoke by default: proves the pipeline runs inside the image.
CMD ["notify", "smoke"]
