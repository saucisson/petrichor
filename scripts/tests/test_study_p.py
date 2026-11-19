"""Tests for the study-p driver (spec §20, issue #41, ADR-0021)."""

import argparse
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from petrichor.checks import RunId, SessionId
from petrichor.engines import api_runner
from petrichor.petri_canonical import ContentHash, ModelId
from petrichor.study import runner as runner_module
from petrichor.study.runner import PiloteBudgetReport, PiloteReport, PiloteRunSummary
from scripts import study_p

if TYPE_CHECKING:
    import pytest


def parse_study_p_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered study-p subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    study_p.register(subparsers)
    return parser.parse_args(["study-p", *command_line_arguments])


def _fake_report(*, within_cap: bool, output_dir: Path) -> PiloteReport:
    """Build a minimal report as run_pilote would return it."""
    return PiloteReport(
        study_id="pilote",
        study_version="v1",
        preregistered_at=date(2026, 11, 18),
        executed_at=datetime.now(UTC),
        model_file="model.pnml",
        model_sha256=ContentHash("sha256:" + "0" * 64),
        model_content_hash=ContentHash("sha256:" + "1" * 64),
        model_id=ModelId("mdl_10c2c092"),
        session_id=SessionId("ses_fake"),
        engine="verifypn",
        engine_version="4.4.2",
        budget=PiloteBudgetReport(
            cap_seconds=3600.0,
            consumed_seconds=1.0,
            reserved_seconds=0.0,
            remaining_seconds=3599.0 if within_cap else -1.0,
            run_count=8,
            within_cap=within_cap,
        ),
        questions_total=8,
        questions_arbitrated=8,
        runs=(
            PiloteRunSummary(
                run_id=RunId("run_fake"),
                family="safety",
                kind="safety.invariant",
                formula="AG (productSlots_1 <= 1)",
                status="done",
                verdict="satisfied",
                tool_seconds=0.1,
            ),
        ),
        contract_sha256=ContentHash("sha256:" + "2" * 64),
        markdown_sha256=ContentHash("sha256:" + "3" * 64),
        journal_sha256=ContentHash("sha256:" + "4" * 64),
        materials={"model.pnml": ContentHash("sha256:" + "0" * 64)},
        output_dir=str(output_dir),
    )


class _FakeVerifypnRunner:
    """Stands in for the compose runner; records its archive wiring."""

    def __init__(self, *, archive: object) -> None:
        self.archive = archive


class TestStudyPRegistration:
    def test_study_p_subcommand_is_registered_with_defaults(self) -> None:
        arguments = parse_study_p_arguments([])
        assert arguments.output_dir == str(study_p.DEFAULT_OUTPUT_DIR)
        assert arguments.check_timeout == study_p.DEFAULT_CHECK_TIMEOUT_SECONDS
        assert callable(arguments.runner)

    def test_study_p_accepts_output_dir_and_timeout(self, tmp_path: Path) -> None:
        arguments = parse_study_p_arguments(
            ["--output-dir", str(tmp_path / "out"), "--check-timeout", "120"]
        )
        assert arguments.output_dir == str(tmp_path / "out")
        assert arguments.check_timeout == 120


class TestStudyPRun:
    def test_run_wires_the_real_engine_and_returns_success_within_cap(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_pilote(**kwargs: object) -> PiloteReport:
            captured.update(kwargs)
            return _fake_report(within_cap=True, output_dir=Path(str(kwargs["output_dir"])))

        monkeypatch.setattr(api_runner, "VerifypnCheckRunner", _FakeVerifypnRunner)
        monkeypatch.setattr(runner_module, "run_pilote", fake_run_pilote)
        arguments = parse_study_p_arguments(["--output-dir", str(tmp_path / "out")])
        exit_code = arguments.runner(arguments=arguments)
        assert exit_code == 0
        assert captured["engine_name"] == "verifypn"
        assert captured["engine_version"] == "4.4.2"
        assert captured["check_timeout_seconds"] == study_p.DEFAULT_CHECK_TIMEOUT_SECONDS
        assert "rapport.json" in capsys.readouterr().out

    def test_run_returns_criteria_unmet_when_over_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run_pilote(**kwargs: object) -> PiloteReport:
            return _fake_report(within_cap=False, output_dir=Path(str(kwargs["output_dir"])))

        monkeypatch.setattr(api_runner, "VerifypnCheckRunner", _FakeVerifypnRunner)
        monkeypatch.setattr(runner_module, "run_pilote", fake_run_pilote)
        arguments = parse_study_p_arguments(["--output-dir", str(tmp_path / "out")])
        assert arguments.runner(arguments=arguments) == 1
