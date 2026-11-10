"""Tests of the verifypn (Tapaal) engine adapter (issue #9, spec §17.2, §19.4).

The pure handlers of ``verifypn_server`` are unit-tested with a fake
verifypn binary; the ``@engine`` test runs the real container on the
Philosophes-3 net: an engine failure is a CI failure, never a skip.
"""

import json
import stat
from typing import TYPE_CHECKING

import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st
from philosophes3 import build_philosophes3

from petrichor.checks import (
    CheckCompleted,
    CheckFailed,
    CheckQuestion,
    KindPattern,
)
from petrichor.engines.api_runner import VerifypnCheckRunner
from petrichor.engines.archive import RunArchive
from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    VERIFYPN_ENGINE_NAME,
    VERIFYPN_ENGINE_VERSION,
    Cost,
    DriverError,
    EngineQuestion,
    EngineTimeoutError,
    InputHash,
    NormalizedOutput,
    RunRecord,
    new_run_id,
    question_input_hash,
)
from petrichor.engines.driver import ComposeEngineDriver
from petrichor.engines.pnml_export import PnmlExportError, export_pnml
from petrichor.engines.verifypn_server import (
    DEADLOCK_PQL,
    build_normalized_output,
    build_pql_query,
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

FAKE_VERIFYPN = """#!/bin/sh
# Fake verifypn: exit 0 with a two-step trace on stderr when the query is
# the deadlock one, exit 1 otherwise; always print a statistics line.
echo "FORMULA query.pql TRUE TECHNIQUES EXPLICIT"
echo "explored states:   14"
case "$(cat "$2")" in
  *deadlock*)
    echo "Trace:" >&2
    echo "<trace>" >&2
    echo '\\t<transition id="takeL_1" index="0">' >&2
    echo "\\t</transition>" >&2
    echo '\\t<transition id="takeL_2" index="1">' >&2
    echo "\\t</transition>" >&2
    echo '\\t<transition id="takeL_3" index="2">' >&2
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
        # The helper takes any str on purpose: rejection paths (out-of-scope
        # families) are exercised through it, and pydantic enforces the
        # CheckFamily literal at runtime.
        family=family,  # type: ignore[arg-type]
        formula=formula,
        pattern=formula,
        parameters={"model": MODEL},
    )


def test_build_pql_query_deadlock_maps_to_global_properties() -> None:
    """The deadlock family maps to the MCC GlobalProperties.ReachabilityDeadlock dialect."""
    query = build_pql_query(family="deadlock", formula="EF DEADLOCK")
    assert query == DEADLOCK_PQL == "EF deadlock"


def test_build_pql_query_reachability_passes_the_formula_through() -> None:
    """ReachabilityCardinality formulas are PQL as-is."""
    assert build_pql_query(family="reachability", formula="EF (eat_1 > 0)") == "EF (eat_1 > 0)"


def test_build_pql_query_rejects_out_of_scope_families() -> None:
    """CTL/LTL families are P20, not this socle: explicit rejection."""
    with pytest.raises(Exception, match="socle minimal"):
        build_pql_query(family="liveness", formula="AG AF (eat_1 > 0)")


def test_parse_verdict_reads_the_formula_line() -> None:
    """v4.4.2 prints the verdict on stdout (exit code stays 0): FORMULA line + MAYBE."""
    assert parse_verdict(stdout="FORMULA query.pql TRUE TECHNIQUES EXPLICIT\n") == "satisfied"
    assert parse_verdict(stdout="FORMULA query.pql FALSE TECHNIQUES EXPLICIT\n") == "violated"
    assert parse_verdict(stdout="Query is MAYBE satisfied.\n") == "unknown"
    with pytest.raises(Exception, match="no FORMULA verdict"):
        parse_verdict(stdout="nothing\n")


def test_parse_states_explored_reads_the_statistics_line() -> None:
    """The explored-state count comes from the statistics lines."""
    assert parse_states_explored(stdout="explored states:   14\n") == 14
    assert parse_states_explored(stdout="STATE_SPACE STATES 14 1\n") == 14
    assert parse_states_explored(stdout="no stats") == 0


def test_parse_trace_transitions_reads_the_xml_trace_in_order() -> None:
    """The fired transitions are extracted in firing order from stderr."""
    stderr = (
        'Trace:\n<trace>\n\t<transition id="takeL_1" index="0">\n\t</transition>\n'
        '\t<transition id="rel_2" index="5">\n\t</transition>\n</trace>\n'
    )
    assert parse_trace_transitions(stderr=stderr) == ["takeL_1", "rel_2"]
    assert parse_trace_transitions(stderr="no trace") == []


def test_replay_trace_builds_section16_steps() -> None:
    """A fired sequence replays into §16 steps: initial marking, then one step per firing."""
    steps = replay_trace(model=MODEL, fired=["takeL_1", "takeR_1"])
    assert steps[0]["fired"] is None
    assert steps[0]["marking"] == sorted(
        ["think_1", "think_2", "think_3", "fork_1", "fork_2", "fork_3"]
    )
    assert steps[1]["fired"] == "takeL_1"
    assert "hasL_1" in steps[1]["marking"]
    assert "think_1" not in steps[1]["marking"]
    assert "fork_1" not in steps[1]["marking"]
    assert steps[2]["fired"] == "takeR_1"
    assert "eat_1" in steps[2]["marking"]


def test_replay_trace_rejects_a_disabled_firing() -> None:
    """A trace that fires a disabled transition is inconsistent: explicit error."""
    with pytest.raises(Exception, match="disabled"):
        replay_trace(model=MODEL, fired=["takeR_1"])


def test_export_pnml_uses_semantic_names_as_ids() -> None:
    """PNML ids are the element names (unique), so PQL queries address them."""
    pnml = export_pnml(model=MODEL)
    assert '<place id="think_1">' in pnml
    assert "<initialMarking><text>1</text></initialMarking>" in pnml
    assert '<transition id="takeL_1"/>' in pnml
    assert 'source="think_1" target="takeL_1"' in pnml
    assert pnml.count("<arc ") == 30


def test_export_pnml_rejects_non_pt_models() -> None:
    """Symmetric nets are not unfolded here (P27): explicit rejection."""
    with pytest.raises(PnmlExportError, match="P/T"):
        export_pnml(model={**MODEL, "formalism": "symmetric"})


def test_build_normalized_output_satisfied_with_witness() -> None:
    """A TRUE FORMULA line + trace on stderr yields a satisfied verdict with a §16 witness."""
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    output = build_normalized_output(
        stdout="FORMULA query.pql TRUE TECHNIQUES EXPLICIT\nexplored states:   14\n",
        stderr=stderr,
        model=MODEL,
        tool_seconds=0.5,
    )
    assert output["verdict"] == "satisfied"
    witness = output["witness"]
    assert isinstance(witness, dict)
    assert witness["kind"] == "trace"
    steps = witness["steps"]
    assert isinstance(steps, list)
    # steps is a plain list[dict] (JsonValue): the test pins the exact shape
    # build_normalized_output produced two lines above.
    assert steps[1]["fired"] == "takeL_1"  # type: ignore[index]
    assert output["cost"] == {"tool_seconds": 0.5, "states_explored": 14}
    assert output["technique"] == "verifypn-4.4.2"


def test_build_normalized_output_violated_without_witness() -> None:
    """A FALSE FORMULA line yields a violated verdict and no witness (no trace printed)."""
    output = build_normalized_output(
        stdout="FORMULA query.pql FALSE TECHNIQUES QUERY_REDUCTION\n",
        stderr="",
        model=MODEL,
        tool_seconds=0.1,
    )
    assert output["verdict"] == "violated"
    assert output["witness"] is None


def test_handle_check_with_fake_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end handler path against a fake verifypn binary (no container)."""
    fake = tmp_path / "verifypn"
    fake.write_text(FAKE_VERIFYPN, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("VERIFYPN_BINARY", str(fake))
    body = json.dumps(_question(family="deadlock", formula="EF DEADLOCK").model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 200
    output = json.loads(payload.decode("utf-8"))
    assert output["verdict"] == "satisfied"
    assert output["witness"]["steps"][-1]["fired"] == "takeL_3"
    assert output["cost"]["states_explored"] == 14


def test_handle_check_rejects_missing_model() -> None:
    """A question without the canonical model in parameters is a 422."""
    question = EngineQuestion(family="deadlock", formula="EF DEADLOCK", pattern="deadlock")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 422
    assert "model" in json.loads(payload.decode("utf-8"))["detail"]


def test_handle_check_rejects_out_of_scope_family() -> None:
    """A CTL family is outside the socle minimal scope: 422, never reinterpreted."""
    question = EngineQuestion(
        # "liveness" is a valid CheckFamily but outside the socle-minimal
        # SUPPORTED_FAMILIES: the point of this test is the 422 rejection.
        family="liveness",  # type: ignore[arg-type]
        formula="AG AF (eat_1 > 0)",
        pattern="liveness.unconditional",
        parameters={"model": MODEL},
    )
    body = json.dumps(question.model_dump(mode="json"))
    status, _ = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 422


MALFORMED_VERIFYPN = """#!/bin/sh
# Fake verifypn: exit 0 but no FORMULA verdict line (engine contract violation).
echo "explored states:   3"
exit 0
"""


def test_handle_check_maps_malformed_exit0_stdout_to_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 0 without a FORMULA line is a typed HTTP 500, never a dropped connection."""
    fake = tmp_path / "verifypn"
    fake.write_text(MALFORMED_VERIFYPN, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("VERIFYPN_BINARY", str(fake))
    body = json.dumps(_question(family="deadlock", formula="EF DEADLOCK").model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 500
    assert "invalid" in json.loads(payload.decode("utf-8"))["detail"]


@pytest.mark.property
@settings(deadline=None)
@given(ids=st.lists(st.from_regex(r"[A-Za-z_][A-Za-z0-9_]*", fullmatch=True), max_size=20))
def test_property_parse_trace_transitions_roundtrip(ids: list[str]) -> None:
    """Any well-formed XML trace parses back to exactly the fired ids, in order."""
    stderr = (
        "<trace>"
        + "".join(
            f'<transition id="{name}" index="{index}"></transition>'
            for index, name in enumerate(ids)
        )
        + "</trace>"
    )
    assert parse_trace_transitions(stderr=stderr) == ids


class _StubDriver:
    """A driver stub programmed with one outcome, to exercise the runner paths."""

    def __init__(self, *, outcome: RunRecord | Exception) -> None:
        self._outcome = outcome

    def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def _check_question() -> CheckQuestion:
    """A deadlock check question carrying the Philosophes-3 model."""
    return CheckQuestion(
        family="deadlock",
        kind=KindPattern("deadlock"),
        formula="EF DEADLOCK",
        parameters={"model": MODEL},
    )


def _completed_record() -> RunRecord:
    """The archived record of a successful run, as the driver would return it."""
    return RunRecord(
        run_id=new_run_id(),
        engine=VERIFYPN_ENGINE_NAME,
        engine_version=VERIFYPN_ENGINE_VERSION,
        command=["docker", "compose", "run", "verifypn"],
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        input_hash=InputHash("sha256:" + "0" * 64),
        output=NormalizedOutput(
            verdict="satisfied",
            witness=None,
            cost=Cost(tool_seconds=0.1, states_explored=14),
            technique="verifypn-4.4.2",
        ),
        measured_seconds=0.2,
        archived_at="2026-11-10T00:00:00+00:00",
    )


class TestVerifypnCheckRunnerNarration:
    """The runner's log events are part of the narration contract: pinned here."""

    def test_completed_run_is_narrated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = VerifypnCheckRunner(archive=RunArchive(directory=tmp_path / "runs"))
        record = _completed_record()
        monkeypatch.setattr(runner, "_driver", _StubDriver(outcome=record))
        with structlog.testing.capture_logs() as events:
            outcome = runner.run(question=_check_question(), timeout_seconds=30)
        assert isinstance(outcome, CheckCompleted)
        completed = [event for event in events if event["event"] == "runner_run_completed"]
        assert len(completed) == 1
        assert completed[0]["run_id"] == record.run_id
        assert completed[0]["engine"] == VERIFYPN_ENGINE_NAME
        assert completed[0]["verdict"] == "satisfied"

    def test_timeout_is_narrated_as_a_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = VerifypnCheckRunner(archive=RunArchive(directory=tmp_path / "runs"))
        monkeypatch.setattr(
            runner, "_driver", _StubDriver(outcome=EngineTimeoutError("exceeded 30s"))
        )
        with structlog.testing.capture_logs() as events:
            outcome = runner.run(question=_check_question(), timeout_seconds=30)
        assert isinstance(outcome, CheckFailed)
        assert outcome.kind == "timeout"
        timeouts = [event for event in events if event["event"] == "runner_timeout"]
        assert len(timeouts) == 1
        assert timeouts[0]["family"] == "deadlock"
        assert timeouts[0]["kind"] == "deadlock"

    def test_driver_error_is_narrated_as_a_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = VerifypnCheckRunner(archive=RunArchive(directory=tmp_path / "runs"))
        monkeypatch.setattr(
            runner, "_driver", _StubDriver(outcome=DriverError("compose up failed"))
        )
        with structlog.testing.capture_logs() as events:
            outcome = runner.run(question=_check_question(), timeout_seconds=30)
        assert isinstance(outcome, CheckFailed)
        assert outcome.kind == "engine_error"
        errors = [event for event in events if event["event"] == "runner_engine_error"]
        assert len(errors) == 1
        assert errors[0]["family"] == "deadlock"
        assert errors[0]["error"] == "compose up failed"


@pytest.mark.engine
def test_verifypn_driver_answers_in_the_real_container(tmp_path: Path) -> None:
    """Oracle of the MCC benchmark: Philosophes-3 has a reachable deadlock (EF deadlock)."""
    archive = RunArchive(directory=tmp_path / "runs")
    driver = ComposeEngineDriver(
        archive=archive,
        host_port=18081,
        service_name="verifypn",
        engine_name=VERIFYPN_ENGINE_NAME,
        engine_version=VERIFYPN_ENGINE_VERSION,
    )
    question = _question(family="deadlock", formula="EF DEADLOCK")

    record = driver.run(question=question, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    assert record.engine == VERIFYPN_ENGINE_NAME
    assert record.engine_version == VERIFYPN_ENGINE_VERSION
    assert record.output.verdict == "satisfied"
    assert record.output.witness is not None
    steps = record.output.witness.steps
    assert len(steps) >= 2
    assert steps[0].fired is None
    assert steps[-1].fired is not None
    assert record.output.cost.states_explored > 0
    assert record.output.technique == "verifypn-4.4.2"
    assert record.input_hash == question_input_hash(question=question)
    assert len(archive) == 1


@pytest.mark.engine
def test_verifypn_reachability_cardinality_in_the_real_container(tmp_path: Path) -> None:
    """ReachabilityCardinality: EF (eat_1 > 0) holds, EF (eat_1 > 1) is violated (1-safe)."""
    archive = RunArchive(directory=tmp_path / "runs")
    driver = ComposeEngineDriver(
        archive=archive,
        host_port=18081,
        service_name="verifypn",
        engine_name=VERIFYPN_ENGINE_NAME,
        engine_version=VERIFYPN_ENGINE_VERSION,
    )

    satisfied = driver.run(
        question=_question(family="reachability", formula="EF (eat_1 > 0)"),
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )
    violated = driver.run(
        question=_question(family="reachability", formula="EF (eat_1 > 1)"),
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )

    assert satisfied.output.verdict == "satisfied"
    assert satisfied.output.witness is not None
    assert violated.output.verdict == "violated"
    assert len(archive) == 2
