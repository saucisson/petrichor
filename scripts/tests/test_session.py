"""Tests for the reviewer session verdicts scripts/session.py (ADR-0006)."""

import pytest

from scripts import session
from scripts.session import (
    UNKNOWN_SEVERITY_NOTE,
    FindingSeverity,
    ReviewFinding,
    ReviewProtocolError,
)


def test_narrow_json_answer_keeps_a_plain_object() -> None:
    """A plain JSON object answer passes through unchanged."""
    answer_text = '{"findings": []}'
    assert session.narrow_json_answer(answer_text=answer_text) == answer_text


def test_narrow_json_answer_strips_a_json_fence() -> None:
    """A ```json fenced answer narrows to its payload."""
    answer_text = '```json\n{"findings": []}\n```'
    assert session.narrow_json_answer(answer_text=answer_text) == '{"findings": []}'


def test_narrow_json_answer_strips_a_bare_fence() -> None:
    """A bare ``` fence narrows to its payload too."""
    answer_text = '```\n{"findings": []}\n```'
    assert session.narrow_json_answer(answer_text=answer_text) == '{"findings": []}'


def test_narrow_json_answer_carves_json_out_of_prose() -> None:
    """Prose around the object is carved away, keeping the object."""
    answer_text = 'Answer: {"findings": []} done'
    assert session.narrow_json_answer(answer_text=answer_text) == '{"findings": []}'


def test_narrow_json_answer_refuses_an_answer_without_object() -> None:
    """No JSON object anywhere: a protocol error, never a guess."""
    with pytest.raises(ReviewProtocolError):
        session.narrow_json_answer(answer_text="still nothing")


def test_parse_finding_valid() -> None:
    """A well-formed finding narrows to a typed ReviewFinding."""
    finding = session.parse_finding(
        finding_container={
            "severity": "TO-FIX",
            "file": "a.py",
            "line": 7,
            "rule": "r",
            "evidence": "e",
        }
    )
    assert finding.severity is FindingSeverity.TO_FIX
    assert finding.file == "a.py"
    assert finding.line == 7
    assert finding.rule == "r"
    assert finding.evidence == "e"


def test_parse_finding_unknown_severity_fails_closed() -> None:
    """An unreadable severity is BLOCKING, with the raw value as the rule."""
    finding = session.parse_finding(
        finding_container={
            "severity": "SEVERE-BEYOND",
            "file": "a.py",
            "line": 7,
            "rule": "r",
            "evidence": "e",
        }
    )
    assert finding.severity is FindingSeverity.BLOCKING
    assert finding.rule == "SEVERE-BEYOND"
    assert finding.evidence == UNKNOWN_SEVERITY_NOTE


def test_parse_finding_refuses_a_missing_field() -> None:
    """A finding without a field is a protocol error."""
    with pytest.raises(ReviewProtocolError):
        session.parse_finding(finding_container={"severity": "TO-FIX"})


def test_parse_finding_refuses_a_non_integer_line() -> None:
    """A finding with a non-integer line is a protocol error."""
    with pytest.raises(ReviewProtocolError):
        session.parse_finding(
            finding_container={
                "severity": "TO-FIX",
                "file": "a.py",
                "line": "7",
                "rule": "r",
                "evidence": "e",
            }
        )


def test_parse_report_refuses_non_json() -> None:
    """A non-JSON answer is a protocol error."""
    with pytest.raises(ReviewProtocolError):
        session.parse_report(answer_text="not json")


def test_parse_report_refuses_a_non_object() -> None:
    """A JSON non-object answer is a protocol error."""
    with pytest.raises(ReviewProtocolError):
        session.parse_report(answer_text="[1]")


def test_parse_report_refuses_a_missing_findings_list() -> None:
    """An object without findings is a protocol error."""
    with pytest.raises(ReviewProtocolError):
        session.parse_report(answer_text="{}")


def test_parse_report_returns_the_validated_findings() -> None:
    """A valid report parses into typed findings, in order."""
    findings = session.parse_report(
        answer_text=(
            '{"findings": ['
            '{"severity": "BLOCKING", "file": "a.py", "line": 7, "rule": "r", "evidence": "e"},'
            '{"severity": "SUGGESTION", "file": "b.py", "line": 9, "rule": "s", "evidence": "f"}'
            "]}"
        )
    )
    assert len(findings) == 2
    assert findings[0].severity is FindingSeverity.BLOCKING
    assert findings[1].severity is FindingSeverity.SUGGESTION


def test_count_by_severity_recomputes_locally() -> None:
    """The counts are recomputed from the findings, never trusted."""
    findings = [
        ReviewFinding(
            severity=FindingSeverity.BLOCKING,
            file="a.py",
            line=1,
            rule="r1",
            evidence="e1",
        ),
        ReviewFinding(
            severity=FindingSeverity.TO_FIX,
            file="a.py",
            line=2,
            rule="r2",
            evidence="e2",
        ),
        ReviewFinding(
            severity=FindingSeverity.TO_FIX,
            file="a.py",
            line=3,
            rule="r3",
            evidence="e3",
        ),
    ]
    counts = session.count_by_severity(findings=findings)
    assert counts == {
        FindingSeverity.BLOCKING: 1,
        FindingSeverity.TO_FIX: 2,
        FindingSeverity.SUGGESTION: 0,
    }


def test_extract_answer_text_returns_the_last_assistant_message() -> None:
    """The answer is the last assistant message, whatever came before."""
    raw_session_output = (
        '[{"role": "user", "content": "hi"},'
        ' {"role": "assistant", "content": "first"},'
        ' {"role": "assistant", "content": "final"}]'
    )
    assert session.extract_answer_text(raw_session_output=raw_session_output) == "final"


def test_extract_answer_text_joins_multiblock_content() -> None:
    """A multi-block assistant answer joins its textual blocks."""
    raw_session_output = (
        '[{"role": "assistant", "content": '
        '[{"text": "part one"}, {"type": "code"}, {"text": "part two"}]}]'
    )
    answer = session.extract_answer_text(raw_session_output=raw_session_output)
    assert answer == "part one\npart two"


def test_extract_answer_text_refuses_output_without_answer() -> None:
    """No assistant message: a protocol error."""
    raw_session_output = '[{"role": "user", "content": "hi"}]'
    with pytest.raises(ReviewProtocolError):
        session.extract_answer_text(raw_session_output=raw_session_output)


def test_render_markdown_comment_zero_findings() -> None:
    """Zero findings render as the explicit empty report."""
    counts = session.count_by_severity(findings=[])
    comment = session.render_markdown_comment(findings=[], counts=counts)
    assert "Zero findings." in comment


def test_render_markdown_comment_escapes_table_cells() -> None:
    """Pipes and newlines in evidence and rule never break the table."""
    findings = [
        ReviewFinding(
            severity=FindingSeverity.TO_FIX,
            file="a.py",
            line=7,
            rule="r1\nr2",
            evidence="a|b\nc",
        )
    ]
    counts = session.count_by_severity(findings=findings)
    comment = session.render_markdown_comment(findings=findings, counts=counts)
    assert "a\\|b c" in comment
    assert "r1 r2" in comment
