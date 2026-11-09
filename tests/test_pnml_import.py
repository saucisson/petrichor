"""Tests for the PNML importer (issue #3, spec §13, §19.4, §18.5).

The acceptance criteria are pinned directly: P/T import into the canonical
form with a computed hash, Symmetric stored non-unfolded, explicit rejection
of invalid or unsupported PNML, deterministic ids, and log narration
(ADR-0005). A property tier pins hash invariance under cosmetic PNML changes.
"""

import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.petri_canonical import (
    CONTENT_HASH_PATTERN,
    compute_content_hash,
    parse_canonical_json,
    serialize_model,
)
from petrichor.pnml_import import (
    MalformedPnmlError,
    UnsupportedPnmlError,
    import_pnml,
)

FIXTURES = Path(__file__).parent / "fixtures"

SAMPLE_PNML = """<?xml version="1.0" encoding="UTF-8"?>
<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">
  <net id="sample" type="http://www.pnml.org/version-2009/grammar/pnml">
    <place id="p1">
      <name><value>think_1</value></name>
      <initialMarking><text>1</text></initialMarking>
      <graphics><position x="320" y="78"/></graphics>
    </place>
    <place id="p2">
      <initialMarking><text>0</text></initialMarking>
    </place>
    <transition id="t1">
      <name><value>take_1</value></name>
      <graphics><position x="254" y="102"/></graphics>
    </transition>
    <arc id="a1" source="p1" target="t1">
      <inscription><text>2</text></inscription>
    </arc>
    <arc id="a2" source="t1" target="p2"/>
  </net>
</pnml>
"""

SYMMETRIC_PNML = """<?xml version="1.0" encoding="UTF-8"?>
<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">
  <net id="sym" type="http://www.pnml.org/version-2009/grammar/pnml+symmetric">
    <place id="p1">
      <name><value>think_1</value></name>
      <type><value>Philosopher</value></type>
      <initialMarking><text>1</text></initialMarking>
    </place>
    <place id="p2">
      <name><value>fork_1</value></name>
      <type><value>Fork</value></type>
      <initialMarking><text>1</text></initialMarking>
    </place>
    <transition id="t1">
      <name><value>take_1</value></name>
    </transition>
    <arc id="a1" source="p1" target="t1"/>
    <arc id="a2" source="p2" target="t1"/>
    <arc id="a3" source="t1" target="p1"/>
  </net>
</pnml>
"""


def fixed_clock() -> datetime:
    """A fixed clock, so imports are deterministic in tests (spec §19.4)."""
    return datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)


