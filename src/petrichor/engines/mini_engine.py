"""The embedded static mini-engine (spec §18.1; issue #5 / P05).

The mini-engine is the sole producer of *static* verdicts (spec §18.1,
§19.4): P/T-invariants (kernel of the incidence matrix), siphons and
traps, structural bounds, Tarjan SCCs over the reachability graph, BFS
``AG EF`` and deadlock detection. It never calls an external tool and
never emits an implicit verdict: a net beyond its capacity is rejected
explicitly with :class:`NetBeyondCapacityError` (spec §14.2, §18.5).
"""

import re
import sys
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import TYPE_CHECKING, Protocol

import structlog
from pydantic import BaseModel, ConfigDict

from petrichor.engines.verdict import (
    STATIC_ENGINE_COST,
    CycleWitness,
    MarkedPlaces,
    PathWitness,
    Verdict,
)
from petrichor.state_graph import StateGraphUnsupportedError
from petrichor.triage import (
    STATIC_KINDS,
    CapacityReport,
    NonVacuityGuard,
    StaticUndecidableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from petrichor.checks import CheckFamilyName, CheckQuestion, KindPattern
    from petrichor.petri_canonical import CanonicalModel, JsonValue

logger = structlog.get_logger(__name__)

MAX_REACHABLE_STATES = 10_000
"""Capacity cap of the reachability-graph exploration."""

MAX_PLACES_FOR_INVARIANT_ENUMERATION = 16
"""Capacity cap of the 0/1-support invariant enumeration (2^n subsets)."""

MAX_PLACES_FOR_AUTOMORPHISM_SEARCH = 16
"""Capacity cap of the automorphism backtracking search."""

MAX_AUTOMORPHISM_NODES = 100_000
"""Backtracking-node budget of the automorphism search."""

type Marking = tuple[int, ...]
"""A full marking vector, indexed like the model's place tuple."""


class PetriEngineError(Exception):
    """Base class of every domain error raised by the engines package."""


class NetBeyondCapacityError(PetriEngineError):
    """The net exceeds the mini-engine capacity: explicit rejection, never a silent result."""


class UnsupportedFormalismError(PetriEngineError):
    """The mini-engine analyzes P/T nets only; Symmetric nets are rejected (dépliage = P27)."""


@dataclass
class _TransitionInputOutput:
    """Weighted pre- and post-sets of one transition, over place names."""

    pre: dict[str, int] = field(default_factory=dict)
    post: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class StateGraph:
    """The reachability graph of a P/T net, in BFS order from the initial marking."""

    place_names: tuple[str, ...]
    initial: Marking
    states: tuple[Marking, ...]
    successors: dict[Marking, tuple[tuple[str, Marking], ...]]
    deadlocks: tuple[Marking, ...]
    arc_count: int


def _require_pt(*, model: CanonicalModel) -> None:
    """Reject non-P/T formalisms explicitly (Symmetric unfolding is P27)."""
    if model.formalism != "pt":
        logger.warning(
            "engine_model_rejected",
            reason="unsupported_formalism",
            formalism=model.formalism,
        )
        raise UnsupportedFormalismError(
            f"The mini-engine analyzes P/T nets only; got formalism {model.formalism!r}."
        )


def _place_names(*, model: CanonicalModel) -> tuple[str, ...]:
    """The place names of the model, in declaration order."""
    return tuple(place.name for place in model.places)


def _transition_input_output(*, model: CanonicalModel) -> dict[str, _TransitionInputOutput]:
    """Per transition, the weighted pre- and post-sets over place names."""
    place_ids: set[str] = {place.id for place in model.places}
    names_by_id: dict[str, str] = {
        element.id: element.name for element in (*model.places, *model.transitions)
    }
    input_output: dict[str, _TransitionInputOutput] = {
        transition.name: _TransitionInputOutput() for transition in model.transitions
    }
    for arc in model.arcs:
        source_is_place = arc.source in place_ids
        target_is_place = arc.target in place_ids
        if source_is_place == target_is_place:
            raise PetriEngineError(
                f"Arc {arc.id} must bind a place to a transition, not two "
                f"{'places' if source_is_place else 'transitions'}."
            )
        if source_is_place:
            input_output[names_by_id[arc.target]].pre[names_by_id[arc.source]] = (
                input_output[names_by_id[arc.target]].pre.get(names_by_id[arc.source], 0)
                + arc.weight
            )
        else:
            input_output[names_by_id[arc.source]].post[names_by_id[arc.target]] = (
                input_output[names_by_id[arc.source]].post.get(names_by_id[arc.target], 0)
                + arc.weight
            )
    return input_output


def _fire(
    *, marking: Marking, index_of: dict[str, int], input_output: _TransitionInputOutput
) -> Marking | None:
    """The successor marking of firing the transition, or None if not enabled."""
    if any(marking[index_of[place]] < weight for place, weight in input_output.pre.items()):
        return None
    result = list(marking)
    for place, weight in input_output.pre.items():
        result[index_of[place]] -= weight
    for place, weight in input_output.post.items():
        result[index_of[place]] += weight
    return tuple(result)


def _explore(
    *, model: CanonicalModel, max_states: int, raise_on_cap: bool
) -> tuple[StateGraph, bool]:
    """BFS the reachability graph up to ``max_states`` states.

    With ``raise_on_cap`` a capacity breach raises ``NetBeyondCapacityError``;
    otherwise the exploration stops at the cap and returns the partial graph
    with ``truncated=True`` — an explicit degradation, never a silent result.
    """
    _require_pt(model=model)
    place_names = _place_names(model=model)
    index_of = {name: position for position, name in enumerate(place_names)}
    initial = tuple(place.initial_marking for place in model.places)
    transitions = tuple(_transition_input_output(model=model).items())
    seen: dict[Marking, int] = {initial: 0}
    queue = [initial]
    successors: dict[Marking, list[tuple[str, Marking]]] = {initial: []}
    deadlocks: list[Marking] = []
    arc_count = 0
    truncated = False
    while queue and not truncated:
        marking = queue.pop(0)
        fired_any = False
        for name, input_output in transitions:
            next_marking = _fire(marking=marking, index_of=index_of, input_output=input_output)
            if next_marking is None:
                continue
            fired_any = True
            arc_count += 1
            if next_marking in seen:
                successors[marking].append((name, next_marking))
                continue
            if len(seen) >= max_states:
                if raise_on_cap:
                    logger.warning(
                        "engine_capacity_exceeded",
                        state_count=len(seen),
                        max_states=max_states,
                    )
                    raise NetBeyondCapacityError(
                        "Reachability graph exceeds the mini-engine capacity"
                        f" of {max_states} states."
                    )
                truncated = True
                break
            successors[marking].append((name, next_marking))
            seen[next_marking] = len(seen)
            successors[next_marking] = []
            queue.append(next_marking)
        if not fired_any and not truncated:
            deadlocks.append(marking)
    logger.debug(
        "state_graph_explored",
        state_count=len(seen),
        arc_count=arc_count,
        deadlock_count=len(deadlocks),
        truncated=truncated,
    )
    graph = StateGraph(
        place_names=place_names,
        initial=initial,
        states=tuple(seen),
        successors={marking: tuple(edges) for marking, edges in successors.items()},
        deadlocks=tuple(deadlocks),
        arc_count=arc_count,
    )
    return graph, truncated


def explore_state_graph(*, model: CanonicalModel) -> StateGraph:
    """BFS the reachability graph; raises NetBeyondCapacityError past the cap."""
    graph, _truncated = _explore(model=model, max_states=MAX_REACHABLE_STATES, raise_on_cap=True)
    return graph


def explore_state_graph_bounded(
    *, model: CanonicalModel, max_states: int
) -> tuple[StateGraph, bool]:
    """BFS the reachability graph up to ``max_states`` states.

    Returns the (possibly partial) graph and whether it was truncated at the
    cap — the explicit degradation path for unbounded nets (spec §14, P13).
    """
    return _explore(model=model, max_states=max_states, raise_on_cap=False)


def explore_state_graph_payload(*, model: CanonicalModel) -> tuple[dict[str, JsonValue], bool]:
    """Bounded reachability graph as a JSON payload (adapter for ``StateGraphExplorer``).

    The verdict lock (spec §19.4) keeps this adapter inside ``engines/``: the
    API only sees plain JSON data. A non-P/T model raises
    ``StateGraphUnsupportedError`` — an explicit rejection, never a graph.
    """
    try:
        graph, truncated = explore_state_graph_bounded(model=model, max_states=MAX_REACHABLE_STATES)
    except UnsupportedFormalismError as error:
        raise StateGraphUnsupportedError(str(error)) from error
    payload: dict[str, JsonValue] = {
        "place_names": list(graph.place_names),
        "initial": list(graph.initial),
        "states": [list(state) for state in graph.states],
        "edges": [
            {"source": list(marking), "transition": name, "target": list(target)}
            for marking, edges in graph.successors.items()
            for name, target in edges
        ],
        "deadlocks": [list(deadlock) for deadlock in graph.deadlocks],
        "arc_count": graph.arc_count,
        "max_states": MAX_REACHABLE_STATES,
    }
    return payload, truncated


def _kernel_basis(*, matrix: list[list[Fraction]]) -> list[list[Fraction]]:
    """Exact null-space basis of a rational matrix, by Gauss-Jordan elimination."""
    rows = [row[:] for row in matrix]
    row_count = len(rows)
    column_count = len(rows[0]) if rows else 0
    pivot_columns: list[int] = []
    pivot_row = 0
    for column in range(column_count):
        pivot = next((r for r in range(pivot_row, row_count) if rows[r][column] != 0), None)
        if pivot is None:
            continue
        rows[pivot_row], rows[pivot] = rows[pivot], rows[pivot_row]
        scale = rows[pivot_row][column]
        rows[pivot_row] = [value / scale for value in rows[pivot_row]]
        for r in range(row_count):
            if r != pivot_row and rows[r][column] != 0:
                factor = rows[r][column]
                rows[r] = [a - factor * b for a, b in zip(rows[r], rows[pivot_row], strict=True)]
        pivot_columns.append(column)
        pivot_row += 1
    basis: list[list[Fraction]] = []
    free_columns = [c for c in range(column_count) if c not in pivot_columns]
    for free in free_columns:
        vector = [Fraction(0)] * column_count
        vector[free] = Fraction(1)
        for r, pivot_column in enumerate(pivot_columns):
            vector[pivot_column] = -rows[r][free]
        basis.append(vector)
    return basis


def _incidence_transposed(*, model: CanonicalModel) -> list[list[Fraction]]:
    """C^T (transitions x places) of the incidence matrix C = post - pre."""
    place_names = _place_names(model=model)
    index_of = {name: position for position, name in enumerate(place_names)}
    input_outputs = _transition_input_output(model=model)
    matrix = [[Fraction(0)] * len(place_names) for _ in input_outputs]
    for row, input_output in enumerate(input_outputs.values()):
        for place, weight in input_output.pre.items():
            matrix[row][index_of[place]] -= weight
        for place, weight in input_output.post.items():
            matrix[row][index_of[place]] += weight
    return matrix


def place_invariants(*, model: CanonicalModel) -> tuple[tuple[Fraction, ...], ...]:
    """Kernel basis of C^T: the P/T-invariant space (spec §18.1, noyau d'incidence)."""
    _require_pt(model=model)
    basis = _kernel_basis(matrix=_incidence_transposed(model=model))
    logger.debug("place_invariants_computed", invariant_count=len(basis))
    return tuple(tuple(vector) for vector in basis)


def _is_invariant_support(
    *, support: frozenset[str], input_outputs: dict[str, _TransitionInputOutput]
) -> bool:
    """Whether the 0/1 indicator of the support is a P-invariant."""
    for input_output in input_outputs.values():
        consumed = sum(weight for place, weight in input_output.pre.items() if place in support)
        produced = sum(weight for place, weight in input_output.post.items() if place in support)
        if consumed != produced:
            return False
    return True


def positive_invariants(*, model: CanonicalModel) -> tuple[tuple[str, ...], ...]:
    """The minimal 0/1-support P-invariants, by bounded support enumeration (spec §18.1).

    Invariant supports are closed under union, so the enumeration is
    filtered down to the minimal ones (no other support is a proper
    subset): those are the generators of the positive invariant cone.
    """
    _require_pt(model=model)
    place_names = _place_names(model=model)
    if len(place_names) > MAX_PLACES_FOR_INVARIANT_ENUMERATION:
        logger.warning(
            "engine_capacity_exceeded",
            place_count=len(place_names),
            max_places=MAX_PLACES_FOR_INVARIANT_ENUMERATION,
        )
        raise NetBeyondCapacityError(
            f"Invariant enumeration is capped at {MAX_PLACES_FOR_INVARIANT_ENUMERATION} places."
        )
    input_outputs = _transition_input_output(model=model)
    supports: list[frozenset[str]] = []
    for mask in range(1, 1 << len(place_names)):
        support = frozenset(
            place_names[position] for position in range(len(place_names)) if mask >> position & 1
        )
        if _is_invariant_support(support=support, input_outputs=input_outputs):
            supports.append(support)
    minimal = [support for support in supports if not any(other < support for other in supports)]
    invariants = tuple(tuple(sorted(support)) for support in minimal)
    logger.debug("positive_invariants_computed", count=len(invariants))
    return invariants


def structural_bounds(*, model: CanonicalModel) -> dict[str, int]:
    """Per-place structural bound from the 0/1 P-invariants (spec §18.1, bornes)."""
    initial: dict[str, int] = {place.name: place.initial_marking for place in model.places}
    bounds: dict[str, int] = {}
    for invariant in positive_invariants(model=model):
        total = sum(initial[place] for place in invariant)
        for place in invariant:
            bounds[place] = min(bounds.get(place, total), total)
    logger.debug("structural_bounds_computed", bounded_places=len(bounds))
    return bounds


def _siphon_keeps(
    *, place: str, current: set[str], input_outputs: dict[str, _TransitionInputOutput]
) -> bool:
    """A place stays in a siphon iff every transition producing it consumes from the siphon."""
    return all(
        any(source in current for source in input_output.pre)
        for input_output in input_outputs.values()
        if place in input_output.post
    )


def _trap_keeps(
    *, place: str, current: set[str], input_outputs: dict[str, _TransitionInputOutput]
) -> bool:
    """A place stays in a trap iff every transition consuming it produces into the trap."""
    return all(
        any(target in current for target in input_output.post)
        for input_output in input_outputs.values()
        if place in input_output.pre
    )


class _KeepsPredicate(Protocol):
    """The place-removal predicate of a siphon/trap fixed-point iteration."""

    def __call__(
        self, *, place: str, current: set[str], input_outputs: dict[str, _TransitionInputOutput]
    ) -> bool: ...


def _greatest_fixed_point(
    *,
    model: CanonicalModel,
    keeps: _KeepsPredicate,
) -> frozenset[str]:
    """Greatest fixed point of a monotone place-removal operator."""
    input_outputs = _transition_input_output(model=model)
    current = set(_place_names(model=model))
    changed = True
    while changed:
        changed = False
        for place in sorted(current):
            if not keeps(place=place, current=current, input_outputs=input_outputs):
                current.discard(place)
                changed = True
    return frozenset(current)


def maximal_siphon(*, model: CanonicalModel) -> frozenset[str]:
    """The maximal siphon: greatest fixed point of the siphon operator (spec §18.1)."""
    _require_pt(model=model)
    siphon = _greatest_fixed_point(model=model, keeps=_siphon_keeps)
    logger.debug("maximal_siphon_computed", size=len(siphon))
    return siphon


def maximal_trap(*, model: CanonicalModel) -> frozenset[str]:
    """The maximal trap: greatest fixed point of the trap operator (spec §18.1)."""
    _require_pt(model=model)
    trap = _greatest_fixed_point(model=model, keeps=_trap_keeps)
    logger.debug("maximal_trap_computed", size=len(trap))
    return trap


@dataclass
class _TarjanState:
    """The mutable working state of Tarjan's SCC algorithm."""

    index: dict[Marking, int] = field(default_factory=dict)
    low: dict[Marking, int] = field(default_factory=dict)
    on_stack: set[Marking] = field(default_factory=set)
    stack: list[Marking] = field(default_factory=list)
    components: list[tuple[Marking, ...]] = field(default_factory=list)
    counter: int = 0


def _strong_connect(*, graph: StateGraph, node: Marking, state: _TarjanState) -> None:
    """One recursive step of Tarjan's SCC algorithm."""
    state.index[node] = state.low[node] = state.counter
    state.counter += 1
    state.stack.append(node)
    state.on_stack.add(node)
    for _, next_marking in graph.successors[node]:
        if next_marking not in state.index:
            _strong_connect(graph=graph, node=next_marking, state=state)
            state.low[node] = min(state.low[node], state.low[next_marking])
        elif next_marking in state.on_stack:
            state.low[node] = min(state.low[node], state.index[next_marking])
    if state.low[node] == state.index[node]:
        component: list[Marking] = []
        while True:
            member = state.stack.pop()
            state.on_stack.discard(member)
            component.append(member)
            if member == node:
                break
        state.components.append(tuple(component))


def tarjan_scc(*, graph: StateGraph) -> tuple[tuple[Marking, ...], ...]:
    """Tarjan's strongly connected components over the reachability graph (spec §18.1)."""
    state = _TarjanState()
    previous_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(previous_limit, MAX_REACHABLE_STATES + 1000))
    try:
        for root in graph.states:
            if root not in state.index:
                _strong_connect(graph=graph, node=root, state=state)
    finally:
        sys.setrecursionlimit(previous_limit)
    logger.debug("tarjan_scc_computed", scc_count=len(state.components))
    return tuple(state.components)


