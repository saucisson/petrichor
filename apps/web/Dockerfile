# Multi-stage build (ADR-0018): node builds the bundle, nginx serves it.
# Production deploys are the owner's business - this image proves the app
# builds and serves; `make compose-up` wires it next to the notify tool.

FROM node:24-bookworm-slim AS builder

WORKDIR /app

# Install dependencies first so source changes do not invalidate the layer.
COPY package.json package-lock.json ./
RUN npm ci

# Then build the bundle.
COPY . .
RUN npm run build

FROM nginx:1.29-alpine AS runtime

COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
