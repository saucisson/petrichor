"""Adapter qualification by MCC benchmark oracles (issue #22, spec §17.5).

Each MCC instance (PNML + ``Examination.xml`` + expected verdict) is a
conformance oracle: an adapter is **qualified** for a family only when it
reproduces the expected verdicts of the benchmark sample on that family.
Before qualification, an adapter is never alone on a clause - the guard
``check_clause_support`` enforces it. The qualification status per
(adapter, family) is stored on disk and exposed to the orchestrator through
the `QualificationLookup` port (``petrichor.checks``), because the verdict
lock (spec §19.4) keeps this module inside ``engines/``.

An engine failure during a replay is never swallowed: it propagates, so in
the ``@engine`` suite it is a test failure - a CI failure, never a skip.
"""

import json
from typing import TYPE_CHECKING

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    CheckFamily,
    EngineError,
    EngineName,
    EngineQuestion,
    Verdict,
)
from petrichor.petri_canonical import JsonValue

if TYPE_CHECKING:
    from pathlib import Path

    from petrichor.checks import QualificationState
    from petrichor.engines.driver import EngineDriver

logger = structlog.get_logger(__name__)

QUALIFICATION_FILENAME = "qualification.json"
"""The on-disk name of the persisted qualification registry."""
MODEL_PARAMETER_KEY = "model"
"""The question parameter carrying the model, as injected by the API layer."""


