"""Witnesses in the §16 format, stored once and referenced n times (spec §16, issue #32).

**Structure = tool**: markings, fired transitions, cyclicity, back-edge —
normalized here from any engine's run output into the §16 document. **Narration
= LLM proposition**: one sentence per step, always labeled ``provenance: llm``,
never a verdict (the guard test scans the whole narration corpus for verdict
terms, spec §19.4).

A witness shared by several violations is **stored once** (keyed by the
content hash of its structure) and **referenced n times** by the runs that
produced it. The store is JSONL on disk, reloaded on construction, like the
other session-scoped stores (issues #11, #16, #34).
"""

import hashlib
import json
import threading
import time
from typing import TYPE_CHECKING, Literal, NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from petrichor.checks import RunId
from petrichor.llm.provider import LLMDegradationError, MistralToken

if TYPE_CHECKING:
    from pathlib import Path

    from petrichor.llm.provider import LLMProvider
    from petrichor.petri_canonical import CanonicalModel, JsonValue

logger = structlog.get_logger(__name__)

WITNESSES_FILE_NAME = "witnesses.jsonl"
WITNESS_REFERENCES_FILE_NAME = "witness_references.jsonl"

WITNESS_ID_PREFIX = "wit_"
WITNESS_ID_HEX_DIGITS = 12

WitnessId = NewType("WitnessId", str)
"""The content-hash identifier of a stored witness, e.g. ``wit_3f9a1c2b7d4e``."""

type WitnessKind = Literal["counterexample", "trace"]
"""The closed set of witness kinds (spec §16)."""

type NarrationProvenance = Literal["llm"]
"""A narration is always an LLM proposition, never a tool verdict (spec §16)."""

VERDICT_TERMS = frozenset({"satisfied", "violated", "unknown", "verdict"})
"""The normalized verdict vocabulary a narration must never carry (spec §19.4)."""


