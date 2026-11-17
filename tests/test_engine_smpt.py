"""Tests of the SMPT engine adapter (issue #26, spec §17.2, §17.4, §18.1, §19.4).

The pure handlers of ``smpt_server`` are unit-tested with a fake SMPT
binary. Container (@engine) tests are deferred: the SMPT image build is
tracked separately. SMPT covers reachability only (SMT + witnesses) plus
polyhedral invariants - no CTL/LTL family (spec §17.2).
"""

import json
import logging
import shutil
import stat
from typing import TYPE_CHECKING, Any, cast

import pytest
from philosophes3 import build_philosophes3

from petrichor.engines.api_runner import SMPT_SERVICE_NAME, SmptCheckRunner
from petrichor.engines.archive import RunArchive
from petrichor.engines.contract import (
    SMPT_ENGINE_IMAGE,
    SMPT_ENGINE_NAME,
    SMPT_ENGINE_VERSION,
    EngineName,
    EngineQuestion,
)
from petrichor.engines.cross_verification import default_pairing_matrix
from petrichor.engines.smpt_server import (
    QuestionError,
    SmptHandler,
    build_invariants_output,
    build_normalized_output,
    handle_check,
    handle_health,
    handle_invariants,
    parse_polyhedral_invariants,
    parse_states_explored,
    parse_trace_transitions,
    parse_verdict,
)
from petrichor.petri_canonical import semantic_projection

if TYPE_CHECKING:
    from pathlib import Path

MODEL = semantic_projection(model=build_philosophes3())

FAKE_SMPT_SATISFIED = """#!/bin/sh
# Fake SMPT: reachability satisfied, with an XML trace on stderr.
echo "FORMULA query.smt2 TRUE"
echo "States: 14"
echo "<trace>" >&2
echo '\\t<transition id="takeL_1" index="0">' >&2
echo "\\t</transition>" >&2
echo "</trace>" >&2
exit 0
"""

FAKE_SMPT_VIOLATED = """#!/bin/sh
# Fake SMPT: reachability violated, counterexample trace on stderr.
echo "FORMULA query.smt2 FALSE"
echo "States: 14"
echo "<trace>" >&2
echo '\\t<transition id="takeL_1" index="0">' >&2
echo "\\t</transition>" >&2
echo "</trace>" >&2
exit 0
"""

FAKE_SMPT_INVARIANTS = """#!/bin/sh
# Fake SMPT: two polyhedral invariants, one with a negative coefficient.
echo "INVARIANT places=fork_1,fork_2,fork_3 coeffs=1,1,1 bound=3"
echo "INVARIANT places=eat_1,think_1 coeffs=1,-1 bound=0"
echo "States: 0"
exit 0
"""

FAKE_SMPT_MALFORMED = """#!/bin/sh
# Fake SMPT: exit 0 with no FORMULA line (contract violation).
echo "garbage"
exit 0
"""


def _question(*, family: str, formula: str) -> EngineQuestion:
    """A question carrying the Philosophes-3 model in its parameters."""
    return EngineQuestion(
        # The helper takes any str: rejection paths are exercised through it,
        # and pydantic enforces the CheckFamily literal at runtime.
        family=family,  # type: ignore[arg-type]
        formula=formula,
        pattern=formula,
        parameters={"model": MODEL},
    )


def _write_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, script: str) -> None:
    """Install a fake SMPT binary as SMPT_BINARY."""
    fake = tmp_path / "smpt"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("SMPT_BINARY", str(fake))


def test_parse_verdict_reads_the_formula_line() -> None:
    """TRUE/FALSE map to satisfied/violated, MAYBE to unknown."""
    assert parse_verdict(stdout="FORMULA query.smt2 TRUE\n") == "satisfied"
    assert parse_verdict(stdout="FORMULA query.smt2 FALSE\n") == "violated"
    assert parse_verdict(stdout="Query is MAYBE satisfied\n") == "unknown"
    with pytest.raises(QuestionError):
        parse_verdict(stdout="garbage\n")


def test_parse_states_explored_and_trace() -> None:
    """The statistics line and the XML trace are parsed."""
    assert parse_states_explored(stdout="States: 14\n") == 14
    assert parse_states_explored(stdout="nothing\n") == 0
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    assert parse_trace_transitions(stderr=stderr) == ["takeL_1"]
    assert parse_trace_transitions(stderr="") == []