class MccOracle(BaseModel):
    """One bounded MCC benchmark sample instance: model + question + expected verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_id: str = Field(min_length=1)
    family: CheckFamily
    formula: str = Field(min_length=1)
    expected_verdict: Verdict
    model: dict[str, JsonValue]


class UnqualifiedSoleSupportError(EngineError):
    """A clause would rest on a single adapter that is not qualified for the family."""


def load_oracles(*, path: Path) -> list[MccOracle]:
    """Load the bounded on-disk MCC sample: one shared model plus its oracles."""
    logger.debug("qualification_oracles_loading", path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EngineError(f"cannot read the MCC oracle sample at {path}: {error}") from error
    try:
        model = payload["model"]
        oracles = [
            MccOracle(
                instance_id=entry["instance_id"],
                family=entry["family"],
                formula=entry["formula"],
                expected_verdict=entry["expected_verdict"],
                model=model,
            )
            for entry in payload["oracles"]
        ]
    except (KeyError, TypeError, ValidationError) as error:
        raise EngineError(f"malformed MCC oracle sample at {path}: {error}") from error
    logger.debug("qualification_oracles_loaded", path=str(path), oracle_count=len(oracles))
    return oracles


class QualificationRegistry:
    """The stored qualification status per (adapter, family), persisted as JSON.

    An adapter absent from the registry is unqualified for every family:
    qualification is earned by replaying the oracles, never assumed.
    """

    def __init__(self, *, directory: Path | None = None) -> None:
        """Load the persisted registry when the directory holds one."""
        self._directory = directory
        self._states: dict[EngineName, dict[CheckFamily, QualificationState]] = {}
        if directory is not None:
            self._load(directory=directory)

    def _path(self, *, directory: Path) -> Path:
        """The JSON file holding the persisted registry."""
        return directory / QUALIFICATION_FILENAME

    def _load(self, *, directory: Path) -> None:
        """Read the persisted registry; a missing file means an empty registry."""
        path = self._path(directory=directory)
        if not path.exists():
            logger.debug("qualification_registry_empty", path=str(path))
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._states = {
            EngineName(engine): dict(families) for engine, families in payload["engines"].items()
        }
        logger.debug("qualification_registry_loaded", path=str(path), engines=len(self._states))

    def _persist(self) -> None:
        """Write the registry to disk when a directory was configured."""
        if self._directory is None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._path(directory=self._directory)
        payload = {
            "engines": {str(engine): dict(families) for engine, families in self._states.items()}
        }
        path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        logger.debug("qualification_registry_persisted", path=str(path))

    def record(self, *, engine: EngineName, family: CheckFamily, state: QualificationState) -> None:
        """Record the qualification state of one adapter for one family."""
        logger.debug("qualification_recorded", engine=engine, family=family, state=state)
        self._states.setdefault(engine, {})[family] = state
        self._persist()

    def is_qualified(self, *, engine: EngineName, family: CheckFamily) -> bool:
        """Whether the adapter is qualified for the family; unknown means unqualified."""
        return self._states.get(engine, {}).get(family) == "qualified"

    def snapshot(self) -> dict[str, dict[str, QualificationState]]:
        """The full qualification status per adapter and family."""
        return {
            str(engine): {str(family): state for family, state in families.items()}
            for engine, families in self._states.items()
        }


def replay_oracle(
    *, driver: EngineDriver, oracle: MccOracle, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
) -> bool:
    """Replay one oracle through the driver: True when the verdict matches the expected one.

    A driver failure propagates to the caller - in the ``@engine`` suite it
    fails the test, it is never converted into a mismatch or a skip.
    """
    question = EngineQuestion(
        family=oracle.family,
        formula=oracle.formula,
        pattern=oracle.formula,
        parameters={MODEL_PARAMETER_KEY: oracle.model},
    )
    logger.debug(
        "qualification_oracle_replaying",
        instance_id=oracle.instance_id,
        family=oracle.family,
        expected_verdict=oracle.expected_verdict,
    )
    record = driver.run(question=question, timeout_seconds=timeout_seconds)
    reproduced = record.output.verdict == oracle.expected_verdict
    logger.info(
        "qualification_oracle_replayed",
        instance_id=oracle.instance_id,
        family=oracle.family,
        expected_verdict=oracle.expected_verdict,
        actual_verdict=record.output.verdict,
        reproduced=reproduced,
    )
    return reproduced


def qualify_engine(
    *,
    driver: EngineDriver,
    engine: EngineName,
    oracles: list[MccOracle],
    registry: QualificationRegistry,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[CheckFamily, QualificationState]:
    """Qualify one adapter per family: qualified iff every oracle of the family replays true."""
    families: dict[CheckFamily, list[MccOracle]] = {}
    for oracle in oracles:
        families.setdefault(oracle.family, []).append(oracle)
    states: dict[CheckFamily, QualificationState] = {}
    for family, family_oracles in families.items():
        reproduced = [
            replay_oracle(driver=driver, oracle=oracle, timeout_seconds=timeout_seconds)
            for oracle in family_oracles
        ]
        state: QualificationState = "qualified" if all(reproduced) else "unqualified"
        registry.record(engine=engine, family=family, state=state)
        states[family] = state
    logger.info(
        "qualification_engine_completed",
        engine=engine,
        families=dict(states),
    )
    return states


def check_clause_support(
    *,
    registry: QualificationRegistry,
    engine: EngineName,
    family: CheckFamily,
    corroborating_adapters: int,
) -> None:
    """The application guard: a clause never rests on a single unqualified adapter.

    Raises `UnqualifiedSoleSupportError` when no other adapter corroborates
    and the engine is not qualified for the family (spec §17.5).
    """
    qualified = registry.is_qualified(engine=engine, family=family)
    logger.debug(
        "qualification_clause_support_checked",
        engine=engine,
        family=family,
        qualified=qualified,
        corroborating_adapters=corroborating_adapters,
    )
    if not qualified and corroborating_adapters <= 0:
        raise UnqualifiedSoleSupportError(
            f"adapter {engine!r} is not qualified for family {family!r} and no other"
            " adapter corroborates: the clause cannot rest on it alone (spec §17.5)"
        )


class EngineQualificationLookup:
    """The `QualificationLookup` port of one adapter, backed by a registry."""

    def __init__(self, *, registry: QualificationRegistry, engine: EngineName) -> None:
        """Bind the lookup to one adapter of the registry."""
        self._registry = registry
        self._engine = engine

    def is_qualified(self, *, family: CheckFamily) -> bool:
        """Whether this lookup's adapter is qualified for the family."""
        return self._registry.is_qualified(engine=self._engine, family=family)

    def snapshot(self) -> dict[str, dict[str, QualificationState]]:
        """The full qualification status per adapter and family."""
        return self._registry.snapshot()
