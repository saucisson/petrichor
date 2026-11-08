"""Tests for the canonical `petri+json` v1.0 schema and hashing (issue #2, spec §13).

The acceptance criteria are pinned directly: deterministic serialization
with lossless round-trip, hash invariance under id/layout/annotation
changes, hash sensitivity to structure and initial marking, and strict
rejection of invalid input. Log narration is asserted too (ADR-0005).
"""

import json
import re
from typing import TYPE_CHECKING, cast

import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st

from petri_canonical import (
    CanonicalModel,
    InvalidCanonicalModelError,
    build_canonical_model,
    compute_content_hash,
    parse_canonical_json,
    serialize_model,
    validate_canonical_model,
)

if TYPE_CHECKING:
    from collections.abc import Callable

CONTENT_HASH_REGEX = re.compile(r"sha256:[0-9a-f]{64}")


def sample_payload() -> dict[str, object]:
    """A two-place, one-transition net with two arcs, as a raw JSON document."""
    return {
        "id": "mdl_8f3ac1",
        "formalism": "pt",
        "places": [
            {
                "id": "pl_011",
                "name": "think_1",
                "initial_marking": 1,
                "layout": {"x": 320, "y": 78},
                "annotations": {"role_hint": "automaton-state"},
            },
            {
                "id": "pl_012",
                "name": "eat_1",
                "initial_marking": 0,
                "layout": {"x": 400, "y": 200},
                "annotations": {},
            },
        ],
        "transitions": [
            {
                "id": "tr_004",
                "name": "takeL_1",
                "layout": {"x": 254, "y": 102},
                "annotations": {"silent": False},
            }
        ],
        "arcs": [
            {
                "id": "ar_031",
                "source": "pl_011",
                "target": "tr_004",
                "weight": 1,
                "annotations": {},
            },
            {
                "id": "ar_032",
                "source": "tr_004",
                "target": "pl_012",
                "weight": 1,
                "annotations": {},
            },
        ],
        "metadata": {"source": "pnml", "imported_at": "2026-11-02T12:00:00Z"},
    }


def elements_of(*, payload: dict[str, object], section: str) -> list[dict[str, object]]:
    """One element list of a raw payload, typed for mutation."""
    return cast("list[dict[str, object]]", payload[section])


def sample_model() -> CanonicalModel:
    """The canonical sample model, stamped with its content hash."""
    return build_canonical_model(payload=sample_payload())


def variant_of(*, mutate: Callable[[dict[str, object]], None]) -> CanonicalModel:
    """The sample model with one mutation applied to its raw payload."""
    payload = sample_payload()
    mutate(payload)
    return build_canonical_model(payload=payload)


def canonical_payload() -> dict[str, object]:
    """The sample model serialized back to a complete canonical document."""
    return cast("dict[str, object]", json.loads(serialize_model(model=sample_model())))


class TestCanonicalSerialization:
    def test_round_trip_is_lossless(self) -> None:
        model = sample_model()
        text = serialize_model(model=model)
        parsed = parse_canonical_json(text=text)
        assert parsed == model
        assert serialize_model(model=parsed) == text

    def test_serialization_is_deterministic_with_sorted_keys(self) -> None:
        text = serialize_model(model=sample_model())
        assert serialize_model(model=sample_model()) == text
        assert json.dumps(json.loads(text), sort_keys=True, separators=(",", ":")) == text

    def test_element_order_is_preserved_not_sorted(self) -> None:
        def reverse_places(payload: dict[str, object]) -> None:
            elements_of(payload=payload, section="places").reverse()

        text = serialize_model(model=variant_of(mutate=reverse_places))
        stored_ids = [place["id"] for place in json.loads(text)["places"]]
        assert stored_ids == ["pl_012", "pl_011"]


def rename_ids(payload: dict[str, object], *, suffix: str = "_r") -> None:
    """Rename every element id and the model id (names unchanged)."""
    for section in ("places", "transitions", "arcs"):
        for element in elements_of(payload=payload, section=section):
            element["id"] = str(element["id"]) + suffix
            for endpoint in ("source", "target"):
                if endpoint in element:
                    element[endpoint] = str(element[endpoint]) + suffix
    payload["id"] = str(payload["id"]) + suffix


def set_field(
    *, payload: dict[str, object], section: str, index: int, field: str, value: object
) -> None:
    """Replace one field of one element of a raw payload."""
    elements_of(payload=payload, section=section)[index][field] = value


