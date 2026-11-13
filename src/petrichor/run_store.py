"""In-memory run store: idempotence and full archiving (spec §5, §12.5; issues #6, #7).

Runs are stored by id and indexed by their idempotence key: creating a run
whose key is already known returns the existing run instead of a duplicate,
which is what makes ``POST /v1/checks/{family}`` idempotent. Every run is
archived with its command, tool version, timeout, input hash and measured
cost (spec §12.5) - and there is deliberately no delete path in this class:
the budget limits what we produce, never what we keep. The store is
thread-safe: runs are executed on background threads and polled over HTTP.
"""

import threading
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import (
    CheckFamilyName,
    KindPattern,
    RunErrorKind,
    RunId,
    RunKey,
    RunStatus,
    ToolVersion,
    new_run_id,
)
from petrichor.petri_canonical import JsonValue, ModelId
from petrichor.triage import NonVacuityGuard, QuestionTriage

logger = structlog.get_logger(__name__)


class RunError(BaseModel):
    """The explicit failure of a run: kind plus detail, never a default verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunErrorKind
    detail: str = Field(min_length=1)


class StoredRun(BaseModel):
    """One asynchronous run as stored and served by the API (spec §5, §6, §12.5).

    Frozen on purpose: state transitions replace the whole entry under the
    store lock (``model_copy(update=...)``), so a reader can serialize the
    object returned by ``get`` outside the lock and never observe a torn
    state such as ``status="done"`` with ``output=None``.

    The archive fields of spec §12.5 are always present: ``timeout_seconds``,
    ``tool_version`` and ``input_hash`` are known when the run is created;
    ``command`` and ``measured_seconds`` (the wall-clock tool time, measured
    around the runner call - never estimated) are filled when the run
    reaches a terminal state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    status: RunStatus
    family: CheckFamilyName
    kind: KindPattern
    formula: str
    model_id: ModelId
    input_hash: RunKey
    timeout_seconds: int = Field(gt=0)
    tool_version: ToolVersion
    command: list[str] | None = None
    measured_seconds: float | None = Field(default=None, ge=0)
    output: dict[str, JsonValue] | None = None
    error: RunError | None = None
    engine_qualified: bool | None = None
    """Whether the engine that produced the output is qualified for the family
    (spec §17.5, issue #22); None while the run is not done or no qualification
    lookup is wired."""
    triage: QuestionTriage
    """The static/MC triage of the question, with its justification (spec §14.2,
    issue #14); a degraded question displays it here, never silently."""
    non_vacuity: NonVacuityGuard | None = None
    """The non-vacuity guard chip of the question (spec §14.4, issue #14)."""


