"""Tests for the canonical `petri+json` v1.0 schema and hashing (issue #2, spec §13).

The acceptance criteria are pinned directly: deterministic serialization
with lossless round-trip, hash invariance under id/layout/annotation
changes, hash sensitivity to structure and initial marking, and strict
rejection of invalid input. Log narration is asserted too (ADR-0005).
"""

import json
import re

import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st

import petri_canonical
from petri_canonical import (
    Arc,
    CanonicalModel,
    ElementId,
    ElementName,
    InvalidCanonicalModelError,
    Layout,
    ModelId,
    Place,
    Transition,
    build_canonical_model,
    compute_content_hash,
    parse_canonical_json,
    serialize_model,
    validate_canonical_model,
)

CONTENT_HASH_REGEX = re.compile(r"sha256:[0-9a-f]{64}")

type NetTuple = tuple[tuple[Place, ...], tuple[Transition, ...], tuple[Arc, ...]]
# A drawn net: places, transitions and arcs.


def make_place(
    *,
    element_id: str = "pl_011",
    name: str = "think_1",
    marking: int = 1,
    x: float = 320.0,
    y: float = 78.0,
    annotations: dict[str, petri_canonical.JsonValue] | None = None,
) -> Place:
    """Build a place with overridable id, name, marking, layout and annotations."""
    return Place(
        id=ElementId(element_id),
        name=ElementName(name),
        initial_marking=marking,
        layout=Layout(x=x, y=y),
        annotations=annotations if annotations is not None else {"role_hint": "automaton-state"},
    )


def make_transition(
    *,
    element_id: str = "tr_004",
    name: str = "takeL_1",
    x: float = 254.0,
    y: float = 102.0,
    annotations: dict[str, petri_canonical.JsonValue] | None = None,
) -> Transition:
    """Build a transition with overridable id, name, layout and annotations."""
    return Transition(
        id=ElementId(element_id),
        name=ElementName(name),
        layout=Layout(x=x, y=y),
        annotations=annotations if annotations is not None else {"silent": False},
    )


def make_arc(
    *,
    element_id: str = "ar_031",
    source: str = "pl_011",
    target: str = "tr_004",
    weight: int = 1,
) -> Arc:
    """Build an arc with overridable id, endpoints and weight."""
    return Arc(
        id=ElementId(element_id),
        source=ElementId(source),
        target=ElementId(target),
        weight=weight,
        annotations={},
    )


def sample_net() -> tuple[tuple[Place, ...], tuple[Transition, ...], tuple[Arc, ...]]:
    """A two-place, one-transition net with two arcs (Philosophes fragment)."""
    places = (
        make_place(),
        make_place(element_id="pl_012", name="eat_1", marking=0, x=400.0, y=200.0),
    )
    transitions = (make_transition(),)
    arcs = (
        make_arc(),
        make_arc(element_id="ar_032", source="tr_004", target="pl_012", weight=1),
    )
    return places, transitions, arcs


def sample_model(*, model_id: str = "mdl_8f3ac1") -> CanonicalModel:
    """Build the canonical sample model, stamped with its content hash."""
    places, transitions, arcs = sample_net()
    return build_canonical_model(
        model_id=ModelId(model_id),
        formalism="pt",
        places=places,
        transitions=transitions,
        arcs=arcs,
        metadata={"source": "pnml", "imported_at": "2026-11-02T12:00:00Z"},
    )


def renamed_model(*, model_id: str = "mdl_8f3ac1", suffix: str = "_renamed") -> CanonicalModel:
    """The sample net with every element id and the model id renamed."""
    places, transitions, arcs = sample_net()
    renamed_places = tuple(
        place.model_copy(update={"id": ElementId(place.id + suffix)}) for place in places
    )
    renamed_transitions = tuple(
        transition.model_copy(update={"id": ElementId(transition.id + suffix)})
        for transition in transitions
    )
    renamed_arcs = tuple(
        arc.model_copy(
            update={
                "id": ElementId(arc.id + suffix),
                "source": ElementId(arc.source + suffix),
                "target": ElementId(arc.target + suffix),
            }
        )
        for arc in arcs
    )
    return build_canonical_model(
        model_id=ModelId(model_id + suffix),
        formalism="pt",
        places=renamed_places,
        transitions=renamed_transitions,
        arcs=renamed_arcs,
        metadata={"source": "pnml"},
    )


class TestCanonicalSerialization:
    def test_round_trip_is_lossless(self) -> None:
        model = sample_model()

        text = serialize_model(model=model)
        parsed = parse_canonical_json(text=text)

        assert parsed == model
        assert serialize_model(model=parsed) == text

    def test_serialization_is_deterministic_with_sorted_keys(self) -> None:
        model = sample_model()

        first = serialize_model(model=model)
        second = serialize_model(model=model)

        assert first == second
        assert json.dumps(json.loads(first), sort_keys=True, separators=(",", ":")) == first

    def test_element_order_is_preserved_not_sorted(self) -> None:
        places, transitions, arcs = sample_net()
        reordered = build_canonical_model(
            model_id=ModelId("mdl_order"),
            formalism="pt",
            places=tuple(reversed(places)),
            transitions=transitions,
            arcs=arcs,
        )
        text = serialize_model(model=reordered)
        stored_ids = [place["id"] for place in json.loads(text)["places"]]
        assert stored_ids == ["pl_012", "pl_011"]


