"""Persistent arbitration of question cards (spec §4.8, §12.7, issue #16).

A human arbitration is a decision on one generated question card: ``accept``
(« c'est normal »), ``refuse`` (« impossible ») or ``verify-first``
(« vérifier d'abord »). Decisions are **append-only**: revising a card records
a new decision and the previous one stays in the history (avant/après, spec
§12.7). No automatic fusion of questions ever happens here - convergence is
a separate, human-only decision (P34, spec §12.2).

The store is JSONL on disk, reloaded on construction, so arbitrations survive
a process restart exactly like sessions (issue #11).
"""

import json
import threading
import time
from typing import TYPE_CHECKING, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import SessionId
from petrichor.elicitation import QuestionId

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

ArbitrationDecisionKind = Literal["accept", "refuse", "verify-first"]
"""The closed set of human decisions on a question card (spec §4.8)."""

ARBITRATIONS_FILE_NAME = "arbitrations.jsonl"


class ArbitrationRecord(BaseModel):
    """One append-only arbitration decision, with the previous one (avant/après)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: SessionId
    question_id: QuestionId
    decision: ArbitrationDecisionKind
    timestamp: float = Field(gt=0)
    previous_decision: ArbitrationDecisionKind | None = None
    """The decision this one revises, None for a first decision (spec §12.7)."""


class ArbitrationStore:
    """Persistent append-only store of arbitration decisions (JSONL)."""

    def __init__(self, *, directory: Path) -> None:
        """Bind the store to a directory and reload any persisted decisions."""
        self._directory = directory
        self._path = directory / ARBITRATIONS_FILE_NAME
        self._lock = threading.Lock()
        self._records: list[ArbitrationRecord] = []
        self._reload()

    def _reload(self) -> None:
        """Reload the decisions from disk, if the file exists."""
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._records.append(ArbitrationRecord.model_validate(json.loads(line)))
        logger.debug(
            "arbitration_store_reloaded",
            decisions=len(self._records),
            directory=str(self._directory),
        )

    def _persist_locked(self, *, record: ArbitrationRecord) -> None:
        """Append one decision line; the caller holds the lock."""
        self._directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        self._records.append(record)

    def decide(
        self,
        *,
        session_id: SessionId,
        question_id: QuestionId,
        decision: ArbitrationDecisionKind,
    ) -> ArbitrationRecord:
        """Record one decision; a repeated question is a revision, never a fusion."""
        now = time.time()
        with self._lock:
            previous = next(
                (
                    record.decision
                    for record in reversed(self._records)
                    if record.session_id == session_id and record.question_id == question_id
                ),
                None,
            )
            record = ArbitrationRecord(
                session_id=session_id,
                question_id=question_id,
                decision=decision,
                timestamp=now,
                previous_decision=previous,
            )
            self._persist_locked(record=record)
        logger.debug(
            "arbitration_decided",
            session_id=session_id,
            question_id=question_id,
            decision=decision,
            previous_decision=previous,
        )
        return record

    def history(self, *, session_id: SessionId) -> list[ArbitrationRecord]:
        """Every decision of a session, in append order (append-only)."""
        with self._lock:
            return [record for record in self._records if record.session_id == session_id]

    def for_question(
        self, *, session_id: SessionId, question_id: QuestionId
    ) -> list[ArbitrationRecord]:
        """The decision history of one question card of a session."""
        with self._lock:
            return [
                record
                for record in self._records
                if record.session_id == session_id and record.question_id == question_id
            ]

    def __len__(self) -> int:
        """The number of stored decisions."""
        with self._lock:
            return len(self._records)
