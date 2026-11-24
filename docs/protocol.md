# The working protocol

Constitutional document. This is the loop the agent team follows
between two human check-ins - one working day. It carries the branch
model, the day and night cycles, the developer and escalation contracts.
`AGENTS.md` is the short constitution; this is the full protocol.

## Roles

Two roles, never one agent doing both:

- **Developer agent** - implements issues, opens pull requests, runs
  the review, merges into `daily` through the merge driver.
- **Reviewer agent** - reviews each diff with isolated context
  (contract in `docs/rules.md`); never merges.

## Branches

| Branch             | Purpose                             | Who merges                                    |
| ------------------ | ----------------------------------- | --------------------------------------------- |
| `main`             | The tested, releasable product      | **Human only**, after human tests             |
| `daily`            | One working day of agent work       | Developer agent, via `make merge-pr`, on the criteria below |
| `feat/...`, `fix/...` | One issue each, short-lived      | Same driver, same criteria, into `daily`      |

`main` and `daily` are both protected branches. `daily` exists so that
agent autonomy has a blast radius of exactly one day; `main` exists so
that a human always stands between "the agents believe it works" and
"the product is updated".

## The day cycle

1. **Morning directive** - the human selects and prioritizes the day's
   issues, and runs `make day-start` (tags `daily-YYYY-MM-DD` as a
   restore point). Agents **never edit the directive**: priorities
   change only by human comment. Re-read the directive at the start of
   every session - the goal-drift guard.
2. **Sync** - if `origin/main` has commits `daily` lacks (template
   sync, hotfix, release), the day session runs `make sync-daily` right
   after `make day-start`, before any work (see below).
3. **Triage gate** - an issue enters the loop only with acceptance
   criteria (the `task` template enforces them), an assignable branch
   name, and no unresolved dependency on a `blocked` issue. Otherwise:
   label `blocked`, comment why, move on.
4. **Work** - one issue at a time, branch `feat/<issue>` or
   `fix/<issue>` from `daily`, small pull requests (target < 500 changed
   lines), rebase on `daily` before opening.
5. **Review and merge into `daily`** - once CI is green on the pushed
   head, the developer agent runs `make review PR=N` (local reviewer
   session), then merges via `make merge-pr PR=N` (see the criteria
   below).
6. **End of day** - freeze merges, `make day-report`, `make
   smoke-test`, then the human tests, audits the day's entries in
   `docs/worklog/` (listed by `make day-report` - one entry per commit
   or pull request, none missing), and merges `daily` into `main`.
7. **Mutation testing** runs on `daily` at EOD (`make mutate`, workflow
   `mutation.yml`) - surviving mutants become issues.

A red `daily` at EOD triggers the incident protocol (see below).

## Review preconditions and merge criteria

The review is the **last** gate, never a lint pass: every finding a
local gate or a re-read of the rules could have caught is a wasted
review session. The developer agent runs `make review PR=N` only once
the preconditions hold - the first two are enforced mechanically by
the review driver, the rest are the developer's honesty rules:

1. the pull request head is the local HEAD: work committed and pushed,
   one command at a time - never a chained `git push && make review`,
   which reviews a stale head;
2. every CI check run on that head has concluded "success" - red or
   pending CI means the review waits, it never substitutes for a
   failing local gate;
3. `make check` is green on the exact commit reviewed, not on an
   earlier one;
4. every new piece of logic ships tests the runner collects;
5. a re-read of the escalation list against the diff shows no
   constitutional file touched without an approved escalation - if it
   does, the escalation comes **before** the review, never after.

"Pending CI means the review waits" is served by an **active** wait:
`make wait-ci PR=N` blocks until CI concludes on the head - exit 0
green (the exact precondition above), exit 1 red or timed out. A
session that ends "waiting for CI" is a session nobody resumes.

On a clean verdict the agent merges via `make merge-pr PR=N`. The
driver merges only when **all three** hold:

1. CI is green on the PR head,
2. the review report contains **zero BLOCKING findings** and was
   written for THIS pull request and its CURRENT head,
3. the change is not on the escalation list.

The reviewer session never merges: it writes the report; the merge
driver reads it. If any criterion is unmet, the PR waits: stalled PRs
are the honest state, silent rule-bending is not.

## The developer contract

### Before writing code

1. Read `AGENTS.md`, `docs/rules.md`, and any ADR in `docs/decisions/`
   relevant to the issue.
