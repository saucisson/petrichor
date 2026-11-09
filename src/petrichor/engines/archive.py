"""Append-only run archive (spec §12.5).

The budget limits what we produce, never what we keep: run records are
appended as JSON lines and there is deliberately no delete path in this
class (issue #7 pins that with a test).
"""

import json
from typing import TYPE_CHECKING

import structlog

from petrichor.engines.contract import RunRecord

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

RUNS_FILE_NAME = "runs.jsonl"


class RunArchive:
    """Stores run records as JSON lines in one directory."""

    def __init__(self, *, directory: Path) -> None:
        """Bind the archive to a directory (created lazily on first append)."""
        self._directory = directory
        self._path = directory / RUNS_FILE_NAME

    def append(self, *, record: RunRecord) -> Path:
        """Append one run record; return the archive file path."""
        self._directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with self._path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        logger.debug(
            "run_archived", run_id=record.run_id, engine=record.engine, path=str(self._path)
        )
        return self._path

    def records(self) -> list[RunRecord]:
        """Read every archived run, in append order."""
        if not self._path.exists():
            return []
        lines = self._path.read_text(encoding="utf-8").splitlines()
        return [RunRecord.model_validate(json.loads(line)) for line in lines if line.strip()]

    def __len__(self) -> int:
        """The number of archived runs."""
        return len(self.records())
