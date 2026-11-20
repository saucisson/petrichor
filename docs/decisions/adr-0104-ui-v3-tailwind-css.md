# ADR-0104: UI v3 — Tailwind CSS et socle de design tokenisé

- Status: proposed (drafted in the P66 PR per ADR-0010; the human accepts it)
- Date: 2026-11-20

## Context

Human observation of 2026-11-20 (wave 9, spec §9 v1.4): the UI v2 works but
has no visual charter — `styles.css` (~150 lines of minimal utility CSS)
carries no hierarchy, no states, no identity. A CSS framework is to be
adopted, Tailwind pressenti, under the constraints of the existing socle:
Vite build (ADR-0101), no heavy runtime dependency, a11y with AA contrast
(mockup P12). The refactor must be at **strict parity**: no behavior change,
no string change, all existing tests (vitest, e2e `@ui`) keep passing.

## Options considered

- **Tailwind CSS v4** (chosen): utility-first, zero runtime (build-time
  only, so the "no heavy runtime dependency" constraint holds); its Vite
  plugin (`@tailwindcss/vite`) purges dead CSS automatically at build time;
  v4's CSS-first configuration (`@theme`) is exactly a named-token design
  system, which the issue demands (palette, spacing, typography, radii,
  states as named tokens); mature with the existing React/TS/Vite stack.
- **Hand-rolled design system** (rejected): it is what `styles.css` already
  is — ~150 lines with no hierarchy or states; growing it into a real design
  system is unbounded work and untestable, the exact friction the wave 9
  observation flags.
- **CSS modules** (rejected): scopes styles per component but provides no
  design tokens, no state conventions, no a11y defaults; it would keep the
  visual poverty while adding build complexity.
- **Other frameworks (Bootstrap, Bulma, …)** (rejected): component-heavy,
  opinionated DOM/markup that fights the existing React components and the
  e2e selectors (`.question-card`, `#steps button[data-step]`, …); heavier
  runtime CSS than needed for an internal tool.

## Decision

- **Tailwind CSS v4**, pinned exact versions in `package.json` +
  `package-lock.json` (`tailwindcss` 4.3.5, `@tailwindcss/vite` 4.3.5),
  wired through its Vite plugin in `vite.config.ts` — dead CSS is purged at
  build time by automatic content detection.
- **Design socle as named tokens** in the `@theme` block of
  `src/petrichor/api/ui/src/styles.css` (the single style entry point):
  - palette: `--color-primary` #1d4ed8 (8.6:1 on white), `--color-primary-hover`
    #1e40af, `--color-ink` #111827, `--color-muted` #4b5563 (7.5:1),
    `--color-line` #d1d5db, `--color-surface` #f9fafb, `--color-error` #b91c1c
    (5.9:1), `--color-highlight` #fde68a (12.6:1 with ink) — **every entry
    chosen for WCAG AA contrast (≥ 4.5:1) on its background**;
  - typography: `--font-sans` system stack, Tailwind type scale;
  - radii: `--radius-control` 0.375rem, `--radius-card` 0.5rem;
  - states: visible focus (3px primary outline, `:focus-visible`),
    `hover:*`, `disabled:opacity-60 disabled:cursor-not-allowed`, error text
    in `--color-error`.
- **No ad hoc style duplicated in components**: reusable control styles are
  defined once as `@utility btn` / `@utility btn-primary` in `styles.css`;
  components apply them (`className="btn"`) plus layout utilities. The
  semantic class names the e2e relies on (`.question-card`, `.profile-entry`,
  `.budget-line`, `.run-verdict`, `.proof-dossier`, `#steps button[data-step]`,
  …) are kept and now defined with `@apply` from the tokens.
- **Strict parity**: same DOM ids, data attributes, aria attributes, i18n
  keys and behavior; the four steps are refactored visually only. The built
  bundle in `src/petrichor/api/static/` is regenerated (`make ui-build`) and
  committed, as ADR-0101 requires.

## Consequences

- `styles.css` shrinks from hand-written rules to tokens + `@apply`
  definitions; new UI work composes utilities instead of growing the
  stylesheet.
- The CSS bundle stays small (purged at build); no runtime dependency is
  added — Tailwind is dev-only.
- P67 (chrome + guided stepper) builds on the `btn`/`btn-primary`
  distinction and the token palette established here.
