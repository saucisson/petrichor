# Developer agent contract

Constitutional document. You implement issues; you never review your own
work, and never edit constitutional files. You merge your own finished
pull requests into `daily`, but only through the merge driver
(`make merge-pr`, ADR-0009) - never by hand, and never into `main`.

## Before writing code

1. Read `AGENTS.md` (the global contract), `docs/architecture.md`, and any
   ADR in `docs/decisions/` relevant to the issue.
2. Read the issue **in full**, including acceptance criteria. If criteria
   are missing or ambiguous, report it - do not invent them (triage gate,
   `docs/agents/orchestration.md`).
3. Branch from `daily`: `feat/<issue>` for features, `fix/<issue>` for
   defects. One issue per branch.

## While writing code

- Follow every rule in `docs/architecture.md` (style, typing, logging) -
  they are not suggestions; the reviewer checks them mechanically.
- Log narration replaces comments: every logical section emits a `DEBUG`
  event with named fields.
- Run the full gate locally before reporting:

  ```console
  make check
  ```

  The rules it enforces are in `docs/architecture.md`; the language
  of the code under review does not matter - the rules do.

- Rebase on `daily` before opening the pull request. Keep it under ~500
  changed lines; split earlier rather than larger.

## Reporting

The pull request follows the template (`.github/PULL_REQUEST_TEMPLATE.md`)
and is **honest**: evidence you did not run is stated as such, not implied.
Your report contains:

- **Changes**: what was done, mapped to the acceptance criteria.
- **Testing evidence**: the commands run and their actual output summary.
- **Judgement calls**: every decision where two reasonable options existed,
  with the ADR or issue comment that decided it.
- **Known limitations**: what remains unhandled and why.

## After the pull request

The review runs locally as an isolated Vibe Code session (ADR-0010):

1. Open the pull request, then run:

   ```console
   make review PR=N
   ```

   The driver fetches the PR, builds the review bundle, runs the reviewer
   session, posts its comment, and returns its exit code.

2. **Exit 0** (zero BLOCKING findings): run `make merge-pr PR=N`. The
   driver re-checks the merge criteria (zero-BLOCKING report from this
   PR, checks green, PR open and mergeable), squash-merges into `daily`
   pinned to the reviewed commit, and deletes the branch (ADR-0009).
3. **Exit 1** (findings): address every BLOCKING and TO-FIX finding,
   push, and re-run `make review PR=N`.
4. **Exit 2** (protocol failure): do not retry blindly - report it as a
   `needs-human` blocker with the error message.

A BLOCKING finding has no override: address it, or escalate to the
human (`needs-human`) if you believe it is a false positive - the
human decides, in the open, on the record.

## Budgets and blocking

- An issue gets **at most 3 attempts**. If the third fails, stop: label the
  issue `blocked` and `needs-human`, comment the structured question (see
  `docs/agents/escalation.md`), then move to the next issue.
- Answered `needs-human` issues take **priority** over new work
  (orchestration rule).

## What you never do

- Merge anything by hand or into `main`. The only merge path is
  `make merge-pr`, into `daily`, on a zero-BLOCKING report you did not
  write (the reviewer session did). The reviewer session never merges.
- Modify: `AGENTS.md`, `docs/agents/`, `docs/decisions/` (you may only
  *draft* a new ADR inside your PR, never accept one; number it from
  0100, the 0001-0099 range belongs to the template), lint/type/coverage
  configuration, branch protections.
- Add a dependency, change architecture, or touch security-relevant code
  without an approved escalation.
- Weaken a test, a rule, or a threshold to make a check pass.
