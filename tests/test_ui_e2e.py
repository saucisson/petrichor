"""E2E navigateur de la voie UI : friction F5 fermée (issue #128, spec §20.10).

Marked ``@ui``: a real Chromium (Playwright) drives the built SPA served by
the real API, itself wired to the real verifypn compose engine - the UI is
observed end to end, not only through component tests. The harness plays
only what the UI cannot: proposing the semantic profile (no HTTP endpoint,
spec §14) and posting the MC checks (the UI only reads runs). Everything
else - amorçage, arbitrages, édition de formule, convergence, témoin,
contrat, dossier de preuve, export, bascule de langue - goes through the
browser (src/petrichor/api/ui/e2e/parcours.spec.ts).

Leçon CI de P39 appliquée : les images compose sont pré-buildées hors watchdog
et ENGINE_TIMEOUT est relevé - un run lent est un verdict lent, jamais une
erreur de watchdog. Rejouable par ``make ui-e2e``.
"""

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING
from wsgiref.simple_server import WSGIServer, make_server

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx
import pytest
import structlog
from pydantic import SecretStr
from test_e2e_p2 import (
    AUTH_HEADERS,
    CONTRACT_HEADERS,
    E2E_ENGINE_TIMEOUT,
    JSON_HEADERS,
    TEST_API_KEY,
    _prebuild_engine_images,
    sample_profile,
)

from petrichor.api.app import WSGIApp, create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.convergence import ConvergenceStore
from petrichor.engines.api_runner import VerifypnCheckRunner
from petrichor.engines.archive import RunArchive
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.question_edits import FormulaEditStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionId, SessionStore
from petrichor.witness import WitnessStore

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "src" / "petrichor" / "api" / "ui"
# The maquette net: its canonical hash is the one the golden LLM corpus is
# keyed on, so inverse readings come back non-degraded (provenance: llm).
PNML_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "philosophes3-maquette.pnml"

logger = structlog.get_logger(__name__)

UI_HOST = "127.0.0.1"
UI_PORT = 18090
UI_BASE_URL = f"http://{UI_HOST}:{UI_PORT}/ui/"

SESSION_APPEAR_TIMEOUT = 120.0
CONTRACT_QUESTIONS_TIMEOUT = 120.0
CHECK_TERMINAL_TIMEOUT = 360.0
PLAYWRIGHT_TIMEOUT = 900.0


