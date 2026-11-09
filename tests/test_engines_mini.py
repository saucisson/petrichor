"""Tests of the embedded static mini-engine (issue #5 / P05, spec §18.1).

The reference test pins the fil rouge numbers of the maquette on
Philosophes-3: 14 states, 27 arcs, 1 deadlock, 6 invariants, 6 symmetry
orbits, and a cyclic starvation witness. The capacity and formalism
rejections are pinned too: a net beyond capacity is an explicit error,
never a silent result (spec §14.2, §18.5).
"""

from fractions import Fraction

import pytest
import structlog.testing
from philosophes3 import build_philosophes3

from petrichor.engines import (
    NetBeyondCapacityError,
    PetriEngineError,
    UnsupportedFormalismError,
    analyze,
    check_ag_ef,
    check_ef_deadlock,
    explore_state_graph,
    find_starvation_cycle,
    maximal_siphon,
    maximal_trap,
    place_invariants,
    positive_invariants,
    structural_bounds,
    symmetry_orbits,
    tarjan_scc,
)
from petrichor.petri_canonical import CanonicalModel, build_canonical_model


def tiny_net_payload() -> dict[str, object]:
    """A two-place cyclic net: think -> take -> eat -> rel -> think (no deadlock)."""
    return {
        "id": "mdl_tiny",
        "formalism": "pt",
        "places": [
            {
                "id": "pl_think",
                "name": "think",
                "initial_marking": 1,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            },
            {
                "id": "pl_eat",
                "name": "eat",
                "initial_marking": 0,
                "layout": {"x": 1, "y": 0},
                "annotations": {},
            },
        ],
        "transitions": [
            {
                "id": "tr_take",
                "name": "take",
                "layout": {"x": 0, "y": 1},
                "annotations": {},
            },
            {
                "id": "tr_rel",
                "name": "rel",
                "layout": {"x": 1, "y": 1},
                "annotations": {},
            },
        ],
        "arcs": [
            {
                "id": "ar_001",
                "source": "pl_think",
                "target": "tr_take",
                "weight": 1,
                "annotations": {},
            },
            {
                "id": "ar_002",
                "source": "tr_take",
                "target": "pl_eat",
                "weight": 1,
                "annotations": {},
            },
            {
                "id": "ar_003",
                "source": "pl_eat",
                "target": "tr_rel",
                "weight": 1,
                "annotations": {},
            },
            {
                "id": "ar_004",
                "source": "tr_rel",
                "target": "pl_think",
                "weight": 1,
                "annotations": {},
            },
        ],
        "metadata": {},
    }


def tiny_net() -> CanonicalModel:
    """The tiny net as a validated canonical model."""
    return build_canonical_model(payload=tiny_net_payload())


