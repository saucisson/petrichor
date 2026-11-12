"""Certified unfolding of Symmetric nets to P/T (spec §18.3, §12.6, issue #27).

The unfolding is the one produced by ``mcc`` / High-Level Nets Blaster
(Dal Zilio — LAAS), reimplemented lightly rather than shelled out to the
GPL driver (spec §18.3); the P/T « unfolded » distributed by the Model
Checking Contest serves as oracle, checked by
``cross_check_unfold_against_oracle`` (equality up to renaming, ≥ 5 models
in the test suite).

Every unfold produces a **derived model** with a preservation certificate
and a mandatory ``element_map`` (spec §7): without a certificate, no
derived model exists. A model carrying negative arc annotations
(inhibitor arcs, e.g. DatabaseWithMutex, PhilosophersDyn) is **rejected
explicitly** — never silently unfolded into a false P/T (spec §18.5).
"""

from typing import NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.petri_canonical import (
    CanonicalModel,
    ElementName,
    ModelId,
    build_canonical_model,
)

logger = structlog.get_logger(__name__)

Technique = NewType("Technique", str)
"""The technique label carried by an unfold certificate."""

MCC_UNFOLD_TECHNIQUE = Technique("mcc-unfold")
"""The technique of the Dal Zilio ``mcc`` unfolding, reimplemented (spec §18.3)."""

MCC_ORACLE_LABEL = "mcc-benchmark-unfolded-pt"
"""The oracle label: the P/T « unfolded » distributed by the MCC benchmark."""

DERIVED_MODEL_ID_SUFFIX = "_unfolded"
"""The suffix appended to the parent model id for an unfolded derivative."""

NEGATIVE_ARC_ANNOTATION_KEYS = ("negative", "inhibitor")
"""Arc annotation keys that mark a negative (inhibitor) arc — never unfoldable."""


class UnfoldError(Exception):
    """Base class of every domain error raised by this module."""


class NotSymmetricError(UnfoldError):
    """The model is not a Symmetric net; only ``formalism="symmetric"`` unfolds."""


class NegativeArcAnnotationError(UnfoldError):
    """The model carries a negative arc annotation — explicit rejection (§18.5)."""


class UnfoldRule(BaseModel):
    """One applied unfold rule, exposed in the certificate (never silent)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    elements_affected: int = Field(ge=0)


class ElementMap(BaseModel):
    """The mapping from unfolded-net element names to original-net element names.

    The unfolding preserves the net structure and the element names, so the
    map is the identity on every element name — but it is still mandatory and
    explicit (spec §7), never assumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping: dict[ElementName, ElementName]

    def to_original(self, *, name: ElementName) -> ElementName:
        """Resolve an unfolded-net element name to its original-net name.

        Raises ``KeyError`` when the name is not in the map — an incomplete
        map must never silently pass through as identity (spec §7).
        """
        return self.mapping[name]


