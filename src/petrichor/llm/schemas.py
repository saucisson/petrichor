"""Typed LLM outputs and the golden-file corpus schema (spec §15).

Four usages, four typed outputs, each validated by its pydantic schema:
``semantic-profile``, ``reading``, ``clarification`` and ``cluster``.
The deterministic mock serves the golden files of the Philosophes-3
corpus (``golden/{model_hash}/{usage}.json``) verbatim; it never invents
an answer — a missing or invalid golden file is an explicit degradation
after a bounded number of re-requests (spec §15).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type LlmUsage = Literal["semantic-profile", "reading", "clarification", "cluster"]
"""The closed set of LLM usages of spec §15."""

type ClusterCause = Literal["model", "specification"]
"""The closed set of cluster causes (spec §15)."""


class ProfileEntry(BaseModel):
    """One semantic-profile entry: a role hypothesis, its evidence and its smells."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    elements: str
    role: str
    conf: float = Field(ge=0.0, le=1.0)
    evidence: str
    options: tuple[str, ...] = ()
    wrong: bool = False
    """A smell: the maquette's corpus deliberately carries one false role."""


class SemanticProfile(BaseModel):
    """The `semantic-profile` output: roles + confidences + evidence + smells."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entries: tuple[ProfileEntry, ...]


class Reading(BaseModel):
    """The `reading` output: a formula read in natural language, and what it does not say."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reading: str
    not_says: str
    pattern: str
    cross: str


class FormulaReadings(BaseModel):
    """The golden `reading` document: each catalogue formula mapped to its reading."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    readings: dict[str, Reading]


class Clarification(BaseModel):
    """The `clarification` output: a reformulation and its option chips."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reform: str
    options: tuple[str, ...]


class Cluster(BaseModel):
    """The `cluster` output: a group of violations with its cause and remediation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cause: ClusterCause
    fix: str
    members: tuple[str, ...]


class ClusterList(BaseModel):
    """The golden `cluster` document: the scripted clusters of the corpus."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    clusters: tuple[Cluster, ...]
