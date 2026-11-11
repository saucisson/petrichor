"""Check domain: families, patterns, questions and the runner port (spec §5, §12.1, §12.4-5).

One endpoint per check family, never polymorphic: the ``kind`` pattern of a
request must belong to the family of the endpoint, otherwise the request is
rejected with ``422`` and never reinterpreted (garde anti-polymorphisme,
spec §12.1). The patterns per family are the closed sets of the HAZOP
catalogue (spec §14.1) mapped to the MCC examinations of spec §17.3.

The verdict lock (spec §19.4) forbids any module outside ``petrichor.engines``
from importing that package, so this module defines its own transport types:
a runner returns the normalized engine output as opaque JSON, which the API
stores and serves verbatim without ever interpreting a verdict.
"""

import hashlib
import json
from typing import Literal, NewType, Protocol

from pydantic import BaseModel, ConfigDict, Field

from petrichor.petri_canonical import HASH_PREFIX, JsonValue, ModelId

type CheckFamilyName = Literal["reachability", "safety", "liveness", "recovery", "deadlock"]
"""The five check families of spec §12.1; one endpoint per family."""

type RunStatus = Literal["running", "done", "error"]
"""The lifecycle states of an asynchronous run (spec §5: polling only)."""

type RunErrorKind = Literal["engine_error", "timeout"]
"""The explicit failure kinds of a run - never a default verdict (issue #6)."""

type QualificationState = Literal["qualified", "unqualified"]
"""The qualification state of an adapter for one family (spec §17.5, issue #22)."""

RunId = NewType("RunId", str)
"""A run identifier, e.g. ``run_7c2a9f``."""

RunKey = NewType("RunKey", str)
"""The idempotence key of a run: hash of (model, question), formatted ``sha256:<hex>``."""

KindPattern = NewType("KindPattern", str)
"""A check pattern kind, e.g. ``safety.invariant``; validated against FAMILY_PATTERNS."""

ToolVersion = NewType("ToolVersion", str)
"""The pinned version of the tool that executed a run, e.g. ``0.1.0`` (spec §12.5)."""

RUN_ID_PREFIX = "run_"
RUN_ID_HEX_DIGITS = 6

FAMILY_PATTERNS: dict[CheckFamilyName, frozenset[str]] = {
    "reachability": frozenset({"reachability.ef", "reachability.fireability"}),
    "safety": frozenset({"safety.invariant", "safety.conservation", "safety.state_invariant"}),
    "liveness": frozenset(
        {"liveness.unconditional", "liveness.conditional", "liveness.conditional_disj"}
    ),
    "recovery": frozenset({"recovery.unconditional"}),
    "deadlock": frozenset({"deadlock"}),
}
"""The closed set of accepted ``kind`` patterns per family (spec §14.1, §17.3)."""


class CheckRequest(BaseModel):
    """The body of ``POST /v1/checks/{family}``: model, pattern, formula, parameters."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_id: ModelId
    kind: KindPattern = Field(min_length=1)
    formula: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class CheckQuestion(BaseModel):
    """The formal question handed to a runner: family is fixed by the endpoint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: CheckFamilyName
    kind: KindPattern = Field(min_length=1)
    formula: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class CheckCompleted(BaseModel):
    """A runner outcome carrying the normalized engine output as opaque JSON."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["completed"] = "completed"
    output: dict[str, JsonValue]
    command: list[str]
    """The tool command that produced this output, archived with the run (spec §12.5)."""


class CheckFailed(BaseModel):
    """An explicit runner failure: engine error or timeout, with a detail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: Literal["failed"] = "failed"
    kind: RunErrorKind
    detail: str = Field(min_length=1)
    command: list[str]
    """The tool command that was attempted, archived with the run (spec §12.5)."""


type CheckOutcome = CheckCompleted | CheckFailed
"""Discriminated union of the runner outcomes (discriminator: ``outcome``)."""


class CheckRunner(Protocol):
    """The runner port: a formal question in, an explicit outcome out.

    Implementations never return a default verdict on failure - a failure is
    always a `CheckFailed` with an explicit kind (issue #6, spec §14.2).
    The ``tool_version`` attribute is archived with every run the runner
    executes (spec §12.5, issue #7).
    """

    tool_version: ToolVersion
    """The pinned version of the tool behind this runner."""

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Execute one check question within the timeout."""
        ...


class QualificationLookup(Protocol):
    """The qualification port: the orchestrator reads adapter qualification (issue #22).

    The verdict lock (spec §19.4) keeps the qualification data inside
    ``petrichor.engines``; this port is the opaque window the API layer
    reads through, exactly like `CheckRunner` carries opaque outputs.
    An adapter absent from the snapshot is unqualified for every family.
    """

    def is_qualified(self, *, family: CheckFamilyName) -> bool:
        """Whether the adapter behind this lookup is qualified for the family."""
        ...

    def snapshot(self) -> dict[str, dict[str, QualificationState]]:
        """The full qualification status per adapter and family."""
        ...


def canonical_question_json(*, question: CheckQuestion) -> str:
    """Deterministic JSON serialization of a question (sorted keys, compact separators)."""
    payload = question.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def run_key(*, model_id: ModelId, question: CheckQuestion) -> RunKey:
    """The idempotence key of a run: hash of (model, question) (spec §5, issue #6)."""
    material = f"{model_id}\n{canonical_question_json(question=question)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return RunKey(f"{HASH_PREFIX}{digest}")


def new_run_id(*, random_hex: str) -> RunId:
    """Build a run identifier from a hex string, e.g. ``run_7c2a9f``."""
    return RunId(f"{RUN_ID_PREFIX}{random_hex[:RUN_ID_HEX_DIGITS]}")
