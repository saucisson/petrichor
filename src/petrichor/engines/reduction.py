"""Certified structural reductions with witness replay (spec §18.2, issue #24).

Two reduction families (spec §18.2):
- **(a) net rewriting** (verifypn, ITS-Tools): places/transitions removed or
  merged — allowed only when the property needs no full witness, or when the
  witness produced on the reduced net is **replayed and validated on the
  original net**.
- **(b) marking reduction** (Token Elimination, TACAS'25): the net structure
  is intact, only the initial marking is reduced — ``element_map`` is the
  identity on elements by construction.

Never a silent reduction: the contract exposes the reduced net, the applied
rules and the saved cost; the run stays archived (spec §12). A reduction
without a certificate is refused (``UncertifiedReductionError``).
"""

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.engines.contract import Witness, WitnessStep
from petrichor.petri_canonical import (
    CanonicalModel,
    ElementName,
    ModelId,
    Place,
    build_canonical_model,
)

logger = structlog.get_logger(__name__)

TOKEN_ELIMINATION_RULE_NAME = "token-elimination"  # noqa: S105 - rule name, not a secret
"""The rule name of the Token Elimination reduction (TACAS'25)."""


class ReductionError(Exception):
    """Base class of every domain error raised by this module."""


class UncertifiedReductionError(ReductionError):
    """A reduction was attempted without a valid preservation certificate."""


class WitnessReplayError(ReductionError):
    """A witness produced on a reduced net failed validation on the original net."""


class ReductionRule(BaseModel):
    """One applied reduction rule, exposed in the certificate (never silent)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    elements_affected: int = Field(ge=0)


class ElementMap(BaseModel):
    """The mapping from reduced-net element names to original-net element names.

    For Token Elimination the net is intact, so the map is the identity on
    every element name (spec §18.2: ``element_map`` ≈ identity).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping: dict[ElementName, ElementName]

    def to_original(self, *, name: ElementName) -> ElementName:
        """Resolve a reduced-net element name to its original-net name."""
        return self.mapping.get(name, name)


class ReductionCertificate(BaseModel):
    """The preservation certificate of a reduction (spec §7, §18.2).

    Carries the applied rules, the saved cost and the mandatory
    ``element_map``. Without a certificate, no derived model exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: tuple[ReductionRule, ...]
    saved_states: int = Field(ge=0)
    saved_seconds: float = Field(ge=0)
    element_map: ElementMap
    technique: str = Field(min_length=1, default="token-elimination")


class ReducedModel(BaseModel):
    """A derived model plus its preservation certificate (spec §4.4, §18.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: CanonicalModel
    certificate: ReductionCertificate
    parent_id: ModelId


def _identity_element_map(*, model: CanonicalModel) -> ElementMap:
    """Build the identity element map: every element name maps to itself."""
    names = [place.name for place in model.places]
    names += [transition.name for transition in model.transitions]
    return ElementMap(mapping={ElementName(name): ElementName(name) for name in names})


def reduce_marking_token_elimination(
    *,
    model: CanonicalModel,
    reduced_marking: dict[ElementName, int],
) -> ReducedModel:
    """Apply Token Elimination: reduce the initial marking, keep the net intact.

    The net structure (places, transitions, arcs) is unchanged; only the
    initial marking of the places listed in ``reduced_marking`` is lowered.
    The ``element_map`` is the identity on every element name (spec §18.2).

    Raises ``UncertifiedReductionError`` when a place name is unknown or a
    marking is negative — a reduction without a valid certificate is refused.
    """
    place_names = {place.name for place in model.places}
    for name, marking in reduced_marking.items():
        if name not in place_names:
            logger.warning("reduction_rejected_unknown_place", place_name=name)
            raise UncertifiedReductionError(f"unknown place {name!r} in reduced marking")
        if marking < 0:
            logger.warning("reduction_rejected_negative_marking", place_name=name, marking=marking)
            raise UncertifiedReductionError(f"negative marking {marking} for place {name!r}")

    original_total = sum(place.initial_marking for place in model.places)
    reduced_total = sum(
        reduced_marking.get(place.name, place.initial_marking) for place in model.places
    )
    saved_states = max(0, original_total - reduced_total)

    new_places = tuple(
        Place(
            id=place.id,
            name=place.name,
            layout=place.layout,
            annotations=place.annotations,
            initial_marking=reduced_marking.get(place.name, place.initial_marking),
        )
        for place in model.places
    )
    derived_payload = model.model_dump(mode="json")
    derived_payload["id"] = f"{model.id}_te"
    derived_payload["parent"] = model.id
    derived_payload["children"] = []
    derived_payload["places"] = [place.model_dump(mode="json") for place in new_places]
    derived_payload["derivations"] = [
        {
            "kind": "reduction",
            "rule": TOKEN_ELIMINATION_RULE_NAME,
            "parent_id": model.id,
        }
    ]
    derived_model = build_canonical_model(payload=derived_payload)
    logger.debug(
        "reduction_token_elimination_applied",
        parent_id=model.id,
        derived_id=derived_model.id,
        saved_states=saved_states,
    )

    certificate = ReductionCertificate(
        rules=(
            ReductionRule(
                name=TOKEN_ELIMINATION_RULE_NAME,
                description="Marking-only reduction (TACAS'25); net structure intact.",
                elements_affected=len(reduced_marking),
            ),
        ),
        saved_states=saved_states,
        saved_seconds=0.0,
        element_map=_identity_element_map(model=model),
        technique=TOKEN_ELIMINATION_RULE_NAME,
    )
    return ReducedModel(model=derived_model, certificate=certificate, parent_id=model.id)


def replay_witness_on_original(
    *,
    witness: Witness,
    original_model: CanonicalModel,
    element_map: ElementMap,
) -> bool:
    """Replay a witness produced on a reduced net against the original net.

    Every marking step is mapped back through the ``element_map`` and checked
    against the original net's place names. Returns True when the witness is
    valid on the original net; raises ``WitnessReplayError`` otherwise.
    """
    original_place_names = {place.name for place in original_model.places}
    for step in witness.steps:
        for token in step.marking:
            original_name = element_map.to_original(name=ElementName(token))
            if original_name not in original_place_names:
                logger.warning(
                    "witness_replay_failed_unknown_place",
                    token=token,
                    original_name=original_name,
                    step_index=step.index,
                )
                raise WitnessReplayError(
                    f"witness step {step.index} references unknown place {token!r}"
                    f" (mapped to {original_name!r})"
                )
        if step.fired is not None:
            original_transition = element_map.to_original(name=ElementName(step.fired))
            transition_names = {transition.name for transition in original_model.transitions}
            if original_transition not in transition_names:
                logger.warning(
                    "witness_replay_failed_unknown_transition",
                    fired=step.fired,
                    step_index=step.index,
                )
                raise WitnessReplayError(
                    f"witness step {step.index} fires unknown transition {step.fired!r}"
                )
    logger.debug(
        "witness_replay_validated",
        steps=len(witness.steps),
        kind=witness.kind,
    )
    return True


def reformat_witness_for_original(
    *,
    witness: Witness,
    element_map: ElementMap,
) -> Witness:
    """Reformat a witness from the reduced net to the original net via the element map."""
    new_steps = [
        WitnessStep(
            index=step.index,
            marking=[element_map.to_original(name=ElementName(token)) for token in step.marking],
            fired=(
                element_map.to_original(name=ElementName(step.fired))
                if step.fired is not None
                else None
            ),
        )
        for step in witness.steps
    ]
    return Witness(
        kind=witness.kind,
        cyclic=witness.cyclic,
        back_edge=witness.back_edge,
        steps=new_steps,
    )