def _place_signature(
    *,
    initial: dict[str, int],
    place: str,
    input_outputs: dict[str, _TransitionInputOutput],
) -> tuple[int, tuple[tuple[str, int], ...]]:
    """Structural signature of a place: initial marking + sorted incident arc kinds."""
    incident = sorted(
        (direction, weight)
        for input_output in input_outputs.values()
        for direction, weight in (
            [("out", w) for w in input_output.pre.values() if place in input_output.pre]
            + [("in", w) for w in input_output.post.values() if place in input_output.post]
        )
    )
    return (initial[place], tuple(incident))


def _input_output_key(
    *,
    input_output: _TransitionInputOutput,
    mapping: dict[str, str],
) -> tuple[frozenset[tuple[str, int]], frozenset[tuple[str, int]]]:
    """The pre/post key of a transition, with place names mapped through the automorphism."""
    pre = frozenset((mapping[place], weight) for place, weight in input_output.pre.items())
    post = frozenset((mapping[place], weight) for place, weight in input_output.post.items())
    return (pre, post)


def _complete_automorphism(
    *,
    model: CanonicalModel,
    input_outputs: dict[str, _TransitionInputOutput],
    mapping: dict[str, str],
) -> dict[str, str] | None:
    """Extend a place map to a full automorphism, or None if it does not extend."""
    own_keys = {
        (frozenset(input_output.pre.items()), frozenset(input_output.post.items())): name
        for name, input_output in input_outputs.items()
    }
    transition_map: dict[str, str] = {}
    for name, input_output in input_outputs.items():
        image = _input_output_key(input_output=input_output, mapping=mapping)
        if image not in own_keys or own_keys[image] in transition_map.values():
            return None
        transition_map[name] = own_keys[image]
    place_ids: set[str] = {place.id for place in model.places}
    names_by_id: dict[str, str] = {
        element.id: element.name for element in (*model.places, *model.transitions)
    }
    arcs: dict[tuple[str, str], int] = {
        (names_by_id[arc.source], names_by_id[arc.target]): arc.weight for arc in model.arcs
    }

    def mapped_name(element_id: str) -> str:
        name = names_by_id[element_id]
        return mapping[name] if element_id in place_ids else transition_map[name]

    for arc in model.arcs:
        key = (mapped_name(arc.source), mapped_name(arc.target))
        if arcs.get(key) != arc.weight:
            return None
    return dict(mapping)


