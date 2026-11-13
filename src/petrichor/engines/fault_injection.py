"""Certified asymmetrization fault injection (spec §20.3, issue #44).

Deterministic, seeded generator of the four fault classes of study C:

- **f1** — inversion d'acquisition: swap the two fork acquisitions of one
  agent (a left-handed philosopher);
- **f2** — libération manquante: drop one release arc of one agent (a fork
  never put back);
- **f3** — polarisation de choix: bias the conflict on the shared fork in
  favor of one agent (arc weight raised on the competitor's acquisition);
- **f4** — duplication asymétrique: duplicate one agent incomplete, without
  its right fork.

Every fault is a **certified transformation** of the Transformer: the derived
model carries a certificate with a mandatory ``element_map`` (identity for
f1-f3, origin-mapping for the duplicated elements of f4) — spec §7. The
generator is deterministic: the seed selects the target agent, and each
variant has its own canonical content hash. A net that does not match the
dining-philosophers agent pattern is rejected explicitly
(``UnsupportedModelError``), never silently faulted.
"""

from typing import Literal, NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.petri_canonical import (
    CanonicalModel,
    ElementId,
    ElementName,
    ModelId,
    build_canonical_model,
)

logger = structlog.get_logger(__name__)

FaultClass = Literal["f1", "f2", "f3", "f4"]
"""The four asymmetrization fault classes of spec §20.3 (closed set)."""

FAULT_CLASSES: tuple[FaultClass, ...] = ("f1", "f2", "f3", "f4")
"""Every fault class, in spec order."""

FaultSeed = NewType("FaultSeed", int)
"""The seed selecting the target agent of a fault variant (deterministic)."""

AgentIndex = NewType("AgentIndex", int)
"""A 1-based agent index in a dining-philosophers-pattern net."""

Technique = NewType("Technique", str)
"""The technique label carried by a fault certificate."""

FAULT_INJECTION_TECHNIQUE = Technique("asymmetrization-fault-injection")
"""The technique of the study-C fault generator (spec §20.3)."""

DERIVED_MODEL_ID_TEMPLATE = "{parent}_fault_{fault_class}_s{seed}"
"""The derived model id template of a fault variant."""

POLARIZED_CONFLICT_WEIGHT = 2
"""The arc weight polarizing a conflict for f3 (spec §20.3: poids)."""

DUPLICATE_PLACE_MARKINGS: tuple[tuple[str, int], ...] = (
    ("think_", 1),
    ("hasL_", 0),
    ("eat_", 0),
)
"""Initial markings of the duplicated agent's places (thinking, empty hands, not eating)."""

UNIT_ARC_WEIGHT = 1
"""The weight of a plain P/T arc (every arc of the duplicated agent)."""

PLACEHOLDER_LAYOUT_X = 0
"""The x coordinate of a placeholder layout (generated nets carry no geometry)."""

PLACEHOLDER_LAYOUT_Y = 0
"""The y coordinate of a placeholder layout (generated nets carry no geometry)."""

AGENT_PLACE_PREFIXES = ("think_", "hasL_", "eat_")
"""Place prefixes identifying one agent of the dining-philosophers pattern."""

AGENT_TRANSITION_PREFIXES = ("takeL_", "takeR_", "rel_")
"""Transition prefixes identifying one agent of the pattern."""

AGENT_ELEMENT_COUNT = len(AGENT_PLACE_PREFIXES) + len(AGENT_TRANSITION_PREFIXES)
"""The number of pattern elements making one complete agent (3 places + 3 transitions)."""


class FaultInjectionError(Exception):
    """Base class of every domain error raised by this module."""


class UnsupportedModelError(FaultInjectionError):
    """The net does not match the dining-philosophers agent pattern."""


class FaultRule(BaseModel):
    """One applied fault rule, exposed in the certificate (never silent)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    elements_affected: int = Field(ge=0)


class ElementMap(BaseModel):
    """The mapping from faulty-net element names to original-net element names.

    Identity for f1-f3 (the net keeps its element names); for f4 the
    duplicated elements map to the original agent's elements — the map is
    mandatory and explicit (spec §7), never assumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping: dict[ElementName, ElementName]

    def to_original(self, *, name: ElementName) -> ElementName:
        """Resolve a faulty-net element name to its original-net name.

        Raises ``KeyError`` when the name is not in the map — an incomplete
        map must never silently pass through as identity (spec §7).
        """
        return self.mapping[name]


