"""Tests for the reviewer session driver scripts/session.py (ADR-0010, template 2aa4d27)."""

import argparse
import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts import session

if TYPE_CHECKING:
    from pathlib import Path


def parse_session_arguments(command_line_arguments: list[str]) -> argparse.Namespace:
    """Parse arguments through the registered agent-review subcommand."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    session.register(subparsers)
    return parser.parse_args(["agent-review", *command_line_arguments])


def test_register_wires_agent_review() -> None:
    """The subcommand contract: --bundle-file and --report-file are required."""
    arguments = parse_session_arguments(["--bundle-file", "b.md", "--report-file", "r.json"])
    assert arguments.bundle_file == "b.md"
    assert arguments.report_file == "r.json"
    with pytest.raises(SystemExit):
        parse_session_arguments(["--bundle-file", "b.md"])


def test_extract_answer_text_reads_the_last_assistant_message() -> None:
    """The final assistant message is the answer."""
    raw_output = json.dumps(
        [
            {"role": "user", "content": "review"},
            {"role": "assistant", "content": "the answer"},
        ]
    )
    assert session.extract_answer_text(raw_session_output=raw_output) == "the answer"


def test_extract_answer_text_joins_multi_block_content() -> None:
    """A multi-block assistant content is joined into one text."""
    raw_output = json.dumps(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "part one"},
                    {"type": "text", "text": "part two"},
                ],
            }
        ]
    )
    assert session.extract_answer_text(raw_session_output=raw_output) == "part one\npart two"


def test_extract_answer_text_fails_closed_on_invalid_json() -> None:
    """A non-JSON session output is a protocol error, never a guess."""
    with pytest.raises(session.ReviewProtocolError):
        session.extract_answer_text(raw_session_output="not json{")


def test_extract_answer_text_fails_closed_without_an_answer() -> None:
    """No assistant message: protocol error."""
    with pytest.raises(session.ReviewProtocolError):
        session.extract_answer_text(
            raw_session_output=json.dumps([{"role": "user", "content": "hi"}])
        )


def test_narrow_json_answer_strips_a_fence() -> None:
    """A fenced ```json answer is narrowed to its payload."""
    assert (
        session.narrow_json_answer(answer_text='```json\n{"findings": []}\n```')
        == '{"findings": []}'
    )


def test_narrow_json_answer_extracts_an_embedded_object() -> None:
    """Prose around the JSON object: the object is extracted."""
    assert (
        session.narrow_json_answer(answer_text='Here: {"findings": []} done') == '{"findings": []}'
    )


def test_narrow_json_answer_fails_closed_without_json() -> None:
    """No JSON object at all: protocol error."""
    with pytest.raises(session.ReviewProtocolError):
        session.narrow_json_answer(answer_text="no json here")


def valid_finding() -> dict[str, object]:
    """Build a well-formed finding entry."""
    return {
        "severity": "BLOCKING",
        "file": "scripts/ci.py",
        "line": 42,
        "rule": "ADR-0006",
        "evidence": "bare int at a boundary",
    }


def test_parse_finding_accepts_a_well_formed_entry() -> None:
    """A complete finding narrows to a ReviewFinding."""
    finding = session.parse_finding(finding_container=valid_finding())
    assert finding.severity is session.FindingSeverity.BLOCKING
    assert finding.file == "scripts/ci.py"
    assert finding.line == 42


def test_parse_finding_fails_closed_on_a_missing_field() -> None:
    """A finding without evidence is a protocol error."""
    entry = valid_finding()
    del entry["evidence"]
    with pytest.raises(session.ReviewProtocolError):
        session.parse_finding(finding_container=entry)


def test_parse_finding_maps_an_unknown_severity_to_blocking() -> None:
    """An unreadable severity fails closed as BLOCKING, never enables a merge."""
    entry = valid_finding()
    entry["severity"] = "CRITICAL"
    finding = session.parse_finding(finding_container=entry)
    assert finding.severity is session.FindingSeverity.BLOCKING
    assert finding.evidence == session.UNKNOWN_SEVERITY_NOTE


def test_parse_report_reads_the_findings_list() -> None:
    """A report object with a findings list parses to findings."""
    answer = json.dumps({"findings": [valid_finding()], "blocking_count": 1})
    findings = session.parse_report(answer_text=answer)
    assert len(findings) == 1
    assert findings[0].rule == "ADR-0006"


def test_parse_report_fails_closed_on_a_non_object() -> None:
    """A non-object answer is a protocol error."""
    with pytest.raises(session.ReviewProtocolError):
        session.parse_report(answer_text=json.dumps(["not", "an", "object"]))


def test_count_by_severity_recomputes_locally() -> None:
    """Counts are recomputed from the findings, never trusted from the session."""
    findings = [
        session.parse_finding(finding_container=valid_finding()),
        session.parse_finding(finding_container={**valid_finding(), "severity": "SUGGESTION"}),
    ]
    counts = session.count_by_severity(findings=findings)
    assert counts[session.FindingSeverity.BLOCKING] == 1
    assert counts[session.FindingSeverity.SUGGESTION] == 1
    assert counts[session.FindingSeverity.TO_FIX] == 0


def test_render_markdown_comment_escapes_table_cells() -> None:
    """Pipes and newlines in rule/evidence are escaped in the table."""
    finding = session.parse_finding(
        finding_container={**valid_finding(), "rule": "a|b", "evidence": "line1\nline2"}
    )
    comment = session.render_markdown_comment(
        findings=[finding],
        counts=session.count_by_severity(findings=[finding]),
    )
    assert "**BLOCKING**: 1" in comment
    assert "a\\|b" in comment
    assert "line1 line2" in comment


def test_run_reviewer_session_launch_failure_is_protocol_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An OSError at launch (preflight passed) fails closed with exit 2, never a traceback."""
    bundle = tmp_path / "bundle.md"
    bundle.write_text("# bundle", encoding="utf-8")
    fake = make_fake_cli(tmp_path, version="vibe 3.1.3", help_text="--prompt-file FILE")
    monkeypatch.setenv(session.CLI_COMMAND_VARIABLE, str(fake))

    real_run = subprocess.run

    def dispatching_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1] in ("--version", "--help"):
            return real_run(command, capture_output=True, text=True, check=False)
        raise FileNotFoundError("vibe vanished after preflight")

    monkeypatch.setattr(subprocess, "run", dispatching_run)
    with pytest.raises(SystemExit) as excinfo:
        session.run_reviewer_session(model_name="", bundle_path=bundle)
    assert excinfo.value.code == session.EXIT_CODE_PROTOCOL_FAILURE


FAKE_CLI_ANSWER = (
    '[{"role":"assistant","content":"{\\"findings\\":[],\\"recommendation\\":\\"merge\\"}"}]'
)


def make_fake_cli(tmp_path: Path, *, version: str, help_text: str, name: str = "fake-vibe") -> Path:
    """Write an executable fake CLI answering --version/--help and recording args."""
    args_file = tmp_path / f"{name}.args"
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> "{args_file}"\n'
        'case "$1" in\n'
        f'  --version) echo "{version}" ;;\n'
        f'  --help) printf "%s\\n" "{help_text}" ;;\n'
        f"  *) echo '{FAKE_CLI_ANSWER}' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def test_load_max_turns_default_override_and_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """REVIEWER_MAX_TOURS defaults to 12 (was a hard-coded 8) and is configurable."""
    monkeypatch.delenv(session.SESSION_MAX_TURNS_VARIABLE, raising=False)
    assert session.load_max_turns() == 12
    monkeypatch.setenv(session.SESSION_MAX_TURNS_VARIABLE, "20")
    assert session.load_max_turns() == 20
    monkeypatch.setenv(session.SESSION_MAX_TURNS_VARIABLE, "nope")
    with pytest.raises(SystemExit) as exit_info:
        session.load_max_turns()
    assert exit_info.value.code == session.EXIT_CODE_PROTOCOL_FAILURE


def test_load_cli_command_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """REVIEWER_CLI_COMMAND pins an exact binary; default stays vibe."""
    monkeypatch.delenv(session.CLI_COMMAND_VARIABLE, raising=False)
    assert session.load_cli_command() == "vibe"
    monkeypatch.setenv(session.CLI_COMMAND_VARIABLE, "/opt/vibe-3.1.3/bin/vibe")
    assert session.load_cli_command() == "/opt/vibe-3.1.3/bin/vibe"


def test_cli_preflight_accepts_a_capable_cli(tmp_path: Path) -> None:
    """A vibe 3.x binary advertising --prompt-file passes the preflight."""
    fake = make_fake_cli(tmp_path, version="vibe 3.1.3", help_text="--prompt-file FILE")
    assert session.cli_preflight(command=str(fake)) is None


def test_cli_preflight_refuses_an_old_binary(tmp_path: Path) -> None:
    """vibe 2.26.1 is refused with an explicit message naming the found version."""
    fake = make_fake_cli(tmp_path, version="vibe 2.26.1", help_text="--prompt-file FILE")
    refusal = session.cli_preflight(command=str(fake))
    assert refusal is not None
    assert "2.26.1" in refusal
    assert ">= 3" in refusal


def test_cli_preflight_refuses_a_missing_capability(tmp_path: Path) -> None:
    """A 3.x binary without --prompt-file in its help is refused."""
    fake = make_fake_cli(tmp_path, version="vibe 3.0.0", help_text="--output json")
    refusal = session.cli_preflight(command=str(fake))
    assert refusal is not None
    assert "--prompt-file" in refusal


def test_cli_preflight_refuses_a_missing_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary absent from PATH is refused, pointing at REVIEWER_CLI_COMMAND."""
    monkeypatch.setattr(shutil, "which", lambda command: None)
    refusal = session.cli_preflight(command="vibe-missing")
    assert refusal is not None
    assert session.CLI_COMMAND_VARIABLE in refusal


def test_run_reviewer_session_preflight_refusal_exits_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A preflight refusal exits 3 (preconditions), never the opaque exit 2."""
    monkeypatch.setattr(shutil, "which", lambda command: None)
    monkeypatch.setattr(session, "load_cli_command", lambda: "vibe-missing")
    bundle = tmp_path / "bundle.md"
    bundle.write_text("# bundle", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        session.run_reviewer_session(model_name="", bundle_path=bundle)
    assert exit_info.value.code == session.EXIT_CODE_PRECONDITIONS_UNMET


def test_run_reviewer_session_uses_env_cli_and_max_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session launches the pinned CLI with --max-turns from REVIEWER_MAX_TOURS."""
    fake = make_fake_cli(tmp_path, version="vibe 3.1.3", help_text="--prompt-file FILE")
    monkeypatch.setenv(session.CLI_COMMAND_VARIABLE, str(fake))
    monkeypatch.setenv(session.SESSION_MAX_TURNS_VARIABLE, "17")
    bundle = tmp_path / "bundle.md"
    bundle.write_text("# bundle", encoding="utf-8")
    answer = session.run_reviewer_session(model_name="", bundle_path=bundle)
    assert "findings" in answer
    # Only the pinned fake CLI writes this args file: its existence proves the
    # session launched the REVIEWER_CLI_COMMAND binary, not the default one.
    recorded = (tmp_path / "fake-vibe.args").read_text(encoding="utf-8")
    launch_line = next(line for line in recorded.splitlines() if "--prompt-file" in line)
    assert "--max-turns 17" in launch_line
