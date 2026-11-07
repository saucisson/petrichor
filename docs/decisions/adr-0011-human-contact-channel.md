# ADR-0011: Human contact - Discord webhook via the notify tool

- Status: accepted
- Date: 2026-11-02

## Context

Agents must be able to reach the owner during the autonomous day, with
a notification channel that actually pushes (an open issue the owner
never looks at is not a notification). The channel also needs a
severity contract: some questions park, some stop everything.

## Options considered

- **Discord incoming webhook through a small reference tool**
  (chosen): zero server to operate, push notification to the owner's
  phone/desktop, trivial to set up, and the tool doubles as a worked
  example of the whole template (onion, ports, adapters, settings).
- **Slack webhook**: equivalent mechanics; Discord chosen on owner
  preference - the pattern ports to Slack by writing one adapter,
  which is the template's own extension pattern.
- **Email**: delivery latency and filtering make it a poor interrupt
  channel.
- **Platform mentions only (GitHub/GitLab)**: requires the owner to
  watch the platform actively - not a push guarantee.

## Decision

- `notify send --severity <question|urgent|info> --message ...
  [--issue-identifier ...]` posts to a Discord incoming webhook
  (`DISCORD_WEBHOOK_URL`); console fallback when unconfigured.
- Severity contract: `question` parks the issue and work continues;
  `urgent` **stops all merges** until answered and requires the
  webhook (refusing to degrade to console rather than silently
  dropping urgency).
- Answered `needs-human` issues take priority over new work
  (orchestration rule).

## Consequences

- One command covers the whole contact protocol; the escalation
  comment stays on the issue (durable), the webhook is only the
  doorbell.
- Negative: webhook URLs are per-channel secrets; the secret scan
  covers the leak risk, and the escalation list requires human
  involvement for any change to the notification flow itself.
