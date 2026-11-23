# ADR-0109: web v1 bascule — OIDC browser login, session cookie, X-API-Key removal

- Status: proposed (drafted in the #207 PR per ADR-0010; the human accepts it)
- Date: 2026-11-23

## Context

Issues #205-#210 (milestone 12, contract `docs/consigne-interface-web-v1.md`)
introduce public zones and a browser login. Until now the API authenticated
every `/v1/` route with a single static `X-API-Key` (`PETRICHOR_API_KEY`,
required by ADR-0102's compose interpolation), and the Bearer OIDC layer was
optional on top of it. A static shared key cannot back a multi-user web
login: it is a bearer credential that leaks through browsers, logs and
bookmarks.

## Decision

- The browser authenticates with an OIDC Authorization Code + PKCE S256 flow
  (`/auth/login`, `/auth/callback`, POST `/auth/logout`, `/auth/me`, all
  discovery-driven — no hard-coded endpoint). The applicative session is a
  server-side record keyed by an opaque id carried in an HttpOnly, Secure,
  SameSite=Lax cookie (`petrichor-session`); a JWT is never put in a cookie,
  and the cookie value is never logged (digest only, ADR-0005).
- The `X-API-Key` credential is **removed** from code, UI, tests, docs and
  deployment config (`docker-compose.yml` no longer requires
  `PETRICHOR_API_KEY`). Machine callers (CLI/CI, the study runner) use a
  Bearer PAT validated by the existing PT-1 verifier; the auth gate accepts
  the session cookie or a Bearer PAT and answers `401 session_required`
  otherwise. `AUTH_DISABLED=1` stays the L3 dev-only bypass, never in prod.
- Public read-only routes (issue #206: `GET /v1/catalog/models`,
  `GET /v1/models/{id}`) stay credential-less; `/auth/*` and the static UI
  are public by design.

## Consequences

- ADR-0102's "API key required" clause is superseded by this ADR (compose no
  longer interpolates `PETRICHOR_API_KEY`).
- Sessions are in-memory and TTL-bounded (8 h): a restart logs everybody
  out. A shared session store is a deliberate non-goal of web v1.
- Issue #210 tracks the roadmap/backlog reference updates.
