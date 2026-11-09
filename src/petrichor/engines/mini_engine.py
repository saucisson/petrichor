"""The embedded static mini-engine (spec §18.1; issue #5 / P05).

The mini-engine is the sole producer of *static* verdicts (spec §18.1,
§19.4): P/T-invariants (kernel of the incidence matrix), siphons and
traps, structural bounds, Tarjan SCCs over the reachability graph, BFS
``AG EF`` and deadlock detection. It never calls an external tool and
never emits an implicit verdict: a net beyond its capacity is rejected
explicitly with :class:`NetBeyondCapacityError` (spec §14.2, §18.5).
"""

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

if TYPE_CHECKING:
    from collections.abc import Callable

    from petrichor.petri_canonical import CanonicalModel

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
class _TransitionIO:
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


def _transition_io(*, model: CanonicalModel) -> dict[str, _TransitionIO]:
    """Per transition, the weighted pre- and post-sets over place names."""
    place_ids: set[str] = {place.id for place in model.places}
    names_by_id: dict[str, str] = {
        element.id: element.name for element in (*model.places, *model.transitions)
    }
    io: dict[str, _TransitionIO] = {
        transition.name: _TransitionIO() for transition in model.transitions
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
            io[names_by_id[arc.target]].pre[names_by_id[arc.source]] = (
                io[names_by_id[arc.target]].pre.get(names_by_id[arc.source], 0) + arc.weight
            )
        else:
            io[names_by_id[arc.source]].post[names_by_id[arc.target]] = (
                io[names_by_id[arc.source]].post.get(names_by_id[arc.target], 0) + arc.weight
            )
    return io


def _fire(*, marking: Marking, index_of: dict[str, int], io: _TransitionIO) -> Marking | None:
    """The successor marking of firing the transition, or None if not enabled."""
    if any(marking[index_of[place]] < weight for place, weight in io.pre.items()):
        return None
    result = list(marking)
    for place, weight in io.pre.items():
        result[index_of[place]] -= weight
    for place, weight in io.post.items():
        result[index_of[place]] += weight
    return tuple(result)


def _register_state(
    *,
    seen: dict[Marking, int],
    queue: list[Marking],
    successors: dict[Marking, list[tuple[str, Marking]]],
    marking: Marking,
) -> None:
    """Add a freshly discovered marking, enforcing the capacity cap."""
    if len(seen) >= MAX_REACHABLE_STATES:
        logger.warning(
            "engine_capacity_exceeded",
            state_count=len(seen),
            max_states=MAX_REACHABLE_STATES,
        )
        raise NetBeyondCapacityError(
            f"Reachability graph exceeds the mini-engine capacity of {MAX_REACHABLE_STATES} states."
        )
    seen[marking] = len(seen)
    successors[marking] = []
    queue.append(marking)


def explore_state_graph(*, model: CanonicalModel) -> StateGraph:
    """BFS the reachability graph; raises NetBeyondCapacityError past the cap."""
    _require_pt(model=model)
    place_names = _place_names(model=model)
    index_of = {name: position for position, name in enumerate(place_names)}
    initial = tuple(place.initial_marking for place in model.places)
    transitions = tuple(_transition_io(model=model).items())
    seen: dict[Marking, int] = {initial: 0}
    queue = [initial]
    successors: dict[Marking, list[tuple[str, Marking]]] = {initial: []}
    deadlocks: list[Marking] = []
    arc_count = 0
    while queue:
        marking = queue.pop(0)
        fired_any = False
        for name, io in transitions:
            nxt = _fire(marking=marking, index_of=index_of, io=io)
            if nxt is None:
                continue
            fired_any = True
            arc_count += 1
            successors[marking].append((name, nxt))
            if nxt not in seen:
                _register_state(seen=seen, queue=queue, successors=successors, marking=nxt)
        if not fired_any:
            deadlocks.append(marking)
    logger.debug(
        "state_graph_explored",
        state_count=len(seen),
        arc_count=arc_count,
        deadlock_count=len(deadlocks),
    )
    return StateGraph(
        place_names=place_names,
        initial=initial,
        states=tuple(seen),
        successors={marking: tuple(edges) for marking, edges in successors.items()},
        deadlocks=tuple(deadlocks),
        arc_count=arc_count,
    )


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
    ios = _transition_io(model=model)
    matrix = [[Fraction(0)] * len(place_names) for _ in ios]
    for row, io in enumerate(ios.values()):
        for place, weight in io.pre.items():
            matrix[row][index_of[place]] -= weight
        for place, weight in io.post.items():
            matrix[row][index_of[place]] += weight
    return matrix


def place_invariants(*, model: CanonicalModel) -> tuple[tuple[Fraction, ...], ...]:
    """Kernel basis of C^T: the P/T-invariant space (spec §18.1, noyau d'incidence)."""
    _require_pt(model=model)
    basis = _kernel_basis(matrix=_incidence_transposed(model=model))
    logger.debug("place_invariants_computed", invariant_count=len(basis))
    return tuple(tuple(vector) for vector in basis)


def _is_invariant_support(*, support: frozenset[str], ios: dict[str, _TransitionIO]) -> bool:
    """Whether the 0/1 indicator of the support is a P-invariant."""
    for io in ios.values():
        consumed = sum(weight for place, weight in io.pre.items() if place in support)
        produced = sum(weight for place, weight in io.post.items() if place in support)
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
    ios = _transition_io(model=model)
    supports: list[frozenset[str]] = []
    for mask in range(1, 1 << len(place_names)):
        support = frozenset(
            place_names[position] for position in range(len(place_names)) if mask >> position & 1
        )
        if _is_invariant_support(support=support, ios=ios):
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


def _siphon_keeps(*, place: str, current: set[str], ios: dict[str, _TransitionIO]) -> bool:
    """A place stays in a siphon iff every transition producing it consumes from the siphon."""
    return all(
        any(source in current for source in io.pre) for io in ios.values() if place in io.post
    )


def _trap_keeps(*, place: str, current: set[str], ios: dict[str, _TransitionIO]) -> bool:
    """A place stays in a trap iff every transition consuming it produces into the trap."""
    return all(
        any(target in current for target in io.post) for io in ios.values() if place in io.pre
    )


class _KeepsPredicate(Protocol):
    """The place-removal predicate of a siphon/trap fixed-point iteration."""

    def __call__(self, *, place: str, current: set[str], ios: dict[str, _TransitionIO]) -> bool: ...


def _greatest_fixed_point(
    *,
    model: CanonicalModel,
    keeps: _KeepsPredicate,
) -> frozenset[str]:
    """Greatest fixed point of a monotone place-removal operator."""
    ios = _transition_io(model=model)
    current = set(_place_names(model=model))
    changed = True
    while changed:
        changed = False
        for place in sorted(current):
            if not keeps(place=place, current=current, ios=ios):
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
    for _, nxt in graph.successors[node]:
        if nxt not in state.index:
            _strong_connect(graph=graph, node=nxt, state=state)
            state.low[node] = min(state.low[node], state.low[nxt])
        elif nxt in state.on_stack:
            state.low[node] = min(state.low[node], state.index[nxt])
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
    ios: dict[str, _TransitionIO],
) -> tuple[int, tuple[tuple[str, int], ...]]:
    """Structural signature of a place: initial marking + sorted incident arc kinds."""
    incident = sorted(
        (direction, weight)
        for io in ios.values()
        for direction, weight in (
            [("out", w) for w in io.pre.values() if place in io.pre]
            + [("in", w) for w in io.post.values() if place in io.post]
        )
    )
    return (initial[place], tuple(incident))


