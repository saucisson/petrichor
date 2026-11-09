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
