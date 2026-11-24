"""Study driver: run the pilote study session (spec §20, issue #41).

``make study-p`` executes one full elicitation session on the frozen v1
materials (``src/petrichor/study/pilote_v1``) with the real verifypn compose
engine, and archives the record - rapport, contract exports, journal, run
archive - under the output directory (default ``.petrichor/study-p``,
runtime state, not committed). The frozen materials are hash-verified
before anything runs; a drifted material aborts the session.

The engine wiring lives here, in ``scripts/``, on purpose: the verdict lock
(spec §19.4) forbids any module of the product package outside
``petrichor.engines`` from importing that package, so the composition root
that builds a ``CheckRunner`` must live outside ``src/petrichor``.

The product package lives in ``src/`` (ADR-0100) and is not installed as a
distribution, so this driver bootstraps ``src`` onto ``sys.path`` and loads
the product modules dynamically: the CI type-check of ``scripts/`` runs
without ``src`` on its path, and static imports would not resolve there.
"""

import importlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from scripts._shared import EXIT_CODE_CRITERIA_UNMET, EXIT_CODE_SUCCESS

if TYPE_CHECKING:
    import argparse

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_OUTPUT_DIR = REPO_ROOT / ".petrichor" / "study-p"
DEFAULT_CHECK_TIMEOUT_SECONDS = 300
DEFAULT_RECORD_DIR = REPO_ROOT / "docs" / "studies" / "pilote" / "v1" / "execution"


def _run_pilote_session(*, output_dir: Path, check_timeout: int) -> int:
    """Load the product and run the pilote session; return the exit code.

    The product modules are loaded dynamically (see the module docstring):
    ``src/`` is bootstrapped onto ``sys.path`` above, and the CI type-check
    of ``scripts/`` does not have ``src`` on its path.
    """
    api_runner = importlib.import_module("petrichor.engines.api_runner")
    archive = importlib.import_module("petrichor.engines.archive")
    contract = importlib.import_module("petrichor.engines.contract")
    mini_engine = importlib.import_module("petrichor.engines.mini_engine")
    runner = importlib.import_module("petrichor.study.runner")

    engine_runner = api_runner.VerifypnCheckRunner(
        archive=archive.RunArchive(directory=output_dir / "runs")
    )
    report = runner.run_pilote(
        output_dir=output_dir,
        runner=engine_runner,
        static_resolver=mini_engine.MiniEngineResolver(),
        engine_name=contract.VERIFYPN_ENGINE_NAME,
        engine_version=contract.VERIFYPN_ENGINE_VERSION,
        check_timeout_seconds=check_timeout,
    )
    print(f"pilote record written to {output_dir / runner.RAPPORT_FILE_NAME}")
    print(
        f"session {report.session_id}: {report.questions_total} questions,"
        f" {len(report.runs)} runs, consumed {report.budget.consumed_seconds:.3f}s"
        f" of {report.budget.cap_seconds:.0f}s cap (within_cap={report.budget.within_cap})"
    )
    return EXIT_CODE_SUCCESS if report.budget.within_cap else EXIT_CODE_CRITERIA_UNMET


def run_study_p(*, arguments: argparse.Namespace) -> int:
    """Entry point: output dir in, execution record written, exit code out."""
    return _run_pilote_session(
        output_dir=Path(arguments.output_dir), check_timeout=arguments.check_timeout
    )


def _audit_pilote_record(*, record_dir: Path, output: Path, audited_dir: Path) -> int:
    """Audit one execution record; write audit.json; return the exit code."""
    audit = importlib.import_module("petrichor.study.audit")

    report = audit.audit_execution(record_dir=record_dir, audited_dir=audited_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"audit written to {output} (ok={report.ok})")
    for check in report.checks:
        print(f"  {'PASS' if check.ok else 'FAIL'} {check.name}: {check.detail}")
    return EXIT_CODE_SUCCESS if report.ok else EXIT_CODE_CRITERIA_UNMET


def run_study_p_audit(*, arguments: argparse.Namespace) -> int:
    """Entry point: record dir in, audit.json written, exit code out."""
    record_dir = Path(arguments.record_dir)
    # Repo-relative when possible: the committed audit.json must stay
    # machine-independent (no absolute local paths).
    try:
        audited_dir: Path = record_dir.relative_to(REPO_ROOT)
    except ValueError:
        audited_dir = record_dir
    output = (
        Path(arguments.output) if arguments.output else record_dir.parent / "audit" / "audit.json"
    )
    return _audit_pilote_record(record_dir=record_dir, output=output, audited_dir=audited_dir)


def register_study_p(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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


def register_study_p_audit(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the study-p-audit subcommand."""
    subparser = subparsers.add_parser(
        "study-p-audit",
        help="Audit a pilote execution record (spec §20.10, issue #42).",
        description="Audit a pilote execution record: no verdict without an archived run.",
    )
    subparser.add_argument(
        "--record-dir",
        default=str(DEFAULT_RECORD_DIR),
        help="Execution record to audit (default: docs/studies/pilote/v1/execution).",
    )
    subparser.add_argument(
        "--output",
        default=None,
        help="Where to write audit.json (default: <record-dir>/../audit/audit.json).",
    )
    subparser.set_defaults(runner=run_study_p_audit)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register both study-p subcommands."""
    register_study_p(subparsers)
    register_study_p_audit(subparsers)