2. Read the issue **in full**, including acceptance criteria. If
   criteria are missing or ambiguous, report it - do not invent them
   (triage gate).
3. Branch from `daily`: `feat/<issue>` for features, `fix/<issue>` for
   defects. One issue per branch.

### While writing code

- Follow every rule in `docs/rules.md` - they are not suggestions; the
  reviewer checks them mechanically.
- Run the full gate locally before reporting: `make check`, on the
  **final** state - a green gate on an earlier commit is not evidence
  about the one you push.
- Every new piece of logic ships tests, in a location the runner
  collects (`tools/<tool>/tests` for a tool, `scripts/tests/` for a
  script): tests the gate never collects are theater.
- Rebase on `daily` before opening the pull request. Keep it under
  ~500 changed lines; split earlier rather than larger.

### Reporting

The pull request follows the template (`.github/PULL_REQUEST_TEMPLATE.md`)
and is **honest**: evidence you did not run is stated as such, never
implied. It contains:

- **Changes**: what was done, mapped to the acceptance criteria.
- **Testing evidence**: the commands run and their actual output summary.
- **Judgement calls**: every decision where two reasonable options
  existed, with the ADR or issue comment that decided it.
- **Known limitations**: what remains unhandled and why.

The worklog entry is generated by `make pr` when the pull request is
created (the driver writes `docs/worklog/<date>-pr-<N>.md`, commits it
on the branch as `worklog: PR #N`, and pushes) - never write it by
hand. A direct commit on `daily` (revert protocol, docs-only fix)
carries its entry from `docs/worklog/_template.md` in the same commit.

### After the pull request

1. Push the branch, open the pull request via
   `make pr TITLE="..." BODY_FILE=...` - the only creation path, it
   works attended and unattended (never `gh`) - then wait **actively**,
   one command at a time:

   ```console
   make wait-ci PR=N
   ```

   NEVER end the turn or the session "waiting for CI": nobody resumes a
   session, an unattended one least of all - block on `make wait-ci`,
   or stop honestly and explain the state. Then verify the preconditions
   yourself (§ review preconditions), and run:

   ```console
   make review PR=N
   ```

   The driver fetches the PR, builds the review bundle, runs the
   reviewer session, posts its comment, and returns its exit code. It
   refuses to start (exit 3) unless the pull request head is the local
   HEAD and every CI check on that head has concluded "success".

2. **Exit 0** (zero BLOCKING findings): run `make merge-pr PR=N`. The
   driver re-checks the merge criteria, merges into `daily` pinned to
   the reviewed commit with the linear method the repository allows
   (squash, or a rebase merge when squash is disabled), and deletes the
   branch. Then **clear the session** (`/clear` in the Vibe Code CLI; a
   new session elsewhere): the next issue starts from a fresh context,
   re-anchored on the directive and the constitution.
3. **Exit 1** (findings): address every BLOCKING and TO-FIX finding,
   push, wait for CI, and re-run `make review PR=N`. A size-budget
   finding (diff over ~500 lines) is resolved by a split proposal
   written into the pull request body: compose the new body file (the
   existing body plus the proposal) and run
   `make update-pr PR=N BODY_FILE=...` - the only body-update path; it
   replaces the whole body, so keep the existing body in the file.
4. **Exit 2** (protocol failure): do not retry blindly - report it as a
   `needs-human` blocker with the error message.
5. **Exit 3** (preconditions unmet): not a verdict and not an attempt -
   wait for CI (or push the head first), then re-run `make review`.

A BLOCKING finding has no override: address it, or escalate to the
human (`needs-human`) if you believe it is a false positive - the human
decides, in the open, on the record.

### Budgets

- An issue gets **at most 3 attempts** across the whole develop-review
  loop. If the third fails, stop: label the issue `blocked` and
  `needs-human`, comment the structured question, then move to the
  next issue.
- Answered `needs-human` issues take **priority** over new work.

### What you never do

- Merge anything by hand or into `main`. The only merge path is
  `make merge-pr`, into `daily`, on a zero-BLOCKING report you did not
  write (the reviewer session did).
- Open a pull request outside `make pr`, or update a body outside
  `make update-pr`: the raw API is denied in an unattended session, and
  that denial is the allowlist working - the driver is the path.
