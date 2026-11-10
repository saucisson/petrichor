"""Tests of the ITS-Tools engine adapter (issue #21, spec §17.2, §17.4, §19.4).

The pure handlers of ``its_tools_server`` are unit-tested with a fake
ITS-Tools binary; the ``@engine`` test runs the real container on the
Philosophes-3 net: an engine failure is a CI failure, never a skip.
"""

import json
import stat
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from philosophes3 import build_philosophes3

from petrichor.engines.contract import EngineQuestion
from petrichor.engines.its_tools_server import (
    DEADLOCK_QUERY,
    build_its_query,
    build_normalized_output,
    detect_cyclicity,
    handle_check,
    parse_states_explored,
    parse_trace_transitions,
    parse_verdict,
    replay_trace,
)
from petrichor.petri_canonical import semantic_projection

if TYPE_CHECKING:
    from pathlib import Path

MODEL = semantic_projection(model=build_philosophes3())

FAKE_ITS = """#!/bin/sh
# Fake ITS-Tools: exit 0 with a FORMULA TRUE line and a trace on stderr.
echo "FORMULA query.props TRUE"
echo "States: 14"
case "$(cat "$3")" in
  *deadlock*)
    echo "<trace>" >&2
    echo '\\t<transition id="takeL_1" index="0">' >&2
    echo "\\t</transition>" >&2
    echo "</trace>" >&2
    exit 0
    ;;
  *)
    exit 1
    ;;
esac
"""


def _question(*, family: str, formula: str) -> EngineQuestion:
    """A question carrying the Philosophes-3 model in its parameters."""
    return EngineQuestion(
        family=family,  # type: ignore[arg-type]
        formula=formula,
        pattern=formula,
        parameters={"model": MODEL},
    )


def test_build_its_query_deadlock_maps_to_ef_deadlock() -> None:
    """The deadlock family maps to the fixed EF deadlock query."""
    assert (
        build_its_query(family="deadlock", formula="EF DEADLOCK") == DEADLOCK_QUERY == "EF deadlock"
    )


def test_build_its_query_passes_all_families_through() -> None:
    """All five families pass the MCC formula through (ITS-Tools accepts CTL/LTL)."""
    assert build_its_query(family="reachability", formula="EF (eat_1 > 0)") == "EF (eat_1 > 0)"
    assert build_its_query(family="safety", formula="AG (eat_1 <= 1)") == "AG (eat_1 <= 1)"
    assert build_its_query(family="liveness", formula="AG EF (eat_1 > 0)") == "AG EF (eat_1 > 0)"
    assert build_its_query(family="recovery", formula="AG (think_1 == 0 or EF (eat_1 > 0))") == (
        "AG (think_1 == 0 or EF (eat_1 > 0))"
    )


def test_build_its_query_rejects_unknown_families() -> None:
    """An unknown family is explicitly rejected."""
    with pytest.raises(Exception, match="outside the supported scope"):
        build_its_query(family="unknown_family", formula="EF true")


def test_parse_verdict_reads_the_formula_line() -> None:
    """The FORMULA line carries the verdict; MAYBE maps to unknown."""
    assert parse_verdict(stdout="FORMULA query.props TRUE\n") == "satisfied"
    assert parse_verdict(stdout="FORMULA query.props FALSE\n") == "violated"
    assert parse_verdict(stdout="Query is MAYBE satisfied.\n") == "unknown"
    with pytest.raises(Exception, match="no FORMULA verdict"):
        parse_verdict(stdout="nothing\n")


def test_parse_states_explored_reads_the_statistics_line() -> None:
    """The state count comes from the States: line."""
    assert parse_states_explored(stdout="States: 14\n") == 14
    assert parse_states_explored(stdout="no stats") == 0


def test_parse_trace_transitions_reads_the_xml_trace() -> None:
    """The fired transitions are extracted in order from stderr."""
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    assert parse_trace_transitions(stderr=stderr) == ["takeL_1"]
    assert parse_trace_transitions(stderr="no trace") == []


