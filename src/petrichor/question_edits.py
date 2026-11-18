"""Append-only formula edits of question cards (spec §9 maquette P7; issue #37).

The engineer can edit the formula of a question card; the edit is
**journaled** (old and new formula both kept - the witness of the old
formula is never lost) and the edited formula is re-verified through the
standard check chain. The store is JSONL on disk, reloaded on
construction, like the other session-scoped stores (issues #11, #16, #34).
"""

import json
import threading
import time
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import SessionId
from petrichor.elicitation import QuestionId

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

FORMULA_EDITS_FILE_NAME = "formula_edits.jsonl"


class FormulaEditRecord(BaseModel):
    """One append-only formula edit of a question card (spec §9, issue #37)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: SessionId
    question_id: QuestionId
    old_formula: str = Field(min_length=1)
    new_formula: str = Field(min_length=1)
    timestamp: float = Field(gt=0)


class FormulaEditStore:
    """Persistent append-only store of formula edits (JSONL)."""

    def __init__(self, *, directory: Path) -> None:
        """Bind the store to a directory and reload any persisted edits."""
        self._directory = directory
        self._path = directory / FORMULA_EDITS_FILE_NAME
        self._lock = threading.Lock()
        self._records: list[FormulaEditRecord] = []
        self._reload()

    def _reload(self) -> None:
        """Reload the edits from disk, if the file exists."""
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self._records.append(FormulaEditRecord.model_validate(json.loads(line)))
        logger.debug(
            "formula_edit_store_reloaded",
            edits=len(self._records),
            directory=str(self._directory),
        )

    def edit(
        self, *, session_id: SessionId, question_id: QuestionId, old_formula: str, new_formula: str
    ) -> FormulaEditRecord:
        """Record one edit; the previous formula stays in the history (append-only)."""
        record = FormulaEditRecord(
            session_id=session_id,
            question_id=question_id,
            old_formula=old_formula,
            new_formula=new_formula,
            timestamp=time.time(),
        )
        with self._lock:
            self._directory.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            with self._path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")
            self._records.append(record)
        logger.debug(
            "formula_edited",
            session_id=session_id,
            question_id=question_id,
            old_formula=old_formula,
            new_formula=new_formula,
        )
        return record

    def history(self, *, session_id: SessionId) -> list[FormulaEditRecord]:
        """Every edit of a session, in append order (append-only)."""
        with self._lock:
            return [record for record in self._records if record.session_id == session_id]

    def latest_formula(
        self, *, session_id: SessionId, question_id: QuestionId, original: str
    ) -> str:
        """The current formula of a question: the latest edit's, or the original."""
        with self._lock:
            for record in reversed(self._records):
                if record.session_id == session_id and record.question_id == question_id:
                    return record.new_formula
        return original

    def __len__(self) -> int:
        """The number of stored edits."""
        with self._lock:
            return len(self._records)
