"""Audit of a pilote execution record (spec §20.10, issue #42).

The audit crosses the session journal with the run archive and the exported
contract: **no verdict value without a corresponding archived run** ("zéro
verdict inventé"). Concretely, on one execution record directory:

- every proof of every contract dossier points to an archived run whose
  verdict matches the proof's verdict;
- every archived run is referenced by at least one proof (no orphan run);
- every ``run_added`` journal event references an archived run;
- the report's budget is the recomputed sum of the archived runs'
  ``measured_seconds`` (cap, consumed, run count);
- the report's material hashes match the frozen v1 files;
- the report's contract/markdown/journal hashes match the exported files.

The audit never mutates the record: it returns an ``AuditReport`` (and the
CLI writes it as ``audit.json`` next to the record).
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.study import pilote

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

RUNS_FILE_NAME = "runs/runs.jsonl"
CONTRACT_FILE_NAME = "contrat.json"
MARKDOWN_FILE_NAME = "contrat.md"
JOURNAL_FILE_NAME = "journal.json"
RAPPORT_FILE_NAME = "rapport.json"

COST_MATCH_TOLERANCE = 1e-9
"""Proof/archive cost join tolerance (the cost is copied verbatim, exact match expected)."""

BUDGET_RELATIVE_TOLERANCE = 0.05
"""The API measures the wall-clock around the runner call, the driver inside it:
same quantity, two clocks - 5% relative tolerance."""

BUDGET_ABSOLUTE_FLOOR_SECONDS = 0.05


class AuditError(Exception):
    """The execution record is missing or unreadable."""


def _as_dict(*, value: object, what: str) -> dict[str, object]:
    """A JSON object, narrowed explicitly."""
    if not isinstance(value, dict):
        raise AuditError(f"expected an object for {what}, got {value!r}")
    return value


def _as_float(*, value: object, what: str) -> float:
    """A JSON number, narrowed explicitly."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuditError(f"expected a number for {what}, got {value!r}")
    return float(value)


