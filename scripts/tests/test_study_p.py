"""Tests for the study-p driver (spec §20, issue #41, ADR-0021).

The driver wires the real verifypn compose engine into the product runner;
these tests exercise the argument parsing and the exit-code contract through
the ``_run_pilote_session`` seam, without loading the product package (the
CI type-check of ``scripts/`` runs without ``src`` on its path).
"""

import argparse
from typing import TYPE_CHECKING

from scripts import study_p

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def parse_study_p_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered study-p subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    study_p.register(subparsers)
    return parser.parse_args(["study-p", *command_line_arguments])


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
    def test_run_forwards_output_dir_and_timeout_and_returns_the_session_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: dict[str, object] = {}

        def fake_session(*, output_dir: Path, check_timeout: int) -> int:
            captured["output_dir"] = output_dir
            captured["check_timeout"] = check_timeout
            print("pilote record written to rapport.json")
            return 0

        monkeypatch.setattr(study_p, "_run_pilote_session", fake_session)
        arguments = parse_study_p_arguments(
            ["--output-dir", str(tmp_path / "out"), "--check-timeout", "120"]
        )
        assert arguments.runner(arguments=arguments) == 0
        assert captured["output_dir"] == tmp_path / "out"
        assert captured["check_timeout"] == 120
        assert "rapport.json" in capsys.readouterr().out

    def test_run_propagates_the_over_cap_exit_code(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(study_p, "_run_pilote_session", lambda *, output_dir, check_timeout: 1)
        arguments = parse_study_p_arguments(["--output-dir", str(tmp_path / "out")])
        assert arguments.runner(arguments=arguments) == 1


def parse_study_p_audit_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered study-p-audit subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    study_p.register(subparsers)
    return parser.parse_args(["study-p-audit", *command_line_arguments])


class TestStudyPAudit:
    def test_audit_subcommand_defaults(self) -> None:
        arguments = parse_study_p_audit_arguments([])
        assert arguments.record_dir == str(study_p.DEFAULT_RECORD_DIR)
        assert arguments.output is None
        assert callable(arguments.runner)

    def test_audit_forwards_record_dir_and_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_audit(*, record_dir: Path, output: Path) -> int:
            captured["record_dir"] = record_dir
            captured["output"] = output
            return 0

        monkeypatch.setattr(study_p, "_audit_pilote_record", fake_audit)
        arguments = parse_study_p_audit_arguments(
            ["--record-dir", str(tmp_path / "rec"), "--output", str(tmp_path / "a.json")]
        )
        assert arguments.runner(arguments=arguments) == 0
        assert captured["record_dir"] == tmp_path / "rec"
        assert captured["output"] == tmp_path / "a.json"

    def test_audit_default_output_is_next_to_the_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_audit(*, record_dir: Path, output: Path) -> int:
            captured["output"] = output
            return 1

        monkeypatch.setattr(study_p, "_audit_pilote_record", fake_audit)
        arguments = parse_study_p_audit_arguments(
            ["--record-dir", str(tmp_path / "v1" / "execution")]
        )
        assert arguments.runner(arguments=arguments) == 1
        assert captured["output"] == tmp_path / "v1" / "audit" / "audit.json"
