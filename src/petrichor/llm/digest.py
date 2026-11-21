"""The canonical bounded digest of a net, sent with every LLM call (P73, annexe B §B.2).

A pure domain service: no side effects, no I/O, fully tested. Three levels:

- ``profile`` — the complete summary for bootstrapping and global usages:
  places (id, name, initial marking, layout), transitions (id, name),
  incidence per transition (consumed/produced places with weights), arc
  degrees, groupings by id prefix (e.g. ``fork_1..3``), totals and the PNML
  layout coordinates. Ceiling ≈ 4000 tokens; beyond it, a grouped summary
  with counters, then an explicit degradation — never a silent truncation.
- ``places-only`` — only the places cited by the formulas of the frozen
  grid, with their accepted roles and confidences.
- ``trace`` — per fired step: number, transition (id, name), places
  consumed/produced with weights, roles of the involved places; at most 50
  steps, ceiling ≈ 4000 tokens.
"""

import json
import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from petrichor.llm.provider import LLMError
from petrichor.petri_canonical import ElementId, ElementName

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel, JsonValue

type DigestLevel = Literal["profile", "places-only", "trace"]
"""The three digest levels of annexe B §B.2."""

DIGEST_TOKEN_CEILING = 4000
"""The token ceiling of a digest (annexe B §B.2: ≈ 4000 tokens)."""

TRACE_MAX_STEPS = 50
"""The maximum number of fired steps in a `trace` digest (annexe B §B.2)."""

CHARS_PER_TOKEN = 4
"""The token estimation heuristic: ≈ 4 characters per token."""

_NUMERIC_SUFFIX = re.compile(r"^(?P<prefix>.+)_(?P<index>\d+)$")
"""Splits an element id into its grouping prefix and numeric suffix."""


class DigestError(LLMError):
    """Explicit degradation: the digest cannot be built within its ceilings."""


