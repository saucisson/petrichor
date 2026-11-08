"""Canonical `petri+json` v1.0 schema and canonical content hashing (spec §13).

The canonical JSON document is the storage truth of Petrichor: stable
element ids, first-class layout and annotations, and a content hash that
covers only the *semantic* part of the net - structure, initial marking,
arc weights - never ids, layout, annotations or metadata. Renaming an id,
moving a node or editing an annotation therefore does not create a new
model, while any structural or marking change does.
"""

import hashlib
import json
import re
from typing import Literal, NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = structlog.get_logger(__name__)

ElementId = NewType("ElementId", str)
"""A stable element identifier (place, transition or arc), e.g. ``pl_011``."""

ElementName = NewType("ElementName", str)
"""The semantic name of a place or transition; the hash keys on it, not on ids."""

ModelId = NewType("ModelId", str)
"""A stable model identifier, e.g. ``mdl_8f3ac1``."""

ContentHash = NewType("ContentHash", str)
"""A canonical content hash, formatted ``sha256:<64 lowercase hex>``."""

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
"""Any JSON-compatible value (PEP 695 recursive alias)."""

type Formalism = Literal["pt", "symmetric"]
"""The net formalism stored by the canonical schema (spec §13)."""

HASH_ALGORITHM_NAME = "sha256"
HASH_PREFIX = f"{HASH_ALGORITHM_NAME}:"
HEX_DIGITS_PER_HASH = 64
CONTENT_HASH_PATTERN = re.compile(rf"{HASH_ALGORITHM_NAME}:[0-9a-f]{{{HEX_DIGITS_PER_HASH}}}")
CANONICAL_JSON_SEPARATORS = (",", ":")
PLACEHOLDER_CONTENT_HASH = ContentHash(f"{HASH_PREFIX}{'0' * HEX_DIGITS_PER_HASH}")


class PetriCanonicalError(Exception):
    """Base class of every domain error raised by this module."""


class InvalidCanonicalModelError(PetriCanonicalError):
    """A payload does not conform to the `petri+json` v1.0 schema."""


class Layout(BaseModel):
    """Graphical position of an element; excluded from the content hash."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: float
    y: float


class Element(BaseModel):
    """A net element with a stable id, a semantic name and presentation data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ElementId
    name: ElementName
    layout: Layout
    annotations: dict[str, JsonValue]


class Place(Element):
    """One place of the net, with its initial marking."""

    initial_marking: int = Field(ge=0)


class Transition(Element):
    """One transition of the net."""


class Arc(BaseModel):
    """One arc, binding a source element to a target element with a weight."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ElementId
    source: ElementId
    target: ElementId
    weight: int = Field(ge=1)
    annotations: dict[str, JsonValue]


class CanonicalModel(BaseModel):
    """The `petri+json` v1.0 document: the storage truth (spec §13)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ModelId
    formalism: Formalism
    content_hash: ContentHash
    parent: ModelId | None
    children: tuple[ModelId, ...]
    derivations: tuple[JsonValue, ...]
    certificates: tuple[JsonValue, ...]
    places: tuple[Place, ...]
    transitions: tuple[Transition, ...]
    arcs: tuple[Arc, ...]
    color_domain: dict[str, JsonValue] | None
    metadata: dict[str, JsonValue]

    @field_validator("content_hash")
    @classmethod
    def check_content_hash_format(cls, value: ContentHash) -> ContentHash:
        """Reject any hash that is not ``sha256:`` + 64 lowercase hex digits."""
        if CONTENT_HASH_PATTERN.fullmatch(value) is None:
            raise ValueError(
                f"content_hash must be '{HASH_PREFIX}' followed by"
                f" {HEX_DIGITS_PER_HASH} lowercase hex digits."
            )
        return value

    @model_validator(mode="after")
    def check_semantic_integrity(self) -> CanonicalModel:
        """Enforce the invariants the canonical hash relies on."""
        elements: tuple[Element | Arc, ...] = (*self.places, *self.transitions, *self.arcs)
        element_ids = [element.id for element in elements]
        if len(set(element_ids)) != len(element_ids):
            raise ValueError("Element ids must be unique across places, transitions and arcs.")
        names = [element.name for element in (*self.places, *self.transitions)]
        if len(set(names)) != len(names):
            raise ValueError("Element names must be unique across places and transitions.")
        known_ids = set(element_ids)
        for arc in self.arcs:
            if arc.source not in known_ids or arc.target not in known_ids:
                raise ValueError(f"Arc {arc.id} references an unknown element id.")
        if self.formalism == "pt" and self.color_domain is not None:
            raise ValueError("A pt net must have color_domain set to null.")
        if self.formalism == "symmetric" and self.color_domain is None:
            raise ValueError("A symmetric net must declare its color_domain.")
        return self


