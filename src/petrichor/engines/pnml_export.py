"""PNML export of a semantic model projection (spec §13, §19.4; issue #9).

Standard library only, on purpose: the verifypn engine image ships no
third-party dependency, so this module must not import pydantic or
structlog. The input is the semantic projection of the canonical model
(``semantic_projection``: places/transitions by name, arcs between names)
as a plain JSON-compatible dict - the ``parameters["model"]`` payload of an
engine question. Element names become PNML ids because verifypn addresses
places and transitions by id in its PQL queries.
"""

from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

PNML_NAMESPACE = "http://www.pnml.org/version-2009/grammar/pnml"
PNML_PT_NET_TYPE = "http://www.pnml.org/version-2009/grammar/pnml"

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None
"""Any JSON-compatible value (local alias: this module must stay stdlib-only)."""


class PnmlExportError(Exception):
    """The model payload cannot be exported to PNML."""


@dataclass(frozen=True)
class _PlaceView:
    """A validated place payload: semantic name and initial marking."""

    name: str
    initial_marking: int


@dataclass(frozen=True)
class _TransitionView:
    """A validated transition payload: semantic name."""

    name: str


@dataclass(frozen=True)
class _ArcView:
    """A validated arc payload: endpoint names and weight."""

    source: str
    target: str
    weight: int


def _place_view(*, element: Json) -> _PlaceView:
    """Validate one place payload of the semantic projection."""
    if not isinstance(element, dict):
        raise PnmlExportError("Every place must be a JSON object.")
    name = element.get("name")
    marking = element.get("initial_marking", 0)
    if not isinstance(name, str) or not name:
        raise PnmlExportError("Every place needs a non-empty name.")
    if not isinstance(marking, int) or isinstance(marking, bool) or marking < 0:
        raise PnmlExportError(f"Place {name!r} needs a non-negative integer initial_marking.")
    return _PlaceView(name=name, initial_marking=marking)


def _transition_view(*, element: Json) -> _TransitionView:
    """Validate one transition payload of the semantic projection."""
    if not isinstance(element, dict):
        raise PnmlExportError("Every transition must be a JSON object.")
    name = element.get("name")
    if not isinstance(name, str) or not name:
        raise PnmlExportError("Every transition needs a non-empty name.")
    return _TransitionView(name=name)


def _arc_view(*, arc: Json) -> _ArcView:
    """Validate one arc payload of the semantic projection."""
    if not isinstance(arc, dict):
        raise PnmlExportError("Every arc must be a JSON object.")
    source = arc.get("source")
    target = arc.get("target")
    weight = arc.get("weight", 1)
    if not isinstance(source, str) or not isinstance(target, str):
        raise PnmlExportError("Every arc needs string source and target names.")
    if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
        raise PnmlExportError("Every arc needs a positive integer weight.")
    return _ArcView(source=source, target=target, weight=weight)


def export_pnml(*, model: dict[str, Json]) -> str:
    """Serialize a semantic model projection to a PNML document.

    Element names are unique across places and transitions (canonical
    invariant), so they are used directly as PNML ids.
    """
    if model.get("formalism") != "pt":
        raise PnmlExportError(
            f"Only P/T nets export to PNML for verifypn; got {model.get('formalism')!r}."
        )
    places = model.get("places")
    transitions = model.get("transitions")
    arcs = model.get("arcs")
    if not (isinstance(places, list) and isinstance(transitions, list) and isinstance(arcs, list)):
        raise PnmlExportError("The model payload needs places, transitions and arcs lists.")
    place_views = [_place_view(element=place) for place in places]
    transition_views = [_transition_view(element=transition) for transition in transitions]
    arc_views = [_arc_view(arc=arc) for arc in arcs]
    views: list[_PlaceView | _TransitionView] = [*place_views, *transition_views]
    names = {view.name for view in views}
    if len(names) != len(place_views) + len(transition_views):
        raise PnmlExportError("Element names must be unique across places and transitions.")
    for arc in arc_views:
        if arc.source not in names or arc.target not in names:
            raise PnmlExportError(
                f"Arc {arc.source!r} -> {arc.target!r} references an unknown element name."
            )
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f"<pnml xmlns={quoteattr(PNML_NAMESPACE)}>",
        f'  <net id="model" type={quoteattr(PNML_PT_NET_TYPE)}>',
    ]
    for place in place_views:
        lines.append(f"    <place id={quoteattr(place.name)}>")
        lines.append(f"      <initialMarking><text>{place.initial_marking}</text></initialMarking>")
        lines.append("    </place>")
    for transition in transition_views:
        lines.append(f"    <transition id={quoteattr(transition.name)}/>")
    for position, arc in enumerate(arc_views, start=1):
        lines.append(
            f"    <arc id={quoteattr(f'arc_{position:03d}')}"
            f" source={quoteattr(arc.source)} target={quoteattr(arc.target)}>"
        )
        lines.append(f"      <inscription><text>{arc.weight}</text></inscription>")
        lines.append("    </arc>")
    lines.append("  </net>")
    lines.append("</pnml>")
    return "\n".join(lines) + "\n"
