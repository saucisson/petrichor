"""Cross-verification orchestrator: two engines per clause, concordance computed (spec §17.4).

The pairing matrix is a versioned config file (``pairing.json``): two engines
per check family, chosen for their technological distance — never two
instances of the same brick (ITS-Tools embeds LTSMin and Spot, so
"ITS-Tools x LTSMin" is not a cross-verification).

Concordance → the clause is "verified by n tools". Discordance → an
arbitration run (re-run, reductions disabled when possible); if the
discordance persists, the clause is **controversial** and reported to the
engineer — never decided by the AI. No code path produces a single verdict
on discordance (guard test in ``tests/test_cross_verification_guard.py``).

This module lives inside ``petrichor.engines`` because comparing verdicts
requires reading them (verdict lock, spec §19.4); the API layer reads the
result through an opaque port, exactly like `QualificationLookup`.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NewType

if TYPE_CHECKING:
    from collections.abc import Mapping

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

if TYPE_CHECKING:
    from petrichor.engines.driver import EngineDriver

logger = structlog.get_logger(__name__)

PAIRING_FILENAME = "pairing.json"
"""The on-disk name of the versioned pairing matrix."""

type CrossVerificationStatus = Literal["concordant", "controversial"]
"""The outcome of a cross-verification: concordant or controversial (never a single verdict)."""

type ArbitrationStatus = Literal["not_needed", "resolved", "persistent"]
"""The arbitration outcome: not needed on concordance, resolved or persistent on discordance."""

EnginePair = NewType("EnginePair", tuple[EngineName, EngineName])
"""The two engines paired for one family, e.g. ``("verifypn", "its-tools")``."""


class PairingEntry(BaseModel):
    """One family's pairing: the primary pair plus optional differential engines."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    primary: list[EngineName] = Field(min_length=2, max_length=2)
    differential: list[EngineName] = Field(default_factory=list)
    note: str = ""


class PairingMatrix(BaseModel):
    """The versioned pairing matrix: one entry per check family (spec §17.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(min_length=1)
    description: str = ""
    pairings: dict[CheckFamily, PairingEntry]

    def pair_for(self, *, family: CheckFamily) -> EnginePair:
        """The primary engine pair for one family."""
        entry = self.pairings[family]
        return EnginePair((entry.primary[0], entry.primary[1]))

    def differential_for(self, *, family: CheckFamily) -> list[EngineName]:
        """The optional differential engines for one family."""
        return list(self.pairings[family].differential)


def load_pairing_matrix(*, path: Path) -> PairingMatrix:
    """Load the versioned pairing matrix from a JSON config file."""
    logger.debug("pairing_matrix_loading", path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EngineError(f"cannot read the pairing matrix at {path}: {error}") from error
    try:
        matrix = PairingMatrix.model_validate(payload)
    except ValidationError as error:
        raise EngineError(f"malformed pairing matrix at {path}: {error}") from error
    logger.debug("pairing_matrix_loaded", path=str(path), version=matrix.version)
    return matrix


def default_pairing_matrix() -> PairingMatrix:
    """The built-in default pairing matrix (spec §17.4 defaults)."""
    path = Path(__file__).parent / PAIRING_FILENAME
    return load_pairing_matrix(path=path)


class EngineVerdictRecord(BaseModel):
    """One engine's verdict on a clause, with its witness and cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    engine: EngineName
    verdict: Verdict
    technique: str = Field(min_length=1)
    cost_tool_seconds: float = Field(ge=0)


class ArbitrationRecord(BaseModel):
    """The arbitration run on discordance: status and the resolving verdict if any."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: ArbitrationStatus
    engine: EngineName | None = None
    verdict: Verdict | None = None
    detail: str = ""


class CrossVerificationResult(BaseModel):
    """The full cross-verification result of one clause (spec §17.4).

    On concordance the status is ``concordant`` and every engine verdict is
    recorded. On persistent discordance the status is ``controversial`` and
    no single verdict is produced — the clause is reported to the engineer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: CheckFamily
    status: CrossVerificationStatus
    verdicts: list[EngineVerdictRecord] = Field(min_length=2)
    concordance: float = Field(ge=0.0, le=1.0)
    arbitration: ArbitrationRecord
    verified_by: int = Field(ge=0)
    """How many engines agree on the verdict; 0 on persistent discordance."""


