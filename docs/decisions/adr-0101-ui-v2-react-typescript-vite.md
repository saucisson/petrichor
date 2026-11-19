# ADR-0101: UI v2 — React 18+ / TypeScript strict / Vite

- Status: proposed (drafted in the P52 PR per ADR-0010; the human accepts it)
- Date: 2026-11-19
- Supersedes: the P18 decision « UI statique sans dépendance » (vanilla
  JS in `src/petrichor/api/static/`, delivered by issue #18 / PR #106)

## Context

The P18 UI was a deliberate trade: zero build step, zero dependency, three
static files served as-is. Spec §9 v1.3 (2026-11-19) replaces that decision:
the reference mockup `docs/ui-mockup/philosophes-3.tsx` is already React
TSX, the wave 7 features (P34 convergence, P37 editable formula, P38 proof
folders, P53 i18n) need a component model, and friction F5 (the UI was never
observed end-to-end) demands real component tests and a browser e2e — both
mature in the React ecosystem. The vanilla UI also grew to ~13 Ko of
hand-rolled DOM code that tests could not reach.

## Decision

- **Framework**: React 18+ with TypeScript strict and Vite as the build
  tool. The app lives in `src/petrichor/api/ui/` (product code stays under
  `src/petrichor/`, ADR-0100); `vite build` emits into
  `src/petrichor/api/static/`, which the API serves under `/ui/` **without
  any URL change** — no Node runtime is needed at serve time.
- **Toolchain pinned**: Node version fixed in `.node-version` (22.12.0),
  `package-lock.json` committed, `npm ci` in CI.
- **Gates** (wired into `make check` and CI): ESLint, `tsc --noEmit`,
  vitest + `@testing-library/react`. The Python gates are unchanged.
- **Parity first**: the React port reproduces the four steps of the vanilla
  UI against the same `/v1` endpoints (proven by component tests); the
  vanilla files are deleted only once parity is proven.
- **Server stays the source of truth** (spec §9, §19.4): no verdict is
  ever computed client-side; the a11y markers of the mockup (P12) are
  preserved.

## Options considered

- **Svelte / Solid** (rejected): smaller bundles, but the reference mockup
  is React TSX — porting it would cost a rewrite of the P1–P12 reference,
  and the i18n (react-i18next) and testing (@testing-library/react)
  ecosystems are mature and match the wave 7 needs (P53 i18n, P57 Playwright
  e2e).
- **Vue** (rejected): same ecosystem argument; no existing asset in the
  repo is Vue.
- **Keep vanilla JS** (rejected): spec §9 v1.3 supersedes the P18 decision;
  the untestable hand-rolled DOM is exactly what friction F5 flagged.

## Consequences

- The built bundle (`static/index.html` + `static/assets/*`) is committed,
  like the vanilla files were; `make ui-build` regenerates it.
- CI installs Node from `.node-version` and runs the frontend gates.
