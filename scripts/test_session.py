"""Tests for the reviewer session driver scripts/session.py (ADR-0010, template 2aa4d27)."""

import argparse
import json
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
    """A missing reviewer CLI fails closed with exit 2, never a traceback."""
    bundle = tmp_path / "bundle.md"
    bundle.write_text("# bundle", encoding="utf-8")

    def broken_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("vibe not found")

    monkeypatch.setattr(subprocess, "run", broken_run)
    with pytest.raises(SystemExit) as excinfo:
        session.run_reviewer_session(model_name="", bundle_path=bundle)
    assert excinfo.value.code == session.EXIT_CODE_PROTOCOL_FAILURE
