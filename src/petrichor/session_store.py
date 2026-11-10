"""Persistent session store with append-only journal (spec §4 Gérer, issue #11).

A session is the unit of resumable work: it owns a model, a list of runs
and a timestamped append-only journal. The whole state is serialized as
JSON lines on disk, so a session survives a process restart - the store
reloads from the journal file on construction.

There is deliberately no delete path: the journal is append-only, and a
session is never removed (spec §4, same rule as the run archive §12.5).
"""

import json
import threading
import time
from typing import TYPE_CHECKING, NewType
from uuid import uuid4

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import RunId
from petrichor.petri_canonical import JsonValue, ModelId

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

SessionId = NewType("SessionId", str)
"""A session identifier, e.g. ``ses_7c2a9f``."""

SESSION_ID_PREFIX = "ses_"
SESSION_ID_HEX_DIGITS = 6

SESSIONS_FILE_NAME = "sessions.jsonl"
JOURNAL_FILE_NAME = "journal.jsonl"


def new_session_id(*, random_hex: str) -> SessionId:
    """Build a session id from random hex, truncated to the fixed width."""
    return SessionId(f"{SESSION_ID_PREFIX}{random_hex[:SESSION_ID_HEX_DIGITS]}")


class JournalEntry(BaseModel):
    """One timestamped append-only journal entry (spec §4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timestamp: float = Field(gt=0)
    """Unix epoch seconds when the entry was appended."""

    event: str = Field(min_length=1)
    """The event name, e.g. ``session_created``, ``run_added``."""

    detail: dict[str, JsonValue] = Field(default_factory=dict)
    """Named fields of the event, never a free-text blob."""


class StoredSession(BaseModel):
    """One session as stored and served by the API (spec §4).

    Frozen on purpose: state transitions replace the whole entry under the
    store lock, so a reader can serialize the object returned by ``get``
    outside the lock and never observe a torn state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    session_id: SessionId
    model_id: ModelId
    created_at: float = Field(gt=0)
    updated_at: float = Field(gt=0)
    run_ids: list[RunId] = Field(default_factory=list)
    """The runs attached to this session, in append order."""


class SessionStore:
    """Persistent session store: JSONL state file plus per-session journal.

    The state file (``sessions.jsonl``) holds one line per session with its
    full serialized state; the journal file (``journal.jsonl``) holds one
    line per session with its append-only timestamped entries. Both are
    reloaded on construction, so a process restart loses nothing.
    """

    def __init__(self, *, directory: Path) -> None:
        """Bind the store to a directory and reload any persisted state."""
        self._directory = directory
        self._state_path = directory / SESSIONS_FILE_NAME
        self._lock = threading.Lock()
        self._sessions: dict[SessionId, StoredSession] = {}
        self._journals: dict[SessionId, list[JournalEntry]] = {}
        self._reload()

    def _reload(self) -> None:
        """Reload sessions and journals from disk, if the files exist."""
        if self._state_path.exists():
            for line in self._state_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                session = StoredSession.model_validate(json.loads(line))
                self._sessions[session.session_id] = session
        for session_id in self._sessions:
            journal_path = self._journal_path(session_id=session_id)
            if journal_path.exists():
                entries = [
                    JournalEntry.model_validate(json.loads(line))
                    for line in journal_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self._journals[session_id] = entries
        logger.debug(
            "session_store_reloaded",
            sessions=len(self._sessions),
            directory=str(self._directory),
        )

    def _journal_path(self, *, session_id: SessionId) -> Path:
        """The journal file path of one session."""
        return self._directory / str(session_id) / JOURNAL_FILE_NAME

    def _persist_session_locked(self, *, session: StoredSession) -> None:
        """Rewrite the state file with all sessions; the caller holds the lock."""
        self._directory.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(s.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            for s in self._sessions.values()
        ]
        self._state_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _append_journal_locked(self, *, session_id: SessionId, entry: JournalEntry) -> None:
        """Append one entry to the session journal; the caller holds the lock."""
        journal_path = self._journal_path(session_id=session_id)
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with journal_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        self._journals.setdefault(session_id, []).append(entry)

    def create(self, *, model_id: ModelId) -> StoredSession:
        """Create a new session for a model and persist it."""
        now = time.time()
        session_id = new_session_id(random_hex=uuid4().hex)
        while session_id in self._sessions:
            session_id = new_session_id(random_hex=uuid4().hex)
        session = StoredSession(
            session_id=session_id,
            model_id=model_id,
            created_at=now,
            updated_at=now,
            run_ids=[],
        )
        with self._lock:
            self._sessions[session_id] = session
            self._persist_session_locked(session=session)
            self._append_journal_locked(
                session_id=session_id,
                entry=JournalEntry(
                    timestamp=now,
                    event="session_created",
                    detail={"model_id": str(model_id)},
                ),
            )
        logger.debug("session_created", session_id=session_id, model_id=str(model_id))
        return session

    def get(self, *, session_id: SessionId) -> StoredSession | None:
        """Return the stored session for an id, or None."""
        with self._lock:
            return self._sessions.get(session_id)

    def list_all(self) -> list[StoredSession]:
        """Return every session, in creation order."""
        with self._lock:
            return sorted(self._sessions.values(), key=lambda s: s.created_at)

    def append_run(self, *, session_id: SessionId, run_id: RunId) -> StoredSession | None:
        """Attach a run to a session and journal the event; None if unknown session."""
        now = time.time()
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            updated = session.model_copy(
                update={"run_ids": [*session.run_ids, run_id], "updated_at": now}
            )
            self._sessions[session_id] = updated
            self._persist_session_locked(session=updated)
            self._append_journal_locked(
                session_id=session_id,
                entry=JournalEntry(
                    timestamp=now,
                    event="run_added",
                    detail={"run_id": run_id},
                ),
            )
        logger.debug("session_run_added", session_id=session_id, run_id=run_id)
        return updated

    def journal(self, *, session_id: SessionId) -> list[JournalEntry]:
        """Return the append-only journal of a session, in append order."""
        with self._lock:
            return list(self._journals.get(session_id, []))

    def __len__(self) -> int:
        """The number of stored sessions."""
        with self._lock:
            return len(self._sessions)
