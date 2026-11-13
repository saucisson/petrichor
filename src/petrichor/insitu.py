"""In-situ amorçage on the permanent net view (spec §9, §14; maquette P4-P6, issue #13).

The session's model is permanent: its state graph is consultable at any
time of the session, its elements carry in-situ annotations (persisted in
the canonical ``annotations`` section, outside the content hash), and the
coverage of the net is exposed as an overlay the front greys out. A card
(coverage group) resolves to the net elements it mentions — the highlight
resolution behind the maquette's hover (P5) and greyed coverage (P6).
"""

import re
from typing import Literal, NewType

import structlog
from pydantic import BaseModel, ConfigDict

from petrichor.petri_canonical import CanonicalModel, Element, ElementId, ElementName, ModelId
from petrichor.profile import CoverageGroup, GroupId

logger = structlog.get_logger(__name__)

ElementSpec = NewType("ElementSpec", str)
"""A card element spec, e.g. ``think_1..3`` or ``fork_1,fork_2``."""

ELEMENT_RANGE_PATTERN = re.compile(r"^(?P<prefix>.+?)(?P<start>\d+)\.\.(?P<end>\d+)$")
"""The ``prefix_a..b`` range form of an element spec."""


class InvalidElementSpecError(ValueError):
    """A card element spec is neither a range nor a comma-separated name list."""


def expand_element_spec(*, spec: str) -> tuple[ElementName, ...]:
    """Expand a card element spec into element names.

    ``think_1..3`` expands to ``think_1, think_2, think_3``; a comma-separated
    list of plain names is accepted as-is. An empty or inverted spec is an
    explicit error, never an empty silent resolution.
    """
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    if not parts:
        raise InvalidElementSpecError(f"Empty element spec {spec!r}.")
    names: list[ElementName] = []
    for part in parts:
        match = ELEMENT_RANGE_PATTERN.fullmatch(part)
        if match is None:
            names.append(ElementName(part))
            continue
        start = int(match.group("start"))
        end = int(match.group("end"))
        if end < start:
            raise InvalidElementSpecError(f"Inverted element range {part!r}: {start}..{end}.")
        prefix = match.group("prefix")
        names.extend(ElementName(f"{prefix}{index}") for index in range(start, end + 1))
    return tuple(names)


def resolve_group_elements(*, group: CoverageGroup, model: CanonicalModel) -> tuple[ElementId, ...]:
    """Resolve a coverage group's element spec to the element ids present in the model.

    Names absent from the model are skipped — the resolution is against the
    permanent net, never against a phantom element.
    """
    names = expand_element_spec(spec=group.elements)
    ids_by_name = {element.name: element.id for element in (*model.places, *model.transitions)}
    return tuple(ids_by_name[name] for name in names if name in ids_by_name)


OverlayKind = Literal["place", "transition", "arc"]
"""The kind of a net element in the overlay."""


class OverlayElement(BaseModel):
    """One net element with its coverage state (greyed when not covered)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ElementId
    kind: OverlayKind
    name: ElementName | None
    """The semantic name; None for arcs (arcs carry no name in the canonical schema)."""

    covered: bool
    groups: tuple[GroupId, ...] = ()
    """The covered groups mentioning this element (the highlight sources)."""


class GroupElements(BaseModel):
    """One coverage group resolved against the net (card → elements, maquette P5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: GroupId
    role: str
    covered: bool
    elements: tuple[ElementId, ...] = ()


class NetOverlay(BaseModel):
    """The coverage overlay of the permanent net, consumable by the front (maquette P6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: ModelId
    elements: tuple[OverlayElement, ...]
    groups: tuple[GroupElements, ...]

    def greyed_ids(self) -> tuple[ElementId, ...]:
        """The uncovered element ids — the greyed part of the net."""
        return tuple(element.id for element in self.elements if not element.covered)


def build_net_overlay(*, model: CanonicalModel, groups: tuple[CoverageGroup, ...]) -> NetOverlay:
    """Build the coverage overlay of ``model`` from the session's coverage groups.

    An element is covered when at least one covered group mentions it; the
    others are the greyed overlay. With no groups (no profile yet), every
    element is greyed.
    """
    group_elements = tuple(
        GroupElements(
            group_id=group.group_id,
            role=group.role,
            covered=bool(group.covered_by),
            elements=resolve_group_elements(group=group, model=model),
        )
        for group in groups
    )
    covered_ids = {
        element_id
        for group_element in group_elements
        if group_element.covered
        for element_id in group_element.elements
    }
    elements: list[OverlayElement] = []
    sections: tuple[tuple[OverlayKind, tuple[Element, ...]], ...] = (
        ("place", tuple(model.places)),
        ("transition", tuple(model.transitions)),
    )
    for kind, members in sections:
        for member in members:
            covering = tuple(
                group_element.group_id
                for group_element in group_elements
                if group_element.covered and member.id in group_element.elements
            )
            elements.append(
                OverlayElement(
                    id=member.id,
                    kind=kind,
                    name=member.name,
                    covered=member.id in covered_ids,
                    groups=covering,
                )
            )
    for arc in model.arcs:
        elements.append(OverlayElement(id=arc.id, kind="arc", name=None, covered=False, groups=()))
    overlay = NetOverlay(model_id=model.id, elements=tuple(elements), groups=group_elements)
    logger.debug(
        "net_overlay_built",
        model_id=model.id,
        element_count=len(overlay.elements),
        greyed=len(overlay.greyed_ids()),
        group_count=len(overlay.groups),
    )
    return overlay
