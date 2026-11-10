"""Driver running an engine inside its real docker compose service (spec §19.4).

The driver starts the compose service, waits for its healthcheck, posts the
formal question to the engine over loopback, and archives the normalized
output as a run (spec §12.5). The ``@engine`` test in ``tests/`` exercises
this against the real container: an engine failure is a test failure.
"""

import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
import structlog
from pydantic import ValidationError

from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    DUMMY_ENGINE_NAME,
    DUMMY_ENGINE_VERSION,
    DriverError,
    EngineName,
    EngineQuestion,
    EngineTimeoutError,
    EngineVersion,
    InvalidOutputError,
    NormalizedOutput,
    RunId,
    RunRecord,
    canonical_question_json,
    new_run_id,
    question_input_hash,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from petrichor.engines.archive import RunArchive

logger = structlog.get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DUMMY_SERVICE_NAME = "dummy"
DEFAULT_DUMMY_HOST_PORT = 18080
COMPOSE_START_TIMEOUT_SECONDS = 1200
"""Cold builds of real engine images (verifypn compiles C++ for ~10 min) exceed
the dummy's few seconds; 1200s covers a cold CI runner with headroom."""
CHECK_PATH = "/check"
DOCKER_BINARY = "docker"


class EngineDriver(Protocol):
    """The driver contract: a formal question in, an archived run out."""

    def run(self, *, question: EngineQuestion, timeout_seconds: int) -> RunRecord:
        """Execute a question on the engine and archive the run."""
        ...


def parse_engine_output(*, body: bytes) -> NormalizedOutput:
    """Parse and validate a raw engine answer into the normalized output."""
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidOutputError(f"engine answer is not valid JSON: {error}") from error
    try:
        return NormalizedOutput.model_validate(payload)
    except ValidationError as error:
        raise InvalidOutputError(f"engine answer is not a normalized output: {error}") from error


def build_run_record(  # noqa: PLR0913 - one keyword per archived run field (spec §12.5)
    *,
    question: EngineQuestion,
    output: NormalizedOutput,
    command: list[str],
    timeout_seconds: int,
    measured_seconds: float,
    archived_at: str,
    run_id: RunId,
    engine_name: EngineName = DUMMY_ENGINE_NAME,
    engine_version: EngineVersion = DUMMY_ENGINE_VERSION,
) -> RunRecord:
    """Assemble the archived run record of one driver execution (spec §12.5)."""
    return RunRecord(
        run_id=run_id,
        engine=engine_name,
        engine_version=engine_version,
        command=command,
        timeout_seconds=timeout_seconds,
        input_hash=question_input_hash(question=question),
        output=output,
        measured_seconds=measured_seconds,
        archived_at=archived_at,
    )


class ComposeEngineDriver:
    """Runs an engine in its real container through docker compose."""

    def __init__(
        self,
        *,
        archive: RunArchive,
        compose_file: Path = DEFAULT_COMPOSE_FILE,
        host_port: int = DEFAULT_DUMMY_HOST_PORT,
        service_name: str = DUMMY_SERVICE_NAME,
        engine_name: EngineName = DUMMY_ENGINE_NAME,
        engine_version: EngineVersion = DUMMY_ENGINE_VERSION,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Wire the driver to a compose file, an archive, a service and a host port."""
        docker = shutil.which(DOCKER_BINARY)
        if docker is None:
            raise DriverError(f"the {DOCKER_BINARY!r} binary was not found on PATH")
        self._docker = docker
        self._compose_file = compose_file
        self._archive = archive
        self._host_port = host_port
        self._service_name = service_name
        self._engine_name = engine_name
        self._engine_version = engine_version
        self._clock = clock or (lambda: datetime.now(UTC))

    def _compose(self, *arguments: str, timeout: int) -> list[str]:
        """Run one docker compose command; return the argv that was executed."""
        command = [self._docker, "compose", "-f", str(self._compose_file), *arguments]
        logger.debug("driver_compose_command", command=command)
        try:
            subprocess.run(  # noqa: S603 - fixed argv, no shell, binary resolved via which
                command, capture_output=True, check=True, timeout=timeout
            )
        except subprocess.TimeoutExpired as error:
            raise EngineTimeoutError(
                f"docker compose {' '.join(arguments)} exceeded {timeout}s"
            ) from error
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode("utf-8", errors="replace")
            raise DriverError(f"docker compose {' '.join(arguments)} failed: {detail}") from error
        return command

    def run(
        self, *, question: EngineQuestion, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    ) -> RunRecord:
        """Start the service, post the question, archive the run, stop the service."""
        started = time.monotonic()
        command = self._compose(
            "up",
            "--detach",
            "--wait",
            "--build",
            self._service_name,
            timeout=COMPOSE_START_TIMEOUT_SECONDS,
        )
        try:
            output = self._check(question=question, timeout_seconds=timeout_seconds)
        finally:
            try:
                self._compose("down", timeout=COMPOSE_START_TIMEOUT_SECONDS)
            except DriverError as error:
                logger.warning("driver_compose_down_failed", error=str(error))
        measured_seconds = time.monotonic() - started
        record = build_run_record(
            question=question,
            output=output,
            command=command,
            timeout_seconds=timeout_seconds,
            measured_seconds=measured_seconds,
            archived_at=self._clock().isoformat(),
            run_id=new_run_id(),
            engine_name=self._engine_name,
            engine_version=self._engine_version,
        )
        self._archive.append(record=record)
        logger.info(
            "driver_run_archived",
            run_id=record.run_id,
            engine=record.engine,
            verdict=record.output.verdict,
            measured_seconds=record.measured_seconds,
        )
        return record

    def _check(self, *, question: EngineQuestion, timeout_seconds: int) -> NormalizedOutput:
        """POST the question to the running engine and parse its answer."""
        url = f"http://127.0.0.1:{self._host_port}{CHECK_PATH}"
        body = canonical_question_json(question=question)
        logger.debug("driver_check_posted", url=url, timeout_seconds=timeout_seconds)
        try:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(
                    url, content=body.encode("utf-8"), headers={"Content-Type": "application/json"}
                )
        except httpx.TimeoutException as error:
            raise EngineTimeoutError(
                f"engine at {url} did not answer within {timeout_seconds}s"
            ) from error
        if response.status_code != HTTPStatus.OK.value:
            raise DriverError(f"engine at {url} answered {response.status_code}: {response.text}")
        return parse_engine_output(body=response.content)