class RunAdmission(BaseModel):
    """The record of one run admission request (spec §5, §12.4-5).

    Groups the related fields of a run creation so the store's ``admit``
    takes one record instead of a growing parameter list (ADR-0003).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: RunKey
    family: CheckFamilyName
    kind: KindPattern
    formula: str
    model_id: ModelId
    timeout_seconds: int = Field(gt=0)
    tool_version: ToolVersion
    session_budget_seconds: float = Field(gt=0)
    """The session budget cap in tool seconds (spec §12.4); no carry-over."""
    triage: QuestionTriage
    """The triage computed at admission (spec §14.2, issue #14)."""
    non_vacuity: NonVacuityGuard | None = None
    """The non-vacuity guard computed at admission (spec §14.4, issue #14)."""


class RunAdmitted(BaseModel):
    """A run accepted by the session budget: the run plus its replay flag."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run: StoredRun
    created: bool


class BudgetRefused(BaseModel):
    """The explicit refusal of a run that does not fit the session budget."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    remaining_budget_seconds: float
    session_budget_seconds: float
    consumed_seconds: float
    reserved_seconds: float
    timeout_seconds: int


type AdmissionResult = RunAdmitted | BudgetRefused
"""Discriminated union of the admission outcomes (narrow with isinstance)."""


class RunStore:
    """Stores runs by id, indexed by idempotence key, guarded by a lock.

    Append-only by construction: the class exposes no delete, remove, purge
    or clear path (spec §12.5, issue #7 pins that with a guard test).

    The session budget is enforced atomically at admission: an in-flight run
    reserves its worst-case tool time (its timeout plus the watchdog grace)
    against the cap, and the reservation is replaced by the measured cost
    when the run reaches a terminal state - so the cumulative measured cost
    never exceeds the cap, even under concurrent requests (spec §20.2).
    """

    def __init__(self) -> None:
        """Start with an empty store."""
        self._lock = threading.Lock()
        self._by_id: dict[RunId, StoredRun] = {}
        self._by_key: dict[RunKey, RunId] = {}
        self._reservations: dict[RunId, float] = {}

    def admit(self, *, admission: RunAdmission, reservation_seconds: float) -> AdmissionResult:
        """Check the session budget and create the run under one lock.

        A known idempotence key replays the existing run without touching
        the budget. Otherwise the run is admitted only if its reservation
        fits in the remaining budget (cap minus measured cost minus in-flight
        reservations); when it does not fit, the refusal is explicit and
        carries the remaining budget.
        """
        with self._lock:
            existing_id = self._by_key.get(admission.key)
            if existing_id is not None:
                logger.debug("run_store_key_hit", run_key=admission.key, run_id=existing_id)
                return RunAdmitted(run=self._by_id[existing_id], created=False)
            consumed_seconds = self._consumed_seconds_locked()
            reserved_seconds = sum(self._reservations.values())
            remaining_budget_seconds = (
                admission.session_budget_seconds - consumed_seconds - reserved_seconds
            )
            if remaining_budget_seconds < reservation_seconds:
                logger.warning(
                    "run_store_budget_refused",
                    remaining_budget_seconds=remaining_budget_seconds,
                    session_budget_seconds=admission.session_budget_seconds,
                    consumed_seconds=consumed_seconds,
                    reserved_seconds=reserved_seconds,
                    timeout_seconds=admission.timeout_seconds,
                )
                return BudgetRefused(
                    remaining_budget_seconds=remaining_budget_seconds,
                    session_budget_seconds=admission.session_budget_seconds,
                    consumed_seconds=consumed_seconds,
                    reserved_seconds=reserved_seconds,
                    timeout_seconds=admission.timeout_seconds,
                )
            run_id = new_run_id(random_hex=uuid4().hex)
            while run_id in self._by_id:
                run_id = new_run_id(random_hex=uuid4().hex)
            run = StoredRun(
                run_id=run_id,
                status="running",
                family=admission.family,
                kind=admission.kind,
                formula=admission.formula,
                model_id=admission.model_id,
                input_hash=admission.key,
                timeout_seconds=admission.timeout_seconds,
                tool_version=admission.tool_version,
                triage=admission.triage,
                non_vacuity=admission.non_vacuity,
            )
            self._by_id[run.run_id] = run
            self._by_key[admission.key] = run.run_id
            self._reservations[run.run_id] = reservation_seconds
            logger.debug("run_store_created", run_id=run.run_id, run_key=admission.key)
            return RunAdmitted(run=run, created=True)

    def get(self, *, run_id: RunId) -> StoredRun | None:
        """Return the stored run for an id, or None."""
        with self._lock:
            return self._by_id.get(run_id)

    def consumed_seconds(self) -> float:
        """The cumulative measured tool time of terminal runs (spec §12.4).

        Only terminal runs contribute: a running run has no measured cost
        yet - the cost is measured, never estimated. In-flight runs are
        accounted separately as reservations at admission time.
        """
        with self._lock:
            return self._consumed_seconds_locked()

    def _consumed_seconds_locked(self) -> float:
        """The measured cost of terminal runs; the caller holds the lock."""
        return sum(
            run.measured_seconds for run in self._by_id.values() if run.measured_seconds is not None
        )

    def _release_reservation_locked(self, *, run_id: RunId) -> None:
        """Drop the admission reservation of a run reaching a terminal state."""
        self._reservations.pop(run_id, None)

    def mark_done(
        self,
        *,
        run_id: RunId,
        output: dict[str, JsonValue],
        command: list[str],
        measured_seconds: float,
        engine_qualified: bool | None = None,
    ) -> None:
        """Move a running run to the ``done`` state with its normalized output."""
        with self._lock:
            run = self._by_id[run_id]
            if run.status != "running":
                logger.warning("run_store_terminal_state_kept", run_id=run_id, status=run.status)
                return
            self._release_reservation_locked(run_id=run_id)
            self._by_id[run_id] = run.model_copy(
                update={
                    "status": "done",
                    "output": output,
                    "command": command,
                    "measured_seconds": measured_seconds,
                    "engine_qualified": engine_qualified,
                }
            )
        logger.debug(
            "run_store_done", run_id=run_id, measured_seconds=measured_seconds, command=command
        )

    def mark_error(
        self,
        *,
        run_id: RunId,
        kind: RunErrorKind,
        detail: str,
        command: list[str],
        measured_seconds: float,
    ) -> None:
        """Move a running run to the explicit ``error`` state - never a default verdict."""
        with self._lock:
            run = self._by_id[run_id]
            if run.status != "running":
                logger.warning("run_store_terminal_state_kept", run_id=run_id, status=run.status)
                return
            self._release_reservation_locked(run_id=run_id)
            self._by_id[run_id] = run.model_copy(
                update={
                    "status": "error",
                    "output": None,
                    "error": RunError(kind=kind, detail=detail),
                    "command": command,
                    "measured_seconds": measured_seconds,
                }
            )
        logger.debug(
            "run_store_error",
            run_id=run_id,
            error_kind=kind,
            measured_seconds=measured_seconds,
            command=command,
        )

    def __len__(self) -> int:
        """The number of stored runs."""
        with self._lock:
            return len(self._by_id)
