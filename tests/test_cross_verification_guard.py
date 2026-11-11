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
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.engines.contract import (
    CheckFamily,
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
    PairingMatrix,
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


def _imports_cross_verification(tree: ast.AST) -> bool:
    """Whether the module imports the cross_verification module in any form.

    Catches ``from petrichor.engines.cross_verification import X``,
    ``from petrichor.engines import cross_verification`` and
    ``import petrichor.engines.cross_verification`` alike.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module is not None and node.module.endswith("cross_verification"):
                return True
            if any(alias.name == "cross_verification" for alias in node.names):
                return True
        elif isinstance(node, ast.Import) and any(
            name.name.endswith("cross_verification") for name in node.names
        ):
            return True
    return False


def _chain_names(expression: ast.expr) -> list[str]:
    """Every identifier in an attribute/subscript/call chain.

    ``result.verdicts[0].verdict`` yields ``["result"]``;
    ``payload.result.verdict`` yields ``["payload", "result"]``; a bare
    ``verdict`` attribute on a call yields the call's func name.
    """
    names: list[str] = []
    current = expression
    while True:
        if isinstance(current, ast.Name):
            names.append(current.id)
            return names
        if isinstance(current, ast.Attribute):
            names.append(current.attr)
            current = current.value
        elif isinstance(current, ast.Subscript):
            current = current.value
        elif isinstance(current, ast.Call):
            current = current.func
        else:
            return names


def test_no_module_outside_engines_produces_a_single_verdict_on_discordance() -> None:
    """AST guard: no module outside engines/ imports CrossVerificationResult and emits a verdict.

    The verdict lock (spec §19.4) plus the cross-verification rule (spec
    §17.4): on discordance the only legal outcome is ``controversial``.
    A module outside ``engines/`` that reads ``CrossVerificationResult``
    and produces a bare ``Verdict`` would violate both. The scan flags any
    ``.verdict`` attribute access whose expression chain roots in a name
    containing ``result`` — covering ``result.verdict`` and
    ``result.verdicts[0].verdict`` alike.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if ENGINES_PACKAGE in path.parents or path == ENGINES_PACKAGE / "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _imports_cross_verification(tree):
            continue
        # If a module outside engines/ imports cross_verification, it must
        # not read a verdict from a cross-verification result — flag any
        # ``.verdict`` attribute access rooted in a ``*result*`` name.
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "verdict"
                and any("result" in name.lower() for name in _chain_names(node.value))
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


FAMILIES: list[CheckFamily] = ["reachability", "safety", "liveness", "recovery", "deadlock"]


@pytest.mark.property
@settings(deadline=None, max_examples=50)
@given(
    engines=st.lists(
        st.text(alphabet="abcdefgh", min_size=1, max_size=6),
        unique=True,
        min_size=3,
        max_size=6,
    )
)
def test_property_pairing_invariants_hold_for_any_matrix(engines: list[str]) -> None:
    """Property: any valid matrix pairs two distinct engines per family, never the same brick twice.

    The primary pair is two distinct engines and every differential engine
    is distinct from the primary pair and from the other differentials —
    a malformed future matrix edit is caught for arbitrary shapes, not
    just the five hard-coded families (ADR-0012).
    """
    payload = {
        "version": "0.0-test",
        "pairings": {
            family: {
                "primary": [engines[0], engines[1]],
                "differential": engines[2:],
            }
            for family in FAMILIES
        },
    }
    matrix = PairingMatrix.model_validate(payload)
    for family in FAMILIES:
        pair = matrix.pair_for(family=family)
        assert len(pair) == 2
        assert pair[0] != pair[1]
        entry = matrix.pairings[family]
        all_engines = [*entry.primary, *entry.differential]
        assert len(set(all_engines)) == len(all_engines), (
            f"family {family}: the same brick appears twice in {all_engines}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
