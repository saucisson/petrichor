"""Assemble the deterministic review bundle for the reviewer agent (ADR-0016).

The bundle is the reviewer's ENTIRE context: role contract, architecture
rules, review checklist, the relevant ADRs, the pull request specification
and the diff under review. Nothing else enters the prompt - context
isolation is enforced by construction, not by prompt discipline.

Stdlib only, so it runs in the bareest CI image.

Usage::

    python scripts/build_review_bundle.py --base-ref origin/daily
    python scripts/build_review_bundle.py --diff-file diff.patch
"""

import argparse
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
ROLE_DOCUMENT_PATH = REPOSITORY_ROOT / "docs/agents/reviewer.md"
ARCHITECTURE_DOCUMENT_PATH = REPOSITORY_ROOT / "docs/architecture.md"
CHECKLIST_DOCUMENT_PATH = REPOSITORY_ROOT / "docs/review-checklist.md"
DECISIONS_DIRECTORY_PATH = REPOSITORY_ROOT / "docs/decisions"
SECTION_SEPARATOR = "\n\n---\n\n"
NO_DESCRIPTION_TEXT = "No pull request description was provided."
EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1


def run_git_diff(*, base_ref: str) -> str:
    """Return the diff between the merge base of base_ref and HEAD."""
    completed_process = subprocess.run(
        ["git", "diff", "--no-color", f"{base_ref}...HEAD"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed_process.stdout


def read_text_file(*, path: Path) -> str:
    """Read one text file, failing loudly on a missing bundle component."""
    if not path.is_file():
        print(f"Error: bundle component missing: {path}", file=sys.stderr)
        raise SystemExit(EXIT_CODE_FAILURE)
    return path.read_text(encoding="utf-8")


def collect_decisions() -> list[tuple[str, str]]:
    """Return every ADR as (title, content), in numeric order."""
    decision_paths = sorted(DECISIONS_DIRECTORY_PATH.glob("adr-*.md"))
    decisions: list[tuple[str, str]] = []
    for decision_path in decision_paths:
        decision_title = decision_path.stem.replace("-", " ").capitalize()
        decisions.append((decision_title, decision_path.read_text(encoding="utf-8")))
    return decisions


def render_bundle(
    *,
    diff_text: str,
    description_text: str,
    decisions: Sequence[tuple[str, str]],
) -> str:
    """Assemble the bundle sections in a fixed, deterministic order."""
    sections: list[str] = []
    sections.append("## ROLE CONTRACT\n\n" + read_text_file(path=ROLE_DOCUMENT_PATH))
    sections.append(
        "## ARCHITECTURE RULES\n\n" + read_text_file(path=ARCHITECTURE_DOCUMENT_PATH)
    )
    sections.append(
        "## REVIEW CHECKLIST\n\n" + read_text_file(path=CHECKLIST_DOCUMENT_PATH)
    )
    for decision_title, decision_content in decisions:
        sections.append(f"## DECISION - {decision_title}\n\n{decision_content}")
    sections.append(f"## PULL REQUEST SPECIFICATION\n\n{description_text}")
    sections.append(f"## DIFF UNDER REVIEW\n\n{diff_text}")
    return SECTION_SEPARATOR.join(sections)


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the script contract: one diff source, optional description."""
    argument_parser = argparse.ArgumentParser(
        prog="build_review_bundle",
        description="Assemble the deterministic review bundle (ADR-0016).",
    )
    diff_source = argument_parser.add_mutually_exclusive_group(required=True)
    diff_source.add_argument(
        "--diff-file",
        help="Path to a file containing the diff under review.",
    )
    diff_source.add_argument(
        "--base-ref",
        help="Git ref to diff against (merge base with HEAD), e.g. origin/daily.",
    )
    argument_parser.add_argument(
        "--description-file",
        default=None,
        help="Path to the pull request description (the specification).",
    )
    argument_parser.add_argument(
        "--output",
        default=None,
        help="Path to write the bundle to; defaults to stdout.",
    )
    return argument_parser.parse_args(command_line_arguments)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Entry point: gather every component, print or write the bundle."""
    arguments = parse_arguments(command_line_arguments=command_line_arguments)
    if arguments.diff_file is not None:
        diff_text = read_text_file(path=Path(arguments.diff_file))
    else:
        diff_text = run_git_diff(base_ref=arguments.base_ref)
    if arguments.description_file is not None:
        description_text = read_text_file(path=Path(arguments.description_file))
    else:
        description_text = NO_DESCRIPTION_TEXT
    bundle_text = render_bundle(
        diff_text=diff_text,
        description_text=description_text,
        decisions=collect_decisions(),
    )
    if arguments.output is not None:
        Path(arguments.output).write_text(bundle_text, encoding="utf-8")
    else:
        print(bundle_text)
    return EXIT_CODE_SUCCESS


if __name__ == "__main__":
    sys.exit(main(command_line_arguments=sys.argv[1:]))
