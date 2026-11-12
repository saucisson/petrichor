"""PNML import: parse an interop document into the canonical `petri+json` model (spec §13, §19.4).

P/T nets import as ``formalism="pt"``; Symmetric nets import **non-unfolded**
(``formalism="symmetric"`` with their declared color domain) - unfolding is
P27. Invalid or unsupported PNML is rejected explicitly with a typed error,
never silently unfolded or half-parsed (§18.5, §19.4).
"""

import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import UTC, datetime

import structlog
from pydantic import ValidationError

from petrichor.petri_canonical import (
    HASH_PREFIX,
    INHIBITOR_ARC_ANNOTATION_KEY,
    Arc,
    CanonicalModel,
    ElementId,
    ElementName,
    Formalism,
    InvalidCanonicalModelError,
    JsonValue,
    Layout,
    ModelId,
    Place,
    Transition,
    build_canonical_model,
)

logger = structlog.get_logger(__name__)

type Clock = Callable[[], datetime]
"""An injectable clock, so imports are deterministic in tests (spec §19.4)."""

PNML_ROOT_ELEMENT = "pnml"
PNML_PT_NET_TYPE = "http://www.pnml.org/version-2009/grammar/pnml"
SYMMETRIC_NET_TYPE_MARKER = "symmetric"
EXPECTED_NET_COUNT = 1
MODEL_ID_HEX_DIGITS = 8
PENDING_MODEL_ID = ModelId("mdl_pending")
PLACE_ID_PREFIX = "pl_"
TRANSITION_ID_PREFIX = "tr_"
ARC_ID_PREFIX = "ar_"
DEFAULT_LAYOUT_COORDINATE = 0.0
DEFAULT_INITIAL_MARKING = 0
DEFAULT_ARC_WEIGHT = 1


class PnmlImportError(Exception):
    """Base class of every PNML import failure."""


class MalformedPnmlError(PnmlImportError):
    """The document is not well-formed PNML that this importer can read."""


class UnsupportedPnmlError(PnmlImportError):
    """Well-formed PNML, but a dialect explicitly not supported (never unfolded)."""


def _local_name(tag: str) -> str:
    """Strip the XML namespace from an element tag."""
    return tag.rsplit("}", maxsplit=1)[-1]


def _children(*, element: ET.Element, name: str) -> list[ET.Element]:
    """Direct children of an element whose local tag name matches."""
    return [child for child in element if _local_name(child.tag) == name]


def _child(*, element: ET.Element, name: str) -> ET.Element | None:
    """The first direct child with the given local tag name, if any."""
    children = _children(element=element, name=name)
    return children[0] if children else None


def _text_content(*, element: ET.Element | None, child_name: str) -> str | None:
    """The stripped text of a PNML string container (``<value>`` or ``<text>``)."""
    if element is None:
        return None
    child = _child(element=element, name=child_name)
    if child is None or child.text is None:
        return None
    stripped = child.text.strip()
    return stripped if stripped else None


def _text_of(*, element: ET.Element | None) -> str | None:
    """The text of a ``<name>``/``<type>`` container, which wraps a ``<value>``."""
    return _text_content(element=element, child_name="value")


