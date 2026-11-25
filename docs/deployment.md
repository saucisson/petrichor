# Deployment channels (ADR-0008 N3), petrichor instance

Two deployment channels exist, one per protected branch, and both are
automatic: the merge itself is the deploy trigger, so a merged pull
request never waits for a manual staging step. The split follows the
branch model (ADR-0005): `daily` is the agent blast radius, `main` is
the human boundary, and the deployment channels mirror exactly that.

| Branch | Channel | Trigger | Human role |
| ------ | ------- | ------- | ---------- |
| `daily` | staging | every merge into `daily` (the pull request flow) | tests the staging URL |
| `main` | release | every human merge of `daily` into `main` | owns the release |

## What petrichor instantiates today

`.github/workflows/deploy.yml` implements the staging channel: every
push of `daily` rebuilds and restarts the petrichor services on the
OVH VPS over SSH (ADR-0103), through the idempotent `deploy/deploy.sh`
script running on the server, so the CI path and the manual path are
identical. The release channel (`main`) is not instantiated yet: the
repository has no release-path deploy beyond the release-please tag
and the PyPI publish of the package. Wiring it means giving
`deploy.yml` its `main` trigger and a release GitHub environment with
its own secrets, keeping the two channels separable in protection
rules and deployment history.

The template's generic connection smoke is superseded here: the
instance replaces the smoke with its real distribution step, which is
exactly what ADR-0008 N3 asks of an instance.