- Rebase or force-push `daily` outside `make sync-daily`, or run the
  sync unattended: rewriting the day's shared branch is exactly one
  driver, never a hand command, and `main` is never rewritten at all.
- Work an item you have not claimed first (auto-discovered night
  sessions): `make claim ISSUE=N` or `make claim-pr PR=N` is what keeps
  two sessions off the same issue or the same abandoned pull request.
- Modify constitutional files: `AGENTS.md`, `docs/protocol.md`,
  `docs/rules.md`, `docs/decisions/` (you may only *draft* a new ADR
  inside your PR, never accept one; number it from 0100), lint/type/
  coverage configuration, branch protections.
- Add a dependency, change architecture, or touch security-relevant
  code without an approved escalation.
- Weaken a test, a rule, or a threshold to make a check pass.

## Integrating main into daily

`main` moves without `daily` when the human syncs the template,
hotfixes, or releases. The day session reconciles the branches itself,
early in the day (right after `make day-start`, before opening pull
requests):

- `make sync-daily` rebases `daily` onto `origin/main` and pushes the
  rewritten `daily` with `--force-with-lease`. A strictly behind
  `daily` is fast-forwarded instead - no rewrite, no force. When
  `daily` already contains `origin/main`, the driver exits 0 without
  writing anything.
- The driver refuses anything but a clean `daily` checkout; it verifies
  that the replayed `daily` contains `origin/main` and the same number
  of daily-only commits before pushing.
- A rebase conflict aborts and escalates (`needs-human`): resolving a
  conflict between accepted `main` content and accepted `daily` work
  is a human decision, never an unattended merge.
- `main` is never rewritten. Night sessions never run the sync: the
  unattended loop derives its worklist from `daily` and nobody answers
  a conflict at 3 a.m.
- When the sync rewrites `daily`, local branches based on the old
  `daily` need a rebase before their pull request.

## The unattended loop (night)

`make night` runs the day loop while nobody answers. The directive
comes in two flavors, and the loop runs **one fresh headless session
per item** in both - the process boundary replaces the session clear:
no issue inherits the previous one's residue, and a session that dies
at 3 a.m. costs one issue, not the night.

- **Explicit directive**: `make night ISSUES="12,13,14"`. The human
  writes the issue list before leaving (the morning-directive rule:
  agents never edit it), and the loop runs one session per issue, in
  order.
- **Auto-discovery**: `make night` with no ISSUES. The loop derives each
  worklist from the repository itself - open issues and abandoned pull
  requests, triage gate applied, minus the `night-claimed` label - and
  each session **picks one item and claims it first**: `make claim
  ISSUE=N` for an issue, `make claim-pr PR=N` for a pull request. The
  claim is the concurrency guard and the honesty record: two sessions
  never work the same item, and a failed attempt stays claimed - the
  morning audit sees what was tried, and the human, never an agent,
  decides the retry.
- **Recovery** (auto-discovery only): a session may pick a pull request
  an earlier session left open. A pull request is recoverable when it
  is open, targets `daily`, its head branch is `feat/N` or `fix/N` (a
  descriptive suffix after the number is tolerated - the number decides
  the backing issue), its backing issue #N carries `night-claimed`, and
  the pull request itself is not claimed by another recovery session.
  The session claims it before touching it, diagnoses it (`make wait-ci
  PR=N`, local `make check`, full read of the diff and review comments),
  names the resumption honestly on the pull request, and walks the same
  review-and-merge gate as a fresh issue. A human can also hand a pull
  request to the night by labeling its backing issue `night-claimed` by
  hand.
- Each session follows the **full developer contract** - develop, open
  the pull request through `make pr`, review, **merge into `daily`
  through `make merge-pr`**, or resume an existing pull request - and
  **stops after one item**: the loop, never the session, owns the next
  one and the next worklist.
- The loop applies the triage gate before each session: issues labeled
  `blocked`, `needs-human`, or `night-claimed`, and issues without
  acceptance criteria, are skipped and reported.
- In auto-discovery the loop re-derives the worklist before every
  session and stops when no unclaimed item remains, when the session
  budget (`NIGHT_MAX_SESSIONS`) is spent, or when a session claims
  nothing - the night never spins on one stuck item.
- Nobody answers approvals: allowlisted commands run, and a denied call
  is a stop-and-escalate signal, never an obstacle to route around.
- A session never ends on a wait: CI is waited for **actively**, with
  `make wait-ci PR=N`, inside the session's time budget.
