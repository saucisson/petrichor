"""Smoke test of the P0 jalon: end-to-end on Philosophes-3 (issue #10, spec §11, §19.5).

Scripted scenario: import Philosophes-3, ``POST /v1/checks/deadlock`` with
``EF DEADLOCK``, poll the run, read verdict + witness + cost from the run
store and the archive. Replaying the same check returns the same run and
leaves the journal unchanged (idempotence). Runs against the real verifypn
container: an engine failure is a CI failure, never a skip (spec §19.4).
"""

import json
import time
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.engines.api_runner import VerifypnCheckRunner
from petrichor.engines.archive import RunArchive
from petrichor.model_store import ModelStore
from petrichor.run_store import RunStore

FIXTURES = Path(__file__).parent / "fixtures"
TEST_API_KEY = "smoke-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
TERMINAL_POLL_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 0.2


def _wait_terminal(client: httpx.Client, run_id: str) -> dict[str, object]:
    """Poll GET /v1/runs/{rid} until the run leaves the running state."""
    deadline = time.monotonic() + TERMINAL_POLL_SECONDS
    while time.monotonic() < deadline:
        response = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS)
        assert response.status_code == 200
        run = response.json()
        if run["status"] != "running":
            return run
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} did not reach a terminal state in time")


@pytest.mark.engine
def test_p0_jalon_end_to_end_on_philosophes3(tmp_path: Path) -> None:
    """Import → EF DEADLOCK on the real verifypn engine → poll → read the archived run."""
    archive = RunArchive(directory=tmp_path / "runs")
    runner = VerifypnCheckRunner(archive=archive)
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(settings=settings, store=ModelStore(), run_store=RunStore(), runner=runner)
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")

    import_response = client.post(
        "/v1/models",
        content=(FIXTURES / "philosophes3-maquette.pnml").read_bytes(),
        headers=PNML_HEADERS,
    )
    assert import_response.status_code == 201
    model_id = import_response.json()["id"]

    check_response = client.post(
        "/v1/checks/deadlock",
        json={"model_id": model_id, "kind": "deadlock", "formula": "EF DEADLOCK"},
        headers=JSON_HEADERS,
    )
    assert check_response.status_code == 202
    run_id = check_response.json()["run_id"]
    assert check_response.headers["Location"] == f"/v1/runs/{run_id}"

    run = _wait_terminal(client, run_id)
    assert run["status"] == "done"
    output = run["output"]
    assert isinstance(output, dict)
    assert output["verdict"] == "satisfied"
    witness = output["witness"]
    assert isinstance(witness, dict)
    assert witness["kind"] == "trace"
    assert len(witness["steps"]) >= 2
    cost = output["cost"]
    assert isinstance(cost, dict)
    assert cost["states_explored"] > 0
    assert output["technique"] == "verifypn-4.4.2"

    records = archive.records()
    assert len(records) == 1
    # The engine-side archived run carries its own id and the question input
    # hash (spec §12.5); the API run is linked by (model, question) key.
    assert records[0].input_hash.startswith("sha256:")
    assert records[0].engine == "verifypn"
    assert records[0].output.verdict == "satisfied"
    assert run["input_hash"].startswith("sha256:")

    journal_before = json.dumps(records[0].model_dump(mode="json"), sort_keys=True)

    replay = client.post(
        "/v1/checks/deadlock",
        json={"model_id": model_id, "kind": "deadlock", "formula": "EF DEADLOCK"},
        headers=JSON_HEADERS,
    )
    assert replay.status_code == 202
    assert replay.json()["run_id"] == run_id
    # Replaying the same (model, question) returns the same run and leaves the
    # archive journal unchanged (idempotence, spec §5 and §12.5).
    assert len(archive.records()) == 1
    journal_after = json.dumps(archive.records()[0].model_dump(mode="json"), sort_keys=True)
    assert journal_after == journal_before