@dataclass
class _AutomorphismSearch:
    """The mutable working state of the automorphism backtracking search."""

    order: tuple[str, ...]
    candidates: dict[str, tuple[str, ...]]
    model: CanonicalModel
    input_outputs: dict[str, _TransitionInputOutput]
    mapping: dict[str, str] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)
    found: list[dict[str, str]] = field(default_factory=list)
    budget: int = 0


def _search_automorphisms(*, search: _AutomorphismSearch, position: int) -> None:
    """Backtracking search of every place permutation that extends to an automorphism."""
    search.budget += 1
    if search.budget > MAX_AUTOMORPHISM_NODES:
        logger.warning(
            "engine_capacity_exceeded",
            search_nodes=search.budget,
            max_nodes=MAX_AUTOMORPHISM_NODES,
        )
        raise NetBeyondCapacityError(
            f"Automorphism search exceeds the budget of {MAX_AUTOMORPHISM_NODES} nodes."
        )
    if position == len(search.order):
        automorphism = _complete_automorphism(
            model=search.model, input_outputs=search.input_outputs, mapping=search.mapping
        )
        if automorphism is not None:
            search.found.append(automorphism)
        return
    place = search.order[position]
    for candidate in search.candidates[place]:
        if candidate in search.used:
            continue
        search.mapping[place] = candidate
        search.used.add(candidate)
        _search_automorphisms(search=search, position=position + 1)
        search.used.discard(candidate)
        del search.mapping[place]