def _build_ui_app(*, tmp_path: Path, arbitrator: ProfileArbitrator) -> WSGIApp:
    """The real API wired to the real verifypn compose engine, serving /ui/."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY), check_timeout_seconds=300)
    return create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=VerifypnCheckRunner(archive=RunArchive(directory=tmp_path / "runs-verifypn")),
        session_store=SessionStore(directory=tmp_path / "sessions"),
        arbitrator=arbitrator,
        arbitration_store=ArbitrationStore(directory=tmp_path / "arbitrations"),
        convergence_store=ConvergenceStore(directory=tmp_path / "convergences"),
        witness_store=WitnessStore(directory=tmp_path / "witnesses"),
        llm_provider=DeterministicMockProvider(),
        edit_store=FormulaEditStore(directory=tmp_path / "formula_edits"),
    )


def _wait_for(*, deadline: float, stop: threading.Event, probe: Callable[[], object]) -> object:
    """Poll ``probe`` until it returns a truthy value or the deadline expires."""
    while time.monotonic() < deadline and not stop.is_set():
        value = probe()
        if value:
            return value
        stop.wait(0.5)
    raise AssertionError("the browser-driven scenario did not reach the expected state in time")


def _harness_side(
    *, client: httpx.Client, arbitrator: ProfileArbitrator, stop: threading.Event
) -> None:
    """Server-side parts the UI cannot do: propose the profile, post the checks.

    The browser creates the session through the UI (amorçage); once it
    appears, the semantic profile is proposed (spec §14 has no HTTP endpoint
    for it), then the MC checks are posted on the real engine so the
    Verifications step has runs and witnesses to show. The ``stop`` event lets
    the test end the thread cleanly before the client closes.
    """
    try:
        _harness_side_inner(client=client, arbitrator=arbitrator, stop=stop)
    except Exception:
        if stop.is_set():
            logger.info("ui_e2e_harness_stopped")
        else:
            raise


def _harness_side_inner(
    *, client: httpx.Client, arbitrator: ProfileArbitrator, stop: threading.Event
) -> None:
    deadline = time.monotonic() + SESSION_APPEAR_TIMEOUT
    session_id = _wait_for(
        deadline=deadline,
        stop=stop,
        probe=lambda: next(
            (
                session["session_id"]
                for session in client.get("/v1/sessions", headers=AUTH_HEADERS).json()["sessions"]
            ),
            None,
        ),
    )
    arbitrator.propose(session_id=SessionId(session_id), profile=sample_profile())
    deadline = time.monotonic() + CONTRACT_QUESTIONS_TIMEOUT
    questions = _wait_for(
        deadline=deadline,
        stop=stop,
        probe=lambda: (
            client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS)
            .json()
            .get("questions")
            or None
        ),
    )
    model_id = client.get(f"/v1/sessions/{session_id}", headers=AUTH_HEADERS).json()["model_id"]
    for question in [q for q in questions if q["triage"] == "MC"][:2]:
        posted = client.post(
            f"/v1/checks/{question['family']}",
            json={
                "model_id": model_id,
                "kind": question["kind"],
                "formula": question["formula"],
                "session_id": session_id,
                "parameters": {"tool": "verifypn"},
            },
            headers=JSON_HEADERS,
        )
        assert posted.status_code == 202
        run_id = posted.json()["run_id"]
        deadline = time.monotonic() + CHECK_TERMINAL_TIMEOUT
        while time.monotonic() < deadline and not stop.is_set():
            run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
            if run["status"] != "running":
                break
            stop.wait(0.5)
        assert run["status"] == "done", f"verifypn run failed: {run.get('error')}"


@pytest.mark.ui
def test_ui_e2e_full_parcours_in_a_real_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5: the UI observed end to end in a real browser on the real compose (issue #128)."""
    node = shutil.which("node")
    assert node is not None, "node must be on PATH for a @ui test (see .node-version, ADR-0101)"
    monkeypatch.setenv("ENGINE_TIMEOUT", E2E_ENGINE_TIMEOUT)
    _prebuild_engine_images()

    arbitrator = ProfileArbitrator()
    app = _build_ui_app(tmp_path=tmp_path, arbitrator=arbitrator)
    server: WSGIServer = make_server(UI_HOST, UI_PORT, app)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with httpx.Client(base_url=f"http://{UI_HOST}:{UI_PORT}") as client:
            stop = threading.Event()
            harness_thread = threading.Thread(
                target=_harness_side,
                kwargs={"client": client, "arbitrator": arbitrator, "stop": stop},
                daemon=True,
            )
            harness_thread.start()
            environment = {
                **os.environ,
                "PATH": os.pathsep.join([str(Path(node).parent), os.environ.get("PATH", "")]),
                "PETRICHOR_UI_BASE_URL": UI_BASE_URL,
                "PETRICHOR_UI_API_KEY": TEST_API_KEY,
                "PETRICHOR_PNML_FIXTURE": str(PNML_FIXTURE),
            }
            result = subprocess.run(  # noqa: S603 - fixed argv, no shell
                [node, str(UI_DIR / "node_modules" / "@playwright" / "test" / "cli.js"), "test"],
                cwd=UI_DIR,
                env=environment,
                capture_output=True,
                text=True,
                timeout=PLAYWRIGHT_TIMEOUT,
                check=False,
            )
            stop.set()
            harness_thread.join(timeout=30)
            if result.returncode != 0:
                logger.warning(
                    "playwright_parcours_failed",
                    stdout_tail=result.stdout[-8000:],
                    stderr_tail=result.stderr[-4000:],
                )
            assert result.returncode == 0, "the Playwright parcours failed (trace in test-results/)"
    finally:
        server.shutdown()
        server_thread.join(timeout=10)
        server.server_close()
