"""Claim driver: mark one issue or pull request taken (ADR-0007).

In the auto-discovered night loop (``make night`` without ISSUES) each
session picks its own item, so its FIRST act is to claim it:
``make claim ISSUE=N`` or ``make claim-pr PR=N`` adds the
``night-claimed`` label through the GitHub API (the issues endpoint
serves pull requests too), with the same token as the other drivers.
The claim is both the concurrency guard and the honesty record:

- two sessions never work the same item (the loop filters claimed
  items out of every worklist, and this driver refuses to claim
  twice);
- a failed attempt stays claimed: the morning audit sees what was
  tried, and the human - never an agent - decides the retry.

Pull-request claims cover the recovery path (ADR-0007): a session may
be handed - or pick - a pull request left open by an earlier failed
session (red CI, unaddressed findings). The same rule applies: no
claim, no work.
"""

import sys
from typing import TYPE_CHECKING

from scripts._github import open_github_client
from scripts._shared import (
    CLAIM_LABEL_NAME,
    EXIT_CODE_CRITERIA_UNMET,
    EXIT_CODE_PROTOCOL_FAILURE,
    EXIT_CODE_SUCCESS,
    GITHUB_API_BASE_URL,
    load_github_token,
    resolve_repository_slug,
)

if TYPE_CHECKING:
    import argparse

    import httpx

CLAIM_LABEL_COLOR = "d4c5f9"
CLAIM_LABEL_DESCRIPTION = "Claimed by an unattended night session (ADR-0007)"
EXIT_CODE_CLAIMED = EXIT_CODE_SUCCESS  # a subcommand-local alias, same protocol code


def fetch_issue(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    item_number: int,
) -> dict[str, object]:
    """Fetch the issue or pull request, or fail closed: no item, no claim."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{item_number}"
    )
    if not response.is_success:
        print(
            f"Error: could not fetch #{item_number}"
            f" ({response.status_code}) - do not work an unverified item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    issue: object = response.json()
    if not isinstance(issue, dict):
        print("Error: the item payload is unexpected - do not work it.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return issue


def claim_is_allowed(
    *,
    issue: dict[str, object],
    item_number: int,
    expected_kind: str,
) -> None:
    """Refuse a kind mismatch or an already-claimed item.

    ``expected_kind`` is ``"issue"`` or ``"pull_request"``: the payload
    must be exactly that kind (the issues endpoint serves both), so an
    issue number passed to ``--pr`` - or the reverse - fails closed.
    """
    is_pull_request = "pull_request" in issue
    if expected_kind == "pull_request" and not is_pull_request:
        print(f"Error: #{item_number} is an issue, not a pull request.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    if expected_kind == "issue" and is_pull_request:
        print(f"Error: #{item_number} is a pull request, not an issue.", file=sys.stderr)
        raise SystemExit(EXIT_CODE_CRITERIA_UNMET)
    labels = issue.get("labels", [])
    if isinstance(labels, list):
        label_names = {entry.get("name", "") for entry in labels if isinstance(entry, dict)}
        if CLAIM_LABEL_NAME in label_names:
            print(
                f"Error: #{item_number} is already claimed"
                f" ({CLAIM_LABEL_NAME}) - do not work it; the human decides"
                " the retry.",
                file=sys.stderr,
            )
            raise SystemExit(EXIT_CODE_CRITERIA_UNMET)


def ensure_claim_label(*, http_client: httpx.Client, repository_slug: str) -> None:
    """Create the claim label once; a 403 means the human creates it."""
    response = http_client.get(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/labels/{CLAIM_LABEL_NAME}"
    )
    if response.is_success:
        return
    creation_response = http_client.post(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/labels",
        json={
            "name": CLAIM_LABEL_NAME,
            "color": CLAIM_LABEL_COLOR,
            "description": CLAIM_LABEL_DESCRIPTION,
        },
    )
    if creation_response.is_success:
        return
    print(
        f"Error: could not create the {CLAIM_LABEL_NAME} label"
        f" ({creation_response.status_code}) - create it by hand or grant"
        " issues write on the token, then relaunch the night.",
        file=sys.stderr,
    )
    raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def add_claim_label(
    *,
    http_client: httpx.Client,
    repository_slug: str,
    issue_number: int,
) -> None:
    """Add the claim label; a failure means no claim, no work."""
    response = http_client.post(
        f"{GITHUB_API_BASE_URL}/repos/{repository_slug}/issues/{issue_number}/labels",
        json={"labels": [CLAIM_LABEL_NAME]},
    )
    if not response.is_success:
        print(
            f"Error: could not claim #{issue_number}"
            f" ({response.status_code}) - do not work an unclaimed item.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the claim subcommand: exactly one item, issue or pull request."""
    subparser = subparsers.add_parser(
        "claim",
        help="Claim one issue or pull request for an unattended session (ADR-0007).",
        description="Claim one issue or pull request for an unattended session (ADR-0007).",
    )
    item_group = subparser.add_mutually_exclusive_group(required=True)
    item_group.add_argument(
        "--issue",
        type=int,
        help="Issue number to claim (the session's first act).",
    )
    item_group.add_argument(
        "--pr",
        type=int,
        help="Pull request number to claim (recovery path, ADR-0007).",
    )
    subparser.set_defaults(runner=run)


def run(*, arguments: argparse.Namespace) -> int:
    """Entry point: item number in, claimed item out."""
    github_token = load_github_token()
    repository_slug = resolve_repository_slug()
    if arguments.issue is not None:
        item_number, expected_kind, item_word = arguments.issue, "issue", "Issue"
    else:
        item_number, expected_kind, item_word = arguments.pr, "pull_request", "Pull request"
    with open_github_client(token=github_token) as http_client:
        issue = fetch_issue(
            http_client=http_client,
            repository_slug=repository_slug,
            item_number=item_number,
        )
        claim_is_allowed(
            issue=issue,
            item_number=item_number,
            expected_kind=expected_kind,
        )
        ensure_claim_label(http_client=http_client, repository_slug=repository_slug)
        add_claim_label(
            http_client=http_client,
            repository_slug=repository_slug,
            issue_number=item_number,
        )
    print(
        f"{item_word} #{item_number} claimed ({CLAIM_LABEL_NAME}):"
        " it is yours, and no other session will pick it."
    )
    return EXIT_CODE_CLAIMED
