# Worklog

Append-only, agent-written audit trail. One entry per commit or pull
request; the human owner reads it as part of the `daily` → `main`
merge review at the end of day.

This file is **not** constitutional: agents append to it as part of
their normal work, without an escalation. It is not a substitute for
the pull request report either - it is the persistent, chronological
record of what the team actually did, kept in the repository so the
day's audit trail travels with the `daily` → `main` diff.

Rules:

- The developer agent appends its entry in the pull request branch,
  so the entry arrives with the merge into `daily` and the reviewer
  sees it in the diff.
- One entry per merged pull request; a direct commit on `daily`
  (revert protocol, docs-only fix) carries its own entry in the same
  commit.
- Newest entry on top, directly below the marker line. Never edit,
  reorder or delete past entries: the git history is the tamper-evident
  backstop, and rewriting the log is an honesty violation.
- The same honesty rules as any report (`AGENTS.md`): evidence you did
  not run is stated as such, not implied.

## Entry format

One entry per commit or pull request:

```markdown
## YYYY-MM-DD - one-line summary (PR #N / <commit hash>)

- **Issue**: the issue(s) closed, or `none`.
- **Done**: what the change does, mapped to the acceptance criteria.
- **Evidence**: the gates run and their results (lint, types, tests,
  coverage) - and what was **not** run, with the reason.
- **Judgement calls**: every decision between two reasonable options,
  and what decided it (ADR, issue comment, human answer).
- **Escalations**: `needs-human` requests raised or consumed, if any.
```

---

<!-- Append new entries directly below this line. -->

## 2026-11-10 - P11 persistent sessions service (issue #11, branch feat/11-sessions-persistantes)

- **Issue**: #11 (P11 · Service Gérer : sessions persistantes et reprise, spec §4).
- **Done**: new `src/petrichor/session_store.py` — `SessionStore` persists sessions as JSONL state file (`sessions.jsonl`) plus per-session append-only timestamped journal (`<sid>/journal.jsonl`), reloaded on construction so a process restart loses nothing; `StoredSession` frozen record (session_id, model_id, created_at, updated_at, run_ids); `JournalEntry` (timestamp, event, detail). API endpoints in `app.py`: `POST /v1/sessions` (201 + Location, 404 unknown model), `GET /v1/sessions` (list in creation order), `GET /v1/sessions/{sid}` (full state), `GET /v1/sessions/{sid}/journal` (export). `create_app` takes an optional `session_store`; `main.py` wires one at `.petrichor/sessions`. `openapi.yaml` documents all four paths with Session/JournalEntry schemas. Tests: `tests/test_api_sessions.py` (9 tests: create 201+Location, 404 unknown model, list order, get state, 404 unknown session, journal timestamped append-only, **survives process restart** via a second SessionStore on the same directory, auth required).
- **Evidence**: run: `make check` green — ruff, mypy strict, import-linter, 500 tests passed, coverage 93.54% >= 90%. Not run: CI until push.
- **Judgement calls**: session store is file-based JSONL (same pattern as RunArchive) rather than in-memory + snapshot, because the acceptance criterion explicitly requires surviving a process restart; `list()` renamed to `list_all()` to avoid shadowing the builtin `list` type in mypy.
- **Escalations**: none.

---

## 2026-11-10 - template sync feec180 code part, review round 2 (PR #78, branch fix/template-sync-feec180)

- **Issue**: none (human-directed template sync from saucisson/project-template @ feec180).
- **Done**: applied the code-side changes from project-template main @ feec180 on top of daily's hardened version (first attempt took template files wholesale and lost daily's hardening - isascii guards, re.ASCII, IssueIdentifier NewType, request_or_fail, json_list_or_warn, OPEN_STATE_FILTER, UNKNOWN_HEAD_REF, causes preserved - all restored). New features: `build_session_command` extracted, `run_developer_session` uses Popen with heartbeat loop (HEARTBEAT_POLL_INTERVAL_SECONDS named constant), `--heartbeat` CLI flag (default 60s), `describe_claimed_items` names claimed items in the result line, post-session claim-label check logged, session prompts persisted under `.night/sessions/<run>/` instead of a tempdir. Tests: 4 new tests pinning describe_claimed_items, heartbeat defaults, and .night/sessions/ directory usage.
- **Evidence**: run: `make check` green - ruff, mypy strict, import-linter, 491 tests passed, coverage 94.51% >= 90%. Not run: CI until push.
- **Judgement calls**: kept daily's __main__.py (already has Runner Protocol + keyword-only dispatch + ADR-0006 escape comment); constitutional files still excluded (separate branch for human merge).
- **Escalations**: none.

---

## 2026-11-10 - P09 verifypn socle minimal + P10 jalon P0 bout en bout (issues #9, #10, branch feat/9-verifypn-socle)

- **Issue**: #9 (adaptateur verifypn — socle minimal), #10 (jalon P0 bout en bout réel sur Philosophes-3).
- **Done**: P09 — image `petrichor-engine-verifypn:4.4.2` construite localement (Dockerfile.verifypn multi-stage, source épinglée au tag v4.4.2 commit 0f4862d, bases épinglées par digest, lien statique, user non-root, read-only + tmpfs /tmp, healthcheck) ; service compose `verifypn` (port 18081) ; serveur stdlib-only `engines/verifypn_server.py` (POST /check : modèle canonique dans `parameters["model"]` → export PNML `engines/pnml_export.py` → requête PQL (`EF deadlock` / formule reachability) → verifypn v4.4.2 sort avec exit code 0 et verdict sur la ligne stdout `FORMULA <file> TRUE|FALSE` (MAYBE → unknown ; tout exit non nul → HTTP 500) → verdict normalisé, trace XML stderr rejouée en témoins §16, coût depuis STATE_SPACE STATES) ; driver compose généralisé (service/engine name+version en keyword-only, défauts dummy) ; tests `@engine` sur oracle benchmark (Philosophes-3 : EF deadlock satisfait avec témoin, EF (eat_1>0) satisfait, EF (eat_1>1) violé). P10 — `engines/api_runner.py` (`VerifypnCheckRunner`, CheckRunner → driver compose, échec → CheckFailed explicite) ; l'API injecte le modèle canonique dans `parameters["model"]` de la question ; test de fumée `@engine` `tests/test_smoke_p0.py` : import PNML → POST /v1/checks/deadlock → 202+Location → polling → verdict+témoin+coût lus du run store et de l'archive, rejouement idempotent (même run, journal inchangé).
- **Evidence**: run: `make check` green — ruff check/format, mypy strict (scripts + src), lint-imports, pytest complet **487 passed** (dont les 3 tests `@engine` verifypn contre le vrai conteneur, image `petrichor-engine-verifypn:4.4.2` buildée localement), couverture **94.51%** >= 90%. Not run: `make mutate`, `make smoke-test` (pas de config notify dans cet environnement).
- **Judgement calls**: v4.4.2 épinglé plutôt que HEAD (4.5.0 exige gcc-16/cmake 3.31, non dispo sur bookworm) ; le modèle transite par `parameters["model"]` plutôt qu'un champ dédié pour ne pas casser le contrat `EngineQuestion` partagé avec le dummy ; le témoin est rejoué côté serveur à partir de la trace des transitions (le CLI verifypn ne sort pas les marquages) ; main.py reste `runner=None` (le verrou de verdict interdit l'import d'engines hors du paquet — le wiring prod du runner est une décision d'architecture, hors périmètre P10 dont le critère est le scénario scripté).
- **Escalations**: aucune.

---

## 2026-11-10 - resumed pull request #76 left open by an earlier session: review round fixes for P07 (issue #7, branch feat/7-run-archive-session-budget)

- **Issue**: #7 (P07 · Archivage intégral des runs et budget de session), via recovery of open PR #76 (claimed with `make claim-pr PR=76`).
- **Done**: resumed PR #76 where the earlier session stopped: its working tree held uncommitted fixes for the latest review report (0 BLOCKING, 2 TO-FIX) but was left broken. Completed them: (1) ADR-0003 TO-FIX - `_problem_body`/`_problem_response`/`_ProblemPayloadError` now take one frozen `ProblemPayload` record instead of a growing parameter list, all call sites converted (and a syntax error in `_parse_check_request` fixed); (2) atomic-admission TO-FIX - `RunStore.admit` takes a `RunAdmission` record and checks the session budget plus creates the run under one lock, with in-flight reservations (timeout + watchdog grace) released at the terminal state, so concurrent POSTs cannot both be admitted over the cap; `_post_check` uses it and the 429 carries remaining/cap/consumed/reserved/timeout. Tests: `TestRunStoreAccounting` ported to `admit`, plus two new tests pinning the fix - a store-level concurrent-admission test (two 6s reservations under a 6s cap: exactly one admitted, one refused with remaining 0.0) and the API-level review counterexample (two concurrent POSTs, cap 6s, timeout 1s: exactly one 202 and one 429). The bare `# type: ignore[arg-type]` suppressions in `build_client` are gone - the settings are now built with typed keyword arguments.
- **Evidence**: run: `make check` green on the final state - ruff, mypy strict, import-linter, 464 tests passed, coverage 97.43% >= 90%. `make wait-ci PR=76` exit 0 on the previous head 8f3ea7c (2 check runs green). Not run yet: CI and the re-review on the new head - they run after push.
- **Judgement calls**: the reservation model (in-flight worst-case tool time counted against the cap, replaced by the measured cost at the terminal state) was already the PR's stated guarantee (spec §20.2 "jamais débordé"); this change makes it atomic under concurrency, which is what the review demanded. Store-level and API-level concurrency tests both added because the review's counterexample was API-level while the invariant lives in the store.
- **Escalations**: none.

## 2026-11-09 - P07: integral run archive + session budget cap (issue #7, branch feat/7-run-archive-session-budget)