class WitnessStep(BaseModel):
    """One step of a witness trace (spec §16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    marking: list[str]
    fired: str | None


class Narration(BaseModel):
    """One per-step LLM narration, labeled with its provenance (spec §16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    provenance: NarrationProvenance = "llm"


class _WitnessPayload(BaseModel):
    """The engine-side witness shape, before the content-hash id is computed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: WitnessKind
    cyclic: bool
    back_edge: int | None = None
    steps: tuple[WitnessStep, ...]


class WitnessRecord(BaseModel):
    """The stored-once witness structure, keyed by its content hash (spec §16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    witness_id: WitnessId
    kind: WitnessKind
    cyclic: bool
    back_edge: int | None = None
    steps: tuple[WitnessStep, ...]


class WitnessDocument(BaseModel):
    """The §16 witness document served per run: structure + run id + narrations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    witness_id: WitnessId
    kind: WitnessKind
    cyclic: bool
    back_edge: int | None = None
    steps: tuple[WitnessStep, ...]
    narrations: tuple[Narration, ...] = ()


class WitnessError(Exception):
    """The run output carries a malformed witness (engine contract violation)."""


def witness_id_of(
    *, kind: WitnessKind, cyclic: bool, back_edge: int | None, steps: tuple[WitnessStep, ...]
) -> WitnessId:
    """The content hash of the witness structure - the dedup key (spec §16)."""
    material = json.dumps(
        {
            "kind": kind,
            "cyclic": cyclic,
            "back_edge": back_edge,
            "steps": [step.model_dump(mode="json") for step in steps],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return WitnessId(f"{WITNESS_ID_PREFIX}{digest[:WITNESS_ID_HEX_DIGITS]}")


def normalize_witness_output(
    *, run_id: RunId, output: dict[str, JsonValue]
) -> WitnessDocument | None:
    """Normalize one run output into the §16 document; None when it has no witness.

    Every engine emits the same witness shape (``{kind, cyclic, back_edge,
    steps}``); a malformed witness is a contract violation, never silently
    dropped.
    """
    raw = output.get("witness")
    if raw is None:
        return None
    try:
        payload = _WitnessPayload.model_validate(raw)
    except ValidationError as error:
        raise WitnessError(f"Run {run_id} carries a malformed witness: {error}") from error
    steps = tuple(payload.steps)
    witness_id = witness_id_of(
        kind=payload.kind, cyclic=payload.cyclic, back_edge=payload.back_edge, steps=steps
    )
    document = WitnessDocument(
        run_id=run_id,
        witness_id=witness_id,
        kind=payload.kind,
        cyclic=payload.cyclic,
        back_edge=payload.back_edge,
        steps=steps,
    )
    logger.debug(
        "witness_normalized",
        run_id=run_id,
        witness_id=witness_id,
        kind=document.kind,
        steps=len(steps),
        cyclic=document.cyclic,
    )
    return document


def witness_record_of(*, document: WitnessDocument) -> WitnessRecord:
    """The stored-once structure of a witness document (without the run id)."""
    return WitnessRecord(
        witness_id=document.witness_id,
        kind=document.kind,
        cyclic=document.cyclic,
        back_edge=document.back_edge,
        steps=document.steps,
    )


def narrate_steps(
    *,
    provider: LLMProvider,
    model: CanonicalModel,
    steps: tuple[WitnessStep, ...],
    api_token: MistralToken | None = None,
) -> tuple[Narration, ...]:
    """One LLM narration per fired step, labeled ``provenance: llm`` (spec §16).

    A transition missing from the scripted corpus degrades explicitly (the
    step gets no narration) — the mock never invents a sentence (spec §15).
    ``api_token`` is the per-request Mistral token (web v1 §3, issue #208),
    forwarded to the provider; token errors propagate (they are not
    degradations).
    """
    fired = tuple(step.fired for step in steps if step.fired is not None)
    if not fired:
        return ()
    # P72: ONE LLM call for the complete witness (document granularity), the
    # corpus is stored once (P32). A transition absent from the corpus gets
    # no narration - never an invented sentence.
    try:
        corpus = provider.narrate(model=model, transitions=fired, api_token=api_token)
    except LLMDegradationError:
        logger.warning(
            "witness_narration_degraded", model_hash=model.content_hash, steps=len(steps)
        )
        return ()
    narrations: list[Narration] = []
    for step in steps:
        if step.fired is None:
            continue
        text = corpus.narrations.get(step.fired)
        if text is None:
            logger.warning(
                "witness_narration_not_scripted",
                model_hash=model.content_hash,
                transition=step.fired,
                step_index=step.index,
            )
            continue
        narrations.append(Narration(step_index=step.index, text=text, provenance="llm"))
    logger.debug(
        "witness_narrated",
        model_hash=model.content_hash,
        steps=len(steps),
        narrations=len(narrations),
    )
    return tuple(narrations)


def narration_carries_no_verdict(*, text: str) -> bool:
    """The §19.4 guard: a narration never contains a verdict term."""
    lowered = text.lower()
    return not any(term in lowered for term in VERDICT_TERMS)


class WitnessStore:
    """Persistent store of witnesses (JSONL), deduplicated by content hash.

    ``store`` is idempotent: the same structure stored twice yields one
    record; ``add_reference`` records which runs point at it, so a witness
    shared by several violations is stored once and referenced n times.
    """

    def __init__(self, *, directory: Path) -> None:
        """Bind the store to a directory and reload any persisted state."""
        self._directory = directory
        self._witnesses_path = directory / WITNESSES_FILE_NAME
        self._references_path = directory / WITNESS_REFERENCES_FILE_NAME
        self._lock = threading.Lock()
        self._records: dict[WitnessId, WitnessRecord] = {}
        self._references: dict[WitnessId, list[RunId]] = {}
        self._reload()

    def _reload(self) -> None:
        """Reload witnesses and references from disk, if the files exist."""
        if self._witnesses_path.exists():
            for line in self._witnesses_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    record = WitnessRecord.model_validate(json.loads(line))
                    self._records[record.witness_id] = record
        if self._references_path.exists():
            for line in self._references_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                witness_id = WitnessId(str(entry["witness_id"]))
                run_id = RunId(str(entry["run_id"]))
                self._references.setdefault(witness_id, []).append(run_id)
        logger.debug(
            "witness_store_reloaded",
            witnesses=len(self._records),
            references=sum(len(refs) for refs in self._references.values()),
            directory=str(self._directory),
        )

    def _append_line_locked(self, *, path: Path, line: str) -> None:
        """Append one JSONL line; the caller holds the lock."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    def store(self, *, record: WitnessRecord) -> WitnessRecord:
        """Store one witness structure; an identical one is returned as-is (dedup)."""
        with self._lock:
            existing = self._records.get(record.witness_id)
            if existing is not None:
                logger.debug("witness_store_dedup_hit", witness_id=record.witness_id)
                return existing
            self._records[record.witness_id] = record
            line = json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
            self._append_line_locked(path=self._witnesses_path, line=line)
        logger.debug("witness_stored", witness_id=record.witness_id, steps=len(record.steps))
        return record

    def add_reference(self, *, witness_id: WitnessId, run_id: RunId) -> None:
        """Record that one run references the stored witness."""
        with self._lock:
            if witness_id not in self._records:
                logger.warning(
                    "witness_reference_unknown_witness", witness_id=witness_id, run_id=run_id
                )
                return
            refs = self._references.setdefault(witness_id, [])
            if run_id in refs:
                return
            refs.append(run_id)
            line = json.dumps(
                {"witness_id": str(witness_id), "run_id": str(run_id), "at": time.time()},
                sort_keys=True,
                separators=(",", ":"),
            )
            self._append_line_locked(path=self._references_path, line=line)
        logger.debug("witness_referenced", witness_id=witness_id, run_id=run_id)

    def get(self, *, witness_id: WitnessId) -> WitnessRecord | None:
        """Return the stored witness for an id, or None."""
        with self._lock:
            return self._records.get(witness_id)

    def references(self, *, witness_id: WitnessId) -> list[RunId]:
        """The runs referencing this witness, in append order."""
        with self._lock:
            return list(self._references.get(witness_id, []))

    def __len__(self) -> int:
        """The number of stored (deduplicated) witnesses."""
        with self._lock:
            return len(self._records)
