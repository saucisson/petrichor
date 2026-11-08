# ADR-0021: Driver commands as one CLI package

- **Status**: draft (proposed by the agent, awaiting the human owner's
  acceptance)
- **Date**: 2026-11-08
- **Deciders**: template owner

## Context

The template accumulated one standalone Python file per driver verb:
eleven flat scripts in `scripts/`, opened by eleven `make` recipes.
Three forces pushed against that layout:

1. **Duplicated plumbing.** Every script re-implemented the same
   `.env` loader, the `GITHUB_TOKEN` reader, the `origin` remote
   slug parser, git runners and exit-code constants - about 400
   duplicated lines (11% of the layer), with seven divergent copies
   of the same token reader and four divergent git runners. A fix to
   one copy (say, a clearer "no token" message) never reached the
   others.
2. **File proliferation.** Each new driver verb added a new file, and
   a flat directory of near-twins is the layout where copy-paste
   drift thrives.
3. **One stable surface.** Agents and prompts call `make`, not
   python: about 70 references across the docs and the prompts. Any
   reorganization must keep those working, unchanged.

## Options considered

1. **Replace the drivers with documentation** (a list of allowed git
   and `gh` commands per context). Rejected: documentation verifies
   nothing. The drivers exist to mechanically enforce the criteria
   (review before merge, CI green, open pull request, replace-don't-
   append); a checklist re-reads the same rules with no gate. The
   runtime permission profile (ADR-0015) allowlists the verbs
   precisely because they are code, not prose.
2. **Keep the flat files, share nothing.** Rejected as-is: the
   duplication above is structural, and re-implementing the plumbing
   in every new driver is an invitation to diverge.
3. **One Python package with subcommands.** `scripts/` becomes a
   package; `python -m scripts <command>` dispatches to per-domain
   modules; the shared plumbing lives once in a private module.
   Chosen.

## Decision

- `scripts/` is a package. Each driver runs as one subcommand of
  `python -m scripts` (`claim`, `pr`, `update-pr`, `merge-pr`,
  `wait-ci`, `review`, `review-bundle`, `agent-review`, `day-report`,
  `sync-daily`, `night`), and each domain module registers its own
  subcommand(s): one module, one driver domain, at most a few
  related subcommands.
- **Subcommands mirror the make targets one-to-one.** `make` stays
  THE agent surface (ADR-0015 allowlist, prompts, docs); the recipes
  simply call `$(PYTHON) -m scripts <command>`. No target name, no
  variable, no help line changes.
- Shared plumbing lives in `scripts/_shared.py` (stdlib only:
  environment, token, slug, git runners, exit codes) and the shared
  GitHub API primitives in `scripts/_github.py`. `_shared` imports
  no third-party package, so the git-only subcommands (`day-report`,
  `sync-daily`) and `review-bundle` keep running stdlib-only, in the
  bareest CI image, as they always did.
- **One exit-code contract**, shared: 0 success, 1 criteria unmet
  (refused, nothing written), 2 protocol failure. Subcommands keep
  readable local aliases of the codes they return.
- **Module budget: about 500 lines.** Beyond that a module is split;
  the point is to keep concurrent pull requests from colliding in
  one giant file. The night loop is the current exception (~790
  lines): one loop, one narrative, left whole.

## Consequences

- A new driver verb is one subcommand on the existing CLI - a module,
  a `register`, a make recipe - instead of an eleventh look-alike
  file.
- The protocol-level duplication (~400 lines) is gone; a fix to the
  plumbing lands once.
- Agents must not call `python scripts/<name>.py` anymore: the flat
  files are gone, and `python -m scripts <command>` (or the make
  target) is the only entry point. `mypy scripts` now type-checks a
  package, same strictness.
- The stale `scripts/<name>.py` paths in the historical ADRs (0009,
  0010, 0017, 0018, 0019, 0020) were updated to the new entry point
  in this same change, on the owner's explicit instruction: this
  repository is a template, and a project instantiated from it would
  inherit dead paths on day one. The decisions themselves are
  untouched; only the paths they cite moved with the layout.
- The deferred `httpx` import inside the review runner is deliberate:
  the same module also carries `review-bundle`, which must stay
  stdlib-only. It is marked and commented in the code.
