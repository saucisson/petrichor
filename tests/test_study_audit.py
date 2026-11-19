"""Tests of the pilote execution audit (spec §20.10, issue #42).

The audit crosses the session journal with the run archive and the exported
contract: no verdict value without a corresponding archived run. The tests
build a real record with ``run_pilote`` (fake runner), audit it clean, then
tamper each artifact and assert the audit catches it.
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog.testing

from petrichor.checks import CheckCompleted, CheckQuestion, ToolVersion
from petrichor.engines.archive import RunArchive
from petrichor.engines.contract import (
    Cost,
    InputHash,
    NormalizedOutput,
    RunRecord,
    new_run_id,
)
from petrichor.engines.mini_engine import MiniEngineResolver
from petrichor.study import pilote
from petrichor.study.audit import AuditError, audit_execution
from petrichor.study.runner import run_pilote

FAKE_TOOL_VERSION = ToolVersion("0.0.0-fake")


class ArchivingFakeRunner:
    """A fake runner that archives one RunRecord per run, like the real driver."""

    tool_version = FAKE_TOOL_VERSION

    def __init__(self, *, archive: RunArchive) -> None:
        self._archive = archive

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Serve a fixed done output and archive the run record."""
        started = time.monotonic()
        time.sleep(0.05)
        record = RunRecord(
            run_id=new_run_id(),
            engine="fake",
            engine_version=str(FAKE_TOOL_VERSION),
            command=["fake-tool"],
            timeout_seconds=timeout_seconds,
            input_hash=InputHash("sha256:" + "0" * 64),
            output=NormalizedOutput(
                verdict="satisfied",
                witness=None,
                cost=Cost(tool_seconds=0.1, states_explored=4),
                technique="fake",
            ),
            measured_seconds=time.monotonic() - started,
            archived_at=datetime.now(UTC).isoformat(),
        )
        self._archive.append(record=record)
        return CheckCompleted(output=record.output.model_dump(mode="json"), command=record.command)


def _record(tmp_path: Path) -> Path:
    record_dir = tmp_path / "execution"
    run_pilote(
        output_dir=record_dir,
        runner=ArchivingFakeRunner(archive=RunArchive(directory=record_dir / "runs")),
        static_resolver=MiniEngineResolver(),
        engine_name="fake",
        engine_version=str(FAKE_TOOL_VERSION),
    )
    return record_dir


class TestAuditCleanRecord:
    def test_a_clean_record_passes_every_check(self, tmp_path: Path) -> None:
        with structlog.testing.capture_logs() as logs:
            report = audit_execution(record_dir=_record(tmp_path))
        assert report.ok
        assert {check.name for check in report.checks} == {
            "no_verdict_without_run",
            "no_orphan_run",
            "journal_runs_exist",
            "budget_matches_archive",
            "frozen_materials_intact",
            "exports_hash_match",
        }
        assert all(check.ok for check in report.checks)
        events = [event for event in logs if event["event"] == "pilote_audit_done"]
        assert len(events) == 1
        assert events[0]["ok"] is True

    def test_missing_record_is_a_typed_error(self, tmp_path: Path) -> None:
        with pytest.raises(AuditError, match="missing record file"):
            audit_execution(record_dir=tmp_path / "empty")


class TestAuditDetectsTampering:
    def test_a_verdict_without_run_is_caught(self, tmp_path: Path) -> None:
        record_dir = _record(tmp_path)
        contract_path = record_dir / "contrat.json"
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        contract["proof_dossiers"][0]["proofs"][0]["verdict"] = "violated"
        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        report = audit_execution(record_dir=record_dir)
        assert not report.ok
        failed = next(c for c in report.checks if c.name == "no_verdict_without_run")
        assert not failed.ok
        assert "without archived run" in failed.detail

    def test_a_run_removed_from_the_archive_is_caught(self, tmp_path: Path) -> None:
        record_dir = _record(tmp_path)
        runs_path = record_dir / "runs" / "runs.jsonl"
        lines = runs_path.read_text(encoding="utf-8").splitlines()
        runs_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        report = audit_execution(record_dir=record_dir)
        assert not report.ok
        assert any(c.name == "no_verdict_without_run" and not c.ok for c in report.checks)

    def test_an_orphan_run_is_caught(self, tmp_path: Path) -> None:
        record_dir = _record(tmp_path)
        runs_path = record_dir / "runs" / "runs.jsonl"
        extra = json.loads(runs_path.read_text(encoding="utf-8").splitlines()[0])
        extra["run_id"] = "run_orphan"
        with runs_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(extra) + "\n")
        report = audit_execution(record_dir=record_dir)
        assert not report.ok
        assert any(c.name == "no_orphan_run" and not c.ok for c in report.checks)

    def test_a_tampered_budget_is_caught(self, tmp_path: Path) -> None:
        record_dir = _record(tmp_path)
        rapport_path = record_dir / "rapport.json"
        rapport = json.loads(rapport_path.read_text(encoding="utf-8"))
        rapport["budget"]["consumed_seconds"] = 0.0
        rapport_path.write_text(json.dumps(rapport), encoding="utf-8")
        report = audit_execution(record_dir=record_dir)
        assert not report.ok
        assert any(c.name == "budget_matches_archive" and not c.ok for c in report.checks)

    def test_a_drifted_frozen_material_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        record_dir = _record(tmp_path)
        drifted = tmp_path / "materials"
        drifted.mkdir()
        for name in pilote.load_config().materials:
            (drifted / name).write_bytes((pilote.MATERIALS_DIR / name).read_bytes())
        (drifted / pilote.MODEL_FILE_NAME).write_text("tampered", encoding="utf-8")
        monkeypatch.setattr(pilote, "MATERIALS_DIR", drifted)
        report = audit_execution(record_dir=record_dir)
        assert not report.ok
        assert any(c.name == "frozen_materials_intact" and not c.ok for c in report.checks)


class TestCommittedPiloteRecord:
    def test_the_committed_execution_record_passes_the_audit(self) -> None:
        """The committed pilote record (docs/studies/pilote/v1/execution) is audited in CI."""
        record_dir = Path(__file__).parents[1] / "docs" / "studies" / "pilote" / "v1" / "execution"
        report = audit_execution(record_dir=record_dir)
        assert report.ok, [c.name for c in report.checks if not c.ok]
