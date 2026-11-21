"""Tests for the canonical net digest (P73, annexe B §B.2).

`net_digest(model, level)` is a pure domain service: three levels
(`profile`, `places-only`, `trace`), token ceilings enforced with a grouped
summary then an explicit degradation — never a silent truncation.
"""

from pathlib import Path

import pytest

from petrichor.llm.digest import (
    DIGEST_TOKEN_CEILING,
    TRACE_MAX_STEPS,
    AcceptedRole,
    DigestError,
    estimate_tokens,
    group_prefix,
    net_digest,
)
from petrichor.petri_canonical import (
    Arc,
    CanonicalModel,
    ContentHash,
    ElementId,
    ElementName,
    Layout,
    ModelId,
    Place,
    Transition,
)
from petrichor.pnml_import import import_pnml

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_MODEL = import_pnml(
    text=(FIXTURES / "philosophes3-maquette.pnml").read_text(encoding="utf-8")
)


def make_model(place_count: int = 2) -> CanonicalModel:
    """A small synthetic P/T net: p0 -> t0 -> p1 (plus extra places if asked)."""
    places = [
        Place(
            id=ElementId(f"p{i}"),
            name=ElementName(f"place_{i}"),
            layout=Layout(x=float(i), y=0.0),
            annotations={},
            initial_marking=1 if i == 0 else 0,
        )
        for i in range(place_count)
    ]
    transition = Transition(
        id=ElementId("t0"),
        name=ElementName("fire_0"),
        layout=Layout(x=0.5, y=1.0),
        annotations={},
    )
    arcs = (
        Arc(
            id=ElementId("a0"),
            source=ElementId("p0"),
            target=ElementId("t0"),
            weight=1,
            annotations={},
        ),
        Arc(
            id=ElementId("a1"),
            source=ElementId("t0"),
            target=ElementId("p1"),
            weight=2,
            annotations={},
        ),
    )
    return CanonicalModel(
        id=ModelId("model_test"),
        formalism="pt",
        content_hash=ContentHash("sha256:" + "0" * 64),
        parent=None,
        children=(),
        derivations=(),
        certificates=(),
        places=tuple(places),
        transitions=(transition,),
        arcs=arcs,
        color_domain=None,
        metadata={},
    )


class TestGroupPrefix:
    def test_numeric_suffix_is_stripped(self) -> None:
        assert group_prefix("fork_1") == "fork"
        assert group_prefix("takeL_12") == "takeL"

    def test_id_without_numeric_suffix_is_its_own_prefix(self) -> None:
        assert group_prefix("deadlock") == "deadlock"


