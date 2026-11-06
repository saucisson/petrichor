# ADR-0020: Human-approved waiver of the reviewer

- Status: accepted
- Date: 2026-11-06

## Context

ADR-0016 makes the `reviewer` job a required status check: zero BLOCKING
findings is a merge criterion, enforced mechanically. Sooner or later a
situation arises where the human owner wants to merge despite BLOCKING
findings: a huge generated diff (dependency lockfile, vendored data),
a finding the human judges to be a false positive, or an urgent fix
that cannot wait for another iteration round. Without a codified
mechanism, the only options are to disable branch protection (loud and
dangerous) or to use an administrator merge button (silent and
unauditable) - both place the human OUTSIDE the protocol the agents
read, and neither leaves a record of what was overridden and why.

The same review of launch mechanics showed a second, smaller problem:
the workflow used the `gh` CLI, which is not preinstalled on
GitHub-hosted runners, for reading the PR description and posting the
review comment. This is corrected alongside: every GitHub interaction in
the `reviewer` workflow now goes through the REST API directly.

## Options considered

1. **Administrator merge / temporarily relax branch protection.**
   Rejected: bypasses the codified criteria entirely, is invisible to
   agents reading the repository state, and leaves no trace of which
   findings were overridden.
2. **Tell the reviewer model that a human approved, so it reports zero
   BLOCKING findings itself.** Rejected: the approval enters the
   model's context and can soften its judgment on everything else -
   the review is no longer independent, and the original findings are
   lost rather than recorded.
3. **A deterministic post-review waiver driven by a manually posed
   label (chosen).** The review runs unmodified; afterwards, the runner
   script (`scripts/agent_review.py`) converts the findings according
   to the PR labels.

## Decision

- A manually posed **`human-approved` label** on the pull request
  waives **every blocking case**: each BLOCKING finding - any rule, any
  evidence, including fail-closed unknown severities - is converted to
  a **WAIVED** severity with its original evidence plus the waiver
  note, and the `reviewer` exit code returns to 0. The findings stay
  visible in the report and the comment; nothing is hidden or dropped.
- The waiver is applied **deterministically in the runner script,
  after the review**. The reviewer model never learns that the label
  exists: labels are exported by the CI into
  `.review/pull-request-labels.json` and consumed by
  `scripts/agent_review.py` only, so its judgment cannot soften
  (context isolation of ADR-0011 is preserved).
- **TO-FIX and SUGGESTION findings are never touched** (they do not
  block). **Protocol failures are never waivable** (ADR-0016 exit code
  2): the waiver is a human judgment on review findings, not on
  infrastructure. A missing or unreadable labels file means "no
  waiver" - the fail-closed side of the mechanism.
- The reviewer **re-runs when the label is posed or removed** (GitHub:
  `labeled`/`unlabeled` triggers, filtered to this label; GitLab: re-run
  the merge request pipeline after changing labels, since GitLab does
  not re-trigger on label events). Removing the label re-blocks the
  pull request on the next review.
- Launch mechanics correction: the `reviewer` workflow talks to the
  GitHub **REST API directly** (`curl` + `jq`, both preinstalled) for
  the PR description, the PR labels and the review comment; the `gh`
  CLI is no longer used anywhere.

## Consequences

- The human owner has a codified, auditable override: the label, the
  WAIVED findings with their original evidence, and the report artifact
  together form the record of what was waived and when.
- `zero BLOCKING findings` keeps its exact meaning; the merge criteria
  (ADR-0010) are extended with "or every BLOCKING finding waived by the
  `human-approved` label" rather than weakened.
- Negative: a human can now waive ANYTHING, including security or
  architecture findings. Accepted by definition - the human is the
  ultimate authority in the protocol (ADR-0012) - but the label should
  stay rare, and each use is visible to every future reader of the PR.
- Negative: the waiver re-run costs one extra API call per label
  change on GitHub; on GitLab, the human must remember to re-run the
  pipeline manually after posing the label.
