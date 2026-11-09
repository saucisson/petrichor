"""Unit tests of the engines package: contract, dummy server, archive, driver helpers (issue #4).

Covers the normalized driver contract (question in → ``{verdict, witness,
cost, technique}`` out), the run archive format (spec §12.5), the compose
service compliance with spec §19.4, and the verdict lock: no module outside
``petrichor.engines`` may import the verdict-producing types.
"""

import ast
import json
import re
import threading
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

from petrichor.engines.archive import RUNS_FILE_NAME, RunArchive
from petrichor.engines.contract import (
    DEFAULT_TIMEOUT_SECONDS,
    DUMMY_ENGINE_IMAGE,
    DUMMY_ENGINE_NAME,
    DUMMY_ENGINE_VERSION,
    EngineQuestion,
    InvalidOutputError,
    NormalizedOutput,
    RunId,
    RunRecord,
    canonical_question_json,
    new_run_id,
    question_input_hash,
)
from petrichor.engines.driver import (
    DEFAULT_DUMMY_HOST_PORT,
    REPO_ROOT,
    build_run_record,
    parse_engine_output,
)
from petrichor.engines.dummy_server import (
    CHECK_PATH,
    HEALTH_PATH,
    DummyHandler,
    dummy_output,
    handle_check,
    handle_health,
)

COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DOCKERFILE = REPO_ROOT / "Dockerfile.dummy"
SRC_ROOT = REPO_ROOT / "src" / "petrichor"
VERDICT_LOCKED_NAMES = {"Verdict", "NormalizedOutput", "Witness", "WitnessStep", "Cost"}


def deadlock_question() -> EngineQuestion:
    """The canonical dummy question: EF DEADLOCK on the deadlock family."""
    return EngineQuestion(family="deadlock", formula="EF DEADLOCK", pattern="EF DEADLOCK")


def sample_output() -> NormalizedOutput:
    """A valid normalized output."""
    return NormalizedOutput.model_validate(dummy_output(tool_seconds=0.5))


def test_question_requires_a_known_family_and_a_formula() -> None:
    """A family outside the closed set or an empty formula is rejected."""
    with pytest.raises(ValidationError):
        EngineQuestion(family="model-checking", formula="EF DEADLOCK")
    with pytest.raises(ValidationError):
        EngineQuestion(family="deadlock", formula="")


def test_input_hash_is_stable_and_sensitive_to_the_formula() -> None:
    """Same question → same hash; a different formula → a different hash (§13)."""
    first = question_input_hash(question=deadlock_question())
    second = question_input_hash(question=deadlock_question())
    other = question_input_hash(question=EngineQuestion(family="deadlock", formula="EF deadlock"))
    assert first == second
    assert first != other
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", first)


def test_canonical_question_json_is_deterministic() -> None:
    """Key order does not leak into the canonical serialization."""
    question = EngineQuestion(family="deadlock", formula="EF DEADLOCK", parameters={"b": 2, "a": 1})
    reordered = EngineQuestion(
        family="deadlock", formula="EF DEADLOCK", parameters={"a": 1, "b": 2}
    )
    assert canonical_question_json(question=question) == canonical_question_json(question=reordered)


def test_new_run_id_format() -> None:
    """Run ids look like ``run_7c2a9f`` and are unique."""
    first, second = new_run_id(), new_run_id()
    assert re.fullmatch(r"run_[0-9a-f]{6}", first)
    assert first != second


def test_parse_engine_output_accepts_a_normalized_payload() -> None:
    """A well-formed engine answer parses into the normalized output."""
    body = json.dumps(dummy_output(tool_seconds=1.25)).encode("utf-8")
    output = parse_engine_output(body=body)
    assert output.verdict == "unknown"
    assert output.technique == "dummy"
    assert output.cost.tool_seconds == 1.25
    assert output.witness is not None and len(output.witness.steps) == 1


def test_parse_engine_output_rejects_garbage() -> None:
    """Non-JSON and wrongly-shaped answers raise InvalidOutputError."""
    with pytest.raises(InvalidOutputError):
        parse_engine_output(body=b"not json")
    with pytest.raises(InvalidOutputError):
        parse_engine_output(body=json.dumps({"verdict": "maybe"}).encode("utf-8"))


