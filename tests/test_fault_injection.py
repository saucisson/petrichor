"""Unit and property tests for certified fault injection (spec §20.3, issue #44)."""

from typing import cast

import pytest
import structlog.testing
from hypothesis import given
from hypothesis import strategies as st
from philosophes3 import build_philosophes3

from petrichor.engines.fault_injection import (
    FAULT_CLASSES,
    FAULT_INJECTION_TECHNIQUE,
    POLARIZED_CONFLICT_WEIGHT,
    FaultCertificate,
    FaultClass,
    FaultInjectionError,
    FaultSeed,
    UnsupportedModelError,
    inject_fault,
)
from petrichor.engines.mini_engine import find_starvation_cycle
from petrichor.petri_canonical import (
    CanonicalModel,
    ElementName,
    build_canonical_model,
)


def _philosophers_payload(*, count: int) -> dict[str, object]:
    """A dining-philosophers P/T payload with ``count`` agents (maquette pattern)."""
    places: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    arcs: list[dict[str, object]] = []

    def add_place(*, name: str, initial_marking: int) -> None:
        places.append(
            {
                "id": f"pl_{name}",
                "name": name,
                "initial_marking": initial_marking,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )

    def add_transition(*, name: str) -> None:
        transitions.append(
            {
                "id": f"tr_{name}",
                "name": name,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )

    def add_arc(*, source: str, target: str) -> None:
        arcs.append(
            {
                "id": f"ar_{len(arcs) + 1:03d}",
                "source": source,
                "target": target,
                "weight": 1,
                "annotations": {},
            }
        )

    for index in range(1, count + 1):
        add_place(name=f"think_{index}", initial_marking=1)
        add_place(name=f"hasL_{index}", initial_marking=0)
        add_place(name=f"eat_{index}", initial_marking=0)
    for index in range(1, count + 1):
        add_place(name=f"fork_{index}", initial_marking=1)
    for index in range(1, count + 1):
        right = index % count + 1
        add_transition(name=f"takeL_{index}")
        add_transition(name=f"takeR_{index}")
        add_transition(name=f"rel_{index}")
        add_arc(source=f"pl_think_{index}", target=f"tr_takeL_{index}")
        add_arc(source=f"pl_fork_{index}", target=f"tr_takeL_{index}")
        add_arc(source=f"tr_takeL_{index}", target=f"pl_hasL_{index}")
        add_arc(source=f"pl_hasL_{index}", target=f"tr_takeR_{index}")
        add_arc(source=f"pl_fork_{right}", target=f"tr_takeR_{index}")
        add_arc(source=f"tr_takeR_{index}", target=f"pl_eat_{index}")
        add_arc(source=f"pl_eat_{index}", target=f"tr_rel_{index}")
        add_arc(source=f"tr_rel_{index}", target=f"pl_think_{index}")
        add_arc(source=f"tr_rel_{index}", target=f"pl_fork_{index}")
        add_arc(source=f"tr_rel_{index}", target=f"pl_fork_{right}")
    return {
        "id": f"mdl_philosophers_{count}",
        "formalism": "pt",
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
        "metadata": {"source": "test"},
    }


def _section(payload: dict[str, object], *, name: str) -> list[dict[str, object]]:
    """One list section of a payload built by ``_philosophers_payload``.

    The payload is typed ``dict[str, object]``; the cast narrows a section
    the builder always fills with a list of dicts (justified escape, ADR-0006).
    """
    return cast("list[dict[str, object]]", payload[name])


def _arc_weight(*, model: CanonicalModel, source_name: str, target_name: str) -> int | None:
    id_by_name = {element.name: element.id for element in (*model.places, *model.transitions)}
    for arc in model.arcs:
        if (
            id_by_name.get(ElementName(source_name)) == arc.source
            and id_by_name.get(ElementName(target_name)) == arc.target
        ):
            return arc.weight
    return None


class TestFaultClasses:
    """The four fault classes are certified transformations with element_map."""

    @pytest.mark.parametrize("fault_class", FAULT_CLASSES)
    def test_every_class_produces_a_certified_variant(self, fault_class: FaultClass) -> None:
        model = build_philosophes3()
        faulty = inject_fault(model=model, fault_class=fault_class, seed=FaultSeed(0))
        assert faulty.parent_id == model.id
        assert faulty.model.parent == model.id
        assert faulty.model.id == f"{model.id}_fault_{fault_class}_s0"
        certificate = faulty.certificate
        assert isinstance(certificate, FaultCertificate)
        assert certificate.fault_class == fault_class
        assert certificate.technique == FAULT_INJECTION_TECHNIQUE
        assert certificate.seed == 0
        assert certificate.agent == 1
        assert len(certificate.rules) == 1
        # element_map is mandatory and covers every element of the faulty net
        faulty_names = [
            element.name for element in (*faulty.model.places, *faulty.model.transitions)
        ]
        for name in faulty_names:
            assert certificate.element_map.to_original(name=ElementName(name)) is not None
        # The certificate is attached to the derived model
        assert len(faulty.model.certificates) == 1
        assert faulty.model.certificates[0]["fault_class"] == fault_class
        # Each variant has its own canonical hash, distinct from the parent
        assert faulty.model.content_hash != model.content_hash

    def test_f1_swaps_the_fork_acquisitions_of_one_agent(self) -> None:
        model = build_philosophes3()
        faulty = inject_fault(model=model, fault_class="f1", seed=FaultSeed(0))
        # Agent 1 is left-handed: takeL_1 takes fork_2, takeR_1 takes fork_1
        assert _arc_weight(model=faulty.model, source_name="fork_2", target_name="takeL_1") == 1
        assert _arc_weight(model=faulty.model, source_name="fork_1", target_name="takeR_1") == 1
        assert _arc_weight(model=faulty.model, source_name="fork_1", target_name="takeL_1") is None
        # Other agents untouched
        assert _arc_weight(model=faulty.model, source_name="fork_2", target_name="takeL_2") == 1
        # Structure preserved: same element and arc counts
        assert len(faulty.model.places) == len(model.places)
        assert len(faulty.model.transitions) == len(model.transitions)
        assert len(faulty.model.arcs) == len(model.arcs)

    def test_f2_drops_one_release_arc(self) -> None:
        model = build_philosophes3()
        faulty = inject_fault(model=model, fault_class="f2", seed=FaultSeed(0))
        # Agent 1 never returns its right fork fork_2
        assert _arc_weight(model=faulty.model, source_name="rel_1", target_name="fork_2") is None
        assert _arc_weight(model=faulty.model, source_name="rel_1", target_name="fork_1") == 1
        assert len(faulty.model.arcs) == len(model.arcs) - 1

    def test_f3_polarizes_the_conflict_on_the_shared_fork(self) -> None:
        model = build_philosophes3()
        faulty = inject_fault(model=model, fault_class="f3", seed=FaultSeed(0))
        # The competitor of agent 1 on fork_2 (agent 2) is biased against
        assert (
            _arc_weight(model=faulty.model, source_name="fork_2", target_name="takeL_2")
            == POLARIZED_CONFLICT_WEIGHT
        )
        assert _arc_weight(model=faulty.model, source_name="fork_2", target_name="takeR_1") == 1
        assert len(faulty.model.arcs) == len(model.arcs)

    def test_f4_duplicates_one_agent_without_its_right_fork(self) -> None:
        model = build_philosophes3()
        faulty = inject_fault(model=model, fault_class="f4", seed=FaultSeed(0))
        place_names = {place.name for place in faulty.model.places}
        assert {"think_4", "hasL_4", "eat_4"} <= place_names
        transition_names = {transition.name for transition in faulty.model.transitions}
        assert {"takeL_4", "takeR_4", "rel_4"} <= transition_names
        # The duplicate has no right-fork arc
        assert _arc_weight(model=faulty.model, source_name="fork_2", target_name="takeR_4") is None
        assert _arc_weight(model=faulty.model, source_name="fork_1", target_name="takeL_4") == 1
        # element_map: the duplicated elements map to agent 1's originals
        element_map = faulty.certificate.element_map
        assert element_map.to_original(name=ElementName("think_4")) == ElementName("think_1")
        assert element_map.to_original(name=ElementName("takeR_4")) == ElementName("takeR_1")
        # Existing elements stay identity
        assert element_map.to_original(name=ElementName("think_2")) == ElementName("think_2")


class TestDeterminism:
    """Seeded and deterministic: each variant has its own canonical hash."""

    @pytest.mark.parametrize("fault_class", FAULT_CLASSES)
    def test_same_seed_same_hash(self, fault_class: FaultClass) -> None:
        model = build_philosophes3()
        first = inject_fault(model=model, fault_class=fault_class, seed=FaultSeed(7))
        second = inject_fault(model=model, fault_class=fault_class, seed=FaultSeed(7))
        assert first.model.content_hash == second.model.content_hash
        assert first.certificate.agent == second.certificate.agent

    def test_seed_selects_the_target_agent(self) -> None:
        model = build_philosophes3()
        agents = {
            inject_fault(model=model, fault_class="f2", seed=FaultSeed(seed)).certificate.agent
            for seed in range(6)
        }
        assert agents == {1, 2, 3}

    def test_distinct_variants_have_distinct_hashes(self) -> None:
        model = build_philosophes3()
        hashes = {
            inject_fault(
                model=model, fault_class=fault_class, seed=FaultSeed(seed)
            ).model.content_hash
            for fault_class in FAULT_CLASSES
            for seed in range(3)
        }
        assert len(hashes) == len(FAULT_CLASSES) * 3


class TestRedThreadValidation:
    """Fil rouge: f1 = philosophe gaucher, le témoin de famine cyclique est retrouvé."""

    def test_f1_left_handed_philosopher_starvation_witness_found(self) -> None:
        model = build_philosophes3()
        faulty = inject_fault(model=model, fault_class="f1", seed=FaultSeed(0))
        witness = find_starvation_cycle(model=faulty.model, place="eat_1")
        assert witness is not None
        assert "eat_1" in witness.description


class TestRejections:
    """Unsupported nets are rejected explicitly, never silently faulted."""

    def test_rejects_net_without_agent_pattern(self) -> None:
        payload: dict[str, object] = {
            "id": "mdl_plain",
            "formalism": "pt",
            "places": [
                {
                    "id": "pl_p",
                    "name": "p",
                    "initial_marking": 1,
                    "layout": {"x": 0, "y": 0},
                    "annotations": {},
                },
                {
                    "id": "pl_q",
                    "name": "q",
                    "initial_marking": 0,
                    "layout": {"x": 0, "y": 0},
                    "annotations": {},
                },
            ],
            "transitions": [
                {
                    "id": "tr_t",
                    "name": "t",
                    "layout": {"x": 0, "y": 0},
                    "annotations": {},
                }
            ],
            "arcs": [
                {
                    "id": "ar_001",
                    "source": "pl_p",
                    "target": "tr_t",
                    "weight": 1,
                    "annotations": {},
                },
                {
                    "id": "ar_002",
                    "source": "tr_t",
                    "target": "pl_q",
                    "weight": 1,
                    "annotations": {},
                },
            ],
            "metadata": {"source": "test"},
        }
        model = build_canonical_model(payload=payload)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UnsupportedModelError, match="agent pattern"),
        ):
            inject_fault(model=model, fault_class="f1", seed=FaultSeed(0))
        assert "fault_injection_rejected_unsupported_model" in [entry["event"] for entry in logs]

    def test_rejects_net_with_a_partial_agent(self) -> None:
        payload = _philosophers_payload(count=3)
        # A broken agent 4: one place present, the five other elements missing
        _section(payload, name="places").append(
            {
                "id": "pl_think_4",
                "name": "think_4",
                "initial_marking": 1,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )
        model = build_canonical_model(payload=payload)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UnsupportedModelError, match="agent pattern"),
        ):
            inject_fault(model=model, fault_class="f1", seed=FaultSeed(0))
        rejected = [
            entry
            for entry in logs
            if entry["event"] == "fault_injection_rejected_unsupported_model"
        ]
        assert len(rejected) == 1
        assert rejected[0]["off_pattern_agents"] == [4]

    def test_rejects_net_with_a_gap_in_agent_indices(self) -> None:
        payload = _philosophers_payload(count=3)
        # Agent 2 removed entirely, agent 3 kept: a gap, not a partial agent
        payload["places"] = [
            place
            for place in _section(payload, name="places")
            if not str(place["name"]).endswith("_2")
        ]
        payload["transitions"] = [
            transition
            for transition in _section(payload, name="transitions")
            if not str(transition["name"]).endswith("_2")
        ]
        payload["arcs"] = [
            arc
            for arc in _section(payload, name="arcs")
            if "_2" not in str(arc["source"]) and "_2" not in str(arc["target"])
        ]
        model = build_canonical_model(payload=payload)
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UnsupportedModelError, match="agent pattern"),
        ):
            inject_fault(model=model, fault_class="f1", seed=FaultSeed(0))
        rejected = [
            entry
            for entry in logs
            if entry["event"] == "fault_injection_rejected_unsupported_model"
        ]
        assert len(rejected) == 1
        assert rejected[0]["off_pattern_agents"] == [3]

    def test_rejects_unknown_fault_class(self) -> None:
        model = build_philosophes3()
        with pytest.raises(FaultInjectionError, match="Unknown fault class"):
            # negative test: deliberately invalid class, rejected at runtime
            inject_fault(model=model, fault_class="f9", seed=FaultSeed(0))  # type: ignore[arg-type]

    def test_success_event_is_pinned(self) -> None:
        model = build_philosophes3()
        with structlog.testing.capture_logs() as logs:
            inject_fault(model=model, fault_class="f1", seed=FaultSeed(0))
        events = [entry["event"] for entry in logs]
        assert "fault_injected" in events


class TestFaultInjectionProperties:
    """Property tests: every fault class is deterministic and certified on generated nets."""

    @pytest.mark.property
    @given(count=st.integers(min_value=2, max_value=4), seed=st.integers(min_value=0, max_value=99))
    def test_faults_are_deterministic_and_certified(self, count: int, seed: int) -> None:
        model = build_canonical_model(payload=_philosophers_payload(count=count))
        for fault_class in FAULT_CLASSES:
            first = inject_fault(model=model, fault_class=fault_class, seed=FaultSeed(seed))
            second = inject_fault(model=model, fault_class=fault_class, seed=FaultSeed(seed))
            assert first.model.content_hash == second.model.content_hash
            assert first.model.parent == model.id
            faulty_names = [
                element.name for element in (*first.model.places, *first.model.transitions)
            ]
            for name in faulty_names:
                # The element map resolves every faulty-net element
                assert first.certificate.element_map.to_original(name=ElementName(name))