def _automorphisms(*, model: CanonicalModel) -> tuple[dict[str, str], ...]:
    """Every automorphism of the net, as a place-name map (spec §14.5, symétrie vérifiée)."""
    _require_pt(model=model)
    place_names = _place_names(model=model)
    if len(place_names) > MAX_PLACES_FOR_AUTOMORPHISM_SEARCH:
        logger.warning(
            "engine_capacity_exceeded",
            place_count=len(place_names),
            max_places=MAX_PLACES_FOR_AUTOMORPHISM_SEARCH,
        )
        raise NetBeyondCapacityError(
            f"Automorphism search is capped at {MAX_PLACES_FOR_AUTOMORPHISM_SEARCH} places."
        )
    input_outputs = _transition_input_output(model=model)
    initial: dict[str, int] = {place.name: place.initial_marking for place in model.places}
    signatures = {
        name: _place_signature(initial=initial, place=name, input_outputs=input_outputs)
        for name in place_names
    }
    order = tuple(sorted(place_names, key=lambda name: (signatures[name], name), reverse=True))
    candidates = {
        name: tuple(q for q in place_names if signatures[q] == signatures[name])
        for name in place_names
    }
    search = _AutomorphismSearch(
        order=order, candidates=candidates, model=model, input_outputs=input_outputs
    )
    _search_automorphisms(search=search, position=0)
    logger.debug("automorphisms_computed", count=len(search.found))
    return tuple(search.found)


