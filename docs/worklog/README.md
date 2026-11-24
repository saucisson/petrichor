# Worklog - repository records (human destination)

This folder holds the audit records of the repository, meant for the
human who reviews the day before merging `daily` into `main`.

## Conventions

- `YYYY-MM-DD-pr-<N>.md`: one index card per pull request, generated
  automatically by `make pr` when the pull request is created. The
  driver commits it on the PR branch (`worklog: PR #N`) and pushes it.
- `YYYY-MM-DD-direct-<slug>.md`: one entry per direct commit on `daily`
  (revert, docs-only fix, hotfix), created from `_template.md` in the
  same commit.
- `README.md` and `_template.md`: this documentation and the entry
  template for direct commits.

## Rules

- No agent reads this folder: generation is automatic (pull requests)
  or from the template (direct commits).
- Never modify an existing entry, never purge one: the git history is
  the tamper-evident backstop.
- The evening audit goes through `make day-report`, which lists the
  day's entries.
