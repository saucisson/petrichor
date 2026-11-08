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

## 2026-11-07 - template sync 3463aea, part 2/3: claim_issue driver + make claim (PR #55)

- **Issue**: none (human-directed template sync).
- **Done**: applied saucisson/project-template @ 3463aea, part 2/3: scripts/claim_issue.py (adds the night-claimed label before any work; refuses PRs and already-claimed issues), Makefile target `claim`. Review-driven hardening shared with part 1/3: request_or_fail wraps every HTTP call (transport error = exit 2, never a traceback), named constants for remote parsing, HTTP_TIMEOUT_SECONDS. Tests: scripts/test_claim_issue.py (16 tests).
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

