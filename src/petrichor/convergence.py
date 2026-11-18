"""Human fusions of converging questions (spec §12.2, §14.5, §4.8; issue #34).

Convergence is **asymmetric**: *Proposer* only detects it at generation time
(the ``convergence_hint`` of `petrichor.elicitation`) and *Arbitrer* alone
merges - a human fusion recorded here marks the question ``covered`` and it
is never solicited again. Automatic fusion is forbidden: nothing in this
module ever changes a question status by itself, the store only records
explicit human decisions.

The store is JSONL on disk, reloaded on construction, so fusions survive a
process restart exactly like arbitrations (issue #11, #16).
"""

import json
import threading
import time
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.elicitation import QuestionId
from petrichor.session_store import SessionId

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

CONVERGENCES_FILE_NAME = "convergences.jsonl"

CONVERGENCE_HINT_TEXT = "cette question existe déjà, arbitrez-la une fois"
"""The generation-time alert of a converging question (spec §14.5, issue #34)."""


class ConvergenceRecord(BaseModel):
    """One human fusion of two converging questions (spec §12.2, §4.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: SessionId
    question_id: QuestionId
    """The covered question: never solicited again once fused."""

    proposition_id: QuestionId
    """The surviving question the covered one converges with."""

    timestamp: float = Field(gt=0)


class ConvergenceStore:
    """Persistent append-only store of human fusions (JSONL)."""

    def __init__(self, *, directory: Path) -> None:
        """Bind the store to a directory and reload any persisted fusions."""
        self._directory = directory
        self._path = directory / CONVERGENCES_FILE_NAME
        self._lock = threading.Lock()
        self._records: list[ConvergenceRecord] = []
        self._reload()

    def _reload(self) -> None:
        """Reload the fusions from disk, if the file exists."""
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._records.append(ConvergenceRecord.model_validate(json.loads(line)))
        logger.debug(
            "convergence_store_reloaded",
            fusions=len(self._records),
            directory=str(self._directory),
        )

    def _persist_locked(self, *, record: ConvergenceRecord) -> None:
        """Append one fusion line; the caller holds the lock."""
        self._directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        self._records.append(record)

    def fuse(
        self, *, session_id: SessionId, question_id: QuestionId, proposition_id: QuestionId
    ) -> ConvergenceRecord:
        """Record one human fusion; the covered question is never solicited again."""
        record = ConvergenceRecord(
            session_id=session_id,
            question_id=question_id,
            proposition_id=proposition_id,
            timestamp=time.time(),
        )
        with self._lock:
            self._persist_locked(record=record)
        logger.debug(
            "convergence_fused",
            session_id=session_id,
            question_id=question_id,
            proposition_id=proposition_id,
        )
        return record

    def history(self, *, session_id: SessionId) -> list[ConvergenceRecord]:
        """Every fusion of a session, in append order (append-only)."""
        with self._lock:
            return [record for record in self._records if record.session_id == session_id]

    def covered_map(self, *, session_id: SessionId) -> dict[QuestionId, ConvergenceRecord]:
        """The latest fusion of each covered question (last wins, append-only)."""
        covered: dict[QuestionId, ConvergenceRecord] = {}
        with self._lock:
            for record in self._records:
                if record.session_id == session_id:
                    covered[record.question_id] = record
        return covered

    def covered_by_proposition(
        self, *, session_id: SessionId, proposition_id: QuestionId
    ) -> list[ConvergenceRecord]:
        """Every fusion whose surviving question is the given proposition."""
        with self._lock:
            return [
                record
                for record in self._records
                if record.session_id == session_id and record.proposition_id == proposition_id
            ]

    def __len__(self) -> int:
        """The number of stored fusions."""
        with self._lock:
            return len(self._records)
