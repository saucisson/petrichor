# ADR-0016: Reviewer launch mechanics

- Status: accepted
- Date: 2026-11-03

## Context

ADR-0011 defines the reviewer agent's contract - isolated context,
counterexample method, severity ladder, never merges - but not how the
review is actually triggered and enforced. The merge criteria
("CI green + zero BLOCKING findings") require the second half to be
machine-checkable: if the review is a human habit or an optional agent
mention, `daily` auto-merge has nothing to gate on. Isolation also needs
to be mechanical, not a matter of prompt discipline.

## Options considered

1. **Platform-native review agents** (mention-based agents such as the
   built-in coding agents on GitHub/GitLab). Rejected: the platform owns
   the trigger and the output; we cannot turn the verdict into a status
   check other merges depend on, the context is whatever the platform
   decides to give the agent, and the feature is platform-locked.
2. **A dedicated agent CLI or framework in CI.** Rejected: a new
   dependency and a new secret to manage, and the isolation guarantee
   still relies on the framework's prompt configuration.
3. **A single direct API call from a plain CI job.** Chosen: one
   `httpx` call to the Mistral chat completions API with a
   deterministic bundle as the sole user content. `httpx` is already a
   project dependency, the context is exactly the bundle (isolation by
   construction), the answer is a JSON report validated locally, and
   the job's exit code becomes a required status check.

## Decision

- The review runs as a **CI job named `reviewer`**, on every pull
  request push (`opened`, `synchronize`, `reopened`) on GitHub and on
  every merge request pipeline on GitLab. Re-review after each push is
  included by design.
- The job builds a **review bundle** - role contract, architecture
  rules, review checklist, all ADRs, the pull request specification
  (the PR description, whose acceptance-criteria mapping is the spec),
  and the diff - and sends nothing else to the model.
- The reviewer answers with a **JSON report**; the script recomputes
  every count locally and fails closed: unknown severity becomes
  BLOCKING, an invalid report is a protocol failure.
- **Exit codes** drive the status check: `0` = zero BLOCKING findings,
  `1` = at least one BLOCKING finding (job fails), `2` = protocol
  failure (missing key, invalid answer - job also fails).
- The `reviewer` status check is **required** for `daily` merges, next
  to the quality gate. Merging remains a platform action.
- `REVIEWER_MODEL` (repository variable, default
  `mistral-large-latest`) is the **model-diversity lever** of
  ADR-0011: point it at a different model family than the developer
  agent.

## Consequences

- "Zero BLOCKING findings" is now a machine-checkable gate; auto-merge
  depends only on branch protection settings.
- The reviewer cannot explore the repository beyond the bundle - by
  design; the bundle carries the rules and decisions it needs.
- One review comment per push; the latest is authoritative. Accepted
  trade-off (the alternative is threading state through the API).
- Each push costs one API call (bounded by `max_tokens` and a 120 s
  timeout). `MISTRAL_API_KEY` is a repository secret to manage.
- The bundle builder (`make review-bundle`) doubles as a local
  debugging tool: what it prints is exactly what the reviewer sees.
