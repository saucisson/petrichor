"""@engine test of the dummy driver in its real container (issue #4, spec §19.4).

The driver starts the compose service, waits for its healthcheck, posts the
formal question and archives the run. This test runs the real container: an
engine failure is a CI failure, never a skip.
"""

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from petrichor.engines.archive import RunArchive
from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    DUMMY_ENGINE_NAME,
    DUMMY_ENGINE_VERSION,
    EngineQuestion,
    question_input_hash,
)
from petrichor.engines.driver import ComposeEngineDriver


@pytest.mark.engine
def test_dummy_driver_answers_in_the_real_container(tmp_path: Path) -> None:
    """A dummy run produces verdict + witness + cost, archived as a run (§12.5)."""
    archive = RunArchive(directory=tmp_path / "runs")
    driver = ComposeEngineDriver(archive=archive)
    question = EngineQuestion(family="deadlock", formula="EF DEADLOCK", pattern="EF DEADLOCK")

    record = driver.run(question=question, timeout_seconds=DEFAULT_TIMEOUT_SECONDS)

    assert record.engine == DUMMY_ENGINE_NAME
    assert record.engine_version == DUMMY_ENGINE_VERSION
    assert record.output.verdict == "unknown"
    assert record.output.witness is not None
    assert len(record.output.witness.steps) == 1
    assert record.output.cost.tool_seconds >= 0
    assert record.output.technique == "dummy"
    assert record.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert record.input_hash == question_input_hash(question=question)
    assert record.command[:3] == ["docker", "compose", "-f"] or "docker" in record.command[0]
    assert record.measured_seconds >= record.output.cost.tool_seconds
    assert len(archive) == 1
    assert archive.records()[0].run_id == record.run_id