def toggle_net_payload(*, pair_count: int) -> dict[str, object]:
    """`pair_count` independent toggles a_i -> b_i: 2^pair_count reachable states."""
    places: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    arcs: list[dict[str, object]] = []
    for index in range(pair_count):
        places.append(
            {
                "id": f"pl_a{index}",
                "name": f"a{index}",
                "initial_marking": 1,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )
        places.append(
            {
                "id": f"pl_b{index}",
                "name": f"b{index}",
                "initial_marking": 0,
                "layout": {"x": 1, "y": 0},
                "annotations": {},
            }
        )
        transitions.append(
            {
                "id": f"tr_t{index}",
                "name": f"t{index}",
                "layout": {"x": 0, "y": 1},
                "annotations": {},
            }
        )
        arcs.append(
            {
                "id": f"ar_in{index}",
                "source": f"pl_a{index}",
                "target": f"tr_t{index}",
                "weight": 1,
                "annotations": {},
            }
        )
        arcs.append(
            {
                "id": f"ar_out{index}",
                "source": f"tr_t{index}",
                "target": f"pl_b{index}",
                "weight": 1,
                "annotations": {},
            }
        )
    return {
        "id": "mdl_toggle",
        "formalism": "pt",
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
        "metadata": {},
    }


def self_loop_net_payload(*, place_count: int) -> dict[str, object]:
    """`place_count` places with a self-loop each: one reachable state, many places."""
    places: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    arcs: list[dict[str, object]] = []
    for index in range(place_count):
        places.append(
            {
                "id": f"pl_p{index}",
                "name": f"p{index}",
                "initial_marking": 1,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )
        transitions.append(
            {
                "id": f"tr_s{index}",
                "name": f"s{index}",
                "layout": {"x": 0, "y": 1},
                "annotations": {},
            }
        )
        arcs.append(
            {
                "id": f"ar_in{index}",
                "source": f"pl_p{index}",
                "target": f"tr_s{index}",
                "weight": 1,
                "annotations": {},
            }
        )
        arcs.append(
            {
                "id": f"ar_out{index}",
                "source": f"tr_s{index}",
                "target": f"pl_p{index}",
                "weight": 1,
                "annotations": {},
            }
        )
    return {
        "id": "mdl_loops",
        "formalism": "pt",
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
        "metadata": {},
    }


def symmetric_payload() -> dict[str, object]:
    """A symmetric net (color domain declared): out of the mini-engine scope."""
    payload = tiny_net_payload()
    payload["formalism"] = "symmetric"
    payload["color_domain"] = {"philosopher": ["thinking", "eating"]}
    return payload


class TestReferenceFilRouge:
    """The fil rouge numbers of the maquette on Philosophes-3 (issue #5)."""

    def test_state_graph_has_14_states_27_arcs_and_1_deadlock(self) -> None:
        graph = explore_state_graph(model=build_philosophes3())
        assert len(graph.states) == 14
        assert graph.arc_count == 27
        assert len(graph.deadlocks) == 1

    def test_the_deadlock_is_the_circular_wait(self) -> None:
        graph = explore_state_graph(model=build_philosophes3())
        deadlock = graph.deadlocks[0]
        marked = {
            name for name, count in zip(graph.place_names, deadlock, strict=True) if count > 0
        }
        assert marked == {"hasL_1", "hasL_2", "hasL_3"}

    def test_six_place_invariants(self) -> None:
        invariants = place_invariants(model=build_philosophes3())
        assert len(invariants) == 6

    def test_six_positive_invariants_and_structural_bounds_of_one(self) -> None:
        model = build_philosophes3()
        positives = positive_invariants(model=model)
        assert len(positives) == 6
        bounds = structural_bounds(model=model)
        assert set(bounds) == {place.name for place in model.places}
        assert all(bound == 1 for bound in bounds.values())

    def test_invariants_are_constant_over_every_reachable_marking(self) -> None:
        model = build_philosophes3()
        graph = explore_state_graph(model=model)
        place_names = graph.place_names
        for invariant in place_invariants(model=model):
            totals = {
                sum(
                    coefficient * marking[position]
                    for position, coefficient in enumerate(invariant)
                )
                for marking in graph.states
            }
            assert len(totals) == 1
        assert len(place_names) == 12

    def test_tarjan_finds_the_deadlock_singleton_and_the_main_scc(self) -> None:
        graph = explore_state_graph(model=build_philosophes3())
        components = tarjan_scc(graph=graph)
        assert sorted(len(component) for component in components) == [1, 13]
        singleton = next(c for c in components if len(c) == 1)
        assert singleton[0] == graph.deadlocks[0]

    def test_six_symmetry_orbits_under_the_order_three_rotation(self) -> None:
        model = build_philosophes3()
        graph = explore_state_graph(model=model)
        orbits = symmetry_orbits(model=model, graph=graph)
        assert len(orbits) == 6
        assert sum(len(orbit) for orbit in orbits) == 14

    def test_maximal_siphon_and_trap_are_valid(self) -> None:
        model = build_philosophes3()
        siphon = maximal_siphon(model=model)
        trap = maximal_trap(model=model)
        assert siphon and trap
        assert _is_siphon(model=model, candidate=siphon)
        assert _is_trap(model=model, candidate=trap)

    def test_analyze_bundles_the_whole_report(self) -> None:
        report = analyze(model=build_philosophes3())
        assert report.state_count == 14
        assert report.arc_count == 27
        assert report.deadlock_count == 1
        assert report.invariant_count == 6
        assert len(report.positive_invariants) == 6
        assert report.orbit_count == 6
        assert report.scc_count == 2
        assert report.scc_sizes == (1, 13)


def _transition_sets(*, model: CanonicalModel) -> dict[str, tuple[set[str], set[str]]]:
    """Per transition name, the (pre, post) place-name sets."""
    place_ids = {place.id for place in model.places}
    names_by_id = {element.id: element.name for element in (*model.places, *model.transitions)}
    io: dict[str, tuple[set[str], set[str]]] = {
        transition.name: (set(), set()) for transition in model.transitions
    }
    for arc in model.arcs:
        if arc.source in place_ids:
            io[names_by_id[arc.target]][0].add(names_by_id[arc.source])
        else:
            io[names_by_id[arc.source]][1].add(names_by_id[arc.target])
    return io


def _is_siphon(*, model: CanonicalModel, candidate: frozenset[str]) -> bool:
    """Siphon property: every transition producing into S also consumes from S."""
    for pre, post in _transition_sets(model=model).values():
        if post & candidate and not pre & candidate:
            return False
    return True


def _is_trap(*, model: CanonicalModel, candidate: frozenset[str]) -> bool:
    """Trap property: every transition consuming from S also produces into S."""
    for pre, post in _transition_sets(model=model).values():
        if pre & candidate and not post & candidate:
            return False
    return True


class TestVerdicts:
    def test_ef_deadlock_is_true_with_a_three_firing_witness(self) -> None:
        verdict = check_ef_deadlock(model=build_philosophes3())
        assert verdict.value == "true"
        assert verdict.technique == "deadlock_bfs"
        assert verdict.cost == 0
        assert verdict.witness is not None
        assert verdict.witness.kind == "path"
        assert len(verdict.witness.states) == 4
        assert verdict.witness.states[-1] == (("hasL_1", 1), ("hasL_2", 1), ("hasL_3", 1))

    def test_ef_deadlock_is_false_on_the_tiny_net(self) -> None:
        verdict = check_ef_deadlock(model=tiny_net())
        assert verdict.value == "false"
        assert verdict.witness is None

    def test_ag_ef_eat_1_is_refuted_with_the_deadlock_witness(self) -> None:
        verdict = check_ag_ef(model=build_philosophes3(), place="eat_1")
        assert verdict.value == "false"
        assert verdict.technique == "bfs_ag_ef"
        assert verdict.witness is not None
        assert verdict.witness.states[-1] == (("hasL_1", 1), ("hasL_2", 1), ("hasL_3", 1))

    def test_ag_ef_is_true_on_the_tiny_net(self) -> None:
        verdict = check_ag_ef(model=tiny_net(), place="eat")
        assert verdict.value == "true"
        assert verdict.witness is None

    def test_unknown_place_is_an_explicit_error(self) -> None:
        with pytest.raises(PetriEngineError, match="Unknown place"):
            check_ag_ef(model=tiny_net(), place="nowhere")

    def test_cyclic_starvation_witness_for_eat_1(self) -> None:
        witness = find_starvation_cycle(model=build_philosophes3(), place="eat_1")
        assert witness is not None
        assert witness.kind == "cycle"
        assert len(witness.states) >= 3
        for marking in witness.states:
            assert all(name != "eat_1" for name, _ in marking)

    def test_no_starvation_cycle_when_the_place_is_always_reachable(self) -> None:
        assert find_starvation_cycle(model=tiny_net(), place="eat") is None


class TestCapacityAndFormalism:
    def test_state_graph_beyond_capacity_is_rejected_explicitly(self) -> None:
        model = build_canonical_model(payload=toggle_net_payload(pair_count=14))
        with pytest.raises(NetBeyondCapacityError, match="capacity"):
            explore_state_graph(model=model)

    def test_invariant_enumeration_beyond_capacity_is_rejected(self) -> None:
        model = build_canonical_model(payload=self_loop_net_payload(place_count=17))
        with pytest.raises(NetBeyondCapacityError, match="capped"):
            positive_invariants(model=model)

    def test_automorphism_search_beyond_capacity_is_rejected(self) -> None:
        model = build_canonical_model(payload=self_loop_net_payload(place_count=17))
        graph = explore_state_graph(model=model)
        with pytest.raises(NetBeyondCapacityError, match="capped"):
            symmetry_orbits(model=model, graph=graph)

    def test_symmetric_net_is_rejected_explicitly(self) -> None:
        model = build_canonical_model(payload=symmetric_payload())
        with pytest.raises(UnsupportedFormalismError, match="P/T nets only"):
            explore_state_graph(model=model)

    def test_place_to_place_arc_is_rejected_explicitly(self) -> None:
        payload = tiny_net_payload()
        arcs = payload["arcs"]
        assert isinstance(arcs, list)
        arcs.append(
            {
                "id": "ar_005",
                "source": "pl_think",
                "target": "pl_eat",
                "weight": 1,
                "annotations": {},
            }
        )
        model = build_canonical_model(payload=payload)
        with pytest.raises(PetriEngineError, match="must bind a place to a transition"):
            explore_state_graph(model=model)


class TestLogNarration:
    def test_exploration_narrates_a_debug_event(self) -> None:
        with structlog.testing.capture_logs() as events:
            explore_state_graph(model=build_philosophes3())
        explored = [e for e in events if e["event"] == "state_graph_explored"]
        assert len(explored) == 1
        assert explored[0]["state_count"] == 14
        assert explored[0]["arc_count"] == 27
        assert explored[0]["deadlock_count"] == 1

    def test_capacity_rejection_narrates_a_warning(self) -> None:
        model = build_canonical_model(payload=toggle_net_payload(pair_count=14))
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(NetBeyondCapacityError),
        ):
            explore_state_graph(model=model)
        assert any(e["event"] == "engine_capacity_exceeded" for e in events)

    def test_verdicts_narrate_their_value(self) -> None:
        with structlog.testing.capture_logs() as events:
            check_ef_deadlock(model=build_philosophes3())
        assert any(e["event"] == "ef_deadlock_verdict" and e["value"] == "true" for e in events)


def test_fraction_import_is_used_for_invariant_arithmetic() -> None:
    """Pin the exact-arithmetic import: invariants are rational, not float."""
    invariants = place_invariants(model=build_philosophes3())
    assert all(isinstance(coefficient, Fraction) for inv in invariants for coefficient in inv)
