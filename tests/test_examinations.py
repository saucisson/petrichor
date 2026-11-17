"""The MCC examination mapping and the per-tool dialect translation (spec §17.3, issue #30).

Every ``kind`` pattern of every check family maps to exactly one MCC
examination; safety is verified twice, independently (CTLCardinality on the
invariant as-is, ReachabilityCardinality on the complement ``EF ¬φ``);
liveness and recovery are pure CTLCardinality, never a silent LTL rewrite;
each qualified tool receives the query in its own dialect, and smpt rejects
any non-reachability examination with a typed error instead of rewriting it.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.checks import FAMILY_PATTERNS, CheckFamilyName, KindPattern
from petrichor.examinations import (
    CTL_CARDINALITY,
    DEADLOCK_QUERY,
    EXAMINATION_BY_KIND,
    FIREABILITY,
    REACHABILITY_CARDINALITY,
    REACHABILITY_DEADLOCK,
    EngineDialect,
    ExaminationName,
    UnknownKindError,
    complement_formula,
    dialect_query,
    examination_for,
    safety_counter_variants,
)

ALL_FAMILIES: tuple[CheckFamilyName, ...] = tuple(FAMILY_PATTERNS.keys())
ALL_KINDS = tuple(kind for kinds in FAMILY_PATTERNS.values() for kind in kinds)
LIVENESS_RECOVERY_FAMILIES: tuple[CheckFamilyName, ...] = ("liveness", "recovery")
LIVENESS_RECOVERY_KINDS = tuple(
    kind for family in LIVENESS_RECOVERY_FAMILIES for kind in FAMILY_PATTERNS[family]
)
SAFETY_KINDS = tuple(FAMILY_PATTERNS["safety"])
ENGINES: tuple[EngineDialect, ...] = ("verifypn", "its-tools", "smpt")
INVARIANT_FORMULA = "AG (fork_1 <= 1)"


class TestExaminationMapping:
    def test_every_family_kind_has_exactly_one_examination(self) -> None:
        """The mapping is total over FAMILY_PATTERNS: no kind is left unmapped."""
        for family in ALL_FAMILIES:
            for kind in FAMILY_PATTERNS[family]:
                assert kind in EXAMINATION_BY_KIND
        assert set(EXAMINATION_BY_KIND) == set(ALL_KINDS)

    @pytest.mark.parametrize("family", ALL_FAMILIES)
    @pytest.mark.parametrize("kind", ALL_KINDS)
    def test_examination_for_accepts_only_kinds_of_the_family(
        self, family: CheckFamilyName, kind: str
    ) -> None:
        """A kind of the family maps to its examination; any other kind is a typed error."""
        if kind in FAMILY_PATTERNS[family]:
            assert (
                examination_for(family=family, kind=KindPattern(kind)) == EXAMINATION_BY_KIND[kind]
            )
        else:
            with pytest.raises(UnknownKindError):
                examination_for(family=family, kind=KindPattern(kind))

    def test_liveness_and_recovery_are_pure_ctl_cardinality_never_ltl(self) -> None:
        """Liveness and recovery map to CTLCardinality; no silent LTL rewrite (spec §17.3)."""
        for kind in LIVENESS_RECOVERY_KINDS:
            assert EXAMINATION_BY_KIND[kind] == CTL_CARDINALITY

    def test_deadlock_maps_to_reachability_deadlock(self) -> None:
        """The deadlock family maps to GlobalProperties.ReachabilityDeadlock."""
        assert EXAMINATION_BY_KIND["deadlock"] == REACHABILITY_DEADLOCK


class TestSafetyCounterVariants:
    def test_complement_formula_parenthesizes_the_negation(self) -> None:
        """The complement of ``φ`` is ``not (φ)``, parenthesized to bind tightly."""
        assert complement_formula(formula=INVARIANT_FORMULA) == f"not ({INVARIANT_FORMULA})"

    @pytest.mark.parametrize("kind", sorted(SAFETY_KINDS))
    def test_safety_is_two_independent_verifications(self, kind: str) -> None:
        """CTLCardinality on ``AG φ`` and ReachabilityCardinality on ``EF ¬φ`` (spec §17.3)."""
        ctl_variant, reachability_variant = safety_counter_variants(
            kind=KindPattern(kind), formula=INVARIANT_FORMULA
        )
        assert ctl_variant.examination == CTL_CARDINALITY
        assert ctl_variant.family == "safety"
        assert ctl_variant.formula == INVARIANT_FORMULA
        assert reachability_variant.examination == REACHABILITY_CARDINALITY
        assert reachability_variant.family == "reachability"
        assert reachability_variant.formula == f"EF (not ({INVARIANT_FORMULA}))"
        assert ctl_variant.examination != reachability_variant.examination


class TestDialectQuery:
    @pytest.mark.parametrize("engine", ("verifypn", "its-tools"))
    def test_ctl_tools_receive_the_formula_as_is(self, engine: EngineDialect) -> None:
        """verifypn (PQL) and its-tools accept CTL as-is (spec §17.3)."""
        assert (
            dialect_query(engine=engine, examination=CTL_CARDINALITY, formula=INVARIANT_FORMULA)
            == INVARIANT_FORMULA
        )

    @pytest.mark.parametrize("engine", ENGINES)
    def test_deadlock_maps_to_the_fixed_query_on_every_tool(self, engine: EngineDialect) -> None:
        """The deadlock examination is the fixed ``EF deadlock`` query on every tool."""
        assert (
            dialect_query(engine=engine, examination=REACHABILITY_DEADLOCK, formula="whatever")
            == DEADLOCK_QUERY
        )

    def test_smpt_accepts_reachability_only(self) -> None:
        """smpt passes a reachability formula through (spec §17.2)."""
        assert (
            dialect_query(
                engine="smpt",
                examination=REACHABILITY_CARDINALITY,
                formula="EF (eat_1)",
            )
            == "EF (eat_1)"
        )

    @pytest.mark.parametrize("examination", (CTL_CARDINALITY, FIREABILITY))
    def test_smpt_rejects_non_reachability_with_a_typed_error(
        self, examination: ExaminationName
    ) -> None:
        """A CTL/LTL examination on smpt is a typed error, never a silent rewrite."""
        with pytest.raises(UnknownKindError):
            dialect_query(
                engine="smpt",
                examination=examination,
                formula=INVARIANT_FORMULA,
            )


class TestExaminationPropertyTier:
    @pytest.mark.property
    @settings(deadline=None, max_examples=50)
    @given(kind=st.sampled_from(ALL_KINDS))
    def test_mapping_is_stable_and_total_for_every_kind(self, kind: str) -> None:
        """For any kind of the closed set, the examination is a fixed, total mapping."""
        family = next(family for family in ALL_FAMILIES if kind in FAMILY_PATTERNS[family])
        assert examination_for(family=family, kind=KindPattern(kind)) == EXAMINATION_BY_KIND[kind]