def _io_key(
    *,
    io: _TransitionIO,
    mapping: dict[str, str],
) -> tuple[frozenset[tuple[str, int]], frozenset[tuple[str, int]]]:
    """The pre/post key of a transition, with place names mapped through the automorphism."""
    pre = frozenset((mapping[place], weight) for place, weight in io.pre.items())
    post = frozenset((mapping[place], weight) for place, weight in io.post.items())
    return (pre, post)


def _complete_automorphism(
    *,
    model: CanonicalModel,
    ios: dict[str, _TransitionIO],
    mapping: dict[str, str],
) -> dict[str, str] | None:
    """Extend a place map to a full automorphism, or None if it does not extend."""
    own_keys = {
        (frozenset(io.pre.items()), frozenset(io.post.items())): name for name, io in ios.items()
    }
    transition_map: dict[str, str] = {}
    for name, io in ios.items():
        image = _io_key(io=io, mapping=mapping)
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
    ios: dict[str, _TransitionIO]
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
            model=search.model, ios=search.ios, mapping=search.mapping
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
    ios = _transition_io(model=model)
    initial: dict[str, int] = {place.name: place.initial_marking for place in model.places}
    signatures = {
        name: _place_signature(initial=initial, place=name, ios=ios) for name in place_names
    }
    order = tuple(sorted(place_names, key=lambda name: (signatures[name], name), reverse=True))
    candidates = {
        name: tuple(q for q in place_names if signatures[q] == signatures[name])
        for name in place_names
    }
    search = _AutomorphismSearch(order=order, candidates=candidates, model=model, ios=ios)
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
        for _, nxt in graph.successors[marking]:
            if nxt in visited:
                continue
            visited.add(nxt)
            parents[nxt] = marking
            if target_predicate(nxt):
                path = [nxt]
                while path[-1] != graph.initial:
                    path.append(parents[path[-1]])
                return tuple(reversed(path))
            queue.append(nxt)
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
        nxt = next(target for _, target in successors[current] if target in component)
        if nxt in seen:
            return (*path[seen[nxt] :], nxt)
        seen[nxt] = len(path)
        path.append(nxt)
        current = nxt


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