def _parse_int(*, raw: str | None, field: str, default: int, minimum: int) -> int:
    """Parse a PNML integer field, rejecting non-integers and values below minimum."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise MalformedPnmlError(f"{field} must be an integer, got {raw!r}.") from error
    if value < minimum:
        raise MalformedPnmlError(f"{field} must be >= {minimum}, got {value}.")
    return value


def _parse_layout(*, element: ET.Element) -> Layout:
    """The graphics position of a place or transition; defaults to the origin."""
    graphics = _child(element=element, name="graphics")
    position = _child(element=graphics, name="position") if graphics is not None else None
    if position is None:
        return Layout(x=DEFAULT_LAYOUT_COORDINATE, y=DEFAULT_LAYOUT_COORDINATE)
    try:
        return Layout(
            x=float(position.get("x", str(DEFAULT_LAYOUT_COORDINATE))),
            y=float(position.get("y", str(DEFAULT_LAYOUT_COORDINATE))),
        )
    except ValueError as error:
        raise MalformedPnmlError(f"Invalid graphics position: {error}") from error


def _register_raw_id(
    *, canonical_id_by_raw: dict[str, ElementId], raw_id: str, canonical_id: ElementId
) -> None:
    """Map a raw PNML id to its canonical id, rejecting duplicates."""
    if raw_id in canonical_id_by_raw:
        raise MalformedPnmlError(f"Duplicate element id {raw_id!r} in the PNML document.")
    canonical_id_by_raw[raw_id] = canonical_id


def _parse_place(
    *, element: ET.Element, canonical_id_by_raw: dict[str, ElementId]
) -> tuple[Place, str | None]:
    """One PNML place into a canonical place plus its color class, if declared."""
    raw_id = element.get("id")
    if not raw_id:
        raise MalformedPnmlError("A <place> is missing its id attribute.")
    canonical_id = ElementId(f"{PLACE_ID_PREFIX}{raw_id}")
    _register_raw_id(
        canonical_id_by_raw=canonical_id_by_raw, raw_id=raw_id, canonical_id=canonical_id
    )
    name = _text_of(element=_child(element=element, name="name")) or raw_id
    initial_marking = _parse_int(
        raw=_text_content(
            element=_child(element=element, name="initialMarking"), child_name="text"
        ),
        field="initialMarking",
        default=DEFAULT_INITIAL_MARKING,
        minimum=0,
    )
    color_class = _text_of(element=_child(element=element, name="type"))
    annotations: dict[str, JsonValue] = (
        {"color_class": color_class} if color_class is not None else {}
    )
    place = Place(
        id=canonical_id,
        name=ElementName(name),
        initial_marking=initial_marking,
        layout=_parse_layout(element=element),
        annotations=annotations,
    )
    return place, color_class


def _parse_transition(
    *, element: ET.Element, canonical_id_by_raw: dict[str, ElementId]
) -> Transition:
    """One PNML transition into a canonical transition."""
    raw_id = element.get("id")
    if not raw_id:
        raise MalformedPnmlError("A <transition> is missing its id attribute.")
    canonical_id = ElementId(f"{TRANSITION_ID_PREFIX}{raw_id}")
    _register_raw_id(
        canonical_id_by_raw=canonical_id_by_raw, raw_id=raw_id, canonical_id=canonical_id
    )
    name = _text_of(element=_child(element=element, name="name")) or raw_id
    return Transition(
        id=canonical_id,
        name=ElementName(name),
        layout=_parse_layout(element=element),
        annotations={},
    )


def _parse_arc(
    *,
    element: ET.Element,
    canonical_id_by_raw: dict[str, ElementId],
    arc_raw_ids: set[str],
) -> Arc:
    """One PNML arc into a canonical arc; unknown endpoints are rejected explicitly."""
    raw_id = element.get("id")
    if not raw_id:
        raise MalformedPnmlError("An <arc> is missing its id attribute.")
    if raw_id in arc_raw_ids:
        raise MalformedPnmlError(f"Duplicate arc id {raw_id!r} in the PNML document.")
    arc_raw_ids.add(raw_id)
    source_raw = element.get("source")
    target_raw = element.get("target")
    if source_raw is None or target_raw is None:
        raise MalformedPnmlError(f"Arc {raw_id!r} is missing its source or target attribute.")
    endpoints: list[ElementId] = []
    for endpoint_raw in (source_raw, target_raw):
        canonical_endpoint = canonical_id_by_raw.get(endpoint_raw)
        if canonical_endpoint is None:
            raise MalformedPnmlError(f"Arc {raw_id!r} references unknown element {endpoint_raw!r}.")
        endpoints.append(canonical_endpoint)
    weight = _parse_int(
        raw=_text_content(element=_child(element=element, name="inscription"), child_name="text"),
        field="inscription",
        default=DEFAULT_ARC_WEIGHT,
        minimum=1,
    )
    arc_type = element.get("type")
    annotations: dict[str, JsonValue] = (
        {INHIBITOR_ARC_ANNOTATION_KEY: True}
        if arc_type is not None and arc_type.lower() == INHIBITOR_ARC_ANNOTATION_KEY
        else {}
    )
    return Arc(
        id=ElementId(f"{ARC_ID_PREFIX}{raw_id}"),
        source=endpoints[0],
        target=endpoints[1],
        weight=weight,
        annotations=annotations,
    )


def _net_formalism(*, net_type: str | None) -> Formalism:
    """Map the PNML net type to a supported formalism; anything else is rejected."""
    if net_type is None or net_type == PNML_PT_NET_TYPE:
        return "pt"
    if SYMMETRIC_NET_TYPE_MARKER in net_type.lower():
        return "symmetric"
    logger.warning("pnml_import_rejected", reason="unsupported_net_type", net_type=net_type)
    raise UnsupportedPnmlError(
        f"Unsupported PNML net type {net_type!r}: only P/T and Symmetric nets are"
        " imported; unfolding is a later milestone (P27)."
    )


def _parse_xml(*, text: str) -> ET.Element:
    """Parse the XML document and require a <pnml> root element."""
    try:
        # stdlib ElementTree on Python 3.15 runs on a bundled expat with
        # built-in billion-laughs amplification limits and no external-entity
        # resolution; defusedxml would be a new dependency, which is on the
        # escalation list (ADR-0010).
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as error:
        logger.warning("pnml_import_rejected", reason="malformed_xml", detail=str(error))
        raise MalformedPnmlError(f"Not well-formed XML: {error}") from error
    if _local_name(root.tag) != PNML_ROOT_ELEMENT:
        logger.warning(
            "pnml_import_rejected", reason="wrong_root_element", root=_local_name(root.tag)
        )
        raise MalformedPnmlError(
            f"Root element must be <{PNML_ROOT_ELEMENT}>, got <{_local_name(root.tag)}>."
        )
    return root


def _color_domain(*, formalism: Formalism, color_classes: set[str]) -> dict[str, JsonValue] | None:
    """The declared color domain of a Symmetric net; None for a P/T net."""
    if formalism != "symmetric":
        return None
    classes: list[JsonValue] = []
    for color_class in sorted(color_classes):
        classes.append(color_class)
    return {"classes": classes}


def _net_payload(
    *, net: ET.Element, formalism: Formalism, clock: Clock | None
) -> dict[str, object]:
    """Assemble the raw canonical payload of one PNML net."""
    canonical_id_by_raw: dict[str, ElementId] = {}
    places: list[Place] = []
    color_classes: set[str] = set()
    for element in _children(element=net, name="place"):
        place, color_class = _parse_place(element=element, canonical_id_by_raw=canonical_id_by_raw)
        places.append(place)
        if color_class is not None:
            color_classes.add(color_class)
    transitions = [
        _parse_transition(element=element, canonical_id_by_raw=canonical_id_by_raw)
        for element in _children(element=net, name="transition")
    ]
    arc_raw_ids: set[str] = set()
    arcs = [
        _parse_arc(
            element=element,
            canonical_id_by_raw=canonical_id_by_raw,
            arc_raw_ids=arc_raw_ids,
        )
        for element in _children(element=net, name="arc")
    ]
    color_domain = _color_domain(formalism=formalism, color_classes=color_classes)
    now = clock() if clock is not None else datetime.now(UTC)
    return {
        "id": PENDING_MODEL_ID,
        "formalism": formalism,
        "places": [place.model_dump(mode="json") for place in places],
        "transitions": [transition.model_dump(mode="json") for transition in transitions],
        "arcs": [arc.model_dump(mode="json") for arc in arcs],
        "color_domain": color_domain,
        "metadata": {"source": "pnml", "imported_at": now.isoformat()},
    }


def import_pnml(*, text: str, clock: Clock | None = None) -> CanonicalModel:
    """Parse a PNML document into a canonical model and stamp its content hash.

    The model id is derived from the content hash, so importing the same net
    twice yields the same id (idempotent import, spec §13). Symmetric nets are
    stored non-unfolded with their declared color domain.
    """
    logger.debug("pnml_import_started", text_length=len(text))
    root = _parse_xml(text=text)
    nets = _children(element=root, name="net")
    if len(nets) != EXPECTED_NET_COUNT:
        raise MalformedPnmlError(
            f"A PNML document must contain exactly {EXPECTED_NET_COUNT} <net>, found {len(nets)}."
        )
    formalism = _net_formalism(net_type=nets[0].get("type"))
    payload = _net_payload(net=nets[0], formalism=formalism, clock=clock)
    try:
        model = build_canonical_model(payload=payload)
    except (InvalidCanonicalModelError, ValidationError) as error:
        logger.warning("pnml_import_rejected", reason="canonical_validation", detail=str(error))
        raise MalformedPnmlError(f"The net is not a valid canonical model: {error}") from error
    digest = model.content_hash.removeprefix(HASH_PREFIX)
    model = model.model_copy(update={"id": ModelId(f"mdl_{digest[:MODEL_ID_HEX_DIGITS]}")})
    logger.debug(
        "pnml_import_succeeded",
        model_id=model.id,
        formalism=model.formalism,
        place_count=len(model.places),
        transition_count=len(model.transitions),
        arc_count=len(model.arcs),
        content_hash=model.content_hash,
    )
    return model