def _apply_automorphism(
    *,
    marking: Marking,
    index_of: dict[str, int],
    automorphism: dict[str, str],
) -> Marking:
    """The image of a marking under a place permutation."""
    result = [0] * len(marking)
    for name, position in index_of.items():
        result[index_of[automorphism[name]]] = marking[position]
    return tuple(result)


def symmetry_orbits(*, model: CanonicalModel, graph: StateGraph) -> tuple[frozenset[Marking], ...]:
    """Orbits of the reachable states under the net's automorphism group (spec §14.5)."""
    automorphisms = _automorphisms(model=model)
    index_of = {name: position for position, name in enumerate(graph.place_names)}
    orbits: list[frozenset[Marking]] = []
    seen: set[Marking] = set()
    for state in graph.states:
        if state in seen:
            continue
        orbit = frozenset(
            _apply_automorphism(marking=state, index_of=index_of, automorphism=auto)
            for auto in automorphisms
        )
        seen |= orbit
        orbits.append(orbit)
    logger.debug(
        "symmetry_orbits_computed",
        orbit_count=len(orbits),
        automorphism_count=len(automorphisms),
    )
    return tuple(orbits)


def _reverse_adjacency(*, graph: StateGraph) -> dict[Marking, tuple[Marking, ...]]:
    """The reversed edge list of the reachability graph."""
    reverse: dict[Marking, list[Marking]] = {state: [] for state in graph.states}
    for source, edges in graph.successors.items():
        for _, target in edges:
            reverse[target].append(source)
    return {state: tuple(predecessors) for state, predecessors in reverse.items()}