class AcceptedRole(BaseModel):
    """An accepted semantic role of a place, with its confidence (annexe B §B.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: str
    confidence: float = Field(ge=0.0, le=1.0)


def estimate_tokens(payload: JsonValue) -> int:
    """Estimate the token count of a JSON payload (≈ 4 characters per token)."""
    return max(1, len(json.dumps(payload, ensure_ascii=False)) // CHARS_PER_TOKEN)


def group_prefix(element_id: str) -> str:
    """The grouping prefix of an element id: ``fork_1`` → ``fork``."""
    match = _NUMERIC_SUFFIX.match(element_id)
    return match.group("prefix") if match else element_id


def _groupings(element_names: tuple[str, ...]) -> list[JsonValue]:
    """Group element names by prefix (annexe B: ``fork_1..3``), first-seen order."""
    groups: dict[str, list[str]] = {}
    for name in element_names:
        groups.setdefault(group_prefix(name), []).append(name)
    result: list[JsonValue] = [
        {"prefix": prefix, "members": list(members)}
        for prefix, members in groups.items()
        if len(members) > 1
    ]
    return result


def _incidence(model: CanonicalModel) -> list[JsonValue]:
    """Per transition: the places it consumes and produces, with arc weights."""
    place_ids = {place.id for place in model.places}
    incidence: list[JsonValue] = []
    for transition in model.transitions:
        consumes: list[JsonValue] = []
        produces: list[JsonValue] = []
        for arc in model.arcs:
            if arc.target == transition.id and arc.source in place_ids:
                consumes.append({"place": arc.source, "weight": arc.weight})
            elif arc.source == transition.id and arc.target in place_ids:
                produces.append({"place": arc.target, "weight": arc.weight})
        incidence.append({"transition": transition.id, "consumes": consumes, "produces": produces})
    return incidence


def _arc_degrees(model: CanonicalModel) -> dict[str, JsonValue]:
    """Per element id: the in/out arc degrees."""
    degrees: dict[str, dict[str, int]] = {}
    for arc in model.arcs:
        for endpoint, direction in ((arc.source, "out"), (arc.target, "in")):
            degrees.setdefault(endpoint, {"in": 0, "out": 0})[direction] += 1
    result: dict[str, JsonValue] = {}
    for key, value in degrees.items():
        entry: dict[str, JsonValue] = {"in": value["in"], "out": value["out"]}
        result[key] = entry
    return result


def _profile_digest(model: CanonicalModel) -> dict[str, JsonValue]:
    """The complete `profile` digest of the net."""
    return {
        "level": "profile",
        "totals": {
            "places": len(model.places),
            "transitions": len(model.transitions),
            "arcs": len(model.arcs),
        },
        "places": [
            {
                "id": place.id,
                "name": place.name,
                "initial_marking": place.initial_marking,
                "layout": {"x": place.layout.x, "y": place.layout.y},
            }
            for place in model.places
        ],
        "transitions": [
            {
                "id": transition.id,
                "name": transition.name,
                "layout": {"x": transition.layout.x, "y": transition.layout.y},
            }
            for transition in model.transitions
        ],
        "incidence": _incidence(model),
        "arc_degrees": _arc_degrees(model),
        "groupings": _groupings(
            tuple(element.name for element in (*model.places, *model.transitions))
        ),
    }


def _profile_summary(model: CanonicalModel) -> dict[str, JsonValue]:
    """The grouped summary used when the full digest exceeds the ceiling."""
    return {
        "level": "profile",
        "degraded": "grouped-summary",
        "totals": {
            "places": len(model.places),
            "transitions": len(model.transitions),
            "arcs": len(model.arcs),
        },
        "groupings": _groupings(
            tuple(element.name for element in (*model.places, *model.transitions))
        ),
        "incidence_counters": {
            transition.id: {
                "consumes": sum(1 for arc in model.arcs if arc.source == transition.id),
                "produces": sum(1 for arc in model.arcs if arc.target == transition.id),
            }
            for transition in model.transitions
        },
    }


def _cited_place_names(model: CanonicalModel, formulas: tuple[str, ...]) -> set[str]:
    """The names of the places cited by any of the given formulas."""
    return {
        place.name for place in model.places if any(place.name in formula for formula in formulas)
    }


def _places_only_digest(
    model: CanonicalModel,
    *,
    formulas: tuple[str, ...],
    roles: dict[str, AcceptedRole],
) -> dict[str, JsonValue]:
    """The `places-only` digest: the cited places with their accepted roles."""
    cited = _cited_place_names(model, formulas)
    return {
        "level": "places-only",
        "places": [
            {
                "id": place.id,
                "name": place.name,
                "role": roles[place.name].role if place.name in roles else None,
                "confidence": roles[place.name].confidence if place.name in roles else None,
            }
            for place in model.places
            if place.name in cited
        ],
    }


def _trace_digest(
    model: CanonicalModel,
    *,
    trace: tuple[str, ...],
    roles: dict[str, AcceptedRole],
) -> dict[str, JsonValue]:
    """The `trace` digest: one entry per fired transition, in fired order."""
    if len(trace) > TRACE_MAX_STEPS:
        raise DigestError(
            f"Trace digest: {len(trace)} fired steps exceed the limit of {TRACE_MAX_STEPS}."
        )
    transitions_by_id = {transition.id: transition for transition in model.transitions}
    transitions_by_name = {transition.name: transition for transition in model.transitions}
    place_ids = {place.id for place in model.places}
    place_names = {place.id: place.name for place in model.places}
    steps: list[JsonValue] = []
    for index, fired in enumerate(trace, start=1):
        # The witness fires transitions by name; accept the id as well.
        transition = transitions_by_id.get(ElementId(fired)) or transitions_by_name.get(
            ElementName(fired)
        )
        canonical_id = transition.id if transition else ElementId(fired)
        consumes: list[JsonValue] = []
        produces: list[JsonValue] = []
        involved: set[ElementId] = set()
        for arc in model.arcs:
            if arc.target == canonical_id and arc.source in place_ids:
                consumes.append({"place": arc.source, "weight": arc.weight})
                involved.add(arc.source)
            elif arc.source == canonical_id and arc.target in place_ids:
                produces.append({"place": arc.target, "weight": arc.weight})
                involved.add(arc.target)
        step_roles: dict[str, JsonValue] = {
            place_names[place_id]: roles[place_names[place_id]].role
            for place_id in involved
            if place_names[place_id] in roles
        }
        steps.append(
            {
                "step": index,
                "transition": {
                    "id": fired,
                    "name": transition.name if transition else None,
                },
                "consumes": consumes,
                "produces": produces,
                "roles": step_roles,
            }
        )
    return {"level": "trace", "steps": steps, "step_count": len(steps)}


def net_digest(
    model: CanonicalModel,
    level: DigestLevel,
    *,
    formulas: tuple[str, ...] = (),
    roles: dict[str, AcceptedRole] | None = None,
    trace: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    """Build the canonical digest of a net at the given level (annexe B §B.2).

    Pure and side-effect free. Ceilings are enforced: beyond the token
    ceiling the digest degrades to a grouped summary with counters, and
    beyond that to an explicit :class:`DigestError` — never a silent
    truncation.
    """
    accepted_roles = roles or {}
    if level == "profile":
        digest = _profile_digest(model)
        if estimate_tokens(digest) > DIGEST_TOKEN_CEILING:
            summary = _profile_summary(model)
            if estimate_tokens(summary) > DIGEST_TOKEN_CEILING:
                raise DigestError(
                    "Profile digest exceeds the token ceiling even as a grouped summary."
                )
            return summary
        return digest
    if level == "places-only":
        return _places_only_digest(model, formulas=formulas, roles=accepted_roles)
    digest = _trace_digest(model, trace=trace, roles=accepted_roles)
    if estimate_tokens(digest) > DIGEST_TOKEN_CEILING:
        raise DigestError("Trace digest exceeds the token ceiling.")
    return digest


__all__ = [
    "DIGEST_TOKEN_CEILING",
    "TRACE_MAX_STEPS",
    "AcceptedRole",
    "DigestError",
    "DigestLevel",
    "estimate_tokens",
    "group_prefix",
    "net_digest",
]