def semantic_projection(*, model: CanonicalModel) -> dict[str, object]:
    """Project a model onto its semantic part - the only part the hash covers.

    Ids are resolved to element names, so renaming an id cannot change the
    projection; layout, annotations, lineage and metadata are dropped.
    """
    elements: tuple[Element, ...] = (*model.places, *model.transitions)
    names_by_id = {element.id: element.name for element in elements}
    places: list[dict[str, JsonValue]] = [
        {"name": place.name, "initial_marking": place.initial_marking}
        for place in sorted(model.places, key=lambda place: place.name)
    ]
    transitions: list[dict[str, JsonValue]] = [
        {"name": transition.name}
        for transition in sorted(model.transitions, key=lambda transition: transition.name)
    ]
    arcs: list[dict[str, JsonValue]] = [
        {
            "source": names_by_id[arc.source],
            "target": names_by_id[arc.target],
            "weight": arc.weight,
        }
        for arc in sorted(
            model.arcs,
            key=lambda arc: (names_by_id[arc.source], names_by_id[arc.target], arc.weight),
        )
    ]
    return {
        "formalism": model.formalism,
        "color_domain": model.color_domain,
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
    }


def canonical_json(*, payload: object) -> str:
    """Serialize a JSON payload deterministically: sorted keys, tight separators."""
    return json.dumps(
        payload, sort_keys=True, separators=CANONICAL_JSON_SEPARATORS, ensure_ascii=False
    )


def compute_content_hash(*, model: CanonicalModel) -> ContentHash:
    """Hash the semantic projection of a model (ids, layout, annotations excluded)."""
    projection = semantic_projection(model=model)
    logger.debug(
        "canonical_semantic_projection_built",
        place_count=len(model.places),
        transition_count=len(model.transitions),
        arc_count=len(model.arcs),
    )
    digest = hashlib.sha256(canonical_json(payload=projection).encode("utf-8")).hexdigest()
    content_hash = ContentHash(f"{HASH_PREFIX}{digest}")
    logger.debug("canonical_content_hash_computed", model_id=model.id, content_hash=content_hash)
    return content_hash


def serialize_model(*, model: CanonicalModel) -> str:
    """Full deterministic serialization of a model; round-trips without loss."""
    return canonical_json(payload=model.model_dump(mode="json"))


def validate_canonical_model(*, payload: object) -> CanonicalModel:
    """Validate a parsed payload against the schema and its stored hash."""
    if not isinstance(payload, dict):
        logger.warning("canonical_model_rejected", reason="not_a_json_object")
        raise InvalidCanonicalModelError("The canonical document must be a JSON object.")
    try:
        model = CanonicalModel.model_validate(payload)
    except ValidationError as error:
        logger.warning("canonical_model_rejected", reason="schema_violation", detail=str(error))
        raise InvalidCanonicalModelError(f"Invalid petri+json v1.0 document: {error}") from error
    recomputed_hash = compute_content_hash(model=model)
    if recomputed_hash != model.content_hash:
        logger.warning(
            "canonical_model_rejected",
            reason="content_hash_mismatch",
            stored_hash=model.content_hash,
            recomputed_hash=recomputed_hash,
        )
        raise InvalidCanonicalModelError(
            f"content_hash mismatch: stored {model.content_hash}, recomputed {recomputed_hash}."
        )
    logger.debug("canonical_model_validated", model_id=model.id, content_hash=model.content_hash)
    return model


def parse_canonical_json(*, text: str) -> CanonicalModel:
    """Parse and strictly validate a canonical `petri+json` document."""
    try:
        payload: object = json.loads(text)
    except json.JSONDecodeError as error:
        logger.warning("canonical_model_rejected", reason="malformed_json", detail=str(error))
        raise InvalidCanonicalModelError(f"Not valid JSON: {error}") from error
    return validate_canonical_model(payload=payload)


LINEAGE_DEFAULTS: dict[str, object] = {
    "parent": None,
    "children": [],
    "derivations": [],
    "certificates": [],
    "color_domain": None,
    "metadata": {},
}


def build_canonical_model(*, payload: dict[str, object]) -> CanonicalModel:
    """Assemble a canonical model from a raw document and stamp its content hash.

    Lineage fields default to empty; the ``content_hash`` of the payload, if
    any, is ignored and recomputed from the semantic part.
    """
    full_payload = {**LINEAGE_DEFAULTS, **payload}
    draft = CanonicalModel.model_validate(
        {**full_payload, "content_hash": PLACEHOLDER_CONTENT_HASH}
    )
    content_hash = compute_content_hash(model=draft)
    model = draft.model_copy(update={"content_hash": content_hash})
    logger.debug("canonical_model_built", model_id=draft.id, content_hash=content_hash)
    return model