def _path_from_initial(
    *,
    graph: StateGraph,
    target_predicate: Callable[[Marking], bool],
) -> tuple[Marking, ...]:
    """A BFS path from the initial marking to a state satisfying the predicate."""
    if target_predicate(graph.initial):
        return (graph.initial,)
    parents: dict[Marking, Marking] = {}
    visited = {graph.initial}
    queue = [graph.initial]
    while queue:
        marking = queue.pop(0)
        for _, next_marking in graph.successors[marking]:
            if next_marking in visited:
                continue
            visited.add(next_marking)
            parents[next_marking] = marking
            if target_predicate(next_marking):
                path = [next_marking]
                while path[-1] != graph.initial:
                    path.append(parents[path[-1]])
                return tuple(reversed(path))
            queue.append(next_marking)
    raise PetriEngineError("No reachable state satisfies the predicate; the graph is inconsistent.")


def _sparse(*, marking: Marking, place_names: tuple[str, ...]) -> MarkedPlaces:
    """The sparse, sorted (place, tokens) view of a full marking."""
    return tuple(
        (name, count) for name, count in zip(place_names, marking, strict=True) if count > 0
    )


def check_ag_ef(*, model: CanonicalModel, place: str) -> Verdict:
    """Verdict of ``AG EF (place marked)`` by backward BFS (spec §18.1, BFS AG EF)."""
    graph = explore_state_graph(model=model)
    if place not in graph.place_names:
        raise PetriEngineError(f"Unknown place {place!r}: not in the model.")
    index_of = {name: position for position, name in enumerate(graph.place_names)}
    target = {state for state in graph.states if state[index_of[place]] > 0}
    reverse = _reverse_adjacency(graph=graph)
    can_reach = set(target)
    queue = list(target)
    while queue:
        state = queue.pop(0)
        for predecessor in reverse[state]:
            if predecessor not in can_reach:
                can_reach.add(predecessor)
                queue.append(predecessor)
    if can_reach == set(graph.states):
        logger.debug("ag_ef_verdict", place=place, value="true")
        return Verdict(value="true", technique="bfs_ag_ef", witness=None, cost=STATIC_ENGINE_COST)
    path = _path_from_initial(graph=graph, target_predicate=lambda state: state not in can_reach)
    witness = PathWitness(
        states=tuple(_sparse(marking=state, place_names=graph.place_names) for state in path),
        description=(
            f"AG EF ({place}) is false: from this reachable marking, "
            f"no marking with {place} is reachable."
        ),
    )
    logger.debug("ag_ef_verdict", place=place, value="false")
    return Verdict(value="false", technique="bfs_ag_ef", witness=witness, cost=STATIC_ENGINE_COST)


def check_ef_deadlock(*, model: CanonicalModel) -> Verdict:
    """Verdict of ``EF DEADLOCK`` by reachability-graph exploration (détection d'impasse)."""
    graph = explore_state_graph(model=model)
    if not graph.deadlocks:
        logger.debug("ef_deadlock_verdict", value="false")
        return Verdict(
            value="false", technique="deadlock_bfs", witness=None, cost=STATIC_ENGINE_COST
        )
    deadlock_set = set(graph.deadlocks)
    path = _path_from_initial(graph=graph, target_predicate=lambda state: state in deadlock_set)
    witness = PathWitness(
        states=tuple(_sparse(marking=state, place_names=graph.place_names) for state in path),
        description="EF DEADLOCK is true: this path reaches a marking with no enabled transition.",
    )
    logger.debug("ef_deadlock_verdict", value="true")
    return Verdict(value="true", technique="deadlock_bfs", witness=witness, cost=STATIC_ENGINE_COST)


def _cycle_within(
    *,
    component: frozenset[Marking],
    successors: dict[Marking, tuple[tuple[str, Marking], ...]],
) -> tuple[Marking, ...]:
    """One concrete cycle inside a non-trivial SCC (or a self-loop)."""
    start = next(iter(component))
    seen: dict[Marking, int] = {start: 0}
    path = [start]
    current = start
    while True:
        next_marking = next(target for _, target in successors[current] if target in component)
        if next_marking in seen:
            return (*path[seen[next_marking] :], next_marking)
        seen[next_marking] = len(path)
        path.append(next_marking)
        current = next_marking


def find_starvation_cycle(*, model: CanonicalModel, place: str) -> CycleWitness | None:
    """A reachable cycle of markings where ``place`` never holds (témoin de famine cyclique)."""
    graph = explore_state_graph(model=model)
    if place not in graph.place_names:
        raise PetriEngineError(f"Unknown place {place!r}: not in the model.")
    index_of = {name: position for position, name in enumerate(graph.place_names)}
    restricted = {state for state in graph.states if state[index_of[place]] == 0}
    restricted_graph = replace(
        graph,
        states=tuple(restricted),
        successors={
            state: tuple(edge for edge in graph.successors[state] if edge[1] in restricted)
            for state in restricted
        },
    )
    for component in tarjan_scc(graph=restricted_graph):
        has_self_loop = any(
            target == state
            for state in component
            for _, target in restricted_graph.successors[state]
        )
        if len(component) > 1 or has_self_loop:
            cycle = _cycle_within(
                component=frozenset(component),
                successors=restricted_graph.successors,
            )
            logger.debug("starvation_cycle_found", place=place, cycle_length=len(cycle))
            return CycleWitness(
                states=tuple(
                    _sparse(marking=state, place_names=graph.place_names) for state in cycle
                ),
                description=(
                    f"Cyclic starvation witness: this reachable cycle never marks {place}."
                ),
            )
    logger.debug("starvation_cycle_not_found", place=place)
    return None


