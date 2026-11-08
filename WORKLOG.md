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

## 2026-11-08 - template sync 2aa4d27, part 4a: day + session domains (day-report, sync-daily, agent-review; ADR-0020/0021)

- **Issue**: none (human-directed template sync).
- **Done**: `scripts/day.py` (day-report + sync-daily subcommands, ADR-0020) and `scripts/session.py` (agent-review) from template @ 2aa4d27 replace `day_report.py` and `agent_review.py`; `__main__.py` registers `(pulls, ci, claims, merge, day, session)`; Makefile `day-report` now calls `python -m scripts day-report` and gains the `sync-daily` target (`python -m scripts sync-daily`). No test files existed for the replaced modules; dispatcher smoke-tested via `python -m scripts --help`.
- **Evidence**: run: `make check` green (ruff + format, mypy strict, import-linter, 219 tests, coverage 94.76%) and `python -m scripts --help` lists day-report/sync-daily/agent-review. Not run: CI/review until push; `make sync-daily` itself not run yet (needs part 4b merged first).
- **Judgement calls**: docs/ references to `agent_review.py` are constitutional and stay on the `template/constitutional-2aa4d27` branch, not in this PR.
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

