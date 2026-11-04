# ADR-0014: hatchling build backend

- Status: accepted
- Date: 2026-11-02

## Context

The template needs a build backend for a standard `src/`-layout package.
Candidates: hatchling, setuptools, flit-core, and uv's own young
`uv_build`. Notably, in October 2026 Michał Górny (Gentoo Python
maintainer) publicly warned against backends that lose or approximate
packaging metadata in the name of speed - the survival of distro
packagers depends on standard, complete `dist-info`.

## Options considered

- **hatchling** (chosen): standards-complete, fast, minimal config for
  `src/` layout, actively maintained, no legacy behavior.
- **setuptools**: universal but carries two decades of legacy semantics
  and configuration ambiguity - a tax on every future reader.
- **uv_build**: fastest and uv-native, but young; per the metadata-loss
  concern above, too early to bet the template's packaging on it.
- **flit-core**: minimal, but its flat-metadata model fits small
  single-purpose packages more than templates that will grow tooling.

## Decision

hatchling with an explicit `packages` list, PEP 621 metadata
in `pyproject.toml`, no `setup.py`/`setup.cfg`.

## Consequences

- Standard, complete metadata: `pip install`, distro packagers, and
  release-please all see the same truth.
- Negative: hatchling is not uv-native, so the build step is marginally
  less integrated than `uv_build` would be. When `uv_build` matures and
  the metadata concern is settled upstream, this ADR is the place to
  revisit - a one-line backend swap behind an explicit decision.
