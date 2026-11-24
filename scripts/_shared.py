"""Shared plumbing for every driver command (ADR-0001).

Each module of this package is one driver domain; the plumbing every
driver repeats - the ``.env`` loader, the GitHub token, the repository
slug, the git runners and the protocol exit codes - lives here once,
so a driver module carries only its own protocol.

Stdlib only, on purpose: the git-only subcommands (``day-report``,
``sync-daily``) import this module and must run without ``httpx``
(ADR-0001); the GitHub API helpers live in ``_github`` instead.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

GITHUB_API_BASE_URL = "https://api.github.com"
GITHUB_TOKEN_VARIABLE = "GITHUB_TOKEN"  # noqa: S105 - an env var NAME, never a secret
ENVIRONMENT_FILE_NAME = ".env"
CLAIM_LABEL_NAME = "night-claimed"

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

EXIT_CODE_SUCCESS = 0
EXIT_CODE_CRITERIA_UNMET = 1
EXIT_CODE_PROTOCOL_FAILURE = 2


def load_environment_file() -> None:
    """Read KEY=VALUE pairs from .env into the environment if unset.

    The notify tool already loads .env through its settings; these
    commands read it so ``GITHUB_TOKEN`` can live in one place.
    Existing environment variables always win.
    """
    environment_path = REPOSITORY_ROOT / ENVIRONMENT_FILE_NAME
    if not environment_path.is_file():
        return
    for raw_line in environment_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def load_github_token() -> str:
    """Read the GitHub token; absent means protocol failure."""
    github_token = os.environ.get(GITHUB_TOKEN_VARIABLE, "").strip()
    if not github_token:
        print(
            f"Error: {GITHUB_TOKEN_VARIABLE} is not set - cannot reach the"
            " GitHub API (fine-grained PAT: repository, issues and pull"
            " requests read/write, as the driver at hand requires).",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return github_token


def resolve_repository_slug() -> str:
    """Return "owner/repository" from the origin remote, or fail closed."""
    remote_url = try_git(arguments=["remote", "get-url", "origin"]).stdout.strip()
    marker = "github.com"
    separator_index = remote_url.find(marker)
    if separator_index == -1:
        print(
            f"Error: the origin remote is not on GitHub: {remote_url}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    slug = remote_url[separator_index + len(marker) :]
    slug = slug.removeprefix(":").removeprefix("/").removesuffix(".git").strip("/")
    owner, separator, repository = slug.partition("/")
    if not owner or not separator or not repository.strip("/"):
        print(
            f"Error: cannot parse owner/repository from remote: {remote_url}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return f"{owner}/{repository.strip('/')}"


def try_git(*, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run one git command in the repository; the caller checks the exit."""
    return subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def run_git(*, arguments: Sequence[str]) -> str:
    """Run git in the repository, capturing stdout; fail closed."""
    completed_process = try_git(arguments=arguments)
    if completed_process.returncode != EXIT_CODE_SUCCESS:
        print(
            f"Error: git {' '.join(arguments)} failed: {completed_process.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_CODE_PROTOCOL_FAILURE)
    return completed_process.stdout


def github_headers(*, token: str) -> dict[str, str]:
    """Build the GitHub API headers shared by every driver."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
    }