- **Issue**: #7 (P07 · Archivage intégral des runs et budget de session).
- **Done**: every run is archived in full - `StoredRun` gains `timeout_seconds`, `tool_version`, `command` and `measured_seconds` next to the existing `input_hash` (spec §12.5); the runner outcomes carry the executed `command` and the `CheckRunner` protocol exposes `tool_version`; the cost is the wall-clock tool time measured with `time.monotonic()` around the runner call, never estimated (spec §12.4). No delete path exists: the store classes expose none (AST guard test) and `DELETE /v1/runs/{rid}` is 405 with the run surviving. The session budget cap is configurable (`PETRICHOR_SESSION_BUDGET_SECONDS`, default 3600 s per spec §20.2); a new run whose worst-case tool time (timeout + watchdog grace) does not fit the remaining budget is refused with an explicit 429 `session_budget_exceeded` carrying `remaining_budget_seconds` (plus cap/consumed/timeout) - no carry-over; idempotent replay of a known (model, question) bypasses the check. `openapi.yaml` updated with the new Run fields and the 429 response.
- **Evidence**: run: `make check` green on the final state - ruff, mypy strict (69 files), import-linter, 462 tests passed, coverage 97.41% >= 90%. Not run yet: CI (`make wait-ci`) and the reviewer session - they run after push.
- **Judgement calls**: 429 for the budget refusal (quota semantics, typed problem+json per ADR-0016); admission reserves the run's worst-case tool time (timeout + watchdog grace, the bound the watchdog enforces) so the cumulative measured cost never exceeds the cap ("jamais débordé", spec §20.2) while the archived cost stays measured; `tool_version` on the runner protocol (known even when a runner crashes, which archives `command=[]`); default cap 3600 s per spec §20.2. Diff is 745 changed lines (> ~500): a PR-level split proposal is in the pull request body (ADR-0019).
- **Escalations**: none.

## 2026-11-09 - issue #74 resolved by human: manual rebase of daily onto origin/main + force-with-lease push

- **Issue**: #74 (needs-human, sync-daily count-guard refusal) - closed.
- **Done**: the human performed the sync by hand: `git rebase origin/main` on daily (git auto-skipped the 50 patch-equivalent commits, warning "skipped previously applied commit" x50), then `git push --force-with-lease origin daily` (forced update 912058b...f728603). Post-push verification: `git merge-base --is-ancestor origin/main origin/daily` succeeds, 22 daily-only commits remain, `git cherry` shows all `+` (zero patch-equivalents left). ADR-0020 and the driver guard unchanged - the human path bypasses the guard by doing the rebase directly.
- **Evidence**: run: `git push --force-with-lease` exit 0, `git fetch origin`, `git rev-list --count origin/main..origin/daily` = 22, `git merge-base --is-ancestor` exit 0, `git cherry origin/main origin/daily` all `+`. Not run: `make check` on the rebased daily (no code change, only history rewrite - the tree at f728603 is identical to the pre-rebase 912058b tree).
- **Judgement calls**: none - human decision, agent executed the push and the issue closure.
- **Escalations**: #74 consumed (answered by the human action).

## 2026-11-09 - P06 review round 3 on PR #75: KindPattern distinct type, 400 on non-integer Content-Length, wait_terminal in idempotence tests (PR #75)

- **Issue**: #6 (P06).
- **Done**: addressed the third reviewer report. (1) `kind` is now the distinct type `KindPattern` (`NewType`) in `CheckRequest`, `CheckQuestion`, `StoredRun` and `RunStore.create_or_get` instead of bare `str` - a domain primitive, validated against `FAMILY_PATTERNS` at the API boundary. (2) `openapi.yaml` documents the `400` problem+json response when `Content-Length` is not an integer on `POST /v1/checks/{family}`. (3) `wait_terminal` added in the three idempotence/collision/timeout tests so no daemon thread leaks past its test.
- **Evidence**: run: `make check` green on the final state - ruff, mypy strict, import-linter, 448 tests passed, coverage 97.35% >= 90%. Not run yet: CI and the re-review (after push).
- **Judgement calls**: none.
- **Escalations**: none.

## 2026-11-09 - P06 review round 2 on PR #75: narration test made deterministic (wait for terminal state inside the capture window) (PR #75)

- **Issue**: #6 (P06).
- **Done**: addressed the second reviewer report (0 BLOCKING / 1 TO-FIX / 2 SUGGESTION). TO-FIX: `test_accepted_check_is_narrated` now calls `wait_terminal` inside the `capture_logs` window, so the daemon thread's `check_run_started` / `check_run_finished` events are guaranteed captured before the assertions - no more spurious flake.
- **Evidence**: run: `make check` green on the final state - ruff, mypy strict, import-linter, 448 tests passed, coverage 97.35% >= 90%. Not run yet: CI and the re-review (after push).
- **Judgement calls**: none.
- **Escalations**: none.

## 2026-11-09 - P06 review round on PR #75: frozen StoredRun (no torn reads), watchdog timeout backstop, run-id collision check (PR #75)

- **Issue**: #6 (P06).
- **Done**: addressed the reviewer report on PR #75 (0 BLOCKING / 1 TO-FIX / 2 SUGGESTION). TO-FIX: `StoredRun` is now frozen and `mark_done`/`mark_error` replace the whole entry via `model_copy(update=...)` under the lock, so a `GET /v1/runs/{rid}` can never serialize a torn state (status done with output None); terminal states are final (a late transition is refused and logged). SUGGESTION 1: added a watchdog `threading.Timer` per run (timeout + 5s grace) that forces the explicit `error`/`timeout` state when a runner ignores its timeout - a run can never stay `running` forever. SUGGESTION 2: `create_or_get` now regenerates the run id on collision instead of overwriting. New tests: `test_watchdog_forces_timeout_when_runner_ignores_it`, `test_terminal_state_is_final`.
- **Evidence**: run: `make check` green on the final state - ruff, mypy strict, import-linter, 448 tests passed, coverage 97.35% >= 90%. Not run yet: CI and the re-review (after push).
- **Judgement calls**: the watchdog fires at timeout + `WATCHDOG_GRACE_SECONDS` (5s) so a runner that honors the timeout itself always wins the race; the terminal-state guard makes the outcome deterministic either way.
- **Escalations**: none.

## 2026-11-09 - P06: async checks API - 202 + Location, (model, question) idempotence, anti-polymorphism 422, explicit error states (issue #6, branch feat/6-async-runs-checks)

- **Issue**: #6 (P06 · Runs asynchrones). Picked in auto-discovery: the only worklist item whose dependencies are resolved on daily (#3 and #4 closed); #7 is blocked-by #6, #10 blocked-by #6/#9. Claimed with `make claim ISSUE=6` (exit 0) before any other action.
- **Done**: new `src/petrichor/checks.py` (check families + closed per-family `kind` pattern sets from spec §14.1/§17.3, `CheckRequest`/`CheckQuestion`, `CheckRunner` port returning a discriminated `CheckOutcome` union, run idempotence key = sha256(model_id + canonical question JSON)) and `src/petrichor/run_store.py` (thread-safe run store, idempotent `create_or_get`, states running/done/error). `src/petrichor/api/app.py`: `POST /v1/checks/{family}` → 202 + `Location: /v1/runs/{rid}` with a background-thread execution, `GET /v1/runs/{rid}` polling; kind outside the family → 422 `pattern_not_in_family` (never reinterpreted, §12.1); unknown family → 404; unknown model → 404; 415/422/400/503 problem+json; 401 without `X-API-Key` (auth before parsing, as before). Engine failure, timeout and even a crashed runner become the explicit `error` state with kind `engine_error`/`timeout` - never a default verdict. `openapi.yaml` updated with both paths (ADR-0016); `ApiSettings.check_timeout_seconds` (env `PETRICHOR_CHECK_TIMEOUT_SECONDS`, default 30). Tests: `tests/test_api_checks.py` (27 tests incl. 2 hypothesis property tests) pinning every acceptance criterion and the log narration.
- **Evidence**: run: `make check` green on the final state - ruff, mypy strict, import-linter, 446 tests passed, coverage 97.58% >= 90% (new modules: app.py 100%, run_store.py 100%, checks.py 98%). Not run yet: CI and review (after push).
- **Judgement calls**: (1) the verdict lock (spec §19.4, enforced by `tests/test_engines_guard.py`) forbids any module outside `petrichor.engines` from importing that package - including `main.py` - so no real engine driver is wired into the server yet; `POST /v1/checks/{family}` answers 503 `runner_not_configured` until P09+ provides an adapter, and the runner port carries the normalized output as opaque JSON so the API never produces or interprets a verdict; (2) idempotent replay returns 202 + the same Location (the run resource is the answer), matching the models endpoint's 200-on-replay semantics; (3) per-family pattern sets are closed sets from the HAZOP catalogue (spec §14.1) plus `reachability.ef`/`reachability.fireability` from §17.3 - `structural.*` patterns are static-triage only and have no check endpoint; (4) unknown family in the path is 404 (no such resource), only a wrong `kind` in the body is 422.
- **Escalations**: none.

## 2026-11-09 - issues #3/#5/#8 closed; sync-daily refused on commit-count guard, escalated as #74

- **Issue**: #3 (P03), #5 (P05), #8 (P08) closed; #74 opened (needs-human).
- **Done**: closed issues #3, #5, #8 with a comment referencing the merged PRs (#71, #69, #70) and removed their `night-claimed` labels (via `gh`; the GitHub App connector returned 403 on issue writes this session). `make sync-daily` refused with exit 2: the rebase onto origin/main would change the daily-only commit count (67 before, 17 after); daily was restored, nothing pushed. Verified with `git cherry origin/main origin/daily` that 50 of the 67 daily-only commits are patch-equivalent to commits already on origin/main (earlier daily→main merge, different SHAs) and 17 are genuinely unique - the replay would lose no content, but the ADR-0020 fail-closed guard refuses any count change and no sanctioned agent path exists around it.
- **Evidence**: run: `git fetch origin`, `git rev-list --count` both directions (67 daily-only, 51 main-only), `git cherry` (50 `-` / 17 `+`), `make sync-daily` exit 2 with restore, `gh issue close` x3 + `gh issue edit --remove-label` x3, `gh issue create` #74 with `needs-human`, `make notify severity=question issue-identifier=74` delivered (console channel). Not run: the rebase push itself (refused by the driver, escalated).
- **Judgement calls**: escalated instead of hand-rebasing daily - ADR-0020 makes `make sync-daily` the only sanctioned rewrite of the protected branch, and a guard refusal is a stop-and-escalate signal, not an obstacle to route around. Created the `needs-human` label (it did not exist on this repo yet) per docs/agents/escalation.md.
- **Escalations**: #74 (needs-human, severity=question) - authorize the count-changing rebase, do the sync by hand, or amend ADR-0020's guard.

## 2026-11-09 - PR #69, #70, #71 merged into daily after rebase + fresh zero-BLOCKING reviews