class StaticAnalysis(BaseModel):
    """The full static report of the mini-engine over one model (spec §18.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_count: int
    arc_count: int
    deadlock_count: int
    deadlocks: tuple[MarkedPlaces, ...]
    scc_count: int
    scc_sizes: tuple[int, ...]
    invariant_count: int
    positive_invariants: tuple[tuple[str, ...], ...]
    maximal_siphon: tuple[str, ...]
    maximal_trap: tuple[str, ...]
    structural_bounds: dict[str, int]
    orbit_count: int


def analyze(*, model: CanonicalModel) -> StaticAnalysis:
    """Run every static analysis of the mini-engine and bundle the report."""
    graph = explore_state_graph(model=model)
    components = tarjan_scc(graph=graph)
    report = StaticAnalysis(
        state_count=len(graph.states),
        arc_count=graph.arc_count,
        deadlock_count=len(graph.deadlocks),
        deadlocks=tuple(
            _sparse(marking=deadlock, place_names=graph.place_names) for deadlock in graph.deadlocks
        ),
        scc_count=len(components),
        scc_sizes=tuple(sorted(len(component) for component in components)),
        invariant_count=len(place_invariants(model=model)),
        positive_invariants=positive_invariants(model=model),
        maximal_siphon=tuple(sorted(maximal_siphon(model=model))),
        maximal_trap=tuple(sorted(maximal_trap(model=model))),
        structural_bounds=structural_bounds(model=model),
        orbit_count=len(symmetry_orbits(model=model, graph=graph)),
    )
    logger.debug(
        "static_analysis_completed",
        model_id=model.id,
        state_count=report.state_count,
        invariant_count=report.invariant_count,
        orbit_count=report.orbit_count,
    )
    return report


# ---------------------------------------------------------------------------
# P14 adapters: capacity probe, static resolution, non-vacuity guard (issue #14).
# Plain-data implementations of the StaticResolver protocol (spec §19.4): the
# API receives JSON payloads and guard records, never verdict objects.
# ---------------------------------------------------------------------------

COMPARISON_PATTERN = re.compile(
    r"^\(\s*(?P<place>[A-Za-z_][A-Za-z0-9_]*)\s*"
    r"(?P<op>>=|<=|==|!=|>|<)\s*(?P<value>-?\d+)\s*\)$"
)
"""The ``(place op n)`` state formula of the static fragment, e.g. ``(eat_1 <= 1)``."""

EF_FORMULA_PATTERN = re.compile(r"^EF\s+(?P<inner>.+)$")
"""The ``EF φ`` form of a reachability formula."""

AG_FORMULA_PATTERN = re.compile(r"^AG\s+(?P<inner>.+)$")
"""The ``AG ψ`` form of a safety formula."""


def _parse_comparison(*, text: str) -> tuple[str, str, int] | None:
    """Parse a ``(place op n)`` comparison, or None when the text is not one."""
    match = COMPARISON_PATTERN.fullmatch(text.strip())
    if match is None:
        return None
    return match.group("place"), match.group("op"), int(match.group("value"))


def _satisfies(*, place: str, op: str, value: int, marking: dict[str, int]) -> bool:
    """Evaluate one comparison on a marking; an unknown place never satisfies."""
    actual = marking.get(place)
    if actual is None:
        return False
    if op == ">":
        result = actual > value
    elif op == ">=":
        result = actual >= value
    elif op == "<":
        result = actual < value
    elif op == "<=":
        result = actual <= value
    elif op == "==":
        result = actual == value
    else:
        result = actual != value
    return result


def mini_capacity(*, model: CanonicalModel) -> CapacityReport:
    """Capacity probe adapter of ``StaticResolver`` (issue #14, spec §14.2).

    A non-P/T net or a graph beyond the state cap reports ``state_count=None``:
    the capacity is probed, never assumed.
    """
    try:
        graph, truncated = explore_state_graph_bounded(model=model, max_states=MAX_REACHABLE_STATES)
    except UnsupportedFormalismError:
        graph, truncated = None, True
    return CapacityReport(
        place_count=len(model.places),
        max_places_for_invariants=MAX_PLACES_FOR_INVARIANT_ENUMERATION,
        state_count=None if truncated or graph is None else len(graph.states),
        max_states=MAX_REACHABLE_STATES,
    )


def resolve_static_payload(
    *, model: CanonicalModel, question: CheckQuestion
) -> dict[str, JsonValue]:
    """Resolve a statically-triaged question as an opaque JSON payload (issue #14).

    The static fragment covers the safety patterns over a simple
    ``AG (place op n)`` formula, decided by bounded state enumeration with
    cost zero (spec §18.1). Anything else - or a net beyond capacity - raises
    ``StaticUndecidableError`` so the API degrades the question explicitly
    (spec §14.2): the failure of a heuristic is never a verdict.
    """
    if question.kind not in STATIC_KINDS:
        raise StaticUndecidableError(
            f"Pattern {question.kind!r} is not one of the static targets (spec §14.2)."
        )
    match = AG_FORMULA_PATTERN.fullmatch(question.formula.strip())
    comparison = _parse_comparison(text=match.group("inner")) if match is not None else None
    if comparison is None:
        raise StaticUndecidableError(
            f"Formula {question.formula!r} is outside the static fragment AG (place op n)."
        )
    place, op, value = comparison
    try:
        graph, truncated = explore_state_graph_bounded(model=model, max_states=MAX_REACHABLE_STATES)
    except UnsupportedFormalismError as error:
        raise StaticUndecidableError(str(error)) from error
    if truncated:
        raise StaticUndecidableError(
            f"Reachability graph beyond the cap of {MAX_REACHABLE_STATES} states."
        )
    index_of = {name: position for position, name in enumerate(graph.place_names)}
    if place not in index_of:
        raise StaticUndecidableError(f"Unknown place {place!r}: not in the model.")
    violating = next(
        (
            state
            for state in graph.states
            if not _satisfies(
                place=place,
                op=op,
                value=value,
                marking=dict(zip(graph.place_names, state, strict=True)),
            )
        ),
        None,
    )
    cost: dict[str, JsonValue] = {
        "tool_seconds": STATIC_ENGINE_COST,
        "states_explored": len(graph.states),
    }
    if violating is None:
        logger.debug(
            "static_question_resolved", kind=question.kind, formula=question.formula, verdict="true"
        )
        return {
            "verdict": "true",
            "technique": "static",
            "static_technique": "state_enumeration",
            "witness": None,
            "cost": cost,
        }
    path = _path_from_initial(graph=graph, target_predicate=lambda state: state == violating)
    witness = PathWitness(
        states=tuple(_sparse(marking=state, place_names=graph.place_names) for state in path),
        description=(
            f"AG ({place} {op} {value}) is false: this path reaches a marking violating it."
        ),
    )
    logger.debug(
        "static_question_resolved", kind=question.kind, formula=question.formula, verdict="false"
    )
    return {
        "verdict": "false",
        "technique": "static",
        "static_technique": "state_enumeration",
        "witness": witness.model_dump(mode="json"),
        "cost": cost,
    }


def non_vacuity_guard(
    *, model: CanonicalModel, family: CheckFamilyName, kind: KindPattern, formula: str
) -> NonVacuityGuard:
    """Non-vacuity guard adapter of ``StaticResolver`` (issue #14, spec §14.4).

    For ``EF φ`` the guard is ok when the initial marking does not already
    satisfy φ; for ``AG ψ`` when it does not violate ψ. Any other formula
    carries no guard of this fragment and reports ``not_applicable``
    explicitly - a mute property gets no verdict.
    """
    initial: dict[str, int] = {place.name: place.initial_marking for place in model.places}
    text = formula.strip()
    if text == "EF DEADLOCK":
        return NonVacuityGuard(
            guard=None,
            status="not_applicable",
            detail="EF DEADLOCK carries its own deadlock guard, not an EF φ guard (spec §14.4).",
        )
    ef_match = EF_FORMULA_PATTERN.fullmatch(text)
    if ef_match is not None:
        comparison = _parse_comparison(text=ef_match.group("inner"))
        if comparison is not None:
            place, op, value = comparison
            if place not in initial:
                return NonVacuityGuard(
                    guard="ef_phi",
                    status="not_applicable",
                    detail=f"Place {place!r} of the guard is not in the model.",
                )
            holds = _satisfies(place=place, op=op, value=value, marking=initial)
            return NonVacuityGuard(
                guard="ef_phi",
                status="vacuous" if holds else "ok",
                detail=(
                    f"EF ({place} {op} {value}): the initial marking already satisfies it,"
                    " the property is vacuous on this model."
                    if holds
                    else f"EF ({place} {op} {value}): the initial marking does not satisfy it yet."
                ),
            )
    ag_match = AG_FORMULA_PATTERN.fullmatch(text)
    if ag_match is not None:
        comparison = _parse_comparison(text=ag_match.group("inner"))
        if comparison is not None:
            place, op, value = comparison
            if place not in initial:
                return NonVacuityGuard(
                    guard="ag_psi",
                    status="not_applicable",
                    detail=f"Place {place!r} of the guard is not in the model.",
                )
            holds = _satisfies(place=place, op=op, value=value, marking=initial)
            return NonVacuityGuard(
                guard="ag_psi",
                status="ok" if holds else "vacuous",
                detail=(
                    f"AG ({place} {op} {value}): the initial marking satisfies it, the guard holds."
                    if holds
                    else f"AG ({place} {op} {value}): the initial marking already violates it,"
                    " the property is vacuous on this model."
                ),
            )
    return NonVacuityGuard(
        guard=None,
        status="not_applicable",
        detail=f"Formula {formula!r} is outside the EF φ / AG ψ guards (spec §14.4).",
    )
