"""Guard tests for cross-verification (spec §17.4, issue #23).

Two invariants are pinned here:

1. **No single verdict on discordance** — no code path outside
   ``petrichor.engines`` may produce or select a verdict when engines
   disagree; the only allowed outcome is the ``controversial`` status
   reported to the engineer. An AST scan asserts that no module outside
   ``engines/`` imports the cross-verification result types and emits a
   bare verdict.

2. **The orchestrator never decides** — a property-style test drives the
   orchestrator with mock drivers that disagree persistently and asserts
   the result is ``controversial`` with ``verified_by == 0``, never a
   resolved verdict.
"""

import ast
from pathlib import Path

import pytest

from petrichor.engines.contract import (
    Cost,
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
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "petrichor"
ENGINES_PACKAGE = SRC_ROOT / "engines"


class _MockDriver:
    """A driver stub returning a fixed verdict, for orchestrator tests."""

    def __init__(self, *, engine: EngineName, verdict: Verdict) -> None:
        self._engine = engine
        self._verdict = verdict
        self.run_count = 0

    def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
        self.run_count += 1
        output = NormalizedOutput(
            verdict=self._verdict,
            witness=None,
            cost=Cost(tool_seconds=0.1, states_explored=1),
            technique="mock",
        )
        return RunRecord(
            run_id=RunId(f"run_mock{self.run_count:04d}"),
            engine=self._engine,
            engine_version=EngineVersion("0.0.0"),
            command=["mock"],
            timeout_seconds=timeout_seconds,
            input_hash=InputHash("sha256:mock"),
            output=output,
            measured_seconds=0.1,
            archived_at="2026-11-11T00:00:00+00:00",
        )


def test_no_module_outside_engines_produces_a_single_verdict_on_discordance() -> None:
    """AST guard: no module outside engines/ imports CrossVerificationResult and emits a verdict.

    The verdict lock (spec §19.4) plus the cross-verification rule (spec
    §17.4): on discordance the only legal outcome is ``controversial``.
    A module outside ``engines/`` that reads ``CrossVerificationResult``
    and produces a bare ``Verdict`` would violate both.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if ENGINES_PACKAGE in path.parents or path == ENGINES_PACKAGE / "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports_cross_verification = any(
            isinstance(node, ast.ImportFrom)
            and node.module is not None
            and "cross_verification" in node.module
            for node in ast.walk(tree)
        )
        if not imports_cross_verification:
            continue
        # If a module outside engines/ imports cross_verification, it must
        # not assign a bare Verdict from a result field — flag any
        # ``.verdict`` attribute access on a CrossVerificationResult.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "verdict"
                and isinstance(node.value, ast.Name)
                and "result" in node.value.id.lower()
            ):
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, (
        "modules outside engines/ must not read a verdict from a cross-verification"
        f" result (spec §17.4, §19.4): {offenders}"
    )


def test_persistent_discordance_yields_controversial_never_a_resolved_verdict() -> None:
    """The orchestrator never decides: persistent discordance → controversial, verified_by=0."""
    pairing = default_pairing_matrix()
    drivers = {
        EngineName("verifypn"): _MockDriver(engine=EngineName("verifypn"), verdict="satisfied"),
        EngineName("its-tools"): _MockDriver(engine=EngineName("its-tools"), verdict="violated"),
    }
    orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
    question = EngineQuestion(
        family="reachability",
        formula="EF (eat_1 > 0)",
        pattern="reachability.ef",
        parameters={},
    )
    result = orchestrator.verify(question=question, timeout_seconds=5)
    assert result.status == "controversial"
    assert result.verified_by == 0
    assert result.arbitration.status == "persistent"
    assert result.arbitration.verdict is None
    assert len(result.verdicts) == 2
    assert {v.verdict for v in result.verdicts} == {"satisfied", "violated"}


def test_concordance_yields_concordant_with_all_verdicts() -> None:
    """When both engines agree, the status is concordant and verified_by=2."""
    pairing = default_pairing_matrix()
    drivers = {
        EngineName("verifypn"): _MockDriver(engine=EngineName("verifypn"), verdict="satisfied"),
        EngineName("its-tools"): _MockDriver(engine=EngineName("its-tools"), verdict="satisfied"),
    }
    orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
    question = EngineQuestion(
        family="deadlock",
        formula="EF deadlock",
        pattern="deadlock",
        parameters={},
    )
    result = orchestrator.verify(question=question, timeout_seconds=5)
    assert result.status == "concordant"
    assert result.verified_by == 2
    assert result.concordance == 1.0
    assert result.arbitration.status == "not_needed"


def test_arbitration_resolved_yields_concordant() -> None:
    """Discordance then arbitration agreement → concordant with the arbitration verdict."""

    class _FlakyDriver:
        """Disagrees on the first run, agrees on the arbitration re-run."""

        def __init__(
            self, *, engine: EngineName, first_verdict: Verdict, second_verdict: Verdict
        ) -> None:
            self._engine = engine
            self._first = first_verdict
            self._second = second_verdict
            self.run_count = 0

        def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
            self.run_count += 1
            verdict = self._first if self.run_count == 1 else self._second
            output = NormalizedOutput(
                verdict=verdict,
                witness=None,
                cost=Cost(tool_seconds=0.1, states_explored=1),
                technique="mock",
            )
            return RunRecord(
                run_id=RunId(f"run_flaky{self.run_count:04d}"),
                engine=self._engine,
                engine_version=EngineVersion("0.0.0"),
                command=["mock"],
                timeout_seconds=timeout_seconds,
                input_hash=InputHash("sha256:mock"),
                output=output,
                measured_seconds=0.1,
                archived_at="2026-11-11T00:00:00+00:00",
            )

    pairing = default_pairing_matrix()
    drivers = {
        EngineName("verifypn"): _FlakyDriver(
            engine=EngineName("verifypn"), first_verdict="satisfied", second_verdict="satisfied"
        ),
        EngineName("its-tools"): _FlakyDriver(
            engine=EngineName("its-tools"), first_verdict="violated", second_verdict="satisfied"
        ),
    }
    orchestrator = CrossVerificationOrchestrator(pairing=pairing, drivers=drivers)
    question = EngineQuestion(
        family="safety",
        formula="AG (eat_1 <= 1)",
        pattern="safety.invariant",
        parameters={},
    )
    result = orchestrator.verify(question=question, timeout_seconds=5)
    assert result.status == "concordant"
    assert result.arbitration.status == "resolved"
    assert result.arbitration.verdict == "satisfied"


def test_pairing_matrix_default_has_two_engines_per_family() -> None:
    """The default pairing matrix pairs exactly two engines per family (spec §17.4)."""
    matrix = default_pairing_matrix()
    for family in ("reachability", "safety", "liveness", "recovery", "deadlock"):
        pair = matrix.pair_for(family=family)
        assert len(pair) == 2
        assert pair[0] != pair[1], f"family {family}: the pair must be two distinct engines"


def test_pairing_matrix_never_pairs_same_brick_twice() -> None:
    """Guard: the pairing matrix never pairs two instances of the same brick (spec §17.4)."""
    matrix = default_pairing_matrix()
    for family, entry in matrix.pairings.items():
        assert entry.primary[0] != entry.primary[1], (
            f"family {family}: primary pair {entry.primary} repeats the same engine"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
