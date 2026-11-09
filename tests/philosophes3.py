"""The Philosophes-3 P/T net of the maquette (docs/ui-mockup/philosophes-3.tsx).

Twelve places (``think_i``, ``hasL_i``, ``eat_i``, ``fork_i``), nine
transitions (``takeL_i``, ``takeR_i``, ``rel_i``) and thirty arcs, exactly
as drawn in the mockup. Philosopher ``i`` takes its left fork ``fork_i``,
then its right fork ``fork_{i+1}`` (mod 3), eats, and releases both.
"""

from petrichor.petri_canonical import CanonicalModel, build_canonical_model

PHILOSOPHER_COUNT = 3


def _right_fork(*, index: int) -> int:
    """The right fork of philosopher ``index``: the next one around the table."""
    return index % PHILOSOPHER_COUNT + 1


def philosophes3_payload() -> dict[str, object]:
    """The Philosophes-3 net as a raw `petri+json` document."""
    places: list[dict[str, object]] = []
    transitions: list[dict[str, object]] = []
    arcs: list[dict[str, object]] = []

    def add_place(*, name: str, initial_marking: int) -> None:
        places.append(
            {
                "id": f"pl_{name}",
                "name": name,
                "initial_marking": initial_marking,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )

    def add_transition(*, name: str) -> None:
        transitions.append(
            {
                "id": f"tr_{name}",
                "name": name,
                "layout": {"x": 0, "y": 0},
                "annotations": {},
            }
        )

    def add_arc(*, source: str, target: str) -> None:
        arcs.append(
            {
                "id": f"ar_{len(arcs) + 1:03d}",
                "source": source,
                "target": target,
                "weight": 1,
                "annotations": {},
            }
        )

    for index in range(1, PHILOSOPHER_COUNT + 1):
        add_place(name=f"think_{index}", initial_marking=1)
        add_place(name=f"hasL_{index}", initial_marking=0)
        add_place(name=f"eat_{index}", initial_marking=0)
    for index in range(1, PHILOSOPHER_COUNT + 1):
        add_place(name=f"fork_{index}", initial_marking=1)
    for index in range(1, PHILOSOPHER_COUNT + 1):
        right = _right_fork(index=index)
        add_transition(name=f"takeL_{index}")
        add_transition(name=f"takeR_{index}")
        add_transition(name=f"rel_{index}")
        add_arc(source=f"pl_think_{index}", target=f"tr_takeL_{index}")
        add_arc(source=f"pl_fork_{index}", target=f"tr_takeL_{index}")
        add_arc(source=f"tr_takeL_{index}", target=f"pl_hasL_{index}")
        add_arc(source=f"pl_hasL_{index}", target=f"tr_takeR_{index}")
        add_arc(source=f"pl_fork_{right}", target=f"tr_takeR_{index}")
        add_arc(source=f"tr_takeR_{index}", target=f"pl_eat_{index}")
        add_arc(source=f"pl_eat_{index}", target=f"tr_rel_{index}")
        add_arc(source=f"tr_rel_{index}", target=f"pl_think_{index}")
        add_arc(source=f"tr_rel_{index}", target=f"pl_fork_{index}")
        add_arc(source=f"tr_rel_{index}", target=f"pl_fork_{right}")
    return {
        "id": "mdl_philosophes3",
        "formalism": "pt",
        "places": places,
        "transitions": transitions,
        "arcs": arcs,
        "metadata": {"source": "maquette", "model": "Philosophes-3"},
    }


def build_philosophes3() -> CanonicalModel:
    """The Philosophes-3 net as a validated canonical model, hash stamped."""
    return build_canonical_model(payload=philosophes3_payload())