def test_handle_check_validates_the_question() -> None:
    """The container-side handler answers 200 on a question, 422 otherwise."""
    status, body = handle_check(
        body=canonical_question_json(question=deadlock_question()).encode("utf-8"), started=0.0
    )
    assert status == 200
    assert NormalizedOutput.model_validate(json.loads(body))
    assert handle_check(body=b"{", started=0.0)[0] == 422
    assert handle_check(body=b"{}", started=0.0)[0] == 422


def test_handle_health() -> None:
    """The healthcheck handler answers 200 with a JSON status."""
    status, body = handle_health()
    assert status == 200
    assert json.loads(body) == {"status": "ok"}


def test_dummy_http_server_serves_health_and_check_in_process() -> None:
    """The HTTP plumbing routes /health and /check to the pure handlers."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), DummyHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            health = client.get(HEALTH_PATH)
            check = client.post(
                CHECK_PATH,
                content=canonical_question_json(question=deadlock_question()).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            missing = client.get("/nope")
        assert health.status_code == 200
        assert check.status_code == 200
        assert NormalizedOutput.model_validate(check.json()).verdict == "unknown"
        assert missing.status_code == 404
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_run_archive_appends_and_reads_json_lines(tmp_path: Path) -> None:
    """Records are appended as JSON lines and read back in order (§12.5)."""
    archive = RunArchive(directory=tmp_path / "runs")
    assert archive.records() == []
    record = build_run_record(
        question=deadlock_question(),
        output=sample_output(),
        command=["docker", "compose", "up"],
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        measured_seconds=2.5,
        archived_at=datetime(2026, 11, 10, tzinfo=UTC).isoformat(),
        run_id=RunId("run_abc123"),
    )
    path = archive.append(record=record)
    assert path.name == RUNS_FILE_NAME
    assert len(archive) == 1
    assert archive.records() == [record]
    assert record.engine == DUMMY_ENGINE_NAME
    assert record.engine_version == DUMMY_ENGINE_VERSION
    assert record.input_hash == question_input_hash(question=deadlock_question())
    archive.append(record=record)
    assert len(archive) == 2


def test_build_run_record_wires_the_archive_fields() -> None:
    """The run record carries command, timeout, input hash, output and cost."""
    record = build_run_record(
        question=deadlock_question(),
        output=sample_output(),
        command=["docker", "compose", "up", "--detach", "--wait", "dummy"],
        timeout_seconds=12,
        measured_seconds=3.0,
        archived_at="2026-11-10T00:00:00+00:00",
        run_id=RunId("run_000001"),
    )
    assert isinstance(record, RunRecord)
    assert record.timeout_seconds == 12
    assert record.command[:2] == ["docker", "compose"]
    assert record.output.cost.tool_seconds == 0.5
    assert record.measured_seconds == 3.0


def test_compose_service_complies_with_spec_19_4() -> None:
    """The dummy service is pinned, healthchecked, non-privileged, loopback-only."""
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    assert re.search(r"^  dummy:$", compose, flags=re.MULTILINE)
    assert "healthcheck:" in compose and "test:" in compose
    assert "privileged: true" not in compose and "privileged:true" not in compose
    assert re.search(r'^\s+user: "(?!0)', compose, flags=re.MULTILINE)
    assert "read_only: true" in compose
    assert "cap_drop:" in compose and "- ALL" in compose
    assert "no-new-privileges:true" in compose
    assert f"image: {DUMMY_ENGINE_IMAGE}" in compose
    assert "latest" not in compose
    assert "127.0.0.1:" in compose
    assert f"${{ENGINE_TIMEOUT:-{DEFAULT_TIMEOUT_SECONDS}}}" in compose
    assert f"${{DUMMY_HOST_PORT:-{DEFAULT_DUMMY_HOST_PORT}}}" in compose


def test_dockerfile_pins_the_base_image_by_digest() -> None:
    """The locally built image rests on a digest-pinned base (empreinte)."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    from_line = next(line for line in lines if line.startswith("FROM "))
    assert re.fullmatch(r"FROM .+@sha256:[0-9a-f]{64}", from_line)
    assert "USER engine" in DOCKERFILE.read_text(encoding="utf-8")


def test_verdict_lock_no_module_outside_engines_imports_verdict_types() -> None:
    """Only petrichor.engines may produce verdict values (spec §19.4)."""
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if "engines" in path.relative_to(SRC_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("petrichor.engines")
            ):
                locked = VERDICT_LOCKED_NAMES.intersection(alias.name for alias in node.names)
                if locked:
                    offenders.append(f"{path}: {sorted(locked)}")
    assert offenders == []
