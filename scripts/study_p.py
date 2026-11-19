"""Study driver: run the pilote study session (spec §20, issue #41).

``make study-p`` executes one full elicitation session on the frozen v1
materials (``src/petrichor/study/pilote_v1``) with the real verifypn compose
engine, and archives the record - rapport, contract exports, journal, run
archive - under the output directory (default ``.petrichor/study-p``,
runtime state, not committed). The frozen materials are hash-verified
before anything runs; a drifted material aborts the session.
"""

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from scripts._shared import EXIT_CODE_CRITERIA_UNMET, EXIT_CODE_SUCCESS

if TYPE_CHECKING:
    from collections.abc import Sequence


class Runner(Protocol):
    """A subcommand entry point: keyword-only arguments in, exit code out."""

    def __call__(self, *, arguments: argparse.Namespace) -> int:
        """Run the subcommand."""
        ...


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
# The product package lives in src/ (ADR-0100) and is not installed as a
# distribution: make it importable for `python -m scripts study-p`.
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_OUTPUT_DIR = REPO_ROOT / ".petrichor" / "study-p"
DEFAULT_CHECK_TIMEOUT_SECONDS = 300


def run_study_p(*, arguments: argparse.Namespace) -> int:
    """Entry point: output dir in, execution record written, exit code out."""
    # Deferred imports, on purpose: the product package is only needed when
    # the session actually runs, and the sys.path bootstrap above must run first.
    from petrichor.engines.api_runner import VerifypnCheckRunner  # noqa: PLC0415
    from petrichor.engines.archive import RunArchive  # noqa: PLC0415
    from petrichor.engines.contract import (  # noqa: PLC0415
        VERIFYPN_ENGINE_NAME,
        VERIFYPN_ENGINE_VERSION,
    )
    from petrichor.engines.mini_engine import MiniEngineResolver  # noqa: PLC0415
    from petrichor.study.runner import RAPPORT_FILE_NAME, run_pilote  # noqa: PLC0415

    output_dir = Path(arguments.output_dir)
    runner = VerifypnCheckRunner(archive=RunArchive(directory=output_dir / "runs"))
    report = run_pilote(
        output_dir=output_dir,
        runner=runner,
        static_resolver=MiniEngineResolver(),
        engine_name=VERIFYPN_ENGINE_NAME,
        engine_version=VERIFYPN_ENGINE_VERSION,
        check_timeout_seconds=arguments.check_timeout,
    )
    print(f"pilote record written to {output_dir / RAPPORT_FILE_NAME}")
    print(
        f"session {report.session_id}: {report.questions_total} questions,"
        f" {len(report.runs)} runs, consumed {report.budget.consumed_seconds:.3f}s"
        f" of {report.budget.cap_seconds:.0f}s cap (within_cap={report.budget.within_cap})"
    )
    return EXIT_CODE_SUCCESS if report.budget.within_cap else EXIT_CODE_CRITERIA_UNMET


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the study-p subcommand."""
    subparser = subparsers.add_parser(
        "study-p",
        help="Run the pilote study session on the frozen v1 materials (spec §20, issue #41).",
        description=(
            "Run the pilote study session on the frozen v1 materials (spec §20, issue #41)."
        ),
    )
    subparser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory receiving the archived record (default: .petrichor/study-p).",
    )
    subparser.add_argument(
        "--check-timeout",
        type=int,
        default=DEFAULT_CHECK_TIMEOUT_SECONDS,
        help="Per-run check timeout in seconds (default: 300).",
    )
    subparser.set_defaults(runner=run_study_p)


def main(*, command_line_arguments: Sequence[str]) -> int:
    """Standalone entry point (the dispatcher in __main__ is the usual path)."""
    parser = argparse.ArgumentParser(prog="scripts study-p")
    register(parser.add_subparsers(dest="command", required=True))
    arguments = parser.parse_args(command_line_arguments)
    # ADR-0006 escape: argparse stores the runner default as Any on the Namespace.
    runner: Runner = arguments.runner
    return runner(arguments=arguments)
