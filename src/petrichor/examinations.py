"""MCC examination mapping and per-tool dialect translation (spec §17.3, issue #30).

Each ``kind`` pattern of a check family maps to exactly one MCC examination
(§17.3): liveness and recovery are pure CTLCardinality (``AG AF`` / ``AG EF``),
never a silent LTL rewrite; safety is CTLCardinality (``AG``) with the
ReachabilityCardinality complement available as an independent second
verification; deadlock is GlobalProperties.ReachabilityDeadlock.

This module is pure domain data: it never imports ``petrichor.engines``
(verdict lock, spec §19.4) - the engine names are plain strings here, the
engines package owns its own constants.
"""

from typing import Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import FAMILY_PATTERNS, CheckFamilyName, KindPattern

ExaminationName = NewType("ExaminationName", str)
"""An MCC examination name, e.g. ``CTLCardinality`` (spec §17.3)."""

REACHABILITY_CARDINALITY = ExaminationName("ReachabilityCardinality")
FIREABILITY = ExaminationName("Fireability")
CTL_CARDINALITY = ExaminationName("CTLCardinality")
REACHABILITY_DEADLOCK = ExaminationName("GlobalProperties.ReachabilityDeadlock")
STRUCTURAL = ExaminationName("Structural")

EXAMINATION_BY_KIND: dict[str, ExaminationName] = {
    "reachability.ef": REACHABILITY_CARDINALITY,
    "reachability.fireability": FIREABILITY,
    "safety.invariant": CTL_CARDINALITY,
    "safety.conservation": CTL_CARDINALITY,
    "safety.state_invariant": CTL_CARDINALITY,
    "liveness.unconditional": CTL_CARDINALITY,
    "liveness.conditional": CTL_CARDINALITY,
    "liveness.conditional_disj": CTL_CARDINALITY,
    "recovery.unconditional": CTL_CARDINALITY,
    "deadlock": REACHABILITY_DEADLOCK,
    "structural.precondition": STRUCTURAL,
    "structural.producer": STRUCTURAL,
    "structural.unique_producer": STRUCTURAL,
}
"""The closed mapping kind → MCC examination; covers every kind of FAMILY_PATTERNS."""

EngineDialect = Literal["verifypn", "its-tools", "smpt"]
"""The qualified tools with a dialect translation (P22); plain strings, no engines import."""

DEADLOCK_QUERY = "EF deadlock"
"""The fixed deadlock query of the GlobalProperties.ReachabilityDeadlock examination."""


class UnknownKindError(ValueError):
    """The kind pattern does not belong to the family (spec §12.1 anti-polymorphism)."""


def examination_for(*, family: CheckFamilyName, kind: KindPattern) -> ExaminationName:
    """The MCC examination of one (family, kind) pair (spec §17.3).

    Raises `UnknownKindError` when the kind is outside the family: the
    pattern is rejected, never reinterpreted (spec §12.1).
    """
    if kind not in FAMILY_PATTERNS[family]:
        raise UnknownKindError(f"Pattern {kind!r} does not belong to family {family!r}.")
    return EXAMINATION_BY_KIND[kind]


def complement_formula(*, formula: str) -> str:
    """The negated formula, parenthesized: the complement of ``AG φ`` is ``EF ¬φ``."""
    return f"not ({formula})"


class CounterVariant(BaseModel):
    """One independent verification of a safety question (spec §17.3, issue #30).

    A safety invariant ``AG φ`` is verified twice, independently: by
    CTLCardinality on ``AG φ`` itself, and by ReachabilityCardinality on the
    complement ``EF ¬φ``. The two runs are separate; their concordance is
    computed by the cross-verification orchestrator (P23), never merged here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    examination: ExaminationName
    family: CheckFamilyName
    formula: str = Field(min_length=1)


def safety_counter_variants(
    *, kind: KindPattern, formula: str
) -> tuple[CounterVariant, CounterVariant]:
    """The two independent verifications of a safety question (spec §17.3).

    Returns the CTLCardinality counter-variant (the invariant as-is) and the
    ReachabilityCardinality complement (``EF ¬φ``), as two independent
    verifications with distinct families and examinations.
    """
    return (
        CounterVariant(examination=CTL_CARDINALITY, family="safety", formula=formula),
        CounterVariant(
            examination=REACHABILITY_CARDINALITY,
            family="reachability",
            formula=f"EF ({complement_formula(formula=formula)})",
        ),
    )


def dialect_query(*, engine: EngineDialect, examination: ExaminationName, formula: str) -> str:
    """Translate one examination into the query dialect of a qualified tool (spec §17.3).

    verifypn (PQL) and its-tools accept CTL as-is; the deadlock examination
    maps to the fixed ``EF deadlock`` query on every tool; smpt covers
    reachability only - a CTL/LTL examination on smpt is a typed error, never
    a silent rewrite (spec §17.2).
    """
    if examination == REACHABILITY_DEADLOCK:
        return DEADLOCK_QUERY
    if engine == "smpt" and examination != REACHABILITY_CARDINALITY:
        raise UnknownKindError(
            f"SMPT covers no {examination!r} examination (reachability only, spec §17.2)."
        )
    return formula
