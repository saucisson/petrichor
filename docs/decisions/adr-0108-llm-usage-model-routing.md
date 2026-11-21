# ADR-0108: LLM usage → model routing with pinned exact versions

- Status: proposed (drafted in the P74 PR per ADR-0010; the human accepts it)
- Date: 2026-11-21

## Context

Issue P74 / #180, annexe B §B.4 of the 2026-11-20/21 directive. Until now a
single mutable model, `mistral-small-latest`, served five heterogeneous
usages (semantic profile, reading, clarification, clustering, narration).
The `-latest` alias moves without notice and breaks the replays of P46;
the five usages have different input/output budgets (annexe B §B.4) and
deserve different model tiers.

## Decision

- **One model per usage**, routed by a versioned configuration
  `{usage: {model, version}}` in `src/petrichor/llm/routing.py`
  (`USAGE_MODEL_ROUTING`), validated at provider construction:
  - `semantic-profile` and `cluster` → **Mistral Large 4**
    (`mistral-large-4-2510`);
  - `reading` and `clarification` → **Mistral Small 4**
    (`mistral-small-2603`);
  - `narration` → **Ministral 3 14B** (`ministral-14b-2512`), with a
    pinned **fallback to Mistral Small 4** (`mistral-small-2603`) when the
    primary model degrades.
- **Pinned exact versions only.** Every identifier is exact; any
  `-latest` alias in the configuration is rejected with an explicit
  `RoutingError` (guard test in `tests/test_llm_routing.py`). The exact
  identifiers were pinned on 2026-11-21 against the public Mistral catalog
  (`mistral-small-2603`, `ministral-14b-2512` confirmed; the Large 4
  suffix follows the catalog YYMM convention of its 2026-10 public
  preview) and are re-confirmed against `GET /v1/models` at deploy time.
- **Observability.** `usage_totals()` stays per (usage, model) couple and
  the archive of every request/response carries the exact
  (model, version) of the route used, apart from the tool-time budget
  (spec §12.4, P36).
- **No vendor lock-in.** The routing lives behind `LLMProvider`
  (`MistralProvider` takes the routing table as a constructor argument);
  the deterministic mock is unchanged and remains the default provider of
  the tests. The obsolete `PETRICHOR_LLM_MODEL` setting (whose default
  was a `-latest` alias) is removed.

## Consequences

- Replays (P46) are reproducible: a recorded call names the exact pinned
  model and version it was served by.
- A model change is a one-line, reviewable diff in a versioned file plus
  an ADR amendment — never a silent alias drift.
- The narration fallback is explicit and logged (`llm_narration_fallback`),
  never an invention.