class AuditCheck(BaseModel):
    """One audit check: name, outcome, and the evidence detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    ok: bool
    detail: str = Field(min_length=1)


class AuditReport(BaseModel):
    """The audit outcome of one execution record (spec §20.10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_dir: str
    audited_at: datetime
    ok: bool
    checks: tuple[AuditCheck, ...]


def _read_json(*, path: Path) -> object:
    if not path.is_file():
        raise AuditError(f"missing record file {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise AuditError(f"unreadable record file {path}: {error}") from error


def _read_runs(*, record_dir: Path) -> list[dict[str, object]]:
    path = record_dir / RUNS_FILE_NAME
    if not path.is_file():
        raise AuditError(f"missing run archive {path}")
    runs: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            runs.append(json.loads(line))
    return runs


def _sha256_file(*, path: Path) -> str:
    return f"{pilote.HASH_ALGORITHM}:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _archive_key(*, run: dict[str, object]) -> tuple[object, object, float] | None:
    """The join key of one archived run: (verdict, engine version, tool cost).

    The API run ids and the driver archive ids differ by design; the cost is
    copied verbatim from the archived output into the contract proof, so the
    triple identifies the run on both sides.
    """
    output = run.get("output")
    cost = output.get("cost") if isinstance(output, dict) else None
    if not isinstance(output, dict) or not isinstance(cost, dict):
        return None
    return (
        output.get("verdict"),
        run.get("engine_version"),
        _as_float(value=cost.get("tool_seconds"), what="tool_seconds"),
    )


def audit_execution(*, record_dir: Path) -> AuditReport:
    """Audit one execution record; return the report (never mutates the record)."""
    rapport = _read_json(path=record_dir / RAPPORT_FILE_NAME)
    if not isinstance(rapport, dict):
        raise AuditError(f"{RAPPORT_FILE_NAME} is not an object")
    contract = _read_json(path=record_dir / CONTRACT_FILE_NAME)
    journal = _read_json(path=record_dir / JOURNAL_FILE_NAME)
    runs = _read_runs(record_dir=record_dir)
    if not isinstance(contract, dict) or not isinstance(journal, list):
        raise AuditError("contract must be an object and journal an array")

    checks: list[AuditCheck] = []
    available: list[tuple[dict[str, object], tuple[object, object, float] | None]] = [
        (run, _archive_key(run=run)) for run in runs
    ]

    # 1. Every contract proof is backed by an archived run with the same verdict.
    missing: list[str] = []
    dossiers = contract.get("proof_dossiers", [])
    proof_count = 0
    for dossier in dossiers:
        for proof in dossier.get("proofs", []):
            proof_count += 1
            proof_key = (
                proof.get("verdict"),
                proof.get("tool_version"),
                _as_float(value=proof.get("cost_tool_seconds"), what="cost_tool_seconds"),
            )
            match = next(
                (entry for entry in available if entry[1] is not None and entry[1] == proof_key),
                None,
            )
            if match is None:
                missing.append(str(proof.get("run_id")))
                continue
            available.remove(match)
    checks.append(
        AuditCheck(
            name="no_verdict_without_run",
            ok=not missing,
            detail=(
                f"{proof_count} proofs, each backed by an archived run (verdict+version+cost)"
                if not missing
                else f"proofs without archived run {missing}"
            ),
        )
    )

    # 2. No orphan archived run: every archived run backs a contract proof.
    orphans = sorted(str(run["run_id"]) for run, _key in available)
    checks.append(
        AuditCheck(
            name="no_orphan_run",
            ok=not orphans,
            detail=(
                f"{len(runs)} archived runs, all backing a contract proof"
                if not orphans
                else f"orphan runs {orphans}"
            ),
        )
    )

    # 3. Journal run_added events reference runs recorded in the report.
    report_run_ids = {str(run["run_id"]) for run in rapport.get("runs", [])}
    journal_runs = [
        str(entry.get("detail", {}).get("run_id"))
        for entry in journal
        if isinstance(entry, dict) and entry.get("event") == "run_added"
    ]
    unknown_journal_runs = sorted({r for r in journal_runs if r not in report_run_ids})
    checks.append(
        AuditCheck(
            name="journal_runs_exist",
            ok=not unknown_journal_runs,
            detail=(
                f"{len(journal_runs)} run_added journal events, all in the report"
                if not unknown_journal_runs
                else f"unknown run ids {unknown_journal_runs}"
            ),
        )
    )

    # 4. The report's budget is the recomputed sum of the archived runs.
    consumed = sum(
        _as_float(value=run["measured_seconds"], what="measured_seconds") for run in runs
    )
    budget = _as_dict(value=rapport.get("budget"), what="budget")
    reported_consumed = _as_float(value=budget.get("consumed_seconds"), what="consumed_seconds")
    tolerance = max(BUDGET_ABSOLUTE_FLOOR_SECONDS, BUDGET_RELATIVE_TOLERANCE * consumed)
    budget_ok = (
        abs(reported_consumed - consumed) <= tolerance
        and _as_float(value=budget.get("run_count"), what="run_count") == len(runs)
        and _as_float(value=budget.get("cap_seconds"), what="cap_seconds")
        == pilote.SESSION_BUDGET_SECONDS
    )
    checks.append(
        AuditCheck(
            name="budget_matches_archive",
            ok=budget_ok,
            detail=(
                f"consumed {consumed:.3f}s recomputed from {len(runs)} runs"
                if budget_ok
                else f"report consumed {reported_consumed:.3f}s != archive {consumed:.3f}s"
            ),
        )
    )

    # 5. The report's material hashes match the frozen v1 files.
    materials = rapport.get("materials", {})
    material_mismatches = [
        name
        for name, pinned in materials.items()
        if _sha256_file(path=pilote.MATERIALS_DIR / name) != pinned
    ]
    checks.append(
        AuditCheck(
            name="frozen_materials_intact",
            ok=not material_mismatches,
            detail=(
                f"{len(materials)} frozen materials hash-verified"
                if not material_mismatches
                else f"drifted materials {material_mismatches}"
            ),
        )
    )

    # 6. The report's export hashes match the exported files.
    export_files = {
        "contract_sha256": CONTRACT_FILE_NAME,
        "markdown_sha256": MARKDOWN_FILE_NAME,
        "journal_sha256": JOURNAL_FILE_NAME,
    }
    export_mismatches = [
        name
        for name, file_name in export_files.items()
        if rapport.get(name) != _sha256_file(path=record_dir / file_name)
    ]
    checks.append(
        AuditCheck(
            name="exports_hash_match",
            ok=not export_mismatches,
            detail=(
                "contract, markdown and journal hashes match the report"
                if not export_mismatches
                else f"hash mismatches {export_mismatches}"
            ),
        )
    )

    report = AuditReport(
        record_dir=str(record_dir),
        audited_at=datetime.now(UTC),
        ok=all(check.ok for check in checks),
        checks=tuple(checks),
    )
    logger.info(
        "pilote_audit_done",
        record_dir=str(record_dir),
        ok=report.ok,
        checks=len(checks),
        failed=[check.name for check in checks if not check.ok],
    )
    return report