class TestPtImport:
    def test_pt_net_imports_as_canonical_pt_with_computed_hash(self) -> None:
        model = import_pnml(text=SAMPLE_PNML, clock=fixed_clock)
        assert model.formalism == "pt"
        assert CONTENT_HASH_PATTERN.fullmatch(model.content_hash) is not None
        assert model.content_hash == compute_content_hash(model=model)
        assert model.color_domain is None

    def test_elements_keep_names_marking_layout_and_weights(self) -> None:
        model = import_pnml(text=SAMPLE_PNML, clock=fixed_clock)
        places = {place.name: place for place in model.places}
        assert places["think_1"].initial_marking == 1
        assert places["think_1"].layout.x == 320
        assert places["think_1"].layout.y == 78
        assert places["p2"].initial_marking == 0
        assert places["p2"].layout.x == 0.0
        arcs = {(arc.source, arc.target): arc.weight for arc in model.arcs}
        assert arcs[("pl_p1", "tr_t1")] == 2
        assert arcs[("tr_t1", "pl_p2")] == 1

    def test_model_id_is_derived_from_the_content_hash(self) -> None:
        model = import_pnml(text=SAMPLE_PNML, clock=fixed_clock)
        digest = model.content_hash.removeprefix("sha256:")
        assert model.id == f"mdl_{digest[:8]}"
        assert re.fullmatch(r"mdl_[0-9a-f]{8}", model.id) is not None

    def test_import_is_deterministic_and_round_trips(self) -> None:
        first = import_pnml(text=SAMPLE_PNML, clock=fixed_clock)
        second = import_pnml(text=SAMPLE_PNML, clock=fixed_clock)
        assert first.id == second.id
        assert serialize_model(model=first) == serialize_model(model=second)
        assert parse_canonical_json(text=serialize_model(model=first)) == first
        assert first.metadata["source"] == "pnml"
        assert first.metadata["imported_at"] == "2026-11-09T12:00:00+00:00"

    def test_philosophes3_fixture_imports(self) -> None:
        model = import_pnml(
            text=(FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8"),
            clock=fixed_clock,
        )
        assert model.formalism == "pt"
        assert len(model.places) == 9
        assert len(model.transitions) == 6
        assert len(model.arcs) == 24


class TestSymmetricImport:
    def test_symmetric_net_is_stored_non_unfolded(self) -> None:
        model = import_pnml(text=SYMMETRIC_PNML, clock=fixed_clock)
        assert model.formalism == "symmetric"
        assert model.color_domain == {"classes": ["Fork", "Philosopher"]}
        assert len(model.places) == 2
        assert len(model.transitions) == 1
        assert len(model.arcs) == 3

    def test_symmetric_fixture_is_not_unfolded(self) -> None:
        model = import_pnml(
            text=(FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8"),
            clock=fixed_clock,
        )
        assert model.formalism == "symmetric"
        assert model.color_domain == {"classes": ["Fork", "Philosopher"]}
        assert len(model.places) == 9
        assert len(model.transitions) == 6
        assert len(model.arcs) == 24

    def test_pt_and_symmetric_variants_do_not_share_a_hash(self) -> None:
        pt_model = import_pnml(
            text=(FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8"),
            clock=fixed_clock,
        )
        symmetric_model = import_pnml(
            text=(FIXTURES / "philosophes3-symmetric.pnml").read_text(encoding="utf-8"),
            clock=fixed_clock,
        )
        assert pt_model.content_hash != symmetric_model.content_hash


REJECTION_CASES: list[tuple[str, str, str]] = [
    ("malformed xml", "<pnml><net>", "Not well-formed XML"),
    ("wrong root element", "<html></html>", "Root element must be <pnml>"),
    ("no net", "<pnml></pnml>", "exactly 1 <net>"),
    (
        "two nets",
        '<pnml><net id="a"/><net id="b"/></pnml>',
        "exactly 1 <net>",
    ),
    (
        "unsupported stochastic net type",
        '<pnml><net type="http://www.pnml.org/version-2009/grammar/pnml+stochastic">'
        '<place id="p1"/></net></pnml>',
        "Unsupported PNML net type",
    ),
    (
        "arc with unknown endpoint",
        '<pnml><net><place id="p1"/><arc id="a1" source="p1" target="ghost"/></net></pnml>',
        "references unknown element 'ghost'",
    ),
    (
        "arc missing target",
        '<pnml><net><place id="p1"/><arc id="a1" source="p1"/></net></pnml>',
        "missing its source or target",
    ),
    (
        "place missing id",
        "<pnml><net><place><name><value>x</value></name></place></net></pnml>",
        "missing its id attribute",
    ),
    (
        "duplicate element ids",
        '<pnml><net><place id="p1"/><transition id="p1"/></net></pnml>',
        "Duplicate element id 'p1'",
    ),
    (
        "duplicate arc ids",
        '<pnml><net><place id="p1"/><arc id="a1" source="p1" target="p1"/>'
        '<arc id="a1" source="p1" target="p1"/></net></pnml>',
        "Duplicate arc id 'a1'",
    ),
    (
        "negative initial marking",
        '<pnml><net><place id="p1"><initialMarking><text>-1</text></initialMarking>'
        "</place></net></pnml>",
        "initialMarking must be >= 0",
    ),
    (
        "non-integer marking",
        '<pnml><net><place id="p1"><initialMarking><text>many</text></initialMarking>'
        "</place></net></pnml>",
        "initialMarking must be an integer",
    ),
    (
        "zero arc weight",
        '<pnml><net><place id="p1"/><transition id="t1"/>'
        '<arc id="a1" source="p1" target="t1"><inscription><text>0</text></inscription>'
        "</arc></net></pnml>",
        "inscription must be >= 1",
    ),
    (
        "duplicate element names",
        '<pnml><net><place id="p1"><name><value>same</value></name></place>'
        '<place id="p2"><name><value>same</value></name></place></net></pnml>',
        "not a valid canonical model",
    ),
]


@pytest.mark.parametrize(
    ("case_name", "pnml", "match"), REJECTION_CASES, ids=[case[0] for case in REJECTION_CASES]
)
def test_invalid_or_unsupported_pnml_is_rejected_explicitly(
    case_name: str, pnml: str, match: str
) -> None:
    """Invalid or unsupported PNML raises a typed error - never a false unfolding."""
    with pytest.raises((MalformedPnmlError, UnsupportedPnmlError), match=match):
        import_pnml(text=pnml, clock=fixed_clock)


def test_unsupported_net_type_raises_the_unsupported_error_type() -> None:
    pnml = '<pnml><net type="http://example.org/high-level"><place id="p1"/></net></pnml>'
    with pytest.raises(UnsupportedPnmlError, match="Unsupported PNML net type"):
        import_pnml(text=pnml, clock=fixed_clock)


class TestFieldFallbacksAndRejections:
    def test_empty_name_value_falls_back_to_the_raw_id(self) -> None:
        pnml = (
            '<pnml><net><place id="p1"><name><value>  </value></name></place>'
            '<place id="p2"><name><value/></name></place></net></pnml>'
        )
        model = import_pnml(text=pnml, clock=fixed_clock)
        names = {place.name for place in model.places}
        assert names == {"p1", "p2"}

    def test_invalid_graphics_position_is_rejected(self) -> None:
        pnml = (
            '<pnml><net><place id="p1"><graphics><position x="abc" y="1"/></graphics>'
            "</place></net></pnml>"
        )
        with pytest.raises(MalformedPnmlError, match="Invalid graphics position"):
            import_pnml(text=pnml, clock=fixed_clock)

    def test_transition_missing_id_is_rejected(self) -> None:
        pnml = "<pnml><net><transition/></net></pnml>"
        with pytest.raises(MalformedPnmlError, match="<transition> is missing its id"):
            import_pnml(text=pnml, clock=fixed_clock)

    def test_arc_missing_id_is_rejected(self) -> None:
        pnml = '<pnml><net><place id="p1"/><arc source="p1" target="p1"/></net></pnml>'
        with pytest.raises(MalformedPnmlError, match="<arc> is missing its id"):
            import_pnml(text=pnml, clock=fixed_clock)


class TestLogNarration:
    def test_successful_import_narrates_a_debug_event(self) -> None:
        with structlog.testing.capture_logs() as events:
            model = import_pnml(text=SAMPLE_PNML, clock=fixed_clock)
        success = [e for e in events if e["event"] == "pnml_import_succeeded"]
        assert len(success) == 1
        assert success[0]["model_id"] == model.id
        assert success[0]["content_hash"] == model.content_hash
        assert success[0]["formalism"] == "pt"

    def test_rejection_narrates_a_warning(self) -> None:
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(MalformedPnmlError),
        ):
            import_pnml(text="<pnml><net>", clock=fixed_clock)
        assert any(e["event"] == "pnml_import_rejected" for e in events)


def pnml_of_net(*, place_names: list[str], arc_pairs: list[tuple[str, str]], id_prefix: str) -> str:
    """Render a small P/T net as PNML with the given raw id prefix."""
    places = "".join(
        f'<place id="{id_prefix}p{index}"><name><value>{name}</value></name>'
        "<initialMarking><text>1</text></initialMarking></place>"
        for index, name in enumerate(place_names)
    )
    transitions = f'<transition id="{id_prefix}t0"><name><value>t0</value></name></transition>'
    arcs = "".join(
        f'<arc id="{id_prefix}a{index}" source="{id_prefix}p{source}"'
        f' target="{id_prefix}p{target}"/>'
        for index, (source, target) in enumerate(arc_pairs)
    )
    return (
        f'<pnml xmlns="http://www.pnml.org/version-2009/grammar/pnml">'
        f"<net>{places}{transitions}{arcs}</net></pnml>"
    )


@st.composite
def random_net(draw: st.DrawFn) -> tuple[list[str], list[tuple[int, int]]]:
    """Draw a random small net as place names and arc endpoint indexes."""
    place_count = draw(st.integers(min_value=1, max_value=4))
    place_names = [f"place_{index}" for index in range(place_count)]
    arc_count = draw(st.integers(min_value=0, max_value=4))
    arc_pairs = [
        (
            draw(st.integers(min_value=0, max_value=place_count - 1)),
            draw(st.integers(min_value=0, max_value=place_count - 1)),
        )
        for _ in range(arc_count)
    ]
    return place_names, arc_pairs


@pytest.mark.property
@settings(deadline=None)
@given(net=random_net())
def test_property_cosmetic_pnml_changes_never_change_the_hash(
    net: tuple[list[str], list[tuple[int, int]]],
) -> None:
    """Renaming raw PNML ids never changes the imported content hash (spec §13)."""
    place_names, arc_pairs = net
    first = import_pnml(
        text=pnml_of_net(place_names=place_names, arc_pairs=arc_pairs, id_prefix="x"),
        clock=fixed_clock,
    )
    second = import_pnml(
        text=pnml_of_net(place_names=place_names, arc_pairs=arc_pairs, id_prefix="y"),
        clock=fixed_clock,
    )
    assert first.content_hash == second.content_hash
    assert first.id == second.id


@pytest.mark.property
@settings(deadline=None)
@given(net=random_net())
def test_property_imported_net_round_trips_through_the_canonical_form(
    net: tuple[list[str], list[tuple[int, int]]],
) -> None:
    """Any imported net survives serialize → parse without loss."""
    place_names, arc_pairs = net
    model = import_pnml(
        text=pnml_of_net(place_names=place_names, arc_pairs=arc_pairs, id_prefix="x"),
        clock=fixed_clock,
    )
    assert parse_canonical_json(text=serialize_model(model=model)) == model
