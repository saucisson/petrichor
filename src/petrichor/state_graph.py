"""Reachability-graph serving contract (spec §9, §14; issue #13).

The verdict lock (spec §19.4) forbids any module outside ``petrichor.engines``
from importing the engines package, so the API receives the state graph of a
session's model through this injected explorer protocol. The concrete adapter
lives in ``engines/mini_engine.py``; the payload is plain JSON data, never a
verdict.
"""

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel, JsonValue


class StateGraphUnsupportedError(Exception):
    """The model's formalism has no reachability graph (non-P/T nets)."""


class StateGraphExplorer(Protocol):
    """Explore a model's reachability graph, bounded by the engine capacity.

    Returns the graph as a JSON payload plus whether it was truncated at the
    cap — the explicit degradation for unbounded nets (spec §14, P13).
    """

    def __call__(self, *, model: CanonicalModel) -> tuple[dict[str, JsonValue], bool]:
        """Explore the model's graph; return the JSON payload and the truncated flag."""
        ...
