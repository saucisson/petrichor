"""Unit tests for the cross-verification orchestrator (spec §17.4, issue #23)."""

import json
from typing import TYPE_CHECKING

import pytest
import structlog.testing

if TYPE_CHECKING:
    from pathlib import Path

from petrichor.engines.contract import (
    CheckFamily,
    Cost,
    EngineError,
    EngineName,
    EngineQuestion,
    EngineVersion,
    InputHash,
    NormalizedOutput,
    RunId,
    RunRecord,
    Verdict,
)
from petrichor.engines.cross_verification import (
    CrossVerificationOrchestrator,
    default_pairing_matrix,
    load_pairing_matrix,
)
from petrichor.engines.driver import EngineDriver


class _StubDriver(EngineDriver):
    """A driver stub returning a scripted sequence of verdicts."""

    def __init__(self, *, engine: EngineName, verdicts: list[Verdict]) -> None:
        self._engine = engine
        self._verdicts = list(verdicts)
        self.run_count = 0

    def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
        verdict = self._verdicts[min(self.run_count, len(self._verdicts) - 1)]
        self.run_count += 1
        output = NormalizedOutput(
            verdict=verdict,
            witness=None,
            cost=Cost(tool_seconds=0.5, states_explored=10),
            technique="stub",
        )
        return RunRecord(
            run_id=RunId(f"run_stub{self.run_count:04d}"),
            engine=self._engine,
            engine_version=EngineVersion("1.0.0"),
            command=["stub", "run"],
            timeout_seconds=timeout_seconds,
            input_hash=InputHash("sha256:stub"),
            output=output,
            measured_seconds=0.5,
            archived_at="2026-11-11T00:00:00+00:00",
        )


def _question(
    *, family: CheckFamily = "reachability", formula: str = "EF (eat_1 > 0)"
) -> EngineQuestion:
    return EngineQuestion(family=family, formula=formula, pattern="reachability.ef", parameters={})


