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
    INHIBITOR_ARC_ANNOTATION_KEY,
    CanonicalModel,
    ElementId,
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

NEGATIVE_ARC_ANNOTATION_KEYS = ("negative", INHIBITOR_ARC_ANNOTATION_KEY)
"""Arc annotation keys that mark a negative (inhibitor) arc — never unfoldable."""


class UnfoldError(Exception):
    """Base class of every domain error raised by this module."""


class NotSymmetricError(UnfoldError):
    """The model is not a Symmetric net; only ``formalism="symmetric"`` unfolds."""


class NegativeArcAnnotationError(UnfoldError):
    """The model carries a negative arc annotation — explicit rejection (§18.5)."""


RuleName = NewType("RuleName", str)
"""The name of an unfold rule, e.g. ``mcc-unfold``."""

OracleLabel = NewType("OracleLabel", str)
"""The label identifying the oracle an unfold is cross-checked against."""


class UnfoldRule(BaseModel):
    """One applied unfold rule, exposed in the certificate (never silent)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: RuleName = Field(min_length=1)
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
    oracle: OracleLabel = Field(min_length=1, default=OracleLabel(MCC_ORACLE_LABEL))


class UnfoldedModel(BaseModel):
    """A derived P/T model plus its preservation certificate (spec §4.4, §18.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: CanonicalModel
    certificate: UnfoldCertificate
    parent_id: ModelId


def _has_negative_arc_annotation(*, model: CanonicalModel) -> ElementId | None:
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
    derived_payload["transitions"] = [
        {
            **transition.model_dump(mode="json"),
            "annotations": {
                key: value for key, value in transition.annotations.items() if key != "color_class"
            },
        }
        for transition in model.transitions
    ]
    derived_payload["derivations"] = [
        {
            "kind": "unfold",
            "technique": MCC_UNFOLD_TECHNIQUE,
            "parent_id": model.id,
        }
    ]
    certificate = UnfoldCertificate(
        rules=(
            UnfoldRule(
                name=RuleName(MCC_UNFOLD_TECHNIQUE),
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
        oracle=OracleLabel(MCC_ORACLE_LABEL),
    )
    derived_payload["certificates"] = [certificate.model_dump(mode="json")]
    derived_model = build_canonical_model(payload=derived_payload)
    logger.debug(
        "unfold_symmetric_to_pt_applied",
        parent_id=model.id,
        derived_id=derived_model.id,
        place_count=len(derived_model.places),
        transition_count=len(derived_model.transitions),
        arc_count=len(derived_model.arcs),
    )
    return UnfoldedModel(model=derived_model, certificate=certificate, parent_id=model.id)


class _MatchContext(BaseModel):
    """Prepared lookups for the renaming matcher (kept out of the hot loop)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_elements: tuple[tuple[str, ElementName, int], ...]
    target_by_kind: dict[str, tuple[ElementName, ...]]
    target_marks: dict[ElementName, int]
    source_name_to_id: dict[ElementName, ElementId]
    target_name_to_id: dict[ElementName, ElementId]
    source_id_to_name: dict[ElementId, ElementName]
    target_id_to_name: dict[ElementId, ElementName]
    source_arcs: dict[tuple[ElementId, ElementId], tuple[int, ...]]
    target_arcs: dict[tuple[ElementId, ElementId], tuple[int, ...]]


def _arc_weights_by_pair(
    *, model: CanonicalModel
) -> dict[tuple[ElementId, ElementId], tuple[int, ...]]:
    """Arc weights as a multiset per (source, target) pair — parallel arcs kept."""
    weights: dict[tuple[ElementId, ElementId], list[int]] = {}
    for arc in model.arcs:
        weights.setdefault((arc.source, arc.target), []).append(arc.weight)
    return {pair: tuple(sorted(pair_weights)) for pair, pair_weights in weights.items()}


def _arcs_consistent(
    *,
    context: _MatchContext,
    mapping: dict[ElementName, ElementName],
    source_name: ElementName,
    target_name: ElementName,
) -> bool:
    """Check arcs between the candidate pair and every already-mapped pair.

    Pruning only: compares the weight multisets of both arc directions; the
    final full-multiset check in ``_match_up_to_renaming`` is what makes the
    verdict sound (self-loops and parallel arcs included).
    """
    source_id = context.source_name_to_id[source_name]
    target_id = context.target_name_to_id[target_name]
    for mapped_source, mapped_target in mapping.items():
        mapped_source_id = context.source_name_to_id[mapped_source]
        mapped_target_id = context.target_name_to_id[mapped_target]
        for source_pair, target_pair in (
            ((source_id, mapped_source_id), (target_id, mapped_target_id)),
            ((mapped_source_id, source_id), (mapped_target_id, target_id)),
        ):
            if context.source_arcs.get(source_pair, ()) != context.target_arcs.get(target_pair, ()):
                return False
    return True


def _backtrack_match(
    *,
    context: _MatchContext,
    mapping: dict[ElementName, ElementName],
    used: set[ElementName],
    index: int,
) -> bool:
    if index == len(context.source_elements):
        return True
    kind, source_name, source_mark = context.source_elements[index]
    for candidate in context.target_by_kind[kind]:
        if candidate in used:
            continue
        if kind == "place" and source_mark != context.target_marks[candidate]:
            continue
        if not _arcs_consistent(
            context=context, mapping=mapping, source_name=source_name, target_name=candidate
        ):
            continue
        mapping[source_name] = candidate
        used.add(candidate)
        if _backtrack_match(context=context, mapping=mapping, used=used, index=index + 1):
            return True
        del mapping[source_name]
        used.remove(candidate)
    return False


def _match_up_to_renaming(
    *,
    source: CanonicalModel,
    target: CanonicalModel,
) -> dict[ElementName, ElementName] | None:
    """Find an isomorphism between two P/T nets, up to element renaming.

    Combined backtracking over places (constrained by initial marking) and
    transitions, pruning as soon as an arc between two already-assigned
    elements is not preserved (source, target, weight) in the other net.
    Small nets only (oracle cross-check on benchmark-sized samples), so the
    exponential worst case is acceptable and bounded by the test corpus.
    """
    context = _MatchContext(
        source_elements=tuple(
            [("place", place.name, place.initial_marking) for place in source.places]
            + [("transition", transition.name, 0) for transition in source.transitions]
        ),
        target_by_kind={
            "place": tuple(place.name for place in target.places),
            "transition": tuple(transition.name for transition in target.transitions),
        },
        target_marks={place.name: place.initial_marking for place in target.places},
        source_name_to_id={
            element.name: element.id for element in (*source.places, *source.transitions)
        },
        target_name_to_id={
            element.name: element.id for element in (*target.places, *target.transitions)
        },
        source_id_to_name={
            element.id: element.name for element in (*source.places, *source.transitions)
        },
        target_id_to_name={
            element.id: element.name for element in (*target.places, *target.transitions)
        },
        source_arcs=_arc_weights_by_pair(model=source),
        target_arcs=_arc_weights_by_pair(model=target),
    )
    mapping: dict[ElementName, ElementName] = {}
    if not _backtrack_match(context=context, mapping=mapping, used=set(), index=0):
        return None
    mapped_arcs = sorted(
        (
            mapping[context.source_id_to_name[arc.source]],
            mapping[context.source_id_to_name[arc.target]],
            arc.weight,
        )
        for arc in source.arcs
    )
    oracle_arcs = sorted(
        (context.target_id_to_name[arc.source], context.target_id_to_name[arc.target], arc.weight)
        for arc in target.arcs
    )
    if mapped_arcs != oracle_arcs:
        return None
    return mapping


def cross_check_unfold_against_oracle(
    *,
    unfolded: CanonicalModel,
    oracle_pt: CanonicalModel,
) -> bool:
    """Cross-check an unfolded model against the MCC « unfolded » P/T oracle.

    Equality **up to renaming**: same place/transition/arc counts and an
    isomorphism (marking-preserving on places, arc-structure-preserving on
    both) between the two nets. Returns True when the unfold matches the
    oracle; False otherwise — a mismatch means the unfold is wrong, never
    the oracle.
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

    if _match_up_to_renaming(source=unfolded, target=oracle_pt) is None:
        logger.debug("unfold_cross_check_mismatch", reason="no_isomorphism")
        return False
    logger.debug(
        "unfold_cross_check_matched",
        unfolded_id=unfolded.id,
        oracle_id=oracle_pt.id,
        place_count=len(unfolded.places),
    )
    return True
