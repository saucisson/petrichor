"""Unit tests for the certified Symmetric → P/T unfolding (spec §18.3, issue #27).

Covers the three acceptance criteria: the unfold service with certificate
and ``element_map``, the cross-check against the P/T oracle up to renaming
on ≥ 5 models, and the explicit rejection of negative arc annotations
(spec §18.5).
"""

from pathlib import Path

import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st
from philosophes3 import build_philosophes3

from petrichor.engines.unfold import (
    MCC_ORACLE_LABEL,
    MCC_UNFOLD_TECHNIQUE,
    NegativeArcAnnotationError,
    NotSymmetricError,
    UnfoldCertificate,
    cross_check_unfold_against_oracle,
    unfold_symmetric_to_pt,
)
from petrichor.petri_canonical import (
    CanonicalModel,
    ElementName,
    JsonValue,
    build_canonical_model,
)
from petrichor.pnml_import import import_pnml

FIXTURES = Path(__file__).parent / "fixtures"

MIN_CROSS_CHECK_MODELS = 5
"""The acceptance criterion: cross-check on at least 5 models (spec §18.3)."""


def _element_ids(
    *,
    places: list[tuple[str, str, int]],
    transitions: list[str],
    renaming: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map every bare element name to its canonical id (``pl_``/``tr_`` prefix)."""
    renaming = renaming or {}
    ids: dict[str, str] = {}
    for name, _, _ in places:
        ids[name] = f"pl_{renaming.get(name, name)}"
    for name in transitions:
        ids[name] = f"tr_{renaming.get(name, name)}"
    return ids


def _symmetric_payload(
    *,
    model_id: str,
    places: list[tuple[str, str, int]],
    transitions: list[str],
    arcs: list[tuple[str, str]],
    arc_annotations: dict[int, dict[str, JsonValue]] | None = None,
) -> dict[str, object]:
    """A raw Symmetric `petri+json` payload: places carry a color class."""
    arc_annotations = arc_annotations or {}
    ids = _element_ids(places=places, transitions=transitions)
    return {
        "id": model_id,
        "formalism": "symmetric",
        "color_domain": {"classes": sorted({color for _, color, _ in places})},
        "places": [
            {
                "id": f"pl_{name}",
                "name": name,
                "initial_marking": marking,
                "layout": {"x": 0, "y": 0},
                "annotations": {"color_class": color},
            }
            for name, color, marking in places
        ],
        "transitions": [
            {
                "id": f"tr_{name}",
                "name": name,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
            for name in transitions
        ],
        "arcs": [
            {
                "id": f"ar_{index:03d}",
                "source": ids[source],
                "target": ids[target],
                "weight": 1,
                "annotations": arc_annotations.get(index, {}),
            }
            for index, (source, target) in enumerate(arcs)
        ],
        "metadata": {"source": "test"},
    }


def _pt_oracle_payload(
    *,
    model_id: str,
    places: list[tuple[str, str, int]],
    transitions: list[str],
    arcs: list[tuple[str, str]],
    renaming: dict[str, str] | None = None,
) -> dict[str, object]:
    """The expected P/T « unfolded » oracle, optionally with renamed elements."""
    renaming = renaming or {}
    ids = _element_ids(places=places, transitions=transitions, renaming=renaming)
    return {
        "id": model_id,
        "formalism": "pt",
        "color_domain": None,
        "places": [
            {
                "id": f"pl_{renaming.get(name, name)}",
                "name": renaming.get(name, name),
                "initial_marking": marking,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
            for name, _, marking in places
        ],
        "transitions": [
            {
                "id": f"tr_{renaming.get(name, name)}",
                "name": renaming.get(name, name),
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
            for name in transitions
        ],
        "arcs": [
            {
                "id": f"ar_{index:03d}",
                "source": ids[source],
                "target": ids[target],
                "weight": 1,
                "annotations": {},
            }
            for index, (source, target) in enumerate(arcs)
        ],
        "metadata": {"source": "mcc-oracle"},
    }


def _symmetric_model(
    *,
    model_id: str,
    places: list[tuple[str, str, int]],
    transitions: list[str],
    arcs: list[tuple[str, str]],
    arc_annotations: dict[int, dict[str, JsonValue]] | None = None,
) -> CanonicalModel:
    return build_canonical_model(
        payload=_symmetric_payload(
            model_id=model_id,
            places=places,
            transitions=transitions,
            arcs=arcs,
            arc_annotations=arc_annotations,
        )
    )


def _oracle_model(
    *,
    model_id: str,
    places: list[tuple[str, str, int]],
    transitions: list[str],
    arcs: list[tuple[str, str]],
    renaming: dict[str, str] | None = None,
) -> CanonicalModel:
    return build_canonical_model(
        payload=_pt_oracle_payload(
            model_id=model_id,
            places=places,
            transitions=transitions,
            arcs=arcs,
            renaming=renaming,
        )
    )


# Five Symmetric models with their P/T oracles — the cross-check corpus
# (acceptance criterion: ≥ 5 models, equality up to renaming).
type CrossCheckCase = tuple[str, list[tuple[str, str, int]], list[str], list[tuple[str, str]]]
CROSS_CHECK_CASES: list[CrossCheckCase] = [
    (
        "mdl_pair",
        [("p", "A", 1), ("q", "B", 0)],
        ["t"],
        [("p", "t"), ("t", "q")],
    ),
    (
        "mdl_chain3",
        [("p1", "A", 1), ("p2", "A", 0), ("p3", "B", 0)],
        ["t1", "t2"],
        [("p1", "t1"), ("t1", "p2"), ("p2", "t2"), ("t2", "p3")],
    ),
    (
        "mdl_fork",
        [("in", "A", 2), ("out1", "B", 0), ("out2", "B", 0)],
        ["split"],
        [("in", "split"), ("split", "out1"), ("split", "out2")],
    ),
    (
        "mdl_loop",
        [("a", "A", 1), ("b", "B", 1)],
        ["go", "back"],
        [("a", "go"), ("go", "b"), ("b", "back"), ("back", "a")],
    ),
    (
        "mdl_philosophes_like",
        [
            ("think_1", "Philosopher", 1),
            ("eat_1", "Philosopher", 0),
            ("fork_1", "Fork", 1),
            ("think_2", "Philosopher", 1),
            ("eat_2", "Philosopher", 0),
            ("fork_2", "Fork", 1),
        ],
        ["take_1", "release_1", "take_2", "release_2"],
        [
            ("think_1", "take_1"),
            ("fork_1", "take_1"),
            ("fork_2", "take_1"),
            ("take_1", "eat_1"),
            ("eat_1", "release_1"),
            ("release_1", "think_1"),
            ("release_1", "fork_1"),
            ("release_1", "fork_2"),
            ("think_2", "take_2"),
            ("fork_2", "take_2"),
            ("fork_1", "take_2"),
            ("take_2", "eat_2"),
            ("eat_2", "release_2"),
            ("release_2", "think_2"),
            ("release_2", "fork_2"),
            ("release_2", "fork_1"),
        ],
    ),
]


class TestUnfoldService:
    """The unfold service produces a derived P/T model with certificate."""

    def test_symmetric_fixture_unfolds_to_pt(self) -> None:
        symmetric = import_pnml(
            text=(FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")
        )
        assert symmetric.formalism == "symmetric"
        unfolded = unfold_symmetric_to_pt(model=symmetric)
        assert unfolded.parent_id == symmetric.id
        assert unfolded.model.parent == symmetric.id
        assert unfolded.model.formalism == "pt"
        assert unfolded.model.color_domain is None
        # Structure preserved: same places, transitions, arcs
        assert len(unfolded.model.places) == len(symmetric.places)
        assert len(unfolded.model.transitions) == len(symmetric.transitions)
        assert len(unfolded.model.arcs) == len(symmetric.arcs)
        # Color semantics dropped: no color_class annotation survives
        for place in unfolded.model.places:
            assert "color_class" not in place.annotations
        # Derived model id and lineage
        assert unfolded.model.id == f"{symmetric.id}_unfolded"
        assert unfolded.model.derivations[0]["kind"] == "unfold"

    def test_certificate_carries_element_map_and_oracle(self) -> None:
        symmetric = import_pnml(
            text=(FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8")
        )
        unfolded = unfold_symmetric_to_pt(model=symmetric)
        certificate = unfolded.certificate
        assert isinstance(certificate, UnfoldCertificate)
        assert certificate.technique == MCC_UNFOLD_TECHNIQUE
        assert certificate.oracle == MCC_ORACLE_LABEL
        assert len(certificate.rules) == 1
        # element_map is the identity on every element name, explicitly
        for place in symmetric.places:
            assert certificate.element_map.to_original(name=place.name) == place.name
        for transition in symmetric.transitions:
            assert certificate.element_map.to_original(name=transition.name) == transition.name
        # The certificate is attached to the derived model
        assert len(unfolded.model.certificates) == 1
        assert unfolded.model.certificates[0]["technique"] == MCC_UNFOLD_TECHNIQUE

    def test_success_event_is_pinned(self) -> None:
        symmetric = _symmetric_model(
            model_id="mdl_event_ok",
            places=[("p", "A", 1), ("q", "B", 0)],
            transitions=["t"],
            arcs=[("p", "t"), ("t", "q")],
        )
        with structlog.testing.capture_logs() as logs:
            unfold_symmetric_to_pt(model=symmetric)
        events = [entry["event"] for entry in logs]
        assert "unfold_symmetric_to_pt_applied" in events

    def test_rejects_pt_model(self) -> None:
        pt_model = build_philosophes3()
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(NotSymmetricError, match="only Symmetric nets unfold"),
        ):
            unfold_symmetric_to_pt(model=pt_model)
        assert "unfold_rejected_not_symmetric" in [entry["event"] for entry in logs]

    def test_rejects_negative_arc_annotation(self) -> None:
        model = _symmetric_model(
            model_id="mdl_inhibitor",
            places=[("p", "A", 1), ("q", "B", 0)],
            transitions=["t"],
            arcs=[("p", "t"), ("t", "q")],
            arc_annotations={1: {"inhibitor": True}},
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(NegativeArcAnnotationError, match="negative arc annotation"),
        ):
            unfold_symmetric_to_pt(model=model)
        assert "unfold_rejected_negative_arc_annotation" in [entry["event"] for entry in logs]

    def test_pnml_inhibitor_arc_is_rejected_at_unfold(self) -> None:
        pnml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">\n'
            '<net id="inhib" type="http://www.pnml.org/version-2009/grammar/pnml+symmetric">\n'
            '<place id="p"><name><value>p</value></name><type><value>A</value></type>'
            "<initialMarking><text>1</text></initialMarking></place>\n"
            '<place id="q"><name><value>q</value></name><type><value>B</value></type>'
            "<initialMarking><text>0</text></initialMarking></place>\n"
            '<transition id="t"><name><value>t</value></name></transition>\n'
            '<arc id="a1" source="p" target="t"/>\n'
            '<arc id="a2" source="t" target="q" type="inhibitor"/>\n'
            "</net>\n"
            "</pnml>"
        )
        model = import_pnml(text=pnml)
        inhibitor_arcs = [arc for arc in model.arcs if arc.annotations.get("inhibitor")]
        assert len(inhibitor_arcs) == 1
        with pytest.raises(NegativeArcAnnotationError, match="negative arc annotation"):
            unfold_symmetric_to_pt(model=model)


class TestCrossCheckAgainstOracle:
    """Cross-check: unfold equals the P/T oracle up to renaming, on ≥ 5 models."""

    def test_corpus_has_at_least_five_models(self) -> None:
        assert len(CROSS_CHECK_CASES) >= MIN_CROSS_CHECK_MODELS

    @pytest.mark.parametrize(
        ("model_id", "places", "transitions", "arcs"),
        CROSS_CHECK_CASES,
        ids=[case[0] for case in CROSS_CHECK_CASES],
    )
    def test_unfold_matches_oracle_up_to_renaming(
        self,
        model_id: str,
        places: list[tuple[str, str, int]],
        transitions: list[str],
        arcs: list[tuple[str, str]],
    ) -> None:
        symmetric = _symmetric_model(
            model_id=model_id, places=places, transitions=transitions, arcs=arcs
        )
        unfolded = unfold_symmetric_to_pt(model=symmetric).model
        # The MCC « unfolded » P/T renames elements: apply a fixed permutation
        all_names = [name for name, _, _ in places] + transitions
        renaming = {name: f"oracle_{name}" for name in all_names}
        oracle = _oracle_model(
            model_id=f"{model_id}_oracle",
            places=places,
            transitions=transitions,
            arcs=arcs,
            renaming=renaming,
        )
        assert cross_check_unfold_against_oracle(unfolded=unfolded, oracle_pt=oracle) is True

    def test_cross_check_rejects_structural_mismatch(self) -> None:
        symmetric = _symmetric_model(
            model_id="mdl_mismatch",
            places=[("p", "A", 1), ("q", "B", 0)],
            transitions=["t"],
            arcs=[("p", "t"), ("t", "q")],
        )
        unfolded = unfold_symmetric_to_pt(model=symmetric).model
        # Oracle with a different marking: not equal up to renaming
        oracle = _oracle_model(
            model_id="mdl_mismatch_oracle",
            places=[("p", "A", 0), ("q", "B", 0)],
            transitions=["t"],
            arcs=[("p", "t"), ("t", "q")],
        )
        assert cross_check_unfold_against_oracle(unfolded=unfolded, oracle_pt=oracle) is False

    def test_cross_check_rejects_non_pt(self) -> None:
        symmetric = _symmetric_model(
            model_id="mdl_nonpt",
            places=[("p", "A", 1)],
            transitions=["t"],
            arcs=[("p", "t")],
        )
        assert cross_check_unfold_against_oracle(unfolded=symmetric, oracle_pt=symmetric) is False

    def test_cross_check_pins_match_event(self) -> None:
        symmetric = _symmetric_model(
            model_id="mdl_event",
            places=[("p", "A", 1), ("q", "B", 0)],
            transitions=["t"],
            arcs=[("p", "t"), ("t", "q")],
        )
        unfolded = unfold_symmetric_to_pt(model=symmetric).model
        oracle = _oracle_model(
            model_id="mdl_event_oracle",
            places=[("p", "A", 1), ("q", "B", 0)],
            transitions=["t"],
            arcs=[("p", "t"), ("t", "q")],
        )
        with structlog.testing.capture_logs() as logs:
            assert cross_check_unfold_against_oracle(unfolded=unfolded, oracle_pt=oracle) is True
        assert "unfold_cross_check_matched" in [entry["event"] for entry in logs]


class TestElementMapStrictness:
    """An incomplete element map never silently passes through as identity."""

    def test_to_original_raises_on_unmapped_name(self) -> None:
        symmetric = _symmetric_model(
            model_id="mdl_map",
            places=[("p", "A", 1)],
            transitions=["t"],
            arcs=[("p", "t")],
        )
        unfolded = unfold_symmetric_to_pt(model=symmetric)
        with pytest.raises(KeyError):
            unfolded.certificate.element_map.to_original(name=ElementName("ghost"))


@st.composite
def _symmetric_net_case(
    draw: st.DrawFn,
) -> tuple[list[tuple[str, str, int]], list[str], list[tuple[str, str]]]:
    """Generate a small random Symmetric net: places, transitions, arcs."""
    place_count = draw(st.integers(min_value=1, max_value=6))
    transition_count = draw(st.integers(min_value=1, max_value=4))
    places = [
        (
            f"p{index}",
            draw(st.sampled_from(["A", "B"])),
            draw(st.integers(min_value=0, max_value=3)),
        )
        for index in range(place_count)
    ]
    transitions = [f"t{index}" for index in range(transition_count)]
    element_names = [name for name, _, _ in places] + transitions
    arc_count = draw(st.integers(min_value=1, max_value=8))
    arcs = [
        (
            draw(st.sampled_from(element_names)),
            draw(st.sampled_from(element_names)),
        )
        for _ in range(arc_count)
    ]
    return places, transitions, arcs


@pytest.mark.property
@given(case=_symmetric_net_case())
@settings(max_examples=50)
def test_unfold_preserves_structure_and_markings_on_generated_nets(
    case: tuple[list[tuple[str, str, int]], list[str], list[tuple[str, str]]],
) -> None:
    """Property: unfolding never drops structure, never changes a marking.

    On arbitrary generated Symmetric nets, the unfolded P/T model has the
    same places/transitions/arcs with the same markings and arc structure,
    the certificate's element_map is the identity, and the cross-check
    against the renamed P/T oracle matches.
    """
    places, transitions, arcs = case
    symmetric = _symmetric_model(
        model_id="mdl_prop",
        places=places,
        transitions=transitions,
        arcs=arcs,
    )
    unfolded = unfold_symmetric_to_pt(model=symmetric)
    derived = unfolded.model
    assert derived.formalism == "pt"
    assert derived.color_domain is None
    assert len(derived.places) == len(symmetric.places)
    assert len(derived.transitions) == len(symmetric.transitions)
    assert len(derived.arcs) == len(symmetric.arcs)
    original_marks = {place.name: place.initial_marking for place in symmetric.places}
    for place in derived.places:
        assert place.initial_marking == original_marks[place.name]
        assert "color_class" not in place.annotations
    for place in symmetric.places:
        assert unfolded.certificate.element_map.to_original(name=place.name) == place.name
    for transition in symmetric.transitions:
        assert unfolded.certificate.element_map.to_original(name=transition.name) == transition.name
    # Cross-check against the renamed P/T oracle built from the same structure
    renaming = {name: f"oracle_{name}" for name, _, _ in places}
    renaming.update({name: f"oracle_{name}" for name in transitions})
    oracle = _oracle_model(
        model_id="mdl_prop_oracle",
        places=places,
        transitions=transitions,
        arcs=arcs,
        renaming=renaming,
    )
    assert cross_check_unfold_against_oracle(unfolded=derived, oracle_pt=oracle) is True