def set_root(*, payload: dict[str, object], field: str, value: object) -> None:
    """Replace one top-level field of a raw payload."""
    payload[field] = value


def redecorate(payload: dict[str, object]) -> None:
    """Move a node and rewrite annotations (layout/annotations are not hashed)."""
    set_field(
        payload=payload, section="places", index=0, field="layout", value={"x": 9999, "y": -1}
    )
    set_field(
        payload=payload,
        section="places",
        index=0,
        field="annotations",
        value={"role_hint": "changed"},
    )
    set_field(
        payload=payload, section="transitions", index=0, field="annotations", value={"silent": True}
    )


def drop_second_place(payload: dict[str, object]) -> None:
    """Remove the second place and the second arc (structure change)."""
    elements_of(payload=payload, section="places").pop()
    elements_of(payload=payload, section="arcs").pop()


INVARIANCE_CASES: list[tuple[str, Callable[[dict[str, object]], None]]] = [
    ("ids renamed", rename_ids),
    ("layout and annotations changed", redecorate),
    (
        "metadata changed",
        lambda payload: set_root(payload=payload, field="metadata", value={"source": "hand"}),
    ),
]


@pytest.mark.parametrize(
    ("case_name", "mutate"), INVARIANCE_CASES, ids=[case[0] for case in INVARIANCE_CASES]
)
def test_cosmetic_changes_do_not_change_the_hash(
    case_name: str, mutate: Callable[[dict[str, object]], None]
) -> None:
    """Renaming ids, moving nodes or editing annotations/metadata keeps the hash."""
    assert compute_content_hash(model=variant_of(mutate=mutate)) == sample_model().content_hash


SENSITIVITY_CASES: list[tuple[str, Callable[[dict[str, object]], None]]] = [
    (
        "arc added",
        lambda payload: elements_of(payload=payload, section="arcs").append(
            {
                "id": "ar_033",
                "source": "pl_012",
                "target": "tr_004",
                "weight": 1,
                "annotations": {},
            }
        ),
    ),
    ("place removed", drop_second_place),
    (
        "initial marking changed",
        lambda payload: set_field(
            payload=payload, section="places", index=0, field="initial_marking", value=0
        ),
    ),
    (
        "arc weight changed",
        lambda payload: set_field(
            payload=payload, section="arcs", index=0, field="weight", value=2
        ),
    ),
    (
        "element name changed",
        lambda payload: set_field(
            payload=payload, section="places", index=0, field="name", value="ponder_1"
        ),
    ),
]


@pytest.mark.parametrize(
    ("case_name", "mutate"), SENSITIVITY_CASES, ids=[case[0] for case in SENSITIVITY_CASES]
)
def test_semantic_changes_change_the_hash(
    case_name: str, mutate: Callable[[dict[str, object]], None]
) -> None:
    """Structure, initial marking, arc weights and names are all hash-covered."""
    assert variant_of(mutate=mutate).content_hash != sample_model().content_hash


def test_hash_has_the_canonical_format() -> None:
    assert CONTENT_HASH_REGEX.fullmatch(sample_model().content_hash) is not None


REJECTION_CASES: list[tuple[str, Callable[[dict[str, object]], None], str]] = [
    (
        "unknown arc endpoint",
        lambda p: set_field(payload=p, section="arcs", index=0, field="target", value="tr_x"),
        "unknown element id",
    ),
    (
        "duplicate element ids",
        lambda p: set_field(payload=p, section="transitions", index=0, field="id", value="pl_011"),
        "ids must be unique",
    ),
    (
        "duplicate element names",
        lambda p: set_field(
            payload=p, section="transitions", index=0, field="name", value="think_1"
        ),
        "names must be unique",
    ),
    (
        "negative initial marking",
        lambda p: set_field(
            payload=p, section="places", index=0, field="initial_marking", value=-1
        ),
        "greater than or equal to 0",
    ),
    (
        "zero arc weight",
        lambda p: set_field(payload=p, section="arcs", index=0, field="weight", value=0),
        "greater than or equal to 1",
    ),
    (
        "unknown extra field",
        lambda p: set_root(payload=p, field="unexpected", value=True),
        "unexpected",
    ),
    (
        "malformed content hash",
        lambda p: set_root(payload=p, field="content_hash", value="sha256:not-hex"),
        "content_hash must be",
    ),
    (
        "tampered content hash",
        lambda p: set_root(payload=p, field="content_hash", value="sha256:" + "a" * 64),
        "content_hash mismatch",
    ),
    (
        "pt net with color domain",
        lambda p: set_root(payload=p, field="color_domain", value={"philosopher": ["thinking"]}),
        "color_domain",
    ),
]