class TestContentHash:
    def test_hash_has_the_canonical_format(self) -> None:
        model = sample_model()
        assert CONTENT_HASH_REGEX.fullmatch(model.content_hash) is not None

    def test_renaming_ids_does_not_change_the_hash(self) -> None:
        model = sample_model()
        renamed = renamed_model()

        assert compute_content_hash(model=renamed) == model.content_hash

    def test_layout_and_annotations_do_not_change_the_hash(self) -> None:
        model = sample_model()
        places, transitions, arcs = sample_net()
        redecorated = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=tuple(
                place.model_copy(
                    update={
                        "layout": Layout(x=place.layout.x + 1000.0, y=place.layout.y - 500.0),
                        "annotations": {"role_hint": "changed", "comments": ["edited"]},
                    }
                )
                for place in places
            ),
            transitions=tuple(
                transition.model_copy(
                    update={
                        "layout": Layout(x=0.0, y=0.0),
                        "annotations": {"silent": True},
                    }
                )
                for transition in transitions
            ),
            arcs=arcs,
        )

        assert compute_content_hash(model=redecorated) == model.content_hash

    def test_metadata_does_not_change_the_hash(self) -> None:
        places, transitions, arcs = sample_net()
        other_metadata = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=places,
            transitions=transitions,
            arcs=arcs,
            metadata={"source": "hand-written"},
        )

        assert other_metadata.content_hash == sample_model().content_hash

    def test_adding_an_arc_changes_the_hash(self) -> None:
        model = sample_model()
        places, transitions, arcs = sample_net()
        extended = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=places,
            transitions=transitions,
            arcs=(*arcs, make_arc(element_id="ar_033", source="pl_012", target="tr_004")),
        )

        assert extended.content_hash != model.content_hash

    def test_removing_a_place_changes_the_hash(self) -> None:
        model = sample_model()
        places, transitions, arcs = sample_net()
        reduced = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=places[:1],
            transitions=transitions,
            arcs=(arcs[0],),
        )

        assert reduced.content_hash != model.content_hash

    def test_initial_marking_change_changes_the_hash(self) -> None:
        model = sample_model()
        places, transitions, arcs = sample_net()
        remarking = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=(make_place(marking=0), places[1]),
            transitions=transitions,
            arcs=arcs,
        )

        assert remarking.content_hash != model.content_hash

    def test_arc_weight_change_changes_the_hash(self) -> None:
        model = sample_model()
        places, transitions, arcs = sample_net()
        reweighted = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=places,
            transitions=transitions,
            arcs=(make_arc(weight=2), arcs[1]),
        )

        assert reweighted.content_hash != model.content_hash

    def test_element_name_change_changes_the_hash(self) -> None:
        model = sample_model()
        places, transitions, arcs = sample_net()
        renamed = build_canonical_model(
            model_id=ModelId("mdl_8f3ac1"),
            formalism="pt",
            places=(make_place(name="ponder_1"), places[1]),
            transitions=transitions,
            arcs=arcs,
        )

        assert renamed.content_hash != model.content_hash


class TestStrictValidation:
    def test_malformed_json_is_rejected_with_an_explicit_error(self) -> None:
        with pytest.raises(InvalidCanonicalModelError, match="Not valid JSON"):
            parse_canonical_json(text="{not json")

    def test_non_object_document_is_rejected(self) -> None:
        with pytest.raises(InvalidCanonicalModelError, match="must be a JSON object"):
            parse_canonical_json(text="[]")

    def test_unknown_arc_endpoint_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["arcs"][0]["target"] = "tr_unknown"

        with pytest.raises(InvalidCanonicalModelError, match="unknown element id"):
            validate_canonical_model(payload=payload)

    def test_duplicate_element_ids_are_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["transitions"][0]["id"] = payload["places"][0]["id"]

        with pytest.raises(InvalidCanonicalModelError, match="ids must be unique"):
            validate_canonical_model(payload=payload)

    def test_duplicate_element_names_are_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["transitions"][0]["name"] = payload["places"][0]["name"]

        with pytest.raises(InvalidCanonicalModelError, match="names must be unique"):
            validate_canonical_model(payload=payload)

    def test_negative_initial_marking_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["places"][0]["initial_marking"] = -1

        with pytest.raises(InvalidCanonicalModelError, match="greater than or equal to 0"):
            validate_canonical_model(payload=payload)

    def test_zero_arc_weight_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["arcs"][0]["weight"] = 0

        with pytest.raises(InvalidCanonicalModelError, match="greater than or equal to 1"):
            validate_canonical_model(payload=payload)

    def test_unknown_field_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["unexpected"] = True

        with pytest.raises(InvalidCanonicalModelError, match="unexpected"):
            validate_canonical_model(payload=payload)

    def test_malformed_content_hash_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["content_hash"] = "sha256:not-hex"

        with pytest.raises(InvalidCanonicalModelError, match="content_hash must be"):
            validate_canonical_model(payload=payload)

    def test_tampered_content_hash_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["content_hash"] = "sha256:" + "a" * 64

        with pytest.raises(InvalidCanonicalModelError, match="content_hash mismatch"):
            validate_canonical_model(payload=payload)

    def test_pt_net_with_color_domain_is_rejected(self) -> None:
        payload = json.loads(serialize_model(model=sample_model()))
        payload["color_domain"] = {"philosopher": ["thinking", "eating"]}

        with pytest.raises(InvalidCanonicalModelError, match="color_domain"):
            validate_canonical_model(payload=payload)