class TestProfileLevel:
    def test_profile_digest_carries_the_full_net(self) -> None:
        digest = net_digest(PHILOSOPHES3_MODEL, "profile")
        assert digest["level"] == "profile"
        assert digest["totals"] == {"places": 12, "transitions": 9, "arcs": 30}
        places = digest["places"]
        assert len(places) == 12
        first = places[0]
        assert {"id", "name", "initial_marking", "layout"} <= set(first)
        incidence = digest["incidence"]
        assert len(incidence) == 9
        assert all({"transition", "consumes", "produces"} <= set(entry) for entry in incidence)
        assert digest["arc_degrees"]
        assert digest["groupings"], "the maquette's fork_1..5 must be grouped"
        prefixes = {group["prefix"] for group in digest["groupings"]}
        assert "fork" in prefixes

    def test_profile_digest_respects_the_token_ceiling(self) -> None:
        digest = net_digest(PHILOSOPHES3_MODEL, "profile")
        assert estimate_tokens(digest) <= DIGEST_TOKEN_CEILING

    def test_profile_digest_degrades_to_a_grouped_summary_over_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = net_digest(PHILOSOPHES3_MODEL, "profile")
        # A ceiling between the full digest and its grouped summary.
        monkeypatch.setattr("petrichor.llm.digest.DIGEST_TOKEN_CEILING", estimate_tokens(full) - 1)
        digest = net_digest(PHILOSOPHES3_MODEL, "profile")
        assert digest["degraded"] == "grouped-summary"
        assert digest["totals"]["places"] == 12
        assert "places" not in digest, "no silent truncation: the full list is replaced, not cut"
        # The counters must agree with the full digest's incidence direction
        # (place→transition consumes, transition→place produces).
        full_incidence = {entry["transition"]: entry for entry in full["incidence"]}
        for transition_id, counters in digest["incidence_counters"].items():
            assert counters["consumes"] == len(full_incidence[transition_id]["consumes"])
            assert counters["produces"] == len(full_incidence[transition_id]["produces"])

    def test_profile_digest_raises_explicitly_when_even_the_summary_is_too_big(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("petrichor.llm.digest.DIGEST_TOKEN_CEILING", 1)
        with pytest.raises(DigestError, match="grouped summary"):
            net_digest(PHILOSOPHES3_MODEL, "profile")


class TestPlacesOnlyLevel:
    def test_only_the_cited_places_are_digested_with_their_roles(self) -> None:
        roles = {"eat_1": AcceptedRole(role="état du philosophe", confidence=0.9)}
        digest = net_digest(
            PHILOSOPHES3_MODEL,
            "places-only",
            formulas=("EF (eat_1)", "AG (eat_1 -> AF think_1)"),
            roles=roles,
        )
        assert digest["level"] == "places-only"
        names = [place["name"] for place in digest["places"]]
        assert "eat_1" in names and "think_1" in names
        assert "fork_1" not in names
        eat = next(place for place in digest["places"] if place["name"] == "eat_1")
        assert eat["role"] == "état du philosophe"
        assert eat["confidence"] == 0.9
        think = next(place for place in digest["places"] if place["name"] == "think_1")
        assert think["role"] is None

    def test_places_only_over_the_token_ceiling_degrades_to_counters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = net_digest(PHILOSOPHES3_MODEL, "places-only", formulas=("EF (eat_1)",))
        monkeypatch.setattr("petrichor.llm.digest.DIGEST_TOKEN_CEILING", estimate_tokens(full) - 1)
        digest = net_digest(PHILOSOPHES3_MODEL, "places-only", formulas=("EF (eat_1)",))
        assert digest["degraded"] == "grouped-summary"
        assert digest["cited_place_count"] == 1
        assert "places" not in digest, "no silent truncation: the full list is replaced, not cut"


class TestTraceLevel:
    def test_trace_digest_describes_each_fired_step(self) -> None:
        roles = {"hasL_1": AcceptedRole(role="ressource fourchette", confidence=1.0)}
        digest = net_digest(
            PHILOSOPHES3_MODEL, "trace", trace=("takeL_1", "releaseL_1"), roles=roles
        )
        assert digest["level"] == "trace"
        assert digest["step_count"] == 2
        first = digest["steps"][0]
        assert first["step"] == 1
        assert first["transition"]["id"] == "takeL_1"
        assert first["consumes"] and first["produces"]
        assert first["roles"].get("hasL_1") == "ressource fourchette"

    def test_trace_over_50_steps_degrades_explicitly(self) -> None:
        with pytest.raises(DigestError, match=str(TRACE_MAX_STEPS)):
            net_digest(PHILOSOPHES3_MODEL, "trace", trace=tuple(f"t{i}" for i in range(51)))

    def test_trace_over_the_token_ceiling_degrades_explicitly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("petrichor.llm.digest.DIGEST_TOKEN_CEILING", 10)
        with pytest.raises(DigestError, match="token ceiling"):
            net_digest(PHILOSOPHES3_MODEL, "trace", trace=("takeL_1",))


class TestPurity:
    def test_same_model_same_digest(self) -> None:
        first = net_digest(PHILOSOPHES3_MODEL, "profile")
        second = net_digest(PHILOSOPHES3_MODEL, "profile")
        assert first == second

    def test_synthetic_model_incidence_weights(self) -> None:
        digest = net_digest(make_model(), "profile")
        entry = next(e for e in digest["incidence"] if e["transition"] == "t0")
        assert entry["consumes"] == [{"place": "p0", "weight": 1}]
        assert entry["produces"] == [{"place": "p1", "weight": 2}]