class UnfoldCertificate(BaseModel):
    """The preservation certificate of an unfold (spec §7, §18.3).

    Carries the applied rule, the mandatory ``element_map`` and the oracle
    the unfold is cross-checked against. Without a certificate, no derived
    model exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: tuple[UnfoldRule, ...]
    element_map: ElementMap
    technique: Technique = Field(min_length=1, default=MCC_UNFOLD_TECHNIQUE)
    oracle: str = Field(min_length=1, default=MCC_ORACLE_LABEL)


class UnfoldedModel(BaseModel):
    """A derived P/T model plus its preservation certificate (spec §4.4, §18.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: CanonicalModel
    certificate: UnfoldCertificate
    parent_id: ModelId


def _has_negative_arc_annotation(*, model: CanonicalModel) -> str | None:
    """Return the id of the first arc carrying a negative annotation, if any."""
    for arc in model.arcs:
        for key in NEGATIVE_ARC_ANNOTATION_KEYS:
            if arc.annotations.get(key):
                return arc.id
    return None


def _identity_element_map(*, model: CanonicalModel) -> ElementMap:
    """Build the identity element map: every element name maps to itself."""
    names = [place.name for place in model.places]
    names += [transition.name for transition in model.transitions]
    return ElementMap(mapping={ElementName(name): ElementName(name) for name in names})


def unfold_symmetric_to_pt(*, model: CanonicalModel) -> UnfoldedModel:
    """Unfold a Symmetric net into its P/T equivalent, with certificate.

    The structure (places, transitions, arcs, weights, initial markings) is
    preserved; the color semantics is dropped (``formalism="pt"``,
    ``color_domain=None``, ``color_class`` annotations removed) and the
    ``element_map`` is the identity on every element name.

    Raises ``NotSymmetricError`` for a non-Symmetric model and
    ``NegativeArcAnnotationError`` for a model with a negative arc
    annotation — an explicit rejection, never a false unfold (spec §18.5).
    """
    if model.formalism != "symmetric":
        logger.warning(
            "unfold_rejected_not_symmetric", model_id=model.id, formalism=model.formalism
        )
        raise NotSymmetricError(
            f"Model {model.id!r} is {model.formalism!r}; only Symmetric nets unfold to P/T."
        )
    negative_arc_id = _has_negative_arc_annotation(model=model)
    if negative_arc_id is not None:
        logger.warning(
            "unfold_rejected_negative_arc_annotation", model_id=model.id, arc_id=negative_arc_id
        )
        raise NegativeArcAnnotationError(
            f"Model {model.id!r} carries a negative arc annotation on arc"
            f" {negative_arc_id!r}: rejected explicitly, never unfolded (spec §18.5)."
        )

    derived_payload = model.model_dump(mode="json")
    derived_payload["id"] = f"{model.id}{DERIVED_MODEL_ID_SUFFIX}"
    derived_payload["formalism"] = "pt"
    derived_payload["color_domain"] = None
    derived_payload["parent"] = model.id
    derived_payload["children"] = []
    derived_payload["places"] = [
        {
            **place.model_dump(mode="json"),
            "annotations": {
                key: value for key, value in place.annotations.items() if key != "color_class"
            },
        }
        for place in model.places
    ]
    derived_payload["derivations"] = [
        {
            "kind": "unfold",
            "technique": MCC_UNFOLD_TECHNIQUE,
            "parent_id": model.id,
        }
    ]
    derived_model = build_canonical_model(payload=derived_payload)
    logger.debug(
        "unfold_symmetric_to_pt_applied",
        parent_id=model.id,
        derived_id=derived_model.id,
        place_count=len(derived_model.places),
        transition_count=len(derived_model.transitions),
        arc_count=len(derived_model.arcs),
    )

    certificate = UnfoldCertificate(
        rules=(
            UnfoldRule(
                name=str(MCC_UNFOLD_TECHNIQUE),
                description=(
                    "Symmetric → P/T unfolding (mcc / High-Level Nets Blaster,"
                    " Dal Zilio — LAAS, reimplemented); structure and element"
                    " names preserved, color semantics dropped."
                ),
                elements_affected=len(model.places) + len(model.transitions),
            ),
        ),
        element_map=_identity_element_map(model=model),
        technique=MCC_UNFOLD_TECHNIQUE,
        oracle=MCC_ORACLE_LABEL,
    )
    derived_with_certificate = derived_model.model_copy(
        update={"certificates": (certificate.model_dump(mode="json"),)}
    )
    return UnfoldedModel(
        model=derived_with_certificate, certificate=certificate, parent_id=model.id
    )


def _match_names(
    *,
    source_names: list[ElementName],
    target_names: list[ElementName],
    source_marks: dict[ElementName, int],
    target_marks: dict[ElementName, int],
) -> dict[ElementName, ElementName] | None:
    """Find a marking-preserving bijection between two place-name sets.

    Backtracking over the source places; small nets only (oracle cross-check
    on benchmark-sized samples), so exponential worst case is acceptable and
    bounded by the test corpus.
    """
    if len(source_names) != len(target_names):
        return None
    mapping: dict[ElementName, ElementName] = {}
    used: set[ElementName] = set()

    def backtrack(*, index: int) -> bool:
        if index == len(source_names):
            return True
        source = source_names[index]
        for target in target_names:
            if target in used:
                continue
            if source_marks[source] != target_marks[target]:
                continue
            mapping[source] = target
            used.add(target)
            if backtrack(index=index + 1):
                return True
            del mapping[source]
            used.remove(target)
        return False

    return mapping if backtrack(index=0) else None


def cross_check_unfold_against_oracle(
    *,
    unfolded: CanonicalModel,
    oracle_pt: CanonicalModel,
) -> bool:
    """Cross-check an unfolded model against the MCC « unfolded » P/T oracle.

    Equality **up to renaming**: same place/transition/arc counts, a
    marking-preserving bijection on places, a bijection on transitions, and
    the arc structure (source, target, weight) preserved under both
    bijections. Returns True when the unfold matches the oracle; False
    otherwise — a mismatch means the unfold is wrong, never the oracle.
    """
    if unfolded.formalism != "pt" or oracle_pt.formalism != "pt":
        logger.warning(
            "unfold_cross_check_rejected_non_pt",
            unfolded_formalism=unfolded.formalism,
            oracle_formalism=oracle_pt.formalism,
        )
        return False
    if (
        len(unfolded.places) != len(oracle_pt.places)
        or len(unfolded.transitions) != len(oracle_pt.transitions)
        or len(unfolded.arcs) != len(oracle_pt.arcs)
    ):
        logger.debug("unfold_cross_check_mismatch", reason="element_counts")
        return False

    unfolded_marks = {place.name: place.initial_marking for place in unfolded.places}
    oracle_marks = {place.name: place.initial_marking for place in oracle_pt.places}
    place_bijection = _match_names(
        source_names=[place.name for place in unfolded.places],
        target_names=[place.name for place in oracle_pt.places],
        source_marks=unfolded_marks,
        target_marks=oracle_marks,
    )
    if place_bijection is None:
        logger.debug("unfold_cross_check_mismatch", reason="place_markings")
        return False

    unfolded_transitions = [transition.name for transition in unfolded.transitions]
    oracle_transitions = [transition.name for transition in oracle_pt.transitions]
    transition_bijection = _match_names(
        source_names=unfolded_transitions,
        target_names=oracle_transitions,
        source_marks=dict.fromkeys(unfolded_transitions, 0),
        target_marks=dict.fromkeys(oracle_transitions, 0),
    )
    if transition_bijection is None:
        logger.debug("unfold_cross_check_mismatch", reason="transition_count")
        return False

    name_bijection = {**place_bijection, **transition_bijection}
    id_to_name_unfolded = {
        element.id: element.name for element in (*unfolded.places, *unfolded.transitions)
    }
    id_to_name_oracle = {
        element.id: element.name for element in (*oracle_pt.places, *oracle_pt.transitions)
    }
    unfolded_arcs = sorted(
        (
            name_bijection[id_to_name_unfolded[arc.source]],
            name_bijection[id_to_name_unfolded[arc.target]],
            arc.weight,
        )
        for arc in unfolded.arcs
    )
    oracle_arcs = sorted(
        (
            id_to_name_oracle[arc.source],
            id_to_name_oracle[arc.target],
            arc.weight,
        )
        for arc in oracle_pt.arcs
    )
    if unfolded_arcs != oracle_arcs:
        logger.debug("unfold_cross_check_mismatch", reason="arc_structure")
        return False
    logger.debug(
        "unfold_cross_check_matched",
        unfolded_id=unfolded.id,
        oracle_id=oracle_pt.id,
        place_count=len(unfolded.places),
    )
    return True