class TestLogNarration:
    def test_hash_computation_narrates_a_debug_event(self) -> None:
        with structlog.testing.capture_logs() as events:
            model = sample_model()

        hash_events = [
            event for event in events if event["event"] == "canonical_content_hash_computed"
        ]
        assert len(hash_events) == 1
        assert hash_events[0]["model_id"] == "mdl_8f3ac1"
        assert hash_events[0]["content_hash"] == model.content_hash

    def test_validation_narrates_a_debug_event(self) -> None:
        with structlog.testing.capture_logs() as events:
            parse_canonical_json(text=serialize_model(model=sample_model()))

        assert any(event["event"] == "canonical_model_validated" for event in events)

    def test_rejection_narrates_a_warning(self) -> None:
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(InvalidCanonicalModelError),
        ):
            parse_canonical_json(text="{not json")

        assert any(event["event"] == "canonical_model_rejected" for event in events)


@st.composite
def random_net(draw: st.DrawFn) -> NetTuple:
    """Draw a random small P/T net with unique ids and names."""
    place_count = draw(st.integers(min_value=0, max_value=4))
    transition_count = draw(st.integers(min_value=0, max_value=3))
    places = tuple(
        make_place(
            element_id=f"pl_{index}",
            name=f"place_{index}",
            marking=draw(st.integers(min_value=0, max_value=3)),
        )
        for index in range(place_count)
    )
    transitions = tuple(
        make_transition(element_id=f"tr_{index}", name=f"transition_{index}")
        for index in range(transition_count)
    )
    element_ids = [place.id for place in places] + [transition.id for transition in transitions]
    arc_count = draw(st.integers(min_value=0, max_value=5)) if element_ids else 0
    arcs = tuple(
        make_arc(
            element_id=f"ar_{index}",
            source=element_ids[draw(st.integers(min_value=0, max_value=len(element_ids) - 1))],
            target=element_ids[draw(st.integers(min_value=0, max_value=len(element_ids) - 1))],
            weight=draw(st.integers(min_value=1, max_value=3)),
        )
        for index in range(arc_count)
    )
    return places, transitions, arcs


def build_random_model(
    *, places: tuple[Place, ...], transitions: tuple[Transition, ...], arcs: tuple[Arc, ...]
) -> CanonicalModel:
    """Build a canonical model from a drawn net."""
    return build_canonical_model(
        model_id=ModelId("mdl_property"),
        formalism="pt",
        places=places,
        transitions=transitions,
        arcs=arcs,
    )


@pytest.mark.property
@settings(deadline=None)
@given(net=random_net())
def test_property_round_trip_is_lossless(net: NetTuple) -> None:
    """Any valid net survives serialize → parse without loss."""
    places, transitions, arcs = net
    model = build_random_model(places=places, transitions=transitions, arcs=arcs)

    parsed = parse_canonical_json(text=serialize_model(model=model))

    assert parsed == model


@pytest.mark.property
@settings(deadline=None)
@given(net=random_net(), suffix=st.text(alphabet="rstuvwxyz", min_size=1, max_size=4))
def test_property_renaming_ids_never_changes_the_hash(net: NetTuple, suffix: str) -> None:
    """Renaming every id (elements and model) never changes the content hash."""
    places, transitions, arcs = net
    model = build_random_model(places=places, transitions=transitions, arcs=arcs)
    renamed = build_canonical_model(
        model_id=ModelId("mdl_property" + suffix),
        formalism="pt",
        places=tuple(
            place.model_copy(update={"id": ElementId(place.id + suffix)}) for place in places
        ),
        transitions=tuple(
            transition.model_copy(update={"id": ElementId(transition.id + suffix)})
            for transition in transitions
        ),
        arcs=tuple(
            arc.model_copy(
                update={
                    "id": ElementId(arc.id + suffix),
                    "source": ElementId(arc.source + suffix),
                    "target": ElementId(arc.target + suffix),
                }
            )
            for arc in arcs
        ),
    )

    assert compute_content_hash(model=renamed) == model.content_hash
