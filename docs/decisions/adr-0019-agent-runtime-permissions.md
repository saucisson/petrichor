# ADR-0019: Agent runtime permissions - the `.vibe/` profile

- Status: accepted
- Date: 2026-11-04

## Context

The agent protocol (ADR-0011) and the branch model (ADR-0010) define
what the agents owe the project, but not what the coding agent's
runtime may do on the developer machine. In practice this broke
autonomy: the CLI asked for approval before every shell command, and
each unanswered prompt froze the working day. On WSL2 there is also an
exposure question: `/mnt/c` gives any process in the distro - the agent
included - the user's full Windows filesystem.

## Options considered

1. **`auto-approve` everywhere.** Rejected: it auto-approves shell
   commands too, and it is not a sandbox - calls run with the user's
   permissions. On a machine holding credentials, that violates the
   escalation spirit, and the shell-permission advisories (e.g. the
   MAI-2026-003 family) show approval bypasses do get found.
2. **Keep the default agent (`accept-edits`).** Rejected: it still
   asks for every shell command - exactly the approval fatigue that
   blocked the autonomous day.
3. **`smart-approve` + a project allowlist + `outside_workdir = "deny"`,
   versioned in `.vibe/config.toml`.** Chosen: the agent runs free on
   the project's own commands, a classifier holds back risky calls and
   asks for those, and anything reaching outside the repository is
   refused rather than asked.

## Decision

- The repository ships a **`.vibe/config.toml`** profile:
  - `default_agent = "smart-approve"` - fewer prompts, risky calls
    still gated (a classifier, not a sandbox; said plainly in the file).
  - `outside_workdir = "deny"` - reads, writes and commands outside
    the repository are **refused**, not asked; the agent gets an error
    and keeps working. `/mnt/c` is outside by construction.
  - `[tools.bash]` in `ask` mode with an explicit `allowlist`
    (read-only classics, git including push, uv, make, npm scripts,
    docker compose and docker image commands) and a minimal `denylist`
    (`sudo`, `rm -rf /`). The allowlist **replaces** Vibe's defaults,
    so the classics are re-included on purpose.
- The profile is **version-controlled and reviewed**: it changes how
  the agent behaves, so it joins the escalation list (constitutional
  files). Vibe loads it only from explicitly **trusted folders** -
  trust the folder once, after reviewing the file.
- On WSL2 the recommendation is documented in the profile and README:
  clone inside the Linux filesystem (never `/mnt/c`, also for I/O
  speed) and mount Windows drives read-only or not at all
  (`/etc/wsl.conf`, `[automount]`): the VM boundary is a stronger
  guarantee than any prompt-level check.
- The CLI version matters: approval bypasses have required fixes
  (MAI-2026-003, fixed in 2.25.4) - keep the CLI current before
  widening permissions.

## Consequences

- The autonomous day no longer stalls on routine commands; the human
  is asked only for calls the classifier judges risky.
- A misjudged call can still run: smart-approve reduces prompts, it
  does not limit access. The hard boundaries remain the repository
  protection settings, the WSL mounts and CI - never the classifier.
- New tool commands not in the allowlist will prompt again until the
  profile is extended - that extension is an escalation, by design.
- `git push` is allowlisted: protections on `daily` and `main` are the
  merge boundary (ADR-0010), and the reviewer gate runs on the PR.