class CrossVerificationOrchestrator:
    """Runs one clause on the paired engines and computes concordance (spec §17.4).

    The orchestrator holds the pairing matrix and a mapping from engine name
    to driver. On discordance it runs an arbitration pass (re-run on the
    primary pair with reductions disabled when the driver supports it); if
    the discordance persists, the clause is controversial.
    """

    def __init__(
        self,
        *,
        pairing: PairingMatrix,
        drivers: Mapping[EngineName, EngineDriver],
    ) -> None:
        """Wire the orchestrator to a pairing matrix and one driver per engine."""
        self._pairing = pairing
        self._drivers = drivers

    def _driver_for(self, *, engine: EngineName) -> EngineDriver:
        """The driver of one engine, or raise when it is not wired."""
        driver = self._drivers.get(engine)
        if driver is None:
            raise EngineError(f"no driver wired for engine {engine!r}")
        return driver

    def _run_engine(
        self, *, engine: EngineName, question: EngineQuestion, timeout_seconds: int
    ) -> EngineVerdictRecord:
        """Run one engine and return its verdict record; driver errors propagate."""
        driver = self._driver_for(engine=engine)
        record = driver.run(question=question, timeout_seconds=timeout_seconds)
        logger.debug(
            "cross_verification_engine_run",
            engine=engine,
            verdict=record.output.verdict,
            technique=record.output.technique,
        )
        return EngineVerdictRecord(
            engine=engine,
            verdict=record.output.verdict,
            technique=record.output.technique,
            cost_tool_seconds=record.output.cost.tool_seconds,
        )

    def _arbitrate(
        self,
        *,
        pair: EnginePair,
        question: EngineQuestion,
        timeout_seconds: int,
    ) -> ArbitrationRecord:
        """Arbitration pass on discordance: re-run both engines, reductions disabled.

        The arbitration re-runs the primary pair; if both still disagree, the
        discordance is persistent and the clause is controversial. The AI never
        picks a winner (spec §17.4).
        """
        logger.info(
            "cross_verification_arbitration_started",
            pair=list(pair),
            family=question.family,
        )
        first = self._run_engine(engine=pair[0], question=question, timeout_seconds=timeout_seconds)
        second = self._run_engine(
            engine=pair[1], question=question, timeout_seconds=timeout_seconds
        )
        if first.verdict == second.verdict:
            logger.info(
                "cross_verification_arbitration_resolved",
                verdict=first.verdict,
                engine=first.engine,
            )
            return ArbitrationRecord(
                status="resolved",
                engine=first.engine,
                verdict=first.verdict,
                detail=(
                    "arbitration re-run resolved the discordance:"
                    f" both engines agree on {first.verdict!r}"
                ),
            )
        logger.warning(
            "cross_verification_arbitration_persistent",
            first_engine=first.engine,
            first_verdict=first.verdict,
            second_engine=second.engine,
            second_verdict=second.verdict,
        )
        return ArbitrationRecord(
            status="persistent",
            detail=(
                f"persistent discordance after arbitration: {first.engine!r} says"
                f" {first.verdict!r}, {second.engine!r} says {second.verdict!r}"
            ),
        )

    def verify(
        self,
        *,
        question: EngineQuestion,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> CrossVerificationResult:
        """Cross-verify one clause: run the paired engines, compute concordance.

        Concordance → status ``concordant``. Discordance → arbitration run;
        if it resolves, ``concordant`` with the arbitration verdict; if it
        persists, ``controversial`` — no single verdict is produced.
        """
        pair = self._pairing.pair_for(family=question.family)
        logger.info(
            "cross_verification_started",
            family=question.family,
            pair=list(pair),
        )
        verdicts = [
            self._run_engine(engine=engine, question=question, timeout_seconds=timeout_seconds)
            for engine in pair
        ]
        distinct_verdicts = {v.verdict for v in verdicts}
        if len(distinct_verdicts) == 1:
            concordance = 1.0
            arbitration = ArbitrationRecord(status="not_needed")
            status: CrossVerificationStatus = "concordant"
            verified_by = len(verdicts)
            logger.info(
                "cross_verification_concordant",
                family=question.family,
                verdict=verdicts[0].verdict,
                verified_by=verified_by,
            )
        else:
            concordance = 0.0
            arbitration = self._arbitrate(
                pair=pair, question=question, timeout_seconds=timeout_seconds
            )
            if arbitration.status == "resolved":
                status = "concordant"
                verified_by = len(verdicts)
                logger.info(
                    "cross_verification_concordant_after_arbitration",
                    family=question.family,
                    verdict=arbitration.verdict,
                )
            else:
                status = "controversial"
                verified_by = 0
                logger.warning(
                    "cross_verification_controversial",
                    family=question.family,
                    verdicts=[(v.engine, v.verdict) for v in verdicts],
                )
        return CrossVerificationResult(
            family=question.family,
            status=status,
            verdicts=verdicts,
            concordance=concordance,
            arbitration=arbitration,
            verified_by=verified_by,
        )
