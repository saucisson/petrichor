"""CheckRunner adapter bridging the checks API to a compose engine (issue #10, spec §5, §7).

Lives inside ``engines/`` on purpose: the verdict lock (spec §19.4) forbids
any module outside this package from importing it, so the API composition
receives this runner already built. The runner translates a `CheckQuestion`
into an `EngineQuestion` (the model travels in ``parameters["model"]``,
injected by the API layer) and maps the archived `RunRecord` to the explicit
`CheckCompleted` / `CheckFailed` outcomes - a driver failure is always an
explicit error, never a default verdict (spec §14.2).
"""

from typing import TYPE_CHECKING

import structlog

from petrichor.checks import (
    CheckCompleted,
    CheckFailed,
    CheckOutcome,
    CheckQuestion,
    ToolVersion,
)
from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    VERIFYPN_ENGINE_NAME,
    VERIFYPN_ENGINE_VERSION,
    DriverError,
    EngineQuestion,
    EngineTimeoutError,
)
from petrichor.engines.driver import DEFAULT_COMPOSE_FILE, ComposeEngineDriver

if TYPE_CHECKING:
    from pathlib import Path

    from petrichor.engines.archive import RunArchive

logger = structlog.get_logger(__name__)

DEFAULT_VERIFYPN_HOST_PORT = 18081
VERIFYPN_SERVICE_NAME = "verifypn"


class VerifypnCheckRunner:
    """A `CheckRunner` executing questions on the verifypn compose engine."""

    tool_version: ToolVersion = ToolVersion(VERIFYPN_ENGINE_VERSION)

    def __init__(
        self,
        *,
        archive: RunArchive,
        compose_file: Path = DEFAULT_COMPOSE_FILE,
        host_port: int = DEFAULT_VERIFYPN_HOST_PORT,
    ) -> None:
        """Wire the runner to a run archive and the verifypn compose service."""
        self._driver = ComposeEngineDriver(
            archive=archive,
            compose_file=compose_file,
            host_port=host_port,
            service_name=VERIFYPN_SERVICE_NAME,
            engine_name=VERIFYPN_ENGINE_NAME,
            engine_version=VERIFYPN_ENGINE_VERSION,
        )

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Execute one check question on verifypn within the timeout."""
        engine_question = EngineQuestion(
            family=question.family,
            formula=question.formula,
            pattern=question.kind,
            parameters=question.parameters,
        )
        try:
            record = self._driver.run(
                question=engine_question,
                timeout_seconds=timeout_seconds or DEFAULT_TIMEOUT_SECONDS,
            )
        except EngineTimeoutError as error:
            logger.warning(
                "runner_timeout", family=question.family, kind=question.kind, error=str(error)
            )
            return CheckFailed(kind="timeout", detail=str(error), command=[])
        except DriverError as error:
            logger.warning(
                "runner_engine_error",
                family=question.family,
                kind=question.kind,
                error=str(error),
            )
            return CheckFailed(kind="engine_error", detail=str(error), command=[])
        logger.info(
            "runner_run_completed",
            run_id=record.run_id,
            engine=record.engine,
            verdict=record.output.verdict,
        )
        return CheckCompleted(
            output=record.output.model_dump(mode="json"),
            command=record.command,
        )