def test_parse_polyhedral_invariants_normalizes_lines() -> None:
    """INVARIANT lines become polyhedral_invariant objects, negative coeffs allowed."""
    stdout = (
        "INVARIANT places=fork_1,fork_2,fork_3 coeffs=1,1,1 bound=3\n"
        "INVARIANT places=eat_1,think_1 coeffs=1,-1 bound=0\n"
    )
    invariants = parse_polyhedral_invariants(stdout=stdout)
    assert invariants == [
        {
            "kind": "polyhedral_invariant",
            "places": ["fork_1", "fork_2", "fork_3"],
            "coefficients": [1, 1, 1],
            "bound": 3,
        },
        {
            "kind": "polyhedral_invariant",
            "places": ["eat_1", "think_1"],
            "coefficients": [1, -1],
            "bound": 0,
        },
    ]


def test_parse_polyhedral_invariants_rejects_malformed_line() -> None:
    """A places/coeffs length mismatch is a typed QuestionError, never a guess."""
    with pytest.raises(QuestionError):
        parse_polyhedral_invariants(stdout="INVARIANT places=a,b coeffs=1 bound=0\n")


def test_build_normalized_output_satisfied_with_witness() -> None:
    """A satisfied reachability with a trace yields a §16 trace witness."""
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    output = build_normalized_output(
        stdout="FORMULA query.smt2 TRUE\nStates: 14\n",
        stderr=stderr,
        model=MODEL,
        tool_seconds=0.5,
    )
    assert output["verdict"] == "satisfied"
    assert output["technique"] == "smpt-1.0"
    witness = output["witness"]
    assert isinstance(witness, dict)
    assert witness["kind"] == "trace"
    assert witness["steps"], "the SMT witness is replayed into §16 steps"


def test_build_normalized_output_violated_is_counterexample() -> None:
    """A FALSE verdict + trace yields a counterexample witness (spec §16)."""
    stderr = '<trace>\n<transition id="takeL_1" index="0">\n</transition>\n</trace>\n'
    output = build_normalized_output(
        stdout="FORMULA query.smt2 FALSE\n",
        stderr=stderr,
        model=MODEL,
        tool_seconds=0.3,
    )
    witness = output["witness"]
    assert isinstance(witness, dict)
    assert witness["kind"] == "counterexample"


def test_build_invariants_output_shape() -> None:
    """The invariants endpoint output is normalized and labeled with the technique."""
    output = build_invariants_output(
        stdout="INVARIANT places=fork_1 coeffs=1 bound=1\nStates: 0\n", tool_seconds=0.1
    )
    assert output["technique"] == "smpt-1.0"
    assert output["invariants"] == [
        {
            "kind": "polyhedral_invariant",
            "places": ["fork_1"],
            "coefficients": [1],
            "bound": 1,
        }
    ]


def test_handle_check_with_fake_binary_satisfied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end handler path against a fake SMPT binary (no container)."""
    _write_fake(tmp_path, monkeypatch, FAKE_SMPT_SATISFIED)
    question = _question(family="reachability", formula="EF (eat_1 >= 1)")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 200
    output = json.loads(payload.decode("utf-8"))
    assert output["verdict"] == "satisfied"
    assert output["technique"] == "smpt-1.0"
    assert output["witness"]["kind"] == "trace"


def test_handle_check_with_fake_binary_violated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A violated reachability carries the SMT counterexample replayed as §16 steps."""
    _write_fake(tmp_path, monkeypatch, FAKE_SMPT_VIOLATED)
    question = _question(family="reachability", formula="EF (eat_1 >= 2)")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 200
    output = json.loads(payload.decode("utf-8"))
    assert output["verdict"] == "violated"
    assert output["witness"]["kind"] == "counterexample"


def test_handle_check_rejects_ctl_family() -> None:
    """SMPT covers no CTL/LTL family: safety is a 422, never reinterpreted (spec §17.2)."""
    question = _question(family="safety", formula="AG (fork_1 <= 1)")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 422
    assert "no CTL/LTL" in json.loads(payload.decode("utf-8"))["detail"]


def test_handle_check_rejects_missing_model() -> None:
    """A question without the canonical model in parameters is a 422."""
    question = EngineQuestion(family="reachability", formula="EF true", pattern="reachability.ef")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 422
    assert "model" in json.loads(payload.decode("utf-8"))["detail"]