@pytest.mark.parametrize(
    ("case_name", "mutate", "match"), REJECTION_CASES, ids=[case[0] for case in REJECTION_CASES]
)
def test_invalid_input_is_rejected_with_an_explicit_error(
    case_name: str, mutate: Callable[[dict[str, object]], None], match: str
) -> None:
    """Every schema violation raises InvalidCanonicalModelError with a message."""
    payload = canonical_payload()
    mutate(payload)
    with pytest.raises(InvalidCanonicalModelError, match=match):
        validate_canonical_model(payload=payload)


def test_malformed_json_is_rejected_with_an_explicit_error() -> None:
    with pytest.raises(InvalidCanonicalModelError, match="Not valid JSON"):
        parse_canonical_json(text="{not json")


def test_non_object_document_is_rejected() -> None:
    with pytest.raises(InvalidCanonicalModelError, match="must be a JSON object"):
        validate_canonical_model(payload=[])


class TestLogNarration:
    def test_hash_computation_narrates_a_debug_event(self) -> None:
        with structlog.testing.capture_logs() as events:
            model = sample_model()
        hash_events = [e for e in events if e["event"] == "canonical_content_hash_computed"]
        assert len(hash_events) == 1
        assert hash_events[0]["model_id"] == "mdl_8f3ac1"
        assert hash_events[0]["content_hash"] == model.content_hash

    def test_validation_narrates_a_debug_event(self) -> None:
        with structlog.testing.capture_logs() as events:
            parse_canonical_json(text=serialize_model(model=sample_model()))
        assert any(e["event"] == "canonical_model_validated" for e in events)

    def test_rejection_narrates_a_warning(self) -> None:
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(InvalidCanonicalModelError),
        ):
            parse_canonical_json(text="{not json")
        assert any(e["event"] == "canonical_model_rejected" for e in events)


@st.composite
def random_payload(draw: st.DrawFn) -> dict[str, object]:
    """Draw a random small P/T net as a raw JSON document."""
    place_count = draw(st.integers(min_value=0, max_value=4))
    transition_count = draw(st.integers(min_value=0, max_value=3))
    places = [
        {
            "id": f"pl_{index}",
            "name": f"place_{index}",
            "initial_marking": draw(st.integers(min_value=0, max_value=3)),
            "layout": {"x": 0, "y": 0},
            "annotations": {},
        }
        for index in range(place_count)
    ]
    transitions = [
        {
            "id": f"tr_{index}",
            "name": f"transition_{index}",
            "layout": {"x": 0, "y": 0},
            "annotations": {},
        }
        for index in range(transition_count)
    ]
    element_ids = [place["id"] for place in places] + [t["id"] for t in transitions]
    arc_count = draw(st.integers(min_value=0, max_value=5)) if element_ids else 0
    arcs = [
        {
            "id": f"ar_{index}",
            "source": element_ids[draw(st.integers(min_value=0, max_value=len(element_ids) - 1))],
            "target": element_ids[draw(st.integers(min_value=0, max_value=len(element_ids) - 1))],
            "weight": draw(st.integers(min_value=1, max_value=3)),
            "annotations": {},
        }
        for index in range(arc_count)
    ]
    return {
        "id": "mdl_property",
        "formalism": "pt",
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
        "metadata": {},
    }


@pytest.mark.property
@settings(deadline=None)
@given(payload=random_payload())
def test_property_round_trip_is_lossless(payload: dict[str, object]) -> None:
    """Any valid net survives serialize → parse without loss."""
    model = build_canonical_model(payload=payload)
    assert parse_canonical_json(text=serialize_model(model=model)) == model


@pytest.mark.property
@settings(deadline=None)
@given(payload=random_payload(), suffix=st.text(alphabet="rstuvwxyz", min_size=1, max_size=4))
def test_property_renaming_ids_never_changes_the_hash(
    payload: dict[str, object], suffix: str
) -> None:
    """Renaming every id (elements and model) never changes the content hash."""
    model = build_canonical_model(payload=payload)
    rename_ids(payload, suffix=suffix)
    renamed = build_canonical_model(payload=payload)
    assert compute_content_hash(model=renamed) == model.content_hash
