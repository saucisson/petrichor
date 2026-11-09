"""Verdict types of the embedded engines (spec §18.1, §19.4).

This module is the **only** producer of verdict values in the product:
the guard test ``tests/test_engines_guard.py`` verifies mechanically that
no module outside ``petrichor.engines`` imports it (spec §19.4, verrous de
verdict). The static engine never emits an implicit verdict: a property it
cannot decide within its capacity is an explicit error, not a silent
"unknown" value (spec §14.2).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

type VerdictValue = Literal["true", "false"]
"""The closed set of verdict values a qualified engine may produce."""

type StaticTechnique = Literal[
    "pt_invariants",
    "siphon_trap",
    "structural_bounds",
    "tarjan_scc",
    "bfs_ag_ef",
    "deadlock_bfs",
]
"""The closed set of static techniques the embedded mini-engine reports."""

type MarkedPlaces = tuple[tuple[str, int], ...]
"""A sparse marking: sorted (place name, token count) pairs, tokens > 0 only."""

STATIC_ENGINE_COST = 0
"""Cost of the embedded static engine: zero tool time (spec §18.1, coût nul)."""


class PathWitness(BaseModel):
    """A witness made of a path of markings from the initial marking (spec §16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["path"] = "path"
    states: tuple[MarkedPlaces, ...]
    description: str


class CycleWitness(BaseModel):
    """A witness made of a cycle of markings, e.g. cyclic starvation (spec §16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["cycle"] = "cycle"
    states: tuple[MarkedPlaces, ...]
    description: str


type Witness = PathWitness | CycleWitness
"""Discriminated union of the witness shapes (spec §16)."""


class Verdict(BaseModel):
    """A normalized verdict: the only value-carrying type of spec §19.4."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: VerdictValue
    technique: StaticTechnique
    witness: Witness | None = None
    cost: int = Field(ge=0)

    @field_validator("cost")
    @classmethod
    def check_static_cost_is_zero(cls, value: int) -> int:
        """The embedded static engine consumes no tool time (spec §18.1)."""
        if value != STATIC_ENGINE_COST:
            raise ValueError(f"The embedded static engine always costs {STATIC_ENGINE_COST}.")
        return value
