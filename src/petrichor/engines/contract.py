"""Normalized driver contract of the engines package (spec §4.6, §5, §12.5, §16, §19.4).

A driver takes a formal question in and returns the normalized JSON output
``{verdict, witness, cost, technique}``; every run is archived as a
`RunRecord` (spec §12.5). Only this package may produce verdict values
(verdict lock, spec §19.4) - a guard test in ``tests/`` enforces it.
"""

import hashlib
import json
from typing import Literal, NewType
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from petrichor.petri_canonical import HASH_PREFIX, JsonValue

CheckFamily = Literal["reachability", "safety", "liveness", "recovery", "deadlock"]
"""The five check families of spec §12.1; one endpoint per family, never polymorphic."""

Verdict = Literal["satisfied", "violated", "unknown"]
"""The closed set of normalized verdicts a driver may return."""

RunId = NewType("RunId", str)
"""A run identifier, e.g. ``run_7c2a9f``."""

EngineName = NewType("EngineName", str)
"""The name of an engine service, e.g. ``dummy``."""

EngineVersion = NewType("EngineVersion", str)
"""The pinned version of an engine image, e.g. ``0.1.0``."""

InputHash = NewType("InputHash", str)
"""The sha256 of the canonical question JSON, formatted ``sha256:<64 hex>``."""

DUMMY_ENGINE_NAME = EngineName("dummy")
DUMMY_ENGINE_VERSION = EngineVersion("0.1.0")
DUMMY_ENGINE_IMAGE = f"petrichor-engine-{DUMMY_ENGINE_NAME}:{DUMMY_ENGINE_VERSION}"
VERIFYPN_ENGINE_NAME = EngineName("verifypn")
VERIFYPN_ENGINE_VERSION = EngineVersion("4.4.2")
VERIFYPN_ENGINE_IMAGE = f"petrichor-engine-{VERIFYPN_ENGINE_NAME}:{VERIFYPN_ENGINE_VERSION}"
DEFAULT_TIMEOUT_SECONDS = 30
RUN_ID_PREFIX = "run_"
RUN_ID_HEX_DIGITS = 6


class EngineError(Exception):
    """Base class of every domain error raised by the engines package."""


class InvalidQuestionError(EngineError):
    """A question does not conform to the driver contract."""


class DriverError(EngineError):
    """The driver could not obtain a normalized output from the engine."""


class EngineTimeoutError(DriverError):
    """The engine did not answer within the configured timeout."""


class InvalidOutputError(DriverError):
    """The engine answered with a payload that is not a normalized output."""


class EngineQuestion(BaseModel):
    """The formal question sent to a driver (spec §4.6, §12.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: CheckFamily
    formula: str = Field(min_length=1)
    pattern: str | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class WitnessStep(BaseModel):
    """One step of a witness trace (spec §16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    marking: list[str]
    fired: str | None


class Witness(BaseModel):
    """A witness in the §16 format: structure only, narration stays LLM-side."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["counterexample", "trace"]
    cyclic: bool
    back_edge: int | None
    steps: list[WitnessStep]


class Cost(BaseModel):
    """The measured cost of a run: tool time consumed, states explored (spec §12.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool_seconds: float = Field(ge=0)
    states_explored: int = Field(ge=0)


class NormalizedOutput(BaseModel):
    """The normalized driver output: ``{verdict, witness, cost, technique}``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict
    witness: Witness | None
    cost: Cost
    technique: str = Field(min_length=1)


class RunRecord(BaseModel):
    """The archived run (spec §12.5): command, tool version, timeout, input hash, cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    engine: EngineName
    engine_version: EngineVersion
    command: list[str]
    timeout_seconds: int = Field(gt=0)
    input_hash: InputHash
    output: NormalizedOutput
    measured_seconds: float = Field(ge=0)
    archived_at: str


def canonical_question_json(*, question: EngineQuestion) -> str:
    """Deterministic JSON serialization of a question (sorted keys, compact separators)."""
    payload = question.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def question_input_hash(*, question: EngineQuestion) -> InputHash:
    """The sha256 of the canonical question JSON - the input side of the run cache key (§13)."""
    digest = hashlib.sha256(canonical_question_json(question=question).encode("utf-8")).hexdigest()
    return InputHash(f"{HASH_PREFIX}{digest}")


def new_run_id() -> RunId:
    """A fresh run identifier, e.g. ``run_7c2a9f``."""
    return RunId(f"{RUN_ID_PREFIX}{uuid4().hex[:RUN_ID_HEX_DIGITS]}")