def test_handle_check_malformed_stdout_is_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exit-0 SMPT stdout without a FORMULA line is a typed 500, never a verdict."""
    _write_fake(tmp_path, monkeypatch, FAKE_SMPT_MALFORMED)
    question = _question(family="reachability", formula="EF (eat_1 >= 1)")
    body = json.dumps(question.model_dump(mode="json"))
    status, payload = handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 500
    assert "invalid" in json.loads(payload.decode("utf-8"))["detail"]


def test_handle_invariants_with_fake_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The /invariants endpoint answers the normalized polyhedral invariants."""
    _write_fake(tmp_path, monkeypatch, FAKE_SMPT_INVARIANTS)
    body = json.dumps({"parameters": {"model": MODEL}})
    status, payload = handle_invariants(body=body.encode("utf-8"), timeout_seconds=10)
    assert status == 200
    output = json.loads(payload.decode("utf-8"))
    assert output["technique"] == "smpt-1.0"
    assert len(output["invariants"]) == 2
    assert output["invariants"][1]["coefficients"] == [1, -1]


def test_handle_check_narration_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The handle_check DEBUG narration events are pinned (ADR-0005)."""
    _write_fake(tmp_path, monkeypatch, FAKE_SMPT_SATISFIED)
    question = _question(family="reachability", formula="EF (eat_1 >= 1)")
    body = json.dumps(question.model_dump(mode="json"))
    with caplog.at_level(logging.DEBUG, logger="petrichor.engines.smpt_server"):
        handle_check(body=body.encode("utf-8"), timeout_seconds=10)
    messages = [r.message for r in caplog.records]
    assert "smpt_check_received" in messages
    assert "smpt_question_parsed" in messages
    assert "smpt_run_completed" in messages


def test_handle_invariants_narration_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The handle_invariants DEBUG narration events are pinned (ADR-0005)."""
    _write_fake(tmp_path, monkeypatch, FAKE_SMPT_INVARIANTS)
    body = json.dumps({"parameters": {"model": MODEL}})
    with caplog.at_level(logging.DEBUG, logger="petrichor.engines.smpt_server"):
        handle_invariants(body=body.encode("utf-8"), timeout_seconds=10)
    messages = [r.message for r in caplog.records]
    assert "smpt_invariants_received" in messages
    assert "smpt_run_completed" in messages


def test_handle_health_returns_200() -> None:
    """The healthcheck handler answers 200 ok."""
    status, payload = handle_health()
    assert status == 200
    assert json.loads(payload.decode("utf-8")) == {"status": "ok"}


def test_smpt_narration_events(caplog: pytest.LogCaptureFixture) -> None:
    """The engine server's access-log event is pinned (ADR-0005)."""
    handler = SmptHandler.__new__(SmptHandler)
    with caplog.at_level(logging.DEBUG, logger="petrichor.engines.smpt_server"):
        handler.log_message("GET /health 200")
    assert any("smpt_engine_request" in r.message for r in caplog.records)


def test_default_pairing_matrix_lists_smpt_as_differential() -> None:
    """SMPT is the 3rd differential of reachability and safety (spec §17.4, issue #23)."""
    matrix = default_pairing_matrix()
    assert matrix.differential_for(family="reachability") == [SMPT_ENGINE_NAME]
    assert matrix.differential_for(family="safety") == [SMPT_ENGINE_NAME]
    assert matrix.differential_for(family="liveness") == [EngineName("ty")]


def test_smpt_check_runner_wiring(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The SMPT runner wires the smpt compose service with the pinned version."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    runner = SmptCheckRunner(archive=RunArchive(directory=tmp_path / "runs"))
    assert runner.tool_version == SMPT_ENGINE_VERSION
    # ADR-0006 escape: the driver's engine name is a private attribute; the
    # test reads it through Any to pin the compose wiring without a container.
    driver = cast("Any", runner)._driver
    assert driver._engine_name == SMPT_ENGINE_NAME
    assert driver._service_name == SMPT_SERVICE_NAME
    assert f"petrichor-engine-{SMPT_ENGINE_NAME}:{SMPT_ENGINE_VERSION}" == SMPT_ENGINE_IMAGE
