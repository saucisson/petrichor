"""Tests of the MCC oracle qualification (issue #22, spec §17.5, §19.4).

The unit tests pin the registry, the replay comparison, the per-family
qualification rule and the application guard (a clause never rests on a
single unqualified adapter). The ``@engine`` test replays the bounded MCC
sample through the real verifypn container: an engine failure is a test
failure - a CI failure, never a skip. A mechanical guard test scans every
``@engine`` test and fails if one of them skips.
"""

import ast
import json
from pathlib import Path

import pytest

from petrichor.engines.archive import RunArchive
from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    VERIFYPN_ENGINE_NAME,
    VERIFYPN_ENGINE_VERSION,
    Cost,
    DriverError,
    EngineError,
    EngineName,
    EngineQuestion,
    NormalizedOutput,
    RunRecord,
    Witness,
    WitnessStep,
)
from petrichor.engines.driver import ComposeEngineDriver
from petrichor.engines.qualification import (
    EngineQualificationLookup,
    MccOracle,
    QualificationRegistry,
    UnqualifiedSoleSupportError,
    check_clause_support,
    load_oracles,
    qualify_engine,
    replay_oracle,
)

ORACLES_PATH = Path(__file__).parent / "mcc_oracles.json"
TESTS_DIR = Path(__file__).parent
ALL_FAMILIES = {"reachability", "safety", "liveness", "recovery", "deadlock"}


def _oracle(
    *, family: str = "deadlock", expected_verdict: str = "violated", formula: str = "EF DEADLOCK"
) -> MccOracle:
    """One oracle on a minimal model payload."""
    return MccOracle(
        instance_id="test-instance",
        family=family,  # type: ignore[arg-type]
        formula=formula,
        expected_verdict=expected_verdict,  # type: ignore[arg-type]
        model={"places": []},
    )


def _record(*, verdict: str, question: EngineQuestion) -> RunRecord:
    """A run record carrying the given verdict for the given question."""
    return RunRecord(
        run_id="run_test00",
        engine=EngineName("fake"),
        engine_version="0.0.0",
        command=["fake"],
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        input_hash="sha256:00",
        output=NormalizedOutput(
            verdict=verdict,  # type: ignore[arg-type]
            witness=Witness(
                kind="counterexample",
                cyclic=False,
                back_edge=None,
                steps=[WitnessStep(index=0, marking=[], fired=None)],
            ),
            cost=Cost(tool_seconds=0.1, states_explored=1),
            technique="fake",
        ),
        measured_seconds=0.1,
        archived_at="2026-11-11T00:00:00+00:00",
    )


class FakeDriver:
    """A driver serving queued verdicts; records every question it received."""

    def __init__(self, verdicts: list[str]) -> None:
        self.verdicts = list(verdicts)
        self.questions: list[EngineQuestion] = []

    def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
        """Serve the next queued verdict."""
        self.questions.append(question)
        return _record(verdict=self.verdicts.pop(0), question=question)


class FailingDriver:
    """A driver whose engine always fails: the error must propagate."""

    def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
        """Raise a driver error, like a crashed engine container."""
        raise DriverError("engine container crashed")


class TestLoadOracles:
    def test_sample_covers_every_family_with_the_model_injected(self) -> None:
        oracles = load_oracles(path=ORACLES_PATH)
        assert {oracle.family for oracle in oracles} == ALL_FAMILIES
        assert all(oracle.model for oracle in oracles)
        assert len(oracles) == 6

    def test_malformed_sample_is_a_typed_error(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"oracles": []}), encoding="utf-8")
        with pytest.raises(EngineError, match="malformed MCC oracle sample"):
            load_oracles(path=bad)


class TestQualificationRegistry:
    def test_unknown_adapter_is_unqualified(self) -> None:
        registry = QualificationRegistry()
        assert registry.is_qualified(engine=EngineName("verifypn"), family="deadlock") is False

    def test_record_then_qualified_and_snapshot(self) -> None:
        registry = QualificationRegistry()
        registry.record(engine=EngineName("verifypn"), family="deadlock", state="qualified")
        assert registry.is_qualified(engine=EngineName("verifypn"), family="deadlock") is True
        assert registry.is_qualified(engine=EngineName("verifypn"), family="safety") is False
        assert registry.snapshot() == {"verifypn": {"deadlock": "qualified"}}

    def test_registry_persists_and_reloads(self, tmp_path: Path) -> None:
        registry = QualificationRegistry(directory=tmp_path)
        registry.record(engine=EngineName("verifypn"), family="liveness", state="qualified")
        assert (tmp_path / "qualification.json").exists()
        reloaded = QualificationRegistry(directory=tmp_path)
        assert reloaded.is_qualified(engine=EngineName("verifypn"), family="liveness") is True
        assert reloaded.snapshot() == {"verifypn": {"liveness": "qualified"}}


class TestReplayOracle:
    def test_matching_verdict_reproduces(self) -> None:
        driver = FakeDriver(verdicts=["violated"])
        assert replay_oracle(driver=driver, oracle=_oracle()) is True
        assert driver.questions[0].parameters["model"] == {"places": []}

    def test_mismatched_verdict_does_not_reproduce(self) -> None:
        driver = FakeDriver(verdicts=["satisfied"])
        assert replay_oracle(driver=driver, oracle=_oracle()) is False

    def test_engine_failure_propagates_never_swallowed(self) -> None:
        with pytest.raises(DriverError, match="engine container crashed"):
            replay_oracle(driver=FailingDriver(), oracle=_oracle())


