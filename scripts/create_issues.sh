#!/usr/bin/env bash
# create_issues.sh - populate the GitHub repository (labels, milestones, issues
# P01-P50) from docs/backlog.md, per spec §19.2. Thin wrapper around
# scripts/create_issues.py so the logic stays under the ruff/mypy/pytest gates.
#
# Usage:
#   scripts/create_issues.sh            # dry-run (default): print the plan, create nothing
#   scripts/create_issues.sh --dry-run  # same
#   scripts/create_issues.sh --apply    # create what is missing (idempotent on titles)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec python3 scripts/create_issues.py "$@"