def test_replay_trace_builds_section16_steps() -> None:
    """A fired sequence replays into §16 steps."""
    steps = replay_trace(model=MODEL, fired=["takeL_1", "takeR_1"])
    assert steps[0]["fired"] is None
    assert steps[1]["fired"] == "takeL_1"
    assert steps[2]["fired"] == "takeR_1"


def test_detect_cyclicity_finds_repeated_marking() -> None:
    """A trace whose marking repeats is cyclic."""
    steps = [
        {"index": 0, "marking": ["a"], "fired": None},
        {"index": 1, "marking": ["b"], "fired": "t1"},
        {"index": 2, "marking": ["a"], "fired": "t2"},
    ]
    cyclic, back_edge = detect_cyclicity(steps=steps)
    assert cyclic is True
    assert back_edge == 0


def test_build_normalized_output_satisfied_with_witness() -> None:
    """A TRUE FORMULA line + trace yields a satisfied verdict with a §16 witness."""
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    output = build_normalized_output(
        stdout="FORMULA query.props TRUE\nStates: 14\n",
        stderr=stderr,
        model=MODEL,
        tool_seconds=0.5,
    )
    assert output["verdict"] == "satisfied"
    witness = output["witness"]
    assert isinstance(witness, dict)
    assert witness["kind"] == "trace"
    assert output["cost"] == {"tool_seconds": 0.5, "states_explored": 14}
    assert output["technique"] == "its-tools-10052022"


def test_build_normalized_output_violated_with_trace_is_counterexample() -> None:
    """A FALSE verdict + trace yields a counterexample witness (spec §16)."""
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    output = build_normalized_output(
        stdout="FORMULA query.props FALSE\n",
        stderr=stderr,
        model=MODEL,
        tool_seconds=0.3,
    )
    assert output["verdict"] == "violated"
    witness = output["witness"]
    assert isinstance(witness, dict)
    assert witness["kind"] == "counterexample"


def test_handle_check_with_fake_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end handler path against a fake ITS-Tools binary (no container)."""
    fake = tmp_path / "its"
    fake.write_text(FAKE_ITS, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("ITS_TOOLS_BINARY", str(fake))
    body = json.dumps(_question(family="deadlock", formula="EF DEADLOCK").model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 200
    output = json.loads(payload.decode("utf-8"))
    assert output["verdict"] == "satisfied"
    assert output["technique"] == "its-tools-10052022"


def test_handle_check_rejects_missing_model() -> None:
    """A question without the canonical model in parameters is a 422."""
    question = EngineQuestion(family="deadlock", formula="EF DEADLOCK", pattern="deadlock")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 422
    assert "model" in json.loads(payload.decode("utf-8"))["detail"]


def test_handle_check_rejects_unknown_family() -> None:
    """An unknown family is 422, never reinterpreted (raw JSON bypasses pydantic)."""
    body = json.dumps(
        {
            "family": "unknown_family",
            "formula": "EF true",
            "pattern": "unknown",
            "parameters": {"model": MODEL},
        }
    )
    status, _ = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 422


@pytest.mark.property
@settings(deadline=None, max_examples=50)
@given(
    markings=st.lists(
        st.lists(st.text(alphabet="ab", min_size=1, max_size=3), min_size=1, max_size=5),
        min_size=1,
        max_size=10,
    )
)
def test_property_detect_cyclicity_invariant(markings: list[list[str]]) -> None:
    """Property: cyclic iff a marking repeats; back_edge points to the first occurrence."""
    steps = [
        {"index": i, "marking": sorted(m), "fired": f"t{i}" if i > 0 else None}
        for i, m in enumerate(markings)
    ]
    cyclic, back_edge = detect_cyclicity(steps=steps)
    seen: set[str] = set()
    expected_cyclic = False
    expected_back_edge: int | None = None
    for _i, m in enumerate(markings):
        key = json.dumps(sorted(m), sort_keys=True)
        if key in seen:
            expected_cyclic = True
            if expected_back_edge is None:
                expected_back_edge = next(
                    j
                    for j, m2 in enumerate(markings)
                    if json.dumps(sorted(m2), sort_keys=True) == key
                )
        seen.add(key)
    assert cyclic == expected_cyclic
    if expected_cyclic:
        assert back_edge == expected_back_edge
    else:
        assert back_edge is None