class TestQualifyEngine:
    def test_qualified_only_when_every_oracle_of_the_family_replays(self, tmp_path: Path) -> None:
        registry = QualificationRegistry(directory=tmp_path)
        oracles = [
            _oracle(family="safety", expected_verdict="satisfied", formula="AG (eat_1 <= 1)"),
            _oracle(family="safety", expected_verdict="violated", formula="AG (eat_1 <= 0)"),
            _oracle(family="deadlock", expected_verdict="violated"),
        ]
        driver = FakeDriver(verdicts=["satisfied", "unknown", "violated"])
        states = qualify_engine(
            driver=driver, engine=EngineName("fake"), oracles=oracles, registry=registry
        )
        assert states == {"safety": "unqualified", "deadlock": "qualified"}
        assert registry.is_qualified(engine=EngineName("fake"), family="deadlock") is True
        assert registry.is_qualified(engine=EngineName("fake"), family="safety") is False


class TestClauseSupportGuard:
    def test_sole_unqualified_adapter_is_rejected(self) -> None:
        registry = QualificationRegistry()
        with pytest.raises(UnqualifiedSoleSupportError, match="cannot rest on it alone"):
            check_clause_support(
                registry=registry,
                engine=EngineName("dummy"),
                family="deadlock",
                corroborating_adapters=0,
            )

    def test_qualified_adapter_alone_is_accepted(self) -> None:
        registry = QualificationRegistry()
        registry.record(engine=EngineName("verifypn"), family="deadlock", state="qualified")
        check_clause_support(
            registry=registry,
            engine=EngineName("verifypn"),
            family="deadlock",
            corroborating_adapters=0,
        )

    def test_corroborated_unqualified_adapter_is_accepted(self) -> None:
        registry = QualificationRegistry()
        check_clause_support(
            registry=registry,
            engine=EngineName("dummy"),
            family="deadlock",
            corroborating_adapters=1,
        )


class TestEngineQualificationLookup:
    def test_lookup_reads_the_registry_for_its_engine(self) -> None:
        registry = QualificationRegistry()
        registry.record(engine=EngineName("verifypn"), family="safety", state="qualified")
        lookup = EngineQualificationLookup(registry=registry, engine=EngineName("verifypn"))
        assert lookup.is_qualified(family="safety") is True
        assert lookup.is_qualified(family="deadlock") is False
        assert lookup.snapshot() == {"verifypn": {"safety": "qualified"}}


def _engine_marked_functions() -> list[tuple[Path, ast.FunctionDef]]:
    """Every test function decorated with ``@pytest.mark.engine`` under tests/."""
    marked: list[tuple[Path, ast.FunctionDef]] = []
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Attribute)
                    and decorator.attr == "engine"
                    and isinstance(decorator.value, ast.Attribute)
                    and decorator.value.attr == "mark"
                ):
                    marked.append((path, node))
    return marked


def test_engine_suite_is_never_empty() -> None:
    """Positive control: the scan actually finds the @engine tests."""
    assert len(_engine_marked_functions()) >= 1


def test_no_engine_test_ever_skips() -> None:
    """An engine failure is a CI failure, never a skip (spec §19.4): no @engine
    test may call pytest.skip or carry a skipif marker."""
    offenders: list[str] = []
    for path, node in _engine_marked_functions():
        for child in ast.walk(node):
            is_pytest_skip_call = (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr in {"skip", "skipif"}
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "pytest"
            )
            if (
                is_pytest_skip_call
                and isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
            ):
                offenders.append(f"{path.name}:{node.name} calls pytest.{child.func.attr}")
            if isinstance(child, ast.Attribute) and child.attr == "skipif":
                offenders.append(f"{path.name}:{node.name} carries a skipif marker")
    assert offenders == []


@pytest.mark.engine
def test_mcc_sample_replays_on_verifypn_in_the_real_container(tmp_path: Path) -> None:
    """The bounded MCC sample replays on the real verifypn container and qualifies it.

    Every oracle of the sample must reproduce its expected verdict; the
    registry then marks verifypn qualified for each covered family. An engine
    failure fails this test - it is never a skip.
    """
    oracles = load_oracles(path=ORACLES_PATH)
    archive = RunArchive(directory=tmp_path / "runs")
    driver = ComposeEngineDriver(
        archive=archive,
        host_port=18081,
        service_name="verifypn",
        engine_name=VERIFYPN_ENGINE_NAME,
        engine_version=VERIFYPN_ENGINE_VERSION,
    )
    registry = QualificationRegistry(directory=tmp_path / "qualification")
    states = qualify_engine(
        driver=driver, engine=VERIFYPN_ENGINE_NAME, oracles=oracles, registry=registry
    )
    assert set(states) == ALL_FAMILIES
    assert all(state == "qualified" for state in states.values()), states
    assert len(archive) == len(oracles)
    reloaded = QualificationRegistry(directory=tmp_path / "qualification")
    assert reloaded.is_qualified(engine=VERIFYPN_ENGINE_NAME, family="deadlock") is True