class FaultCertificate(BaseModel):
    """The certificate of an injected fault (spec §7, §20.3).

    Carries the applied rule, the fault class, the seed, the target agent and
    the mandatory ``element_map``. Without a certificate, no faulty model
    exists.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rules: tuple[FaultRule, ...]
    element_map: ElementMap
    fault_class: FaultClass
    seed: FaultSeed = Field(ge=0)
    agent: AgentIndex = Field(ge=1)
    technique: Technique = Field(min_length=1, default=FAULT_INJECTION_TECHNIQUE)


class FaultyModel(BaseModel):
    """A faulty derived model plus its certificate (spec §4.4, §20.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: CanonicalModel
    certificate: FaultCertificate
    parent_id: ModelId


def _right_fork_index(*, agent: AgentIndex, agent_count: int) -> int:
    """The right fork of an agent: the next one around the table (1-based)."""
    return agent % agent_count + 1


def _pattern_element_counts(
    *, place_names: set[ElementName], transition_names: set[ElementName]
) -> dict[int, int]:
    """How many pattern elements each agent index carries.

    A complete agent carries exactly ``AGENT_ELEMENT_COUNT`` elements; a
    partial one (some but not all) means the net does not match the pattern.
    """
    counts: dict[int, int] = {}
    for names in (place_names, transition_names):
        for name in names:
            for prefix in (*AGENT_PLACE_PREFIXES, *AGENT_TRANSITION_PREFIXES):
                if name.startswith(prefix) and name[len(prefix) :].isdigit():
                    index = int(name[len(prefix) :])
                    counts[index] = counts.get(index, 0) + 1
    return counts


def _detect_agents(*, model: CanonicalModel) -> list[AgentIndex]:
    """The agent indices of a dining-philosophers-pattern net, sorted.

    An agent ``i`` exists when every ``{think,hasL,eat}_{i}`` place and every
    ``{takeL,takeR,rel}_{i}`` transition is present, and the agents are exactly
    ``1..k`` with no other pattern element beyond them. Raises
    ``UnsupportedModelError`` when no complete agent matches, or when any
    off-pattern agent index is present (partial agent, or a gap in the
    indices) — a net that does not match the pattern is rejected explicitly,
    never silently faulted as a smaller philosophers net.
    """
    place_names = {place.name for place in model.places}
    transition_names = {transition.name for transition in model.transitions}
    counts = _pattern_element_counts(place_names=place_names, transition_names=transition_names)
    agents: list[AgentIndex] = []
    index = 1
    while counts.get(index, 0) == AGENT_ELEMENT_COUNT:
        agents.append(AgentIndex(index))
        index += 1
    off_pattern = sorted(set(counts) - set(range(1, len(agents) + 1)))
    if off_pattern or not agents:
        logger.warning(
            "fault_injection_rejected_unsupported_model",
            model_id=model.id,
            partial_agents=off_pattern,
        )
        raise UnsupportedModelError(
            f"Model {model.id!r} does not match the dining-philosophers agent pattern"
            f" (complete agents: {agents}, off-pattern agents: {off_pattern})."
        )
    return agents


def _arc_between(
    *, model: CanonicalModel, source_name: str, target_name: str
) -> tuple[ElementId, ElementId, ElementId, int] | None:
    """The (id, source, target, weight) of the arc between two element names."""
    id_by_name = {element.name: element.id for element in (*model.places, *model.transitions)}
    for arc in model.arcs:
        if (
            id_by_name.get(ElementName(source_name)) == arc.source
            and id_by_name.get(ElementName(target_name)) == arc.target
        ):
            return (arc.id, arc.source, arc.target, arc.weight)
    return None


def _require_arc(
    *, model: CanonicalModel, source_name: str, target_name: str
) -> tuple[ElementId, ElementId, ElementId, int]:
    arc = _arc_between(model=model, source_name=source_name, target_name=target_name)
    if arc is None:
        logger.warning(
            "fault_injection_rejected_unsupported_model",
            model_id=model.id,
            missing_arc=f"{source_name}->{target_name}",
        )
        raise UnsupportedModelError(
            f"Model {model.id!r} has no arc {source_name!r} -> {target_name!r}."
        )
    return arc


def _identity_element_map(*, model: CanonicalModel) -> ElementMap:
    """Build the identity element map: every element name maps to itself."""
    names = [place.name for place in model.places]
    names += [transition.name for transition in model.transitions]
    return ElementMap(mapping={ElementName(name): ElementName(name) for name in names})


def _f1_invert_acquisition(
    *, model: CanonicalModel, agent: AgentIndex, agent_count: int
) -> tuple[list[dict[str, object]], str, int]:
    """f1: swap the fork acquisitions of ``agent`` (left-handed philosopher)."""
    right = _right_fork_index(agent=agent, agent_count=agent_count)
    left_arc = _require_arc(model=model, source_name=f"fork_{agent}", target_name=f"takeL_{agent}")
    right_arc = _require_arc(model=model, source_name=f"fork_{right}", target_name=f"takeR_{agent}")
    arcs = [arc.model_dump(mode="json") for arc in model.arcs]
    for arc in arcs:
        if arc["id"] == left_arc[0]:
            arc["source"] = right_arc[1]
        elif arc["id"] == right_arc[0]:
            arc["source"] = left_arc[1]
    description = (
        f"f1 inversion d'acquisition: agent {agent} takes fork_{right} before fork_{agent}"
        " (philosophe gaucher)."
    )
    return arcs, description, 2


def _f2_missing_release(
    *, model: CanonicalModel, agent: AgentIndex, agent_count: int
) -> tuple[list[dict[str, object]], str, int]:
    """f2: drop the release arc returning the right fork of ``agent``."""
    right = _right_fork_index(agent=agent, agent_count=agent_count)
    release_arc = _require_arc(model=model, source_name=f"rel_{agent}", target_name=f"fork_{right}")
    arcs = [arc.model_dump(mode="json") for arc in model.arcs if arc.id != release_arc[0]]
    description = (
        f"f2 libération manquante: agent {agent} never returns fork_{right}"
        " (fourchette jamais reposée)."
    )
    return arcs, description, 1


def _f3_polarize_choice(
    *, model: CanonicalModel, agent: AgentIndex, agent_count: int
) -> tuple[list[dict[str, object]], str, int]:
    """f3: polarize the conflict on the shared right fork against the competitor."""
    right = _right_fork_index(agent=agent, agent_count=agent_count)
    competitor_arc = _require_arc(
        model=model, source_name=f"fork_{right}", target_name=f"takeL_{right}"
    )
    arcs = [arc.model_dump(mode="json") for arc in model.arcs]
    for arc in arcs:
        if arc["id"] == competitor_arc[0]:
            arc["weight"] = POLARIZED_CONFLICT_WEIGHT
    description = (
        f"f3 polarisation de choix: the conflict on fork_{right} is biased against agent"
        f" {right} (arc weight {POLARIZED_CONFLICT_WEIGHT}), so agent {agent} is"
        " systématiquement servi."
    )
    return arcs, description, 1


def _f4_duplicate_asymmetric(
    *, model: CanonicalModel, agent: AgentIndex, agent_count: int
) -> tuple[
    list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], ElementMap, str, int
]:
    """f4: duplicate ``agent`` as agent ``agent_count + 1``, without its right fork."""
    new_index = agent_count + 1
    suffix = str(new_index)
    places = [place.model_dump(mode="json") for place in model.places]
    transitions = [transition.model_dump(mode="json") for transition in model.transitions]
    arcs = [arc.model_dump(mode="json") for arc in model.arcs]
    for prefix, marking in DUPLICATE_PLACE_MARKINGS:
        places.append(
            {
                "id": f"pl_{prefix}{suffix}",
                "name": f"{prefix}{suffix}",
                "initial_marking": marking,
                "layout": {"x": PLACEHOLDER_LAYOUT_X, "y": PLACEHOLDER_LAYOUT_Y},
                "annotations": {},
            }
        )
    for prefix in AGENT_TRANSITION_PREFIXES:
        transitions.append(
            {
                "id": f"tr_{prefix}{suffix}",
                "name": f"{prefix}{suffix}",
                "layout": {"x": PLACEHOLDER_LAYOUT_X, "y": PLACEHOLDER_LAYOUT_Y},
                "annotations": {},
            }
        )
    new_arcs = [
        (f"pl_think_{suffix}", f"tr_takeL_{suffix}"),
        (f"pl_fork_{agent}", f"tr_takeL_{suffix}"),
        (f"tr_takeL_{suffix}", f"pl_hasL_{suffix}"),
        (f"pl_hasL_{suffix}", f"tr_takeR_{suffix}"),
        # No right-fork arc: the duplicate is incomplete (philosophe sans sa
        # fourchette droite).
        (f"tr_takeR_{suffix}", f"pl_eat_{suffix}"),
        (f"pl_eat_{suffix}", f"tr_rel_{suffix}"),
        (f"tr_rel_{suffix}", f"pl_think_{suffix}"),
        (f"tr_rel_{suffix}", f"pl_fork_{agent}"),
    ]
    for position, (source, target) in enumerate(new_arcs, start=1):
        arcs.append(
            {
                "id": f"ar_f4_{position:03d}",
                "source": source,
                "target": target,
                "weight": UNIT_ARC_WEIGHT,
                "annotations": {},
            }
        )
    mapping = _identity_element_map(model=model).mapping
    for prefix in (*AGENT_PLACE_PREFIXES, *AGENT_TRANSITION_PREFIXES):
        mapping[ElementName(f"{prefix}{suffix}")] = ElementName(f"{prefix}{agent}")
    element_map = ElementMap(mapping=mapping)
    description = (
        f"f4 duplication asymétrique: agent {agent} duplicated as agent {new_index},"
        " without its right fork (philosophe sans sa fourchette droite)."
    )
    return arcs, places, transitions, element_map, description, 6