class TestPairingMatrix:
    def test_default_matrix_loads(self) -> None:
        matrix = default_pairing_matrix()
        assert matrix.version == "1.0"
        assert set(matrix.pairings) == {
            "reachability",
            "safety",
            "liveness",
            "recovery",
            "deadlock",
        }

    def test_pair_for_returns_two_distinct_engines(self) -> None:
        matrix = default_pairing_matrix()
        for family in matrix.pairings:
            pair = matrix.pair_for(family=family)
            assert len(pair) == 2
            assert pair[0] != pair[1]

    def test_load_from_custom_path(self, tmp_path: Path) -> None:
        payload = {
            "version": "2.0",
            "description": "custom",
            "pairings": {
                "reachability": {"primary": ["a", "b"], "differential": ["c"], "note": "test"},
                "safety": {"primary": ["a", "b"], "differential": [], "note": ""},
                "liveness": {"primary": ["a", "b"], "differential": [], "note": ""},
                "recovery": {"primary": ["a", "b"], "differential": [], "note": ""},
                "deadlock": {"primary": ["a", "b"], "differential": [], "note": ""},
            },
        }
        path = tmp_path / "pairing.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        matrix = load_pairing_matrix(path=path)
        assert matrix.version == "2.0"
        assert matrix.differential_for(family="reachability") == [EngineName("c")]

    def test_load_malformed_raises_engine_error(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(EngineError, match="cannot read"):
            load_pairing_matrix(path=path)

    def test_load_missing_file_raises_engine_error(self, tmp_path: Path) -> None:
        with pytest.raises(EngineError, match="cannot read"):
            load_pairing_matrix(path=tmp_path / "missing.json")

    def test_unconfigured_family_raises_typed_engine_error(self, tmp_path: Path) -> None:
        payload = {
            "version": "2.0",
            "pairings": {
                "reachability": {"primary": ["a", "b"]},
            },
        }
        path = tmp_path / "pairing.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        matrix = load_pairing_matrix(path=path)
        with pytest.raises(EngineError, match="no pairing configured for family 'safety'"):
            matrix.pair_for(family="safety")
        with pytest.raises(EngineError, match="no pairing configured for family 'safety'"):
            matrix.differential_for(family="safety")


class TestOrchestratorConcordance:
    def test_both_engines_agree_satisfied(self) -> None:
        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _StubDriver(
                engine=EngineName("verifypn"), verdicts=["satisfied"]
            ),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["satisfied"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "concordant"
        assert result.concordance == 1.0
        assert result.verified_by == 2
        assert result.arbitration.status == "not_needed"
        assert len(result.verdicts) == 2
        assert all(v.verdict == "satisfied" for v in result.verdicts)

    def test_both_engines_agree_violated(self) -> None:
        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _StubDriver(
                engine=EngineName("verifypn"), verdicts=["violated"]
            ),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["violated"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        result = orchestrator.verify(
            question=_question(family="deadlock", formula="EF deadlock"), timeout_seconds=5
        )
        assert result.status == "concordant"
        assert result.verified_by == 2
        assert all(v.verdict == "violated" for v in result.verdicts)


class TestOrchestratorDiscordance:
    def test_persistent_discordance_is_controversial(self) -> None:
        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _StubDriver(
                engine=EngineName("verifypn"), verdicts=["satisfied", "satisfied"]
            ),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["violated", "violated"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "controversial"
        assert result.concordance == 0.0
        assert result.verified_by == 0
        assert result.arbitration.status == "persistent"
        assert result.arbitration.verdict is None
        # Both initial runs + both arbitration runs happened
        assert drivers[EngineName("verifypn")].run_count == 2
        assert drivers[EngineName("its-tools")].run_count == 2

    def test_arbitration_resolves_discordance(self) -> None:
        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _StubDriver(
                engine=EngineName("verifypn"), verdicts=["satisfied", "satisfied"]
            ),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["violated", "satisfied"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "concordant"
        assert result.concordance == 1.0
        assert result.arbitration.status == "resolved"
        assert result.arbitration.verdict == "satisfied"

    def test_unknown_verdict_counts_as_discordance(self) -> None:
        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _StubDriver(
                engine=EngineName("verifypn"), verdicts=["satisfied", "satisfied"]
            ),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["unknown", "unknown"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "controversial"
        assert result.verified_by == 0


class TestOrchestratorErrors:
    def test_missing_driver_raises_engine_error(self) -> None:
        pairing = default_pairing_matrix()
        drivers: dict[EngineName, _StubDriver] = {}  # no drivers wired
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        with pytest.raises(EngineError, match="no driver wired"):
            orchestrator.verify(question=_question(), timeout_seconds=5)

    def test_driver_error_propagates(self) -> None:
        class _FailingDriver:
            def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
                raise EngineError("engine exploded")

        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _FailingDriver(),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["satisfied"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        with pytest.raises(EngineError, match="engine exploded"):
            orchestrator.verify(question=_question(), timeout_seconds=5)


class TestResultSerialization:
    def test_result_is_json_serializable(self) -> None:
        pairing = default_pairing_matrix()
        drivers = {
            EngineName("verifypn"): _StubDriver(
                engine=EngineName("verifypn"), verdicts=["satisfied"]
            ),
            EngineName("its-tools"): _StubDriver(
                engine=EngineName("its-tools"), verdicts=["satisfied"]
            ),
        }
        orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
        result = orchestrator.verify(question=_question(), timeout_seconds=5)
        payload = json.loads(result.model_dump_json())
        assert payload["status"] == "concordant"
        assert payload["verified_by"] == 2
        assert len(payload["verdicts"]) == 2


def _stub_drivers(*, verdicts: dict[str, list[Verdict]]) -> dict[EngineName, _StubDriver]:
    return {
        EngineName(name): _StubDriver(engine=EngineName(name), verdicts=script)
        for name, script in verdicts.items()
    }


class TestNarration:
    """The module's log events are part of the narration contract: pinned here (ADR-0005)."""

    def test_matrix_loading_is_narrated(self, tmp_path: Path) -> None:
        path = tmp_path / "pairing.json"
        path.write_text(
            json.dumps(
                {
                    "version": "9.9",
                    "pairings": {
                        family: {"primary": ["a", "b"]}
                        for family in default_pairing_matrix().pairings
                    },
                }
            ),
            encoding="utf-8",
        )
        with structlog.testing.capture_logs() as events:
            matrix = load_pairing_matrix(path=path)
        loading = [event for event in events if event["event"] == "pairing_matrix_loading"]
        assert len(loading) == 1
        assert loading[0]["path"] == str(path)
        loaded = [event for event in events if event["event"] == "pairing_matrix_loaded"]
        assert len(loaded) == 1
        assert loaded[0]["version"] == matrix.version == "9.9"

    def test_concordance_path_is_narrated(self) -> None:
        orchestrator = CrossVerificationOrchestrator(
            pairing=default_pairing_matrix(),
            drivers=_stub_drivers(verdicts={"verifypn": ["satisfied"], "its-tools": ["satisfied"]}),
        )
        with structlog.testing.capture_logs() as events:
            result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "concordant"
        started = [event for event in events if event["event"] == "cross_verification_started"]
        assert len(started) == 1
        assert started[0]["family"] == "reachability"
        assert started[0]["pair"] == ["verifypn", "its-tools"]
        engine_runs = [
            event for event in events if event["event"] == "cross_verification_engine_run"
        ]
        assert [event["engine"] for event in engine_runs] == ["verifypn", "its-tools"]
        assert all(event["verdict"] == "satisfied" for event in engine_runs)
        concordant = [
            event for event in events if event["event"] == "cross_verification_concordant"
        ]
        assert len(concordant) == 1
        assert concordant[0]["verdict"] == "satisfied"
        assert concordant[0]["verified_by"] == 2
        assert not [event for event in events if "arbitration" in event["event"]]

    def test_arbitration_resolved_path_is_narrated(self) -> None:
        orchestrator = CrossVerificationOrchestrator(
            pairing=default_pairing_matrix(),
            drivers=_stub_drivers(
                verdicts={
                    "verifypn": ["satisfied", "satisfied"],
                    "its-tools": ["violated", "satisfied"],
                }
            ),
        )
        with structlog.testing.capture_logs() as events:
            result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "concordant"
        arbitration_started = [
            event for event in events if event["event"] == "cross_verification_arbitration_started"
        ]
        assert len(arbitration_started) == 1
        assert arbitration_started[0]["pair"] == ["verifypn", "its-tools"]
        resolved = [
            event for event in events if event["event"] == "cross_verification_arbitration_resolved"
        ]
        assert len(resolved) == 1
        assert resolved[0]["verdict"] == "satisfied"
        after = [
            event
            for event in events
            if event["event"] == "cross_verification_concordant_after_arbitration"
        ]
        assert len(after) == 1
        assert after[0]["verdict"] == "satisfied"

    def test_persistent_discordance_path_is_narrated(self) -> None:
        orchestrator = CrossVerificationOrchestrator(
            pairing=default_pairing_matrix(),
            drivers=_stub_drivers(
                verdicts={
                    "verifypn": ["satisfied", "satisfied"],
                    "its-tools": ["violated", "violated"],
                }
            ),
        )
        with structlog.testing.capture_logs() as events:
            result = orchestrator.verify(question=_question(), timeout_seconds=5)
        assert result.status == "controversial"
        persistent = [
            event
            for event in events
            if event["event"] == "cross_verification_arbitration_persistent"
        ]
        assert len(persistent) == 1
        assert persistent[0]["first_engine"] == "verifypn"
        assert persistent[0]["first_verdict"] == "satisfied"
        assert persistent[0]["second_engine"] == "its-tools"
        assert persistent[0]["second_verdict"] == "violated"
        controversial = [
            event for event in events if event["event"] == "cross_verification_controversial"
        ]
        assert len(controversial) == 1
        assert controversial[0]["family"] == "reachability"
        assert controversial[0]["verdicts"] == [
            ("verifypn", "satisfied"),
            ("its-tools", "violated"),
        ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