- A night session never rewrites `daily`: `make sync-daily` is a
  day-session action, and rewriting the branch the loop derives its
  worklist from mid-flight, with nobody to answer a conflict, is out of
  the unattended flow entirely.
- The loop speaks to a human on stdout: one line per session (naming
  the item the session claimed) and one verdict at the end. The verbose
  trace - full candidate lists, anti-spin decisions, fetch warnings -
  lands in `.night/night-loop.log` (override: `NIGHT_LOG_FILE`). Every
  session prompt is kept under `.night/sessions/<run>/`, and the trace
  logs a `still running` heartbeat every `NIGHT_HEARTBEAT_SECONDS`
  (default 60, `0` disables it) so a silent session is not a dead one.
- The morning audit is the whole point: the day's `docs/worklog/`
  entries (via `make day-report`), the pull requests, every
  `needs-human` issue, and every `night-claimed` issue tell the
  night's story.

## Escalation and human contact

### The escalation list (strict)

These changes are **never** decided by agents, at any severity - they
are `question` escalations at minimum, before any code is written:

- anything security-relevant (auth, secrets handling, permissions),
- a new dependency, or a major version bump of an existing one,
- an architecture change (layer boundaries, new layers, ports reshuffle),
- a schema or data migration,
- any constitutional file: `AGENTS.md`, `docs/protocol.md`,
  `docs/rules.md`, `docs/decisions/`, lint/type/coverage config, the
  `.vibe/` runtime profile, branch protections,
- any decision worth an ADR (rule of thumb: would a future agent ask
  "why?" → draft an ADR in the PR and label it `needs-human`; project
  ADRs are numbered from 0100).

### Making a request

1. Comment on the relevant issue (or open one if the question is
   generic). The comment is **structured**:
   - **Context**: what work is blocked, with links.
   - **Question**: one precise, answerable question.
   - **Options**: each with its trade-offs; a recommendation is welcome,
     the decision is not.
   - **Blocked**: what stops, and what continues meanwhile.
   - **Impact**: what happens if unanswered at end of day.
2. Apply the label `needs-human`, remove any `in-progress` label.
3. Send the notification:

   ```console
   make notify severity=question message="..." issue-identifier="..."
   ```

   For urgent matters: `severity=urgent` - this requires
   `DISCORD_WEBHOOK_URL` and pushes to the owner's channel (the
   `notify` tool in `tools/notify/` is the implementation).

### Severity contract

| Severity   | Meaning                      | Behavior                          |
| ---------- | ---------------------------- | --------------------------------- |
| `question` | Parkable decision            | Park the issue, continue others  |
| `urgent`   | Day-level blocker or danger  | **All merges stop** until answer  |
| `info`     | FYI, no decision needed      | No workflow impact                |

Examples of `urgent`: `daily` red beyond the revert protocol, a secret
exposed anywhere, every open issue blocked by the same question.

### Answering and resuming

The human answers by commenting the issue. Agents check for answered
`needs-human` issues **first** in every cycle. An answered issue is
un-labeled, taken with priority, and its answer is the authority - if
the answer conflicts with an ADR, the ADR change (drafted,
human-accepted) comes first.

## Branch protections to enable

**GitHub** (`main` and `daily`):

- Require status checks: the `check` and `security` jobs from `ci.yml`.
  There is no `reviewer` status check: the review runs locally and the
  merge driver enforces its verdict.
- Require linear history (the merge driver squash-merges, or
  rebase-merges when squash is disabled).
- Allow force pushes on `daily` only (the sync driver pushes
  `--force-with-lease`); keep them forbidden on `main`.
- Delete head branches on merge (the driver also deletes them).
- `CODEOWNERS` reserves `docs/decisions/` and tool configuration for
  the human owner.

**GitLab**: merge checks = pipelines must succeed; "merge when pipeline
succeeds" plays the merge role (the merge driver targets the GitHub
API); protected branches for `main` and `daily` with no agent allowed
to merge directly.

## Incident protocol

When `daily` is red: **revert first, debug second**.

1. Revert the offending merge commit (never pile a fix on a red branch).
2. Reopen the original issue with the failure evidence.
3. The issue keeps its attempt budget: after **3 failed attempts**, it
   is labeled `blocked` and escalated (`needs-human`).

An incident that cannot be resolved by revert is an **urgent**
escalation: all merges stop until the human answers.
