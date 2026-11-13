"""Tests for the static/MC triage and non-vacuity guards (issue #14, spec §14.2, §14.4).

The triage is computed from the pattern family and the current capacity:
static only for the three static targets within capacity, explicitly degraded
otherwise, MC for everything else. The mini-engine adapters (capacity probe,
static resolution, guard) are pinned against the Philosophes-3 corpus.
"""

import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from philosophes3 import build_philosophes3

from petrichor.checks import FAMILY_PATTERNS, CheckQuestion, KindPattern
from petrichor.engines.mini_engine import (
    MAX_PLACES_FOR_INVARIANT_ENUMERATION,
    MAX_REACHABLE_STATES,
    mini_capacity,
    non_vacuity_guard,
    resolve_static_payload,
)
from petrichor.petri_canonical import build_canonical_model
from petrichor.triage import (
    STATIC_KINDS,
    CapacityReport,
    StaticUndecidableError,
    triage_question,
)

FIXTURES = Path(__file__).parent / "fixtures"
SYMMETRIC_PNML = (FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")

ALL_KINDS = sorted({kind for patterns in FAMILY_PATTERNS.values() for kind in patterns})


def capacity(*, place_count: int, state_count: int | None) -> CapacityReport:
    """A capacity report with the engine's real caps."""
    return CapacityReport(
        place_count=place_count,
        max_places_for_invariants=MAX_PLACES_FOR_INVARIANT_ENUMERATION,
        state_count=state_count,
        max_states=MAX_REACHABLE_STATES,
    )


def big_model(place_count: int) -> object:
    """A P/T model with ``place_count`` places and one self-looping transition."""
    places = [
        {
            "id": f"pl_{index:03d}",
            "name": f"p_{index}",
            "initial_marking": 1 if index == 0 else 0,
            "layout": {"x": index, "y": 0},
            "annotations": {},
        }
        for index in range(place_count)
    ]
    return build_canonical_model(
        payload={
            "id": "mdl_big",
            "formalism": "pt",
            "places": places,
            "transitions": [
                {
                    "id": "tr_000",
                    "name": "loop",
                    "layout": {"x": 0, "y": 100},
                    "annotations": {},
                }
            ],
            "arcs": [
                {
                    "id": "arc_in",
                    "source": "pl_000",
                    "target": "tr_000",
                    "weight": 1,
                    "annotations": {},
                },
                {
                    "id": "arc_out",
                    "source": "tr_000",
                    "target": "pl_000",
                    "weight": 1,
                    "annotations": {},
                },
            ],
            "metadata": {},
        }
    )


class TestTriageQuestion:
    def test_static_kind_within_capacity_is_statique(self) -> None:
        triage = triage_question(
            kind=KindPattern("safety.invariant"), capacity=capacity(place_count=9, state_count=14)
        )
        assert triage.kind == "statique"
        assert "spec §14.2" in triage.justification

    def test_non_static_kind_is_mc_even_within_capacity(self) -> None:
        triage = triage_question(
            kind=KindPattern("deadlock"), capacity=capacity(place_count=9, state_count=14)
        )
        assert triage.kind == "MC"
        assert "run MC" in triage.justification

    def test_static_kind_without_capacity_probe_is_degraded(self) -> None:
        triage = triage_question(kind=KindPattern("safety.conservation"), capacity=None)
        assert triage.kind == "dégradé"
        assert "preuve statique non trouvée" in triage.justification.lower()

    def test_static_kind_beyond_invariant_cap_is_degraded(self) -> None:
        triage = triage_question(
            kind=KindPattern("safety.state_invariant"),
            capacity=capacity(place_count=MAX_PLACES_FOR_INVARIANT_ENUMERATION + 1, state_count=14),
        )
        assert triage.kind == "dégradé"
        assert "déclassée MC" in triage.justification

    def test_static_kind_beyond_state_cap_is_degraded(self) -> None:
        triage = triage_question(
            kind=KindPattern("safety.invariant"), capacity=capacity(place_count=9, state_count=None)
        )
        assert triage.kind == "dégradé"


class TestTriageProperties:
    """Property tier (ADR-0012): the triage rule holds over generated inputs."""

    @pytest.mark.property
    @settings(deadline=None)
    @given(
        kind=st.sampled_from([*ALL_KINDS, "junk.kind"]),
        place_count=st.integers(min_value=0, max_value=30),
        state_count=st.one_of(st.none(), st.integers(min_value=1, max_value=20_000)),
        probed=st.booleans(),
    )
    def test_property_triage_follows_family_and_capacity(
        self, kind: str, place_count: int, state_count: int | None, probed: bool
    ) -> None:
        cap = capacity(place_count=place_count, state_count=state_count) if probed else None
        triage = triage_question(kind=KindPattern(kind), capacity=cap)
        assert triage.kind in {"statique", "MC", "dégradé"}
        assert triage.justification
        if kind not in STATIC_KINDS:
            assert triage.kind == "MC"
        elif cap is None or not cap.within_static_capacity:
            assert triage.kind == "dégradé"
        else:
            assert triage.kind == "statique"

    @pytest.mark.property
    @settings(deadline=None)
    @given(
        place_count=st.integers(min_value=0, max_value=30),
        state_count=st.one_of(st.none(), st.integers(min_value=1, max_value=20_000)),
    )
    def test_property_capacity_flags_are_exact(
        self, place_count: int, state_count: int | None
    ) -> None:
        cap = capacity(place_count=place_count, state_count=state_count)
        assert cap.within_invariant_capacity == (
            place_count <= MAX_PLACES_FOR_INVARIANT_ENUMERATION
        )
        assert cap.within_state_capacity == (state_count is not None)
        assert cap.within_static_capacity == (
            cap.within_invariant_capacity and cap.within_state_capacity
        )


class TestMiniEngineAdapters:
    def test_capacity_probe_on_philosophes3(self) -> None:
        report = mini_capacity(model=build_philosophes3())
        assert report.place_count == 12
        assert report.state_count is not None and report.state_count > 1
        assert report.within_static_capacity

    def test_capacity_probe_beyond_invariant_cap(self) -> None:
        report = mini_capacity(model=big_model(MAX_PLACES_FOR_INVARIANT_ENUMERATION + 1))
        assert not report.within_invariant_capacity
        assert not report.within_static_capacity

    def test_static_resolution_true_and_false(self) -> None:
        model = build_philosophes3()
        true_question = CheckQuestion(
            family="safety", kind=KindPattern("safety.invariant"), formula="AG (eat_1 <= 1)"
        )
        payload = resolve_static_payload(model=model, question=true_question)
        assert payload["verdict"] == "true"
        assert payload["technique"] == "static"
        assert payload["witness"] is None
        false_question = CheckQuestion(
            family="safety", kind=KindPattern("safety.invariant"), formula="AG (eat_1 <= 0)"
        )
        payload = resolve_static_payload(model=model, question=false_question)
        assert payload["verdict"] == "false"
        assert payload["technique"] == "static"
        assert payload["witness"] is not None

    def test_static_resolution_rejects_non_static_kind(self) -> None:
        with pytest.raises(StaticUndecidableError, match="static targets"):
            resolve_static_payload(
                model=build_philosophes3(),
                question=CheckQuestion(
                    family="deadlock", kind=KindPattern("deadlock"), formula="EF DEADLOCK"
                ),
            )

    def test_static_resolution_rejects_formula_outside_the_fragment(self) -> None:
        with pytest.raises(StaticUndecidableError, match="static fragment"):
            resolve_static_payload(
                model=build_philosophes3(),
                question=CheckQuestion(
                    family="safety",
                    kind=KindPattern("safety.invariant"),
                    formula="AG (think_1 == 0 or EF (eat_1 > 0))",
                ),
            )

    def test_static_resolution_rejects_unknown_place(self) -> None:
        with pytest.raises(StaticUndecidableError, match="Unknown place"):
            resolve_static_payload(
                model=build_philosophes3(),
                question=CheckQuestion(
                    family="safety",
                    kind=KindPattern("safety.invariant"),
                    formula="AG (nope_1 <= 1)",
                ),
            )

    def test_guard_ef_phi_ok_and_vacuous(self) -> None:
        model = build_philosophes3()
        ok = non_vacuity_guard(
            model=model,
            family="reachability",
            kind=KindPattern("reachability.ef"),
            formula="EF (eat_1 > 0)",
        )
        assert ok.guard == "ef_phi"
        assert ok.status == "ok"
        vacuous = non_vacuity_guard(
            model=model,
            family="reachability",
            kind=KindPattern("reachability.ef"),
            formula="EF (think_1 > 0)",
        )
        assert vacuous.guard == "ef_phi"
        assert vacuous.status == "vacuous"

    def test_guard_ag_psi_ok_and_vacuous(self) -> None:
        model = build_philosophes3()
        ok = non_vacuity_guard(
            model=model,
            family="safety",
            kind=KindPattern("safety.invariant"),
            formula="AG (eat_1 <= 1)",
        )
        assert ok.guard == "ag_psi"
        assert ok.status == "ok"
        vacuous = non_vacuity_guard(
            model=model,
            family="safety",
            kind=KindPattern("safety.invariant"),
            formula="AG (think_1 <= 0)",
        )
        assert vacuous.guard == "ag_psi"
        assert vacuous.status == "vacuous"

    def test_guard_not_applicable_outside_the_fragment(self) -> None:
        model = build_philosophes3()
        for family, kind, formula in (
            ("deadlock", "deadlock", "EF DEADLOCK"),
            ("liveness", "liveness.unconditional", "AG EF (eat_1 > 0)"),
            ("recovery", "recovery.unconditional", "AG (think_1 == 0 or EF (eat_1 > 0))"),
        ):
            guard = non_vacuity_guard(
                model=model,
                family=family,
                kind=KindPattern(kind),
                formula=formula,
            )
            assert guard.status == "not_applicable"
            assert guard.detail

    def test_guards_over_the_mcc_corpus(self) -> None:
        """Every corpus oracle formula gets its guard computed on Philosophes-3."""
        model = build_philosophes3()
        oracles = json.loads((Path(__file__).parent / "mcc_oracles.json").read_text())["oracles"]
        expected = {
            "EF DEADLOCK": "not_applicable",
            "EF (eat_1 > 0)": "ok",
            "AG (eat_1 <= 1)": "ok",
            "AG (eat_1 <= 0)": "ok",
            "AG EF (eat_1 > 0)": "not_applicable",
            "AG (think_1 == 0 or EF (eat_1 > 0))": "not_applicable",
        }
        assert {oracle["formula"] for oracle in oracles} == set(expected)
        for oracle in oracles:
            guard = non_vacuity_guard(
                model=model,
                family=oracle["family"],
                kind=KindPattern("x"),
                formula=oracle["formula"],
            )
            assert guard.status == expected[oracle["formula"]], oracle["formula"]