def inject_fault(
    *,
    model: CanonicalModel,
    fault_class: FaultClass,
    seed: FaultSeed,
) -> FaultyModel:
    """Inject one deterministic, seeded asymmetrization fault into ``model``.

    The seed selects the target agent (``seed % agent_count + 1``); the same
    (model, fault_class, seed) always yields the same variant, with its own
    canonical content hash. The derived model carries a certificate with the
    mandatory ``element_map`` (spec §7, §20.3).

    Raises ``UnsupportedModelError`` for a net that does not match the
    dining-philosophers agent pattern — never a silent fault.
    """
    if fault_class not in FAULT_CLASSES:
        logger.warning("fault_injection_rejected_unknown_class", fault_class=fault_class)
        raise FaultInjectionError(f"Unknown fault class {fault_class!r}.")
    agents = _detect_agents(model=model)
    agent_count = len(agents)
    agent = AgentIndex(seed % agent_count + 1)

    element_map = _identity_element_map(model=model)
    arcs = [arc.model_dump(mode="json") for arc in model.arcs]
    places = [place.model_dump(mode="json") for place in model.places]
    transitions = [transition.model_dump(mode="json") for transition in model.transitions]
    if fault_class == "f1":
        arcs, description, affected = _f1_invert_acquisition(
            model=model, agent=agent, agent_count=agent_count
        )
    elif fault_class == "f2":
        arcs, description, affected = _f2_missing_release(
            model=model, agent=agent, agent_count=agent_count
        )
    elif fault_class == "f3":
        arcs, description, affected = _f3_polarize_choice(
            model=model, agent=agent, agent_count=agent_count
        )
    else:
        arcs, places, transitions, element_map, description, affected = _f4_duplicate_asymmetric(
            model=model, agent=agent, agent_count=agent_count
        )

    derived_payload = model.model_dump(mode="json")
    derived_payload["id"] = DERIVED_MODEL_ID_TEMPLATE.format(
        parent=model.id, fault_class=fault_class, seed=seed
    )
    derived_payload["parent"] = model.id
    derived_payload["children"] = []
    derived_payload["places"] = places
    derived_payload["transitions"] = transitions
    derived_payload["arcs"] = arcs
    derived_payload["derivations"] = [
        {
            "kind": "fault_injection",
            "fault_class": fault_class,
            "seed": seed,
            "agent": agent,
            "parent_id": model.id,
        }
    ]
    certificate = FaultCertificate(
        rules=(
            FaultRule(
                name=f"{fault_class}-asymmetrization",
                description=description,
                elements_affected=affected,
            ),
        ),
        element_map=element_map,
        fault_class=fault_class,
        seed=seed,
        agent=agent,
        technique=FAULT_INJECTION_TECHNIQUE,
    )
    derived_payload["certificates"] = [certificate.model_dump(mode="json")]
    faulty_model = build_canonical_model(payload=derived_payload)
    logger.debug(
        "fault_injected",
        parent_id=model.id,
        derived_id=faulty_model.id,
        fault_class=fault_class,
        seed=seed,
        agent=agent,
        content_hash=faulty_model.content_hash,
    )
    return FaultyModel(model=faulty_model, certificate=certificate, parent_id=model.id)