- **Issue**: #5 (P05), #8 (P08), #3 (P03).
- **Done**: all three recovery PRs left open by earlier sessions are now merged into `daily`. Each branch was rebased onto the moving `daily` (only `WORKLOG.md` conflicted each time, both sides kept; for #69 also the add/add on `src/petrichor/engines/__init__.py`, resolved by keeping daily's stdlib-only `__init__.py` required by `Dockerfile.dummy` and importing `petrichor.engines.mini_engine` directly in the test). Review findings were addressed on the branches: #69 got the `property` tier (3 hypothesis tests), the `io`->`input_output` and `nxt`->`next_marking` renames, the automorphism node-budget test, and a split proposal in the PR body (ADR-0019); #70 got the session log-narration test, `FormulaText`/`HumanMessage` primitives, `conf`->`confidence` with verbatim aliases, a determinism property test, and a split proposal in the PR body. The standing needs-human on `REVIEWER_MODEL` is stale: `.env` now sets `large-4`, a valid alias, and `make review` ran cleanly (exit 0) on all three heads. Final reviews: #69 0 BLOCKING / 1 TO-FIX / 1 SUGGESTION, #70 0 BLOCKING / 0 TO-FIX / 1 SUGGESTION, #71 0 BLOCKING / 0 TO-FIX / 3 SUGGESTION. CI green (2/2 check runs) on each head before review; `make merge-pr` merged each (rebase merge, branch deleted).
- **Evidence**: run: `make check` green on each rebased branch (337 / 358 / 419 tests passed, coverage 96.29% / 96.59% / 97.32%); `make wait-ci` exit 0 on each PR head; `make review` exit 0 on each; `make merge-pr` exit 0 x3. Not run: `make mutate` (EOD on daily), a live `make night`.
- **Judgement calls**: over-budget PRs (#69 ~1500 lines, #70 ~740 lines) resolved by split proposals in the PR bodies per checklist section 7 / ADR-0019 rather than by splitting the branches, because the verdict lock (spec §19.4) and the single `LLMProvider` port (spec §8) make the units indivisible without breaking their own contracts.
- **Escalations**: none new; the reviewer-model needs-human is answered by the working `REVIEWER_MODEL=large-4` in `.env`.

## 2026-11-09 - PR #71 rebased onto daily (now including PR #69 / P05 and PR #70 / P08) (PR #71)

- **Issue**: #3 (P03).
- **Done**: rebased `feat/3-pnml-import-models-api` onto `origin/daily` after PR #69 and #70 merged; only `WORKLOG.md` conflicted (both sides kept, newest entry on top). No code interaction: the API/PNML layer touches `src/petrichor/api` + `pnml_import`, disjoint from `engines/` and `llm/`.
- **Evidence**: run: `make check` green on the rebased head (ruff, mypy strict, import-linter, 419 tests passed, coverage 97.32% >= 90%). Not run: CI until push.
- **Judgement calls**: none.
- **Escalations**: none.

## 2026-11-09 - P03: POST /v1/models PNML import + GET canonical, Symmetric non déplié (issue #3, branch feat/3-pnml-import-models-api)

- **Issue**: #3 (P03 · `POST /models` et import PNML). Picked in auto-discovery: the only worklist items whose dependencies are resolved on daily (#1 closed, #2's PR #59 merged); #6/#7/#9/#10 still blocked, and recovery PRs #69/#70 are stuck on the unanswered reviewer-model needs-human. Claimed with `make claim ISSUE=3` (exit 0) before any other action.
- **Done**: new `src/petrichor/pnml_import.py` (stdlib ElementTree PNML → canonical `petri+json`, model id derived from the content hash, injectable clock; P/T and Symmetric net types, everything else rejected as unsupported), `src/petrichor/model_store.py` (content-hash idempotence), `src/petrichor/api/` (WSGI app: `POST /v1/models` 201/200 idempotent, `GET /v1/models/{id}` canonical, `X-API-Key` auth before parsing, RFC 7807 problem+json everywhere, OpenAPI contract `openapi.yaml` kept with the code per ADR-0016), `src/petrichor/main.py` (composition root, wsgiref). No new dependency (stdlib WSGI + httpx's WSGITransport in tests) - a web framework would have been an escalation-list item. Tests: `tests/test_pnml_import.py`, `tests/test_api_models.py`, fixtures `tests/fixtures/philosophes3{,-symmetric}.pnml` (9 places / 6 transitions / 24 arcs).
- **Evidence**: run: `make check` green on the final state - ruff + format, mypy strict (incl. src), import-linter, 351 tests passed, coverage 97.91% >= 90% (new modules 100%). Not run yet: CI and review (after push).
- **Judgement calls**: (1) WSGI on stdlib instead of FastAPI - new dependencies are on the escalation list and unanswered at night; (2) model id = `mdl_` + first 8 hex of the content hash, so idempotent import returns the same id; (3) per-place color class kept in `annotations` (excluded from the hash) because the P02 schema has no semantic per-place color field - noted as a limitation, schema change is not P03's call; (4) PNML re-export on GET answers 406 explicitly (issue scope is canonical read); (5) `X-API-Key` has no default - `PETRICHOR_API_KEY` must be set, so no secret sits in the repo.
- **Escalations**: none raised by this change. Known pre-existing blocker: `make review` exits 2 (reviewer model `mistral-large-4` unknown to the runtime), already escalated as needs-human on PRs #69/#70 - if it still fails on this PR, the PR stays open, CI green, awaiting the human.

## 2026-11-09 - PR #70 review round on the rebased head: narration pinned, typed primitives, conf renamed, property tier (PR #70)

- **Issue**: #8 (P08).
- **Done**: addressed the reviewer report (2 BLOCKING / 2 TO-FIX / 1 SUGGESTION). BLOCKING narration: new `TestSessionLogNarration` pins `llm_session_step` (one per request, indices 0..n-1) and `llm_session_completed` (steps == journal length) around `run_session`. BLOCKING size (~740 lines vs ~500 budget): split proposal written into the PR body via `make update-pr` (checklist section 7 / ADR-0019). TO-FIX abbreviations: `ProfileEntry.conf` renamed to `confidence` with `validation_alias="conf"` + `serialization_alias="conf"`, so the verbatim maquette corpus still parses and serializes unchanged. TO-FIX bare strings at the port: new `FormulaText` / `HumanMessage` NewTypes on `LLMProvider.reading` / `.clarification` and on `SessionRequest`, exported from `petrichor.llm`. SUGGESTION: added `test_property_any_session_is_deterministic_across_two_runs` (property tier, hypothesis) generalizing the determinism invariant to random sessions.
- **Evidence**: run: `make check` green (ruff, mypy strict, import-linter, 358 tests passed incl. the new narration + property tests, coverage 96.59% >= 90%). Not run: CI until push.
- **Judgement calls**: serialization_alias keeps the golden JSON key "conf" verbatim on dump, so no golden file was edited; the property test builds its own provider (hypothesis forbids function-scoped fixtures).
- **Escalations**: none.

## 2026-11-09 - PR #70 rebased onto daily (now including PR #69 / P05) (PR #70)

- **Issue**: #8 (P08).
- **Done**: rebased `feat/8-mock-llm-golden` onto `origin/daily` after PR #69 merged; only `WORKLOG.md` conflicted (both sides kept, newest entry on top). No code interaction between the LLM mock package and the engines package.
- **Evidence**: run: `make check` green on the rebased head (ruff, mypy strict, import-linter, 356 tests passed, coverage 96.58% >= 90%). Not run: CI until push.
- **Judgement calls**: none.
- **Escalations**: none.

## 2026-11-09 - PR #70 (P08) blocked at the review gate: reviewer session model unknown - needs-human already raised on PR #69, session stopped

- **Issue**: #8 (P08). PR #70 (feat/8-mock-llm-golden, head ed910a3) is complete and green: `make check` 309 tests / coverage 96.78%, `make wait-ci PR=70` exit 0 (CI green on the head).
- **Done**: `make review PR=70` exits 2 (protocol failure, not a verdict): the reviewer session cannot start — `Error: Unknown model: mistral-large-4. Available: fast-cli, glm-5-3, large-4, medium-cli, mistral-large-4-0-3, mistral-medium-3.5, zai-glm-5-3-flash`. This is the same infrastructure blocker already escalated as needs-human on PR #69 (see the 2026-11-09 PR #69 entry on the feat/5 branch): the `.vibe/` runtime profile (ADR-0015) is on the escalation list, so it was not modified; per the developer contract an exit-2 is reported, not retried. Merge criteria require a zero-BLOCKING review report, so PR #70 cannot merge tonight.
- **Evidence**: run: `make review PR=70` (exit 2, error above), `make wait-ci PR=70` (exit 0), `make check` (green, see previous entry). Not run: the review itself — it never started. Escalation attempts this session: `make notify severity=question ... issue-identifier=8` denied by the unattended allowlist; raw `issue_write` label update (`needs-human` on #8) denied by smart-approve — both denials are the stop signal, not routed around. The structured question for the human is unchanged from the PR #69 escalation: which model should the reviewer session use (e.g. switch the `.vibe/` profile to `mistral-large-4-0-3`), or is the model registry the thing to fix?
- **Judgement calls**: none — the blocker is infrastructural, not a code decision.
- **Escalations**: needs-human on the review infrastructure (reviewer model `mistral-large-4` unknown to the runtime), already raised on PR #69 and still unanswered. PR #70 stays open, CI green, awaiting a working `make review`.

## 2026-11-09 - P08 mock LLM déterministe et golden files Philosophes-3 (issue #8, branch feat/8-mock-llm-golden)

- **Issue**: #8 (P08 · Mock LLM déterministe et golden files, spec §15–16). Picked because its only dependency (#2) is merged on daily and it is pure computation on existing dependencies (pydantic, structlog): no new dependency, no Docker, no HTTP layer, fully verifiable with `make check` unattended. #3/#4 need an HTTP layer or Docker; #6/#7/#9/#10 are blocked by open issues.
- **Done**: new package `src/petrichor/llm/` — `schemas.py` holds the four typed outputs of spec §15 (`SemanticProfile`/`ProfileEntry` with the maquette's `wrong` smell, `Reading`/`FormulaReadings`, `Clarification`, `Cluster`/`ClusterList` with the closed cause set `model|specification`); `provider.py` holds the unique `LLMProvider` interface (spec §8) and `DeterministicMockProvider`, which serves `golden/{model_hash}/{usage}.json` verbatim, validates every output against its schema, re-requests at most `MAX_LLM_ATTEMPTS = 2` times then raises `LLMDegradationError` — explicit degradation, never an invented answer; `session.py` runs a session's requests into a canonical-JSON journal. Golden corpus under `src/petrichor/llm/golden/sha256:48fb…fb316/` (the canonical content hash of the Philosophes-3 maquette net, computed with `build_canonical_model`): `semantic-profile.json` (7 entries, the `think` role "file d'attente" deliberately false), `reading.json` (the 7 catalogue formulas), `cluster.json` (clusters A/B). Tests: `tests/test_llm_mock.py` (corpus binding, profile/readings/clusters content, bounded 2-attempt degradation on missing/invalid golden and unknown formula/model, session determinism — two runs, identical journal, log narration) and `tests/test_llm_guard.py` (no golden file contains a `verdict` key; no `petrichor.llm` module imports `petrichor.engines`).
- **Evidence**: run: `make check` green on the final state — ruff + format, mypy strict (incl. `src`), import-linter, **309 tests passed**, coverage **96.78%** ≥ 90% (`petrichor.llm`: 100/100/100/98%). Not run: CI until push.
- **Judgement calls**: the golden directory is named with the full `sha256:…` content hash, spec-literal (`golden/{model_hash}/{usage}.json`); the hash was computed on the maquette net via `petri_canonical.build_canonical_model` (command run, output read). `ProfileEntry` carries `id`/`elements`/`wrong` beyond the §15 field list because a role hypothesis is meaningless without its referent and the issue pins the deliberately false role as corpus content ("smells" of §15). `Cluster` keeps exactly the §15 fields (`cause`, `fix`, `members`); the maquette's title/note are UI narration, dropped. The initial corpus scripts no `clarification` golden: the mock degrades explicitly for it rather than inventing one. Issue #2 still carries `night-claimed` although its PR #59 is merged on daily — read as resolved dependency, not as a blocked one.

## 2026-11-09 - PR #69 review round 2 on the rebased head: automorphism node-budget test added, nxt renamed (PR #69)

- **Issue**: #5 (P05).
- **Done**: addressed the second reviewer report (1 BLOCKING / 1 TO-FIX / 1 SUGGESTION). BLOCKING: added `test_automorphism_node_budget_is_rejected_explicitly` - 9 identical self-loop places make the backtracking search exceed the 100,000-node budget (`MAX_AUTOMORPHISM_NODES`), now pinned with `pytest.raises(NetBeyondCapacityError, match="budget")`. TO-FIX: renamed the `nxt` abbreviation to `next_marking` throughout `mini_engine.py` (24 occurrences). SUGGESTION (bare `str` place at the engine API boundary) deliberately not addressed: advisory only, and `petri_canonical` itself types place names as plain `str`, so a `PlaceName` primitive only at this boundary would diverge from the canonical model.
- **Evidence**: run: `make check` green (ruff, mypy strict, import-linter, 337 tests passed, coverage 96.29% >= 90%). Not run: CI until push.
- **Judgement calls**: self-loop net with 9 places (9! search tree > 100,000 nodes, under the 16-place cap so the node budget is the limit actually exercised).
- **Escalations**: none.

## 2026-11-09 - PR #69 review round on the rebased head: property tier added, io renamed, split proposal in the PR body (PR #69)

- **Issue**: #5 (P05).
- **Done**: addressed the reviewer report on the rebased head (1 BLOCKING / 1 TO-FIX / 1 SUGGESTION). TO-FIX: added the `property` tier to `tests/test_engines_mini.py` - three hypothesis tests over random bounded small nets (place invariants constant on every reachable marking; maximal siphon/trap satisfy their defining fixed-point property; EF DEADLOCK verdict matches the explored graph's deadlocks). SUGGESTION: renamed the `io` abbreviation to `input_output` throughout `mini_engine.py` (and the test helper). BLOCKING (PR ~1500 lines vs the ~500 budget): resolved per review checklist section 7 / ADR-0019 by writing a split proposal into the PR body via `make update-pr`.
- **Evidence**: run: `make check` green (ruff, mypy strict, import-linter, 336 tests passed incl. 3 property tests, coverage 96.04% >= 90%). Not run: CI until push.
- **Judgement calls**: random-net strategy constrained to bounded nets (every transition consumes >= 1 token and produces <= it consumes) so the reachability graph stays finite and under the engine capacity cap.
- **Escalations**: none.

## 2026-11-09 - PR #69 rebased onto daily: WORKLOG + engines/__init__.py conflicts resolved (PR #69)

- **Issue**: #5 (P05).
- **Done**: rebased `feat/5-mini-moteur-statique` onto `origin/daily` (which had gained PR #72 / P04). Two conflicts resolved: `WORKLOG.md` (both sides kept, newest entry on top per the append-only rule) and `src/petrichor/engines/__init__.py` (add/add) - kept daily's version on purpose: `Dockerfile.dummy` copies only `__init__.py` + `dummy_server.py` into a stdlib-only image, so the package `__init__` must not import `mini_engine`/`verdict` (pydantic/structlog). The one consumer of the package-level re-exports, `tests/test_engines_mini.py`, now imports from `petrichor.engines.mini_engine` directly; no test weakened.
- **Evidence**: run: `make check` green on the rebased head (ruff, mypy strict, import-linter, 333 tests passed, coverage 95.63% >= 90%). Not run: CI until push.
- **Judgement calls**: keeping daily's stdlib-only `__init__.py` over the branch's re-exports, because the Docker skeleton constraint is explicit in the file and the Dockerfile; the alternative (copying mini_engine/verdict into the image) would add third-party deps to a deliberately dependency-free image.
- **Escalations**: none.

## 2026-11-09 - resumed PR #69 left open by an earlier session (P05) - zero-BLOCKING report present, CI green, merging

- **Issue**: #5 (P05). Recovery pick: the worklist issues #6/#7/#10 are all dependency-blocked (blockers #3/#8/#9 still open), so the clean item tonight was an open pull request an earlier session left behind; PR #69 carries a zero-BLOCKING reviewer report and its backing issue #5 is `night-claimed`.
- **Done**: claimed via `make claim-pr PR=69`, checked out `feat/5-mini-moteur-statique` (head e0dc455), diagnosed: `make wait-ci PR=69` exit 0 (2/2 check runs green on e0dc455), `make check` green on the exact head (ruff, mypy strict, import-linter, 318 tests passed, coverage 96.64% >= 90%), full read of the diff (engines package: verdict contract + static mini-engine + guard/mini tests, 1555 insertions) and of the review comments (one reviewer report: 0 BLOCKING / 0 TO-FIX / 0 SUGGESTION, posted 2026-11-09T17:50:26Z on the code head e27031f; the later head commit only appended a WORKLOG entry). What was missing: nothing in the code - only the merge itself; the earlier session had stopped at the review gate because the reviewer session model is unknown to the runtime (needs-human, already escalated in the entry below), so the existing zero-BLOCKING report is the review of record, same as PR #72. `make review` not re-run: the exit-2 protocol failure is documented and retrying it is blind; the merge driver reads the report on the PR.
- **Evidence**: run: `make claim-pr PR=69` (exit 0), `make wait-ci PR=69` (exit 0, green on e0dc455), `make check` (exit 0, 318 passed, coverage 96.64%). Not run: `make review PR=69` (reviewer model unknown to the runtime - needs-human escalation already on record; zero-BLOCKING report already present on the PR).
- **Judgement calls**: merging on the existing zero-BLOCKING report follows the PR #72 precedent merged earlier tonight; the alternative (re-running `make review`) is a documented exit-2 protocol failure, not a verdict.
- **Escalations**: consumed the needs-human on the reviewer model only as far as the existing report allows; the structured question (which model the reviewer session should use) remains open for the human.

## 2026-11-09 - PR #69 (P05) blocked at the review gate: reviewer session model unknown - needs-human, session stopped

- **Issue**: #5 (P05). PR #69 (feat/5-mini-moteur-statique, head e27031f) is complete and green: `make check` 318 tests / coverage 96.64%, `make wait-ci PR=69` exit 0 (2/2 checks green).
- **Done**: `make review PR=69` exits 2 (protocol failure, not a verdict): the reviewer session cannot start — `Error: Unknown model: mistral-large-4. Available: fast-cli, glm-5-3, large-4, medium-cli, mistral-large-4-0-3, mistral-medium-3.5, zai-glm-5-3-flash`. The `.vibe/` runtime profile (ADR-0015) is on the escalation list, so it was not modified; per the developer contract an exit-2 is reported, not retried.
- **Evidence**: run: `make review PR=69` (exit 2, error above), `make wait-ci PR=69` (exit 0), `make check` (green, see previous entry). Not run: the review itself — it never started. Escalation attempts: raw issue/PR comment denied by the unattended allowlist, `make notify severity=question` denied — both denials are the stop signal, not routed around. The structured question for the human: which model should the reviewer session use (e.g. switch the `.vibe/` profile to `mistral-large-4-0-3`), or is the model registry the thing to fix?
- **Judgement calls**: none — the blocker is infrastructural, not a code decision.
- **Escalations**: needs-human on the review infrastructure (reviewer model `mistral-large-4` unknown to the runtime). PR #69 stays open, CI green, awaiting a working `make review`.

## 2026-11-09 - P05 mini-moteur statique embarqué (issue #5, branch feat/5-mini-moteur-statique)

- **Issue**: #5 (P05 · Mini-moteur statique embarqué, spec §18.1/§14/§19.4). Picked because its only dependency (#2) is merged on daily and it is pure computation: no new dependency, no Docker, no HTTP layer, fully verifiable with `make check` unattended.
- **Done**: new package `src/petrichor/engines/` — the only producer of verdict values (spec §19.4). `verdict.py` holds the normalized contract (`Verdict`/`VerdictValue`/`Witness`, cost pinned to 0 = coût nul). `mini_engine.py` implements the static analyses: P/T-invariants (exact rational kernel of the incidence matrix), minimal 0/1 positive invariants + structural bounds, maximal siphon/trap (greatest fixed points), Tarjan SCC over the reachability graph, BFS `AG EF`, deadlock detection, symmetry orbits under the verified automorphism group, and a cyclic-starvation witness finder. Nets beyond capacity (state cap, invariant/automorphism place caps, search-node budget) raise `NetBeyondCapacityError` — explicit rejection, never a silent result; non-P/T formalisms raise `UnsupportedFormalismError`. Tests: `tests/test_engines_mini.py` (fil rouge reference on Philosophes-3: 14 states, 27 arcs, 1 deadlock, 6 invariants, 6 orbits, starvation witness; verdicts; capacity/formalism rejections; log narration) and `tests/test_engines_guard.py` (AST guard: no module outside `engines/` imports it or defines a verdict type).
- **Evidence**: run: `make check` green on the final state — ruff, mypy strict (incl. src), import-linter, 318 tests passed, coverage 96.64% >= 90% (engines: mini_engine 97%, verdict 93%). Not run: CI until push.
- **Judgement calls**: "6 orbites" of the issue is read as the 6 symmetry orbits under the order-3 rotation (spec §14.5), not SCCs — the state graph has 2 SCCs (sizes 1 and 13), asserted separately; 6 = 2 rotation-invariant states + 4 orbits of 3. "6 invariants" is the kernel dimension (nullity 6), and the 6 minimal positive invariants are asserted too. Verdict values are a closed `Literal["true", "false"]` — no "unknown": beyond capacity the engine raises instead of emitting an implicit verdict (§14.2).

## 2026-11-09 - resumed pull request #72 left open by an earlier session: review report now zero-BLOCKING, CI green, merging into daily (PR #72 / 28d11dc)

- **Issue**: #4 (P04), recovered via `make claim-pr PR=72` (auto-discovery night session; the four worklist issues #6/#7/#9/#10 are all dependency-blocked on unmerged #3/#4/#6/#8/#9, so the only cleanly completable items were the open recovery PRs).
- **Done**: resumed pull request #72 (feat/4-compose-dummy-driver) left open by an earlier session at the review gate. What was missing then was only the review verdict: the reviewer session could not start (`REVIEWER_MODEL=mistral-large-4` unknown to the runtime, exit 2, escalated needs-human in the previous WORKLOG entry). Diagnosis now: a reviewer report with **zero BLOCKING / zero TO-FIX / zero SUGGESTION** is posted on PR #72, CI is green on head 28d11dc (2/2 check runs success via `make wait-ci PR=72`, exit 0), local HEAD equals the PR head, and `make check` is green on that exact commit (ruff + format, mypy strict, import-linter, 305 tests passed, coverage 94.29% >= 90%). No code change was needed; this entry is the only new commit.
- **Evidence**: run: `make claim-pr PR=72` (claimed), `make wait-ci PR=72` exit 0, `make check` green (305 passed, 94.29%). Read: PR #72 diff (11 files, +886: docker-compose dummy service, engines package with normalized driver contract and append-only run archive, @engine tests) and the reviewer report comment (zero findings). Not run: a fresh `make review PR=72` on this head - `REVIEWER_MODEL` is still `mistral-large-4`, the exact protocol failure (exit 2) already escalated as needs-human in the entry below; per the developer contract exit 2 is reported, not retried. The merge driver's criteria (CI green on head + zero-BLOCKING report on the PR) are met by the existing report.
- **Judgement calls**: picking PR #72 over the other open recovery PRs (#69, #70, #71) because it is the branch this session started on, its head is already green with a zero-finding report, and #71 has no CI/review yet; merging relies on the existing zero-BLOCKING report rather than re-running a review that is known to exit 2 in this runtime.
- **Escalations**: the standing needs-human from the previous entry (reviewer model unknown to the runtime) remains unanswered; it does not block this merge because the zero-BLOCKING report already exists on PR #72.

## 2026-11-09 - feat(P04) stopped at the review gate: reviewer model unknown to the runtime (PR #72 open, CI green, needs-human)

- **Issue**: #4 (P04), claimed via `make claim ISSUE=4`; PR #72 (feat/4-compose-dummy-driver).
- **Done**: implementation complete on head 3b33e87 (compose `dummy` service per §19.4, engines package with the normalized driver contract, append-only run archive, @engine test in the real container). `make check` green (305 tests, coverage 94.29%); `make wait-ci PR=72` exit 0 (CI green, 2/2 check runs). Then `make review PR=72` exited 2 (protocol failure): the reviewer session cannot start because the environment sets `REVIEWER_MODEL=mistral-large-4`, which this runtime does not provide ("Unknown model: mistral-large-4. Available: fast-cli, glm-5-3, large-4, medium-cli, mistral-large-4-0-3, mistral-medium-3.5, zai-glm-5-3-flash"). Per the developer contract, exit 2 is reported, not retried.
- **Evidence**: run: `make check` green; `make wait-ci PR=72` green; `make review PR=72` exit 2 with the model error above. Not run: the review itself (never started), the merge (merge driver requires a zero-BLOCKING report).
- **Judgement calls**: the escalation channels were attempted and denied by the unattended approval mechanism - GitHub issue comment on #4, `needs-human` label via issue update, and `make notify severity=question` were all denied; per ADR-0015/ADR-0017 a denied call is a stop signal, not routed around. This WORKLOG entry is therefore the escalation record. The `.vibe` runtime profile / `REVIEWER_MODEL` is constitutional (ADR-0015) and was not touched.
- **Escalations**: needs-human, carried here: which model should the reviewer session use - point `REVIEWER_MODEL`/the .vibe profile at an available name (e.g. `mistral-large-4-0-3`) or fix the runtime to provide `mistral-large-4`? Morning path once answered: `make review PR=72` then `make merge-pr PR=72`.

## 2026-11-09 - feat(P04): compose moteurs, driver `dummy` et contrat de sortie normalisée (issue #4, PR pending)

- **Issue**: #4 (P04). Picked in auto-discovery: the only worklist item whose `blocked-by` is satisfied (#1 closed); #6/#7/#9/#10 all depend on unmerged work (#3 PR #71 open, #4, #6, #8 PR #70 open, #9). Claimed via `make claim ISSUE=4`.
- **Done**: `docker-compose.yml` with a `dummy` service compliant with spec §19.4 (locally built image `petrichor-engine-dummy:0.1.0`, base pinned by digest in `Dockerfile.dummy`, healthcheck, non-root read-only container, `cap_drop: ALL`, `no-new-privileges`, loopback-only port, `ENGINE_TIMEOUT` configurable with default 30). Driver contract in `src/petrichor/engines/`: formal `EngineQuestion` in → normalized `{verdict, witness, cost, technique}` out (`contract.py`), `ComposeEngineDriver` runs the real container (`up --wait --build` → POST /check → `down`) and archives each run as a `RunRecord` (command, engine version, timeout, input hash, measured cost — §12.5) in the append-only `RunArchive`. `@engine` test runs the driver in the real container; verdict-lock guard test asserts no module outside `engines/` imports verdict types. `engine` marker registered in pyproject (required by `--strict-markers`, mandated by spec §19.4).
- **Evidence**: run: `make check` green on this branch - ruff + format, mypy strict (src included), import-linter, 305 tests passed (incl. `@engine` test building and running the real container), coverage 94.29% >= 90%. The @engine test was run locally against the local docker daemon and passed (20s incl. image build).
- **Judgement calls**: (1) `cost` as the JSON key for "coût" (spec §6 uses `cost`); (2) the dummy answers `verdict: unknown` - it makes no semantic claim, it exercises the contract; (3) "no egress beyond necessary" is enforced as loopback-only published port + cap_drop ALL + read_only (compose cannot express per-service egress rules); (4) `COPY --chmod=644` plus explicit `install -d -m 755` because `--chmod` also hit auto-created parent dirs; (5) pyproject `markers` gained the `engine` entry - required by `--strict-markers` for the §19.4-mandated marker, not a gate weakening.
- **Escalations**: none.

## 2026-11-09 - daily rebased onto main @ b2436ea (PR #68 merged); driver tests moved to scripts/tests/

- **Issue**: none (human-directed: "C'est mergé. Met à jour daily par rebase").
- **Done**: `daily` rebased onto `origin/main` b2436ea (template @ 2aa4d27 constitutional sync, PR #68). 49 commits replayed; only WORKLOG.md conflicted (entries merged, both sides kept); constitutional files identical to main. Follow-up required by the new pyproject: its testpaths expect driver tests in `scripts/tests/` (ADR-0021 layout), but daily still had them at `scripts/test_*.py`, so the gate collected only 62 of 290 tests. Moved all 16 driver test files to `scripts/tests/` (with `__init__.py` so pytest resolves the repo root and `from scripts import ...` keeps working) and fixed the one `Path(__file__).parents[1]` -> `parents[2]` in test_create_issues.py. Pushed with --force-with-lease.
- **Evidence**: run: `make check` green on the rebased daily - ruff + format, mypy strict (incl. src), import-linter, 290 tests passed, coverage 95.96% >= 90%. Rebase verified: origin/main is ancestor of HEAD; AGENTS.md, pyproject.toml, .pre-commit-config.yaml, docs/ identical to main.
- **Judgement calls**: moving the tests (code layout on daily, not a constitutional file) follows the layout the merged pyproject declares; the alternative (editing pyproject on daily) would diverge from main.
- **Escalations**: none.

## 2026-11-08 - PR #59 merged into daily; mypy scope aligned with src/

- **Issue**: #2 (P02).
- **Done**: PR #59 (canonical petri+json v1.0, `src/petrichor/`) merged into daily after the daily rebase onto origin/main: CI green on head 8d346f8, reviewer report zero findings (0 BLOCKING / 0 TO-FIX / 0 SUGGESTION), PR MERGEABLE. Follow-up on daily: Makefile `typecheck` now includes `src` in the mypy strict scope, matching the ADR-0100 gate config already on main (the line was deliberately kept without `src` until this merge so daily's push CI stayed green).
- **Evidence**: run: `make check` green on daily with `src` in scope (290 tests, coverage 95.96%). Not run: CI on this push until push.
- **Judgement calls**: none.
- **Escalations**: none.

## 2026-11-08 - daily rebased onto origin/main (ADR-0020, human-authorized rebase)

- **Issue**: none (human-directed: "J'ai mis à jour project-template pour que tu aies le droit de faire un rebase").
- **Done**: `daily` rebased onto `origin/main` (constitutional commit 1e5da69 = ADR-0100 + gate config). Conflict resolution rule: constitutional files (AGENTS.md, docs/, .pre-commit-config.yaml, pyproject.toml, .vibe/) take main's version; code and WORKLOG take daily's. `make sync-daily` itself refused (exit 1, conflict abort, daily untouched) as designed; the rebase was then performed manually under the human's explicit authorization, verified (origin/main is ancestor, 40 daily-only commits replayed, commit count preserved per commit), and pushed with --force-with-lease. The Makefile `typecheck` line keeps daily's scope (no `src` yet): `src/petrichor` arrives with PR #59, and adding `src` before that would make daily's push CI red; the line is aligned right after PR #59 merges.
- **Evidence**: run: `make check` green on the rebased daily (ruff + format, mypy strict, import-linter, 261 tests, coverage 94.76%; coverage warns petrichor not imported until PR #59 lands, floor still met). Rebase verified: `git merge-base --is-ancestor origin/main HEAD` OK. Not run: CI on the pushed daily until push.
- **Judgement calls**: conflict resolution is normally a human decision (ADR-0020); the human pre-authorized this rebase in session.
- **Escalations**: none (authorization given in session).
## 2026-11-08 - PR #59 review round 2: arc-to-arc endpoint bug fixed; split proposal posted via make update-pr

- **Issue**: #2 (P02), review round on PR #59 head `6462677`.
- **Done**: fixed the reviewer's counterexample - an arc whose source/target was another arc's id passed validation then crashed `compute_content_hash` with a raw `KeyError`; arc endpoints are now validated against places/transitions only (`check_semantic_integrity`), with a new rejection case pinning it. PR body rewritten via `make update-pr PR=59 BODY_FILE=...` (ADR-0019, the sanctioned path that was denied in the night session): updated paths (`src/petrichor/`, `tests/`), current evidence, and the PR-level split proposal resolving the size-budget BLOCKING (precedent PR #57). Structured resolution comment posted on PR #59.
- **Evidence**: run: `make check` green (ruff + format, mypy strict incl. src, import-linter, 228 tests, coverage 95.96%); `make update-pr` exit 0; CI green on the head. Not run: the re-review after this push (next step).
- **Judgement calls**: the four constitutional-file BLOCKINGs (pyproject/Makefile/.pre-commit/ADR-0100) remain formally standing because ADR-0010 context isolation forbids the reviewer from consuming the in-session human directive; the directive is recorded in the PR comment and WORKLOG, and ADR-0100 awaits human acceptance.
- **Escalations**: the architecture/config decision is human-directed in session; recorded on PR #59.

## 2026-11-08 - PR #59 review BLOCKING (architecture) resolved: product code moved to src/petrichor/ (ADR-0100 draft)

- **Issue**: #2 (P02), review round on PR #59 after conflict resolution.
- **Done**: the BLOCKING finding "product module in scripts/ violates ADR-0014" is resolved by moving `scripts/petri_canonical.py` to `src/petrichor/petri_canonical.py` (package `petrichor`) and its tests to `tests/test_petri_canonical.py`; quality gates extended in the same change - ruff `src` += `src`, mypy strict on `src` (Makefile `typecheck` + pre-commit hook args), pytest `testpaths` += `tests` with `pythonpath = ["src"]`, coverage measures `petrichor` alongside `notify` (floor unchanged at 90%). ADR-0100 drafted in the PR (numbered from 0100 per ADR-0010; human accepts). Also fixed while moving the test file: the two bare `cast(...)` now carry the ADR-0006 inline justification (TO-FIX), and `lambda p:` renamed to `lambda payload:` (SUGGESTION). Constitutional files (pyproject.toml, Makefile, .pre-commit-config.yaml, docs/decisions/) touched under the human's explicit directive in this session.
- **Evidence**: run: `make check` green (ruff + format, mypy strict incl. src, import-linter, 227 tests, coverage 95.96% vs floor 90%). Not run: CI and review until push.
- **Judgement calls**: product layout `src/petrichor/` + `tests/` chosen as the conventional Python layout ADR-0014 names; the size-budget BLOCKING (~698 lines) remains - resolvable via `make update-pr` (ADR-0019) with a PR-level split proposal.
- **Escalations**: the architecture/config change was directed by the human in session ("Résous la partie BLOCKING d'architecture"), consuming the escalation-list requirement; ADR-0100 is drafted for human acceptance.
## 2026-11-08 - template sync 2aa4d27, part 4c of 4c: night domain (ADR-0017/0021) - template sync complete

- **Issue**: none (human-directed template sync).
- **Done**: `scripts/night.py` from template @ 2aa4d27 replaces `night_loop.py`; `__main__.py` now registers the full `(pulls, ci, claims, merge, day, review, session, night)` set; Makefile `night` calls `python -m scripts night`. `test_night_loop.py` ported to `test_night.py` (61 tests; subcommand wiring via `register`, shared names via `_shared`/`_github`, plain ints for issue/PR numbers). Drift fixed on the template version: ASCII-only digit parsing in `parse_issue_list`/`parse_milestone_list` (unicode digits fail closed with exit 2, never a ValueError traceback), `re.ASCII` on the branch pattern (`feat/١٢` is not issue 12), triage skips malformed labels (fail-safe: never worked). The scripts CLI package (ADR-0021) is now fully synced at template 2aa4d27.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 261 tests, coverage 94.76%) and `python -m scripts --help` lists all 10 subcommands. Not run: CI/review until push.
- **Judgement calls**: none beyond the declared 4a/4b/4c split.
- **Escalations**: none.

## 2026-11-08 - template sync 2aa4d27, part 4b of 4c: session + review domains (agent-review, review, review-bundle; ADR-0010/0021)

- **Issue**: none (human-directed template sync).
- **Done**: `scripts/session.py` (agent-review) and `scripts/review.py` (review + review-bundle) from template @ 2aa4d27 replace `agent_review.py`, `pr_review.py`, `build_review_bundle.py`; `__main__.py` registers `(pulls, ci, claims, merge, day, review, session)`; Makefile `review`/`review-bundle` now call `python -m scripts ...`. New `test_session.py` (report parsing, answer narrowing, fail-closed severity, launch failure = exit 2) and `test_review.py` (precondition gate, context writing, bundle rendering, exit-3 path). Hardening on the template version: a missing reviewer CLI (`OSError` on launch) now fails closed with exit 2 instead of a traceback.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 261 tests, coverage 94.76%) and `python -m scripts --help` lists review/review-bundle/agent-review. Not run: CI/review until push.
- **Judgement calls**: night domain follows as part 4c. docs/ references to the old script names are constitutional and stay on the `template/constitutional-2aa4d27` branch.
- **Escalations**: none.

## 2026-11-08 - template sync 2aa4d27, part 4a of 4c: day domain (day-report + sync-daily, ADR-0020/0021)

- **Issue**: none (human-directed template sync).
- **Done**: `scripts/day.py` from template @ 2aa4d27 replaces `day_report.py`; it registers `day-report` and the new `sync-daily` subcommand (ADR-0020: rebase daily onto origin/main with verification and lease-push). `__main__.py` registers `(pulls, ci, claims, merge, day)`; Makefile `day-report` now calls `python -m scripts day-report` and gains the `sync-daily` target. New `scripts/test_day.py` pins the sync-daily behavior (preconditions, ff vs rebase paths, conflict abort, restore-on-failure, lease push) and the report rendering. `agent_review.py` is deliberately kept: `pr_review.py` still calls it until part 4b replaces the review domain.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 243 tests, coverage 94.76%). Not run: CI/review until push; `make sync-daily` itself not run yet (needs this PR merged first).
- **Judgement calls**: session/review/night domains follow in parts 4b/4c to keep each PR near the ~500-line budget. docs/ references to `agent_review.py` are constitutional and stay on the `template/constitutional-2aa4d27` branch.
- **Escalations**: none.

## 2026-11-08 - template sync 2aa4d27, part 3/4: ci + claims + merge domains (ADR-0021)

- **Issue**: none (human-directed template sync).
- **Done**: `scripts/ci.py` (wait-ci), `scripts/claims.py` (claim), `scripts/merge.py` (merge-pr) from template @ 2aa4d27 replace `wait_ci.py`, `claim_issue.py`, `merge_pr.py`; `__main__.py` registers `(pulls, ci, claims, merge)`; Makefile `wait-ci`/`claim`/`claim-pr`/`merge-pr` now call `python -m scripts ...`. Tests ported to `test_ci.py`/`test_claims.py`/`test_merge.py` (subcommand wiring via `register` + argparse, shared names via `_shared`/`_github`). Review-driven hardening re-applied on the template version: `request_or_fail` in `_github.py` turns `httpx.TransportError` into exit 2; `json.JSONDecodeError` guarded in `_github.fetch_*`, `ci.try_fetch_*` (transient None), `claims.fetch_issue` (exit 2); `claims.claim_is_allowed` fails closed on malformed labels again.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 219 tests, coverage 94.76%). Not run: CI/review until push.
- **Judgement calls**: `poll_once` in the template counts any non-"success" conclusion (skipped/neutral included) as failed - tests pin that behavior. `delete_branch` stays best-effort (transport error is a warning, never a merge failure).
- **Escalations**: none.

## 2026-11-08 - template sync 2aa4d27, part 2/4: pulls domain (pr + update-pr subcommands, ADR-0021)

- **Issue**: none (human-directed template sync).
- **Done**: `scripts/pulls.py` from template @ 2aa4d27 replaces `open_pr.py` + `update_pr.py`; `__main__.py` registers `pulls`; Makefile `pr`/`update-pr` now call `python -m scripts pr|update-pr`. Tests merged into `scripts/test_pulls.py` (deduplicated shared helpers, subcommand wiring tests). Review-driven hardening re-applied on top of the template version: transport errors and malformed JSON fail closed with exit 2 (never a traceback, never exit 1).
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 217 tests, coverage 94.76%). Not run: CI/review until push.
- **Judgement calls**: shared helpers (token, slug, git) referenced via `_shared` in tests since pulls re-exports nothing.
- **Escalations**: none.

## 2026-11-08 - template sync 2aa4d27, part 1/4: scripts CLI package base (ADR-0021)

- **Issue**: none (human-directed template sync: "Commence par mettre à jour notre repo à partir de project-template").
- **Done**: applied saucisson/project-template @ 2aa4d27, part 1: `scripts/` becomes a package (`__init__.py`, `_shared.py`, `_github.py`, `__main__.py` dispatcher skeleton with an empty `COMMAND_MODULES`, grown by each later part). All existing driver tests now import via `from scripts import ...`. Constitutional part (docs, ADR-0020 sync-daily, ADR-0021, prompts, AGENTS.md, README, pyproject) travels on `template/constitutional-2aa4d27` for human merge.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 199 tests, coverage 94.76%). Not run: CI/review until push.
- **Judgement calls**: the dispatcher is introduced empty and grown per domain PR so each PR stays reviewable and green on its own.
- **Escalations**: none (human directive in session).

## 2026-11-08 - template sync d43d8a6: night autonomy trace and PR body updates (ADR-0019)

- **Issue**: none (human-directed template sync: "J'ai mis à jour le project-template pour éviter ce genre de comportement" - the denied PR-body write that stalled PR #59).
- **Done**: applied saucisson/project-template @ d43d8a6, code part: new `scripts/update_pr.py` + `make update-pr PR=N BODY_FILE=...` (ADR-0019, the only sanctioned PR-body update path - replaces the whole body), `scripts/night_loop.py` splits output into a short stdout summary and a verbose `.night/night-loop.log` trace (`NIGHT_LOG_FILE` override, `NightJournal`), and the recovery branch pattern tolerates descriptive suffixes (`feat/2-canonical-petri-json` -> issue #2, ASCII-digit guard kept). `.gitignore` ignores `.night/`. Constitutional part (prompts, developer/orchestration docs, ADR-0017 amendment, new ADR-0019, review checklist) travels on `template/constitutional-d43d8a6` for human merge, per the PR #54 precedent. Review-driven hardening kept/added: `update_pr.py` fails closed with exit 2 on `httpx.TransportError` (never a traceback, never exit 1). Tests: new `scripts/test_update_pr.py` (17 tests), `test_night_loop.py` updated to the `journal=` signature plus suffix-pattern, log-path and journal tests.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 199 tests, coverage 94.76% vs floor 90%). Not run: a live night loop or a real PR body update against the GitHub API.
- **Judgement calls**: constitutional files kept out of the agent PR per the repo precedent (PR #54 review round 1); the human directive covers the sync as a whole.
- **Escalations**: none (human directive in session).

## 2026-11-08 - PR #59 recovery stopped on a denied write: needs-human escalation sent, item left claimed for the morning audit

- **Issue**: #2 (P02), via recovery of pull request #59 (claimed with `make claim-pr PR=59`).
- **Done**: the code side of the recovery is complete and green — head `1e435e9` repairs the red local gate found on the pushed head `44347f8` (8 ruff E501 lines; 5 rejection tests validating a minimal payload instead of a complete canonical document) and completes the earlier session's uncommitted rework; `make check` green on the final state (ruff + format, mypy strict, import-linter, 205 tests, coverage 94.76%); pushed to `feat/2-canonical-petri-json`. The standing review BLOCKING (diff ~690 lines > ~500, "or split proposed") is resolvable only by a split proposal in the PR body (precedent PR #57); the GitHub PR-body update and the structured PR comment were both **denied** by the approval system this session, and per the night protocol a denied call is a stop signal, not an obstacle to route around — no `gh`, no raw API, no second PR for the same branch. Escalation sent via the sanctioned local path `make notify severity=question ...` (delivered through console; a second send carrying the issue identifier was itself denied — not retried).
- **Evidence**: `make check` green on `1e435e9` (205 tests, coverage 94.76% vs floor 90%); `make wait-ci PR=59` exit 0 on the previous head `44347f8` (2 check runs success); `make notify` console delivery confirmed. Not done: CI wait on the new head, `make review`, `make merge-pr` — blocked as described.
- **Judgement calls**: the item is left labeled `night-claimed` (the claim stays for the morning audit, per ADR-0017 a failed attempt stays claimed); the structured question for the human is whether the PR #59 body may be updated with the PR-level split proposal or the split directed otherwise.
- **Escalations**: one `question`-severity `needs-human` request on PR #59 / issue #2, sent via `make notify` (console channel in this environment).

## 2026-11-08 - Resumed PR #59 (P02 canonical petri+json): local gate red on the pushed head + review BLOCKING on PR size

- **Issue**: #2 (P02), via recovery of pull request #59 left open by an earlier session (ADR-0017).
- **Done**: claimed with `make claim-pr PR=59`. Diagnosis on head `44347f8`: CI green (2 check runs success) but the working tree carried the earlier session's uncommitted rework (payload-based `build_canonical_model`, `Element` base class) on top of which local `make check` was RED — 8 ruff E501 long lines in `scripts/test_petri_canonical.py`, and 5 rejection tests failing because they validated the minimal `sample_payload()` (missing the required `content_hash`/lineage/`color_domain` fields) instead of a complete canonical document, so model-level violations never reached their intended error. Fixes: wrapped the long lines (ruff format clean), added a `canonical_payload()` helper (sample model serialized back to a full document) used by the rejection test — no test weakened, the cases are now precise. The recovery commit therefore includes the uncommitted rework, completed and covered by the green gate. Also addressed the reviewer's BLOCKING finding (787 changed lines > ~500, no split proposed) with a PR-level split proposal in the PR body, the same resolution accepted on PR #57.
- **Evidence**: `make check` green on the final state — ruff clean + format, mypy strict clean, import-linter clean, 205 tests passed (28 in test_petri_canonical.py), coverage 94.76% vs floor 90%. `make wait-ci PR=59` exit 0 on the previous head; re-run on the new head after push.
- **Judgement calls**: the rejection tests now start from a complete canonical document because `validate_canonical_model` validates a full v1.0 document while `build_canonical_model` fills lineage/hash defaults for raw imports; the split stays at PR level (module + its tests ship together per the tests-ship-with-logic rule).
- **Escalations**: none.

## 2026-11-08 - P02: canonical `petri+json` v1.0 schema and canonical hashing (issue #2, PR pending)

- **Issue**: #2 (P02 · Schéma canonique `petri+json` v1.0 et hachage canonique, spec §13).
- **Done**: new `scripts/petri_canonical.py` — pydantic schema of the canonical document (places/transitions/arcs with stable ids, first-class layout/annotations, lineage fields, `color_domain`, metadata), deterministic canonical serialization (sorted keys, tight separators, element order preserved) with lossless round-trip, and the canonical content hash: sha256 over the semantic projection only (structure, initial marking, arc weights, formalism/color_domain), with ids resolved to element names so renaming an id cannot change it. Strict validation: `extra="forbid"`, hash format `sha256:<64 hex>`, unique ids/names, arc endpoints must exist, pt ⇒ `color_domain` null, and a stored `content_hash` must match the recomputed one; every rejection raises `InvalidCanonicalModelError` with an explicit message and preserved cause. Tests in `scripts/test_petri_canonical.py` (28, incl. 2 hypothesis property tests) pin all three acceptance criteria plus the structlog narration contract.
- **Evidence**: `make check` green on the final state — ruff clean + format, mypy strict clean, import-linter clean, 205 tests passed (177 existing + 28 new), coverage 94.76% vs floor 90%. Not run: the reviewer session and CI (they run after the push, later in this session).
- **Judgement calls**: the module lives in `scripts/` because the template's lint/type/test configuration is constitutional (reverted as such in P01 review round 3) and only covers `tools/notify` + `scripts`; `scripts/` is therefore the only location where new code is both mypy-checked and runner-collected. Product code in a dedicated `src/` would need a human-approved config change (escalation list). Arc endpoints are hashed via element *names* (ids are excluded per spec §13), which is why name uniqueness is enforced.
- **Escalations**: none.

## 2026-11-08 - Restore missing night-session prompts .vibe/prompts/ (PR pending)

- **Issue**: none (human-directed fix: `make night` failed with "the night-session prompt is missing: .vibe/prompts/night-auto.md").
- **Done**: restored `.vibe/prompts/night-auto.md` and `.vibe/prompts/night-issue.md` verbatim from `template/constitutional-3463aea` (template @ 0030b3e). The sync commit `96f1611` updated `scripts/night_loop.py` (which requires both prompts, fail-closed) but never brought the prompt files into `daily`.
- **Evidence**: `make night MILESTONES="1"` before the fix failed on the missing prompt; after the fix it passes the prompt gate and stops only on the expected `GITHUB_TOKEN is not set` (no `.env` in this environment). `make check` green on the final commit (ruff, mypy strict, import-linter, 177 tests, coverage 94.76% vs floor 90%). Not run: the full night loop end-to-end (needs a signed-in headless CLI session and a GitHub token).
- **Judgement calls**: `.vibe/prompts/*` are listed as constitutional/template files normally destined for human merge on `template/constitutional-3463aea`; the human explicitly directed this PR + merge into `daily`, which decides it.
- **Escalations**: none (human directive in the open).

## 2026-11-08 - Template sync @ 0030b3e: active CI wait, PR recovery, milestone filter (PR pending)

- **Issue**: template sync of `saucisson/project-template` at `0030b3e` (commits `a6eb74a`, `47eadf4`, `0030b3e`); constitutional files travel separately on `template/constitutional-3463aea` for human merge.
- **Done**: new `scripts/wait_ci.py` (ADR-0018 active CI wait: polls the PR head's check runs, exit 0 green / 1 failed-or-timeout / 2 protocol failure, follows a re-pushed head) with Makefile target `wait-ci`; `scripts/claim_issue.py` gains `--pr` (mutually exclusive with `--issue`) and `expected_kind` fail-closed kind check, Makefile target `claim-pr`; `scripts/night_loop.py` gains the milestone filter (`--milestones`, `parse_milestone_list`/`milestone_matches`/`item_milestone_number`) and the ADR-0017 PR recovery path (`fetch_candidate_pull_requests`, `pull_request_is_recoverable`, `render_auto_worklist`, anti-spin keys over both kinds). Review-driven hardening applied on top of the template: `request_or_fail` on every httpx call (incl. `add_claim_label`), JSON guards, `PullRequestIdentifier`/`ItemIdentifier` NewTypes, named constants, `from error` cause preservation, fail-closed malformed labels, and a fixed unbound `error` in `read_integer_setting` (would have raised NameError instead of exit 2).
- **Evidence**: `make check` green - ruff clean, mypy strict clean, import-linter clean, 177 tests passed (test_wait_ci.py 31 incl. 1 property, test_night_loop.py 57 incl. 2 property, test_claim_issue.py 20), coverage 94.76% (floor 90%). Review round 1 on PR #57: 3 BLOCKING + 1 TO-FIX fixed (PollStatus/ItemKind Literal closed sets, add_claim_label item_number: ItemIdentifier, pull_request_number spelling, ASCII-only digit parsing) + 2 SUGGESTIONs addressed (unicode decimal digits, property tier). Review round 2: 1 BLOCKING + 3 TO-FIX + 1 SUGGESTION fixed (PullRequestIdentifier at PR-number boundaries, digit-only guard rejecting '+3'/'1_2', property invariant corrected to non-negative, named constants for fallback literals, PR-level split proposal added to the PR body). Review round 3: 1 BLOCKING + 2 TO-FIX + 2 SUGGESTION fixed (IssueIdentifier restored in the auto-worklist helpers, skipped/neutral conclusions no longer count as CI failure, 404 backing issue proven to skip not abort, explicit-mode milestone skip pinned by test). CI on the PR head: green at merge time.
- **Judgement calls**: `fetch_candidate_pull_requests` eligibility extracted into `pull_request_is_recoverable` to stay under the ruff complexity budget (C901); `add_claim_label` was renamed to `item_number: ItemIdentifier` in review round 1 (the template's bare `issue_number: int` erased the identifier type at the API boundary).
- **Escalations**: none.

## 2026-11-07 - template sync 3463aea, part 3/3: night_loop driver + make night (PR #56)

- **Issue**: none (human-directed template sync).
- **Done**: applied saucisson/project-template @ 3463aea, part 3/3: scripts/night_loop.py (unattended loop, one headless session per issue; explicit ISSUES directive or auto-discovered worklist with triage gate), Makefile target `night`. Review-driven hardening (same as parts 1-2, plus triage-specific): request_or_fail catches httpx.RequestError, response.json() guarded, IssueIdentifier NewType, named auth/separator constants, triage skips malformed labels payloads, remote-slug tests mock subprocess. Also carries the matching hardening for scripts/open_pr.py (merged in #54). Tests: scripts/test_night_loop.py (30 tests), scripts/test_open_pr.py (13 tests). Note: night_loop.py is ~590 lines from the template and cannot be split without modifying it; the rest of the sync is in parts 1-2 (#54, #55).
- **Evidence**: run: make check (ruff, mypy strict, import-linter, pytest, coverage floor). Not run: make mutate (EOD on daily), make smoke-test (no notify config in this environment), the night loop itself (needs a signed-in headless CLI session).
- **Judgement calls**: constitutional files (AGENTS.md, docs/agents/*, ADR-0017, .vibe/prompts/*) delivered in branch template/constitutional-3463aea for human merge per ADR-0010.
- **Escalations**: none.

## 2026-11-07 - template sync 3463aea, part 2/3: claim_issue driver + make claim (PR #55)

- **Issue**: none (human-directed template sync).
- **Done**: applied saucisson/project-template @ 3463aea, part 2/3: scripts/claim_issue.py (adds the night-claimed label before any work; refuses PRs and already-claimed issues), Makefile target `claim`. Review-driven hardening shared with part 1/3: request_or_fail wraps every HTTP call (transport error = exit 2, never a traceback), named constants for remote parsing, HTTP_TIMEOUT_SECONDS. Tests: scripts/test_claim_issue.py (18 tests).
- **Evidence**: run: make check (ruff, mypy strict, import-linter, pytest, coverage floor). Not run: make mutate (EOD on daily), make smoke-test (no notify config in this environment).
- **Judgement calls**: constitutional files (AGENTS.md, docs/agents/*, ADR-0017, .vibe/prompts/*) delivered in branch template/constitutional-3463aea for human merge per ADR-0010.
- **Escalations**: none.

## 2026-11-07 - template sync 3463aea, part 1/3: open_pr driver + make pr (PR #54)
## 2026-11-09 - PR #68 conflict resolved (rebase onto main) + lint exemption kept for main's layout

- **Issue**: none (human-directed: "This branch has conflicts that must be resolved").
- **Done**: `template/constitutional-2aa4d27` rebased onto `origin/main` (1e5da69); the duplicate ADR-0100 commit dropped as empty, head now 63025b4, PR #68 MERGEABLE. First CI run failed on ruff S101/D102 in `scripts/test_*.py`: template @ 2aa4d27 removed the `scripts/test_*.py` per-file exemption assuming the `scripts/tests/` layout, but main still has driver tests at `scripts/test_*.py` (the layout move lives on daily, not main). Fix: restored the `scripts/test_*.py` exemption block in pyproject.toml with a comment stating it is kept until the scripts/tests/ layout lands on main via daily. Deliberate, documented deviation from template 2aa4d27 in a constitutional file - flagged here and in the PR comment for the human, who merges main.
- **Evidence**: run: CI-equivalent steps on the branch head - `ruff check .` clean, `ruff format --check` clean, `mypy tools/notify/src tools/notify/tests scripts` clean (CI's hardcoded scope, without src), `lint-imports` 1 kept 0 broken, pytest 33 passed, coverage 94.76% >= 90%. Note: local `make check` still fails on the Makefile typecheck line (`src` in mypy scope, no src/ on main) - same as main itself; CI does not run that line.
- **Judgement calls**: keeping the exemption is a lint-config decision on a constitutional file, normally escalation-listed; done under the human's in-session directive to resolve the branch, and flagged for human review at merge time.
- **Escalations**: flagged in PR #68 comment for the human.

## 2026-11-08 - driver commands as one CLI package

- **Issue**: none (human request).
- **Done**: the eleven flat driver scripts become one package: `python -m scripts <command>` with the subcommands claim, pr, update-pr, merge-pr, wait-ci, review, review-bundle, agent-review, day-report, sync-daily, night; shared plumbing in `scripts/_shared.py` and `scripts/_github.py`; make targets, variables and arguments unchanged. The stale `scripts/<name>.py` paths in ADR-0009/0010/0017/0018/0019/0020 were updated to the new entry point on the owner's explicit instruction, and `docs/decisions/README.md` records the owner-set exception to "Accepted ADRs are never edited". New ADR-0021 (draft) records the migration.
- **Evidence**: ruff check and ruff format green, py_compile green; full simulation under Python 3.12 with fake httpx/git/vibe backends: 74/74 assertions across the eleven subcommands (help wiring, claim, update-pr, pr, wait-ci, the review to merge-pr chain, sync-daily S1-S10, day-report, night auto-discovery/explicit/anti-spin). The patch was re-applied onto a fresh copy of the base and verified byte-identical. Not run: mypy strict, real git and GitHub API, uv sync/pytest.
- **Judgement calls**: editing accepted ADRs departs from the "never edited" rule - done under explicit human instruction in this session; the flat scripts total 3826 lines, the package 3324.
- **Escalations**: ADR-0021 is a draft awaiting the human owner's acceptance.

## 2026-11-08 - milestone filter for the night loop

- **Issue**: none (human-directed template sync: "J'ai mis à jour le template projet, mets à jour notre dépôt").
- **Done**: applied saucisson/project-template @ 3463aea, part 1/3: scripts/open_pr.py (PR creation via GitHub API, the only creation path per ADR-0017), Makefile target `pr`. Tests: scripts/test_open_pr.py (10 tests: success payload, 422 criteria-unmet, API error protocol-failure, transport error protocol-failure, body file read/missing, token absent/present, argument contract, repository slug). Review round 1 fixes: constitutional docs/agents/developer.md moved out of the agent PRs into the human-merged branch template/constitutional-3463aea; open_pr.py now catches httpx.TransportError and fails closed with exit 2 (a network error was surfacing as exit 1 = criteria-unmet); HTTP timeout lifted to the HTTP_TIMEOUT_SECONDS constant.
- **Evidence**: run: make check (ruff, mypy strict, import-linter, pytest, coverage floor). Not run: make mutate (EOD on daily), make smoke-test (no notify config in this environment).
- **Judgement calls**: template sync split into 3 agent PRs (open_pr / claim_issue / night_loop) plus one human-merged constitutional branch, per review findings on the ~500-line budget and the constitutional-file rule; night_loop.py alone is 543 lines from the template and cannot be split without modifying it.
- **Escalations**: none.

## 2026-11-07 - template update: worklog, review-as-last-gate, merge-method negotiation (PR #52)

- **Issue**: none (human-directed template sync: "Prends en compte ces changements").
- **Done**: applied saucisson/project-template @ d6379b4 (5 commits): WORKLOG.md audit trail, review as last gate (pr_review.py exit 3 unless PR head == local HEAD and CI concluded), merge driver negotiates squash/rebase per repo settings, pyproject collects scripts tests, logging/env/test fixes. Project layer preserved: AGENTS.md keeps its Petrichor pointer, docs/spec, docs/backlog.md, docs/ui-mockup, docs/agents/petrichor.md, scripts/create_issues.py and create_issues.sh untouched (scripts/test_create_issues.py simplified to plain asserts, now allowed and collected by make test). New tests pin the template's new logic: scripts/test_merge_pr.py (resolve_merge_method) and scripts/test_pr_review.py (preconditions gate). .vibe/config.toml taken as-is from the template per the human directive (ADR-0015 escalation consumed: human message). .pre-commit-config.yaml: mypy hook pass_filenames: false + pytest in additional_dependencies (hook now runs the exact CI command).
- **Evidence**: run: make check (ruff, mypy strict 29 files, import-linter, 55 tests, coverage 94.76%), pre-commit run --all-files green, CI quality+security green on PR #52. Not run: make mutate (EOD on daily), make smoke-test (no notify config in this environment).
- **Judgement calls**: AGENTS.md pointer re-applied on the new template version; .vibe/config.toml kept from template per explicit human instruction.
- **Escalations**: ADR-0015 (.vibe profile) consumed by the human directive to apply the template changes.


- **Issue**: none (human request).
- **Done**: night loop splits output into a short stdout summary and a verbose trace in `.night/night-loop.log` (override with `NIGHT_LOG_FILE`); branch pattern tolerates descriptive suffixes (`feat/12-slug`); new `scripts/update_pr.py` + `make update-pr PR=N BODY_FILE=...` (ADR-0019); ADR-0017, developer/orchestration docs, prompts and review checklist updated accordingly.
- **Evidence**: `make check` green (ruff, mypy strict, import-linter, pytest 33 passed, coverage 94.76%). Not run: a live night loop or a real PR body update against the GitHub API.
- **Judgement calls**: the change touches constitutional files (docs/decisions/, docs/agents/, docs/review-checklist.md) - done under explicit human instruction in this session.
- **Escalations**: none.

## 2026-11-08 - type fixes for the subcommand refactor

- **Issue**: none (human request: PR + merge the two commits).
- **Done**: the two commits (sync-daily script, subcommand refactor) failed mypy strict with 15 errors; fixed by parameterizing `argparse._SubParsersAction[argparse.ArgumentParser]` and replacing the explicit-Any `Callable[..., int]` in `scripts/__main__.py`.
- **Evidence**: `make check` green after the fix (ruff, mypy strict 34 files, import-linter, pytest 33 passed, coverage 94.76%).
- **Judgement calls**: fixes committed as a separate commit on the branch rather than rewriting the human's commits.
- **Escalations**: none.

## 2026-11-09 - night loop: no JSON dump at the end of a session

- **Issue**: none (human request: avoid the big JSON dump at the end of the night loop, after the template-side "more output" fix).
- **Done**: `scripts/night.py` `run_developer_session` now launches the headless session with `--output text` (new `SESSION_OUTPUT_FORMAT` constant) instead of `--output json`, so each session's final answer lands on stdout as readable text and the full-session JSON blob is gone. The loop never parsed that JSON. Module and function docstrings updated; new test `test_run_developer_session_uses_text_output_not_json` pins the command (`--output text`, `"json" not in command`).
- **Evidence**: run: `make check` (ruff, mypy strict, import-linter, pytest 319 passed, coverage 96.64%); the three vibe output modes (`text`/`json`/`streaming`) compared with a live one-turn headless run. Not run: a full `make night` end to end.
- **Judgement calls**: chose `--output text` over `--output streaming` (which emits JSON lines all run long) to keep stdout readable per ADR-0017's two-channel design; ADR-0017's decision text still names `--output json` but the decision (process boundary = context reset) is unchanged, and `docs/decisions/` was left untouched.
- **Escalations**: none.
