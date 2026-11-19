"""Tests of the pilote study runner (spec §20, issue #41).

``run_pilote`` executes one full elicitation session on the frozen v1
materials (DrinkVendingMachine-PT-02): import, session with the 3600 s cap,
profile proposed and arbitrated, every question arbitrated and checked, the
converging pairs fused, the contract exported, and the cumulative cost
measured against the cap. Everything is archived under the output directory
with content hashes. The engine here is a fake runner; the real verifypn
compose service is wired by the ``study-p`` CLI.
"""

import hashlib
import json
from typing import TYPE_CHECKING

import structlog.testing

from petrichor.checks import CheckCompleted, CheckQuestion, ToolVersion
from petrichor.engines.mini_engine import MiniEngineResolver
from petrichor.study import pilote
from petrichor.study.runner import (
    CONTRACT_JSON_FILE_NAME,
    CONTRACT_MD_FILE_NAME,
    JOURNAL_FILE_NAME,
    RAPPORT_FILE_NAME,
    PiloteReport,
    run_pilote,
)

if TYPE_CHECKING:
    from pathlib import Path

FAKE_TOOL_VERSION = ToolVersion("0.0.0-fake")
DONE_OUTPUT = {
    "verdict": "satisfied",
    "witness": None,
    "cost": {"tool_seconds": 0.1, "states_explored": 4},
    "technique": "fake",
}


class FakeRunner:
    """A runner that completes immediately with a fixed output."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Serve the fixed done output."""
        return CheckCompleted(output=DONE_OUTPUT, command=["fake-tool"])


def _run(tmp_path: Path) -> PiloteReport:
    return run_pilote(
        output_dir=tmp_path / "execution",
        runner=FakeRunner(),
        static_resolver=MiniEngineResolver(),
        engine_name="fake",
        engine_version=str(FAKE_TOOL_VERSION),
    )


class TestPiloteRunner:
    def test_full_session_runs_and_measures_the_budget_against_the_cap(
        self, tmp_path: Path
    ) -> None:
        with structlog.testing.capture_logs() as logs:
            report = _run(tmp_path)
        assert report.study_id == "pilote"
        assert report.study_version == "v1"
        assert (
            report.model_content_hash
            == "sha256:10c2c09236fe45d21e203336943e5f9be4d8d2ad648836c6918bbfc3e868946c"
        )
        assert report.engine == "fake"
        assert report.questions_total > 0
        assert report.questions_arbitrated == report.questions_total
        assert len(report.runs) == report.questions_total
        assert all(run.status == "done" for run in report.runs)
        assert all(run.verdict == "satisfied" for run in report.runs)
        assert report.budget.cap_seconds == 3600.0
        assert report.budget.consumed_seconds > 0.0
        assert report.budget.within_cap
        assert report.budget.remaining_seconds == 3600.0 - report.budget.consumed_seconds
        assert report.budget.run_count == len(report.runs)
        events = [event for event in logs if event["event"] == "pilote_session_executed"]
        assert len(events) == 1
        assert events[0]["within_cap"] is True
        assert events[0]["cap_seconds"] == 3600.0

    def test_everything_is_archived_with_hashes(self, tmp_path: Path) -> None:
        report = _run(tmp_path)
        output_dir = tmp_path / "execution"
        rapport = json.loads((output_dir / RAPPORT_FILE_NAME).read_text(encoding="utf-8"))
        assert rapport["session_id"] == report.session_id
        contract_bytes = (output_dir / CONTRACT_JSON_FILE_NAME).read_bytes()
        markdown_bytes = (output_dir / CONTRACT_MD_FILE_NAME).read_bytes()
        journal_bytes = (output_dir / JOURNAL_FILE_NAME).read_bytes()
        assert report.contract_sha256 == f"sha256:{hashlib.sha256(contract_bytes).hexdigest()}"
        assert report.markdown_sha256 == f"sha256:{hashlib.sha256(markdown_bytes).hexdigest()}"
        assert report.journal_sha256 == f"sha256:{hashlib.sha256(journal_bytes).hexdigest()}"
        contract = json.loads(contract_bytes)
        assert contract["questions"], "the exported contract carries the questions"
        assert "## Dossiers de preuve" in markdown_bytes.decode("utf-8")
        assert journal_bytes, "the session journal is archived"
        assert (output_dir / "sessions").is_dir(), "the session store is archived"
        assert report.materials == pilote.verify_materials(config=pilote.load_config())

    def test_contract_is_complete_and_every_question_arbitrated(self, tmp_path: Path) -> None:
        report = _run(tmp_path)
        contract = json.loads((tmp_path / "execution" / CONTRACT_JSON_FILE_NAME).read_text("utf-8"))
        assert {question["status"] for question in contract["questions"]} == {"accept"}
        assert len(contract["proof_dossiers"]) == report.questions_total
        assert report.questions_total == len(contract["questions"])
