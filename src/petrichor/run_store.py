"""In-memory run store with (model, question) idempotence (spec §5, issue #6).

Runs are stored by id and indexed by their idempotence key: creating a run
whose key is already known returns the existing run instead of a duplicate,
which is what makes ``POST /v1/checks/{family}`` idempotent. The store is
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
    new_run_id,
)
from petrichor.petri_canonical import JsonValue, ModelId

logger = structlog.get_logger(__name__)


class RunError(BaseModel):
    """The explicit failure of a run: kind plus detail, never a default verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: RunErrorKind
    detail: str = Field(min_length=1)


class StoredRun(BaseModel):
    """One asynchronous run as stored and served by the API (spec §5, §6).

    Frozen on purpose: state transitions replace the whole entry under the
    store lock (``model_copy(update=...)``), so a reader can serialize the
    object returned by ``get`` outside the lock and never observe a torn
    state such as ``status="done"`` with ``output=None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    status: RunStatus
    family: CheckFamilyName
    kind: KindPattern
    formula: str
    model_id: ModelId
    input_hash: RunKey
    output: dict[str, JsonValue] | None = None
    error: RunError | None = None


class RunStore:
    """Stores runs by id, indexed by idempotence key, guarded by a lock."""

    def __init__(self) -> None:
        """Start with an empty store."""
        self._lock = threading.Lock()
        self._by_id: dict[RunId, StoredRun] = {}
        self._by_key: dict[RunKey, RunId] = {}

    def create_or_get(
        self,
        *,
        key: RunKey,
        family: CheckFamilyName,
        kind: KindPattern,
        formula: str,
        model_id: ModelId,
    ) -> tuple[StoredRun, bool]:
        """Create a running run, or return the existing one for a known key."""
        with self._lock:
            existing_id = self._by_key.get(key)
            if existing_id is not None:
                logger.debug("run_store_key_hit", run_key=key, run_id=existing_id)
                return self._by_id[existing_id], False
            run_id = new_run_id(random_hex=uuid4().hex)
            while run_id in self._by_id:
                run_id = new_run_id(random_hex=uuid4().hex)
            run = StoredRun(
                run_id=run_id,
                status="running",
                family=family,
                kind=kind,
                formula=formula,
                model_id=model_id,
                input_hash=key,
            )
            self._by_id[run.run_id] = run
            self._by_key[key] = run.run_id
            logger.debug("run_store_created", run_id=run.run_id, run_key=key)
            return run, True

    def get(self, *, run_id: RunId) -> StoredRun | None:
        """Return the stored run for an id, or None."""
        with self._lock:
            return self._by_id.get(run_id)

    def mark_done(self, *, run_id: RunId, output: dict[str, JsonValue]) -> None:
        """Move a running run to the ``done`` state with its normalized output."""
        with self._lock:
            run = self._by_id[run_id]
            if run.status != "running":
                logger.warning("run_store_terminal_state_kept", run_id=run_id, status=run.status)
                return
            self._by_id[run_id] = run.model_copy(update={"status": "done", "output": output})
        logger.debug("run_store_done", run_id=run_id)

    def mark_error(self, *, run_id: RunId, kind: RunErrorKind, detail: str) -> None:
        """Move a running run to the explicit ``error`` state - never a default verdict."""
        with self._lock:
            run = self._by_id[run_id]
            if run.status != "running":
                logger.warning("run_store_terminal_state_kept", run_id=run_id, status=run.status)
                return
            self._by_id[run_id] = run.model_copy(
                update={
                    "status": "error",
                    "output": None,
                    "error": RunError(kind=kind, detail=detail),
                }
            )
        logger.debug("run_store_error", run_id=run_id, error_kind=kind)

    def __len__(self) -> int:
        """The number of stored runs."""
        with self._lock:
            return len(self._by_id)
