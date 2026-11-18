"""Composition root of the Petrichor HTTP API (ADR-0002).

Wires logging, settings, the model store, the run store and the WSGI app,
then serves with the stdlib `wsgiref` server - no adapter is constructed
anywhere else. Run with ``PETRICHOR_API_KEY=... python -m petrichor.main``.

No check runner is wired yet: the verdict lock (spec §19.4) forbids any
module outside ``petrichor.engines`` from importing that package, so the
real engine adapters land with P09+; until then ``POST /v1/checks/{family}``
answers ``503 runner_not_configured``.
"""

import logging
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

import structlog

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.convergence import ConvergenceStore
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore
from petrichor.witness import WitnessStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_SESSIONS_DIRECTORY = ".petrichor/sessions"
DEFAULT_ARBITRATIONS_DIRECTORY = ".petrichor/arbitrations"
DEFAULT_CONVERGENCES_DIRECTORY = ".petrichor/convergences"
DEFAULT_WITNESSES_DIRECTORY = ".petrichor/witnesses"


def configure_logging() -> None:
    """Console rendering locally; the composition root is the only place that configures it."""
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
    )


def build_server(  # noqa: PLR0913 - one keyword per wired dependency (ADR-0003)
    *,
    settings: ApiSettings,
    store: ModelStore,
    run_store: RunStore,
    session_store: SessionStore,
    arbitrator: ProfileArbitrator,
    arbitration_store: ArbitrationStore,
    convergence_store: ConvergenceStore,
    witness_store: WitnessStore,
    llm_provider: DeterministicMockProvider,
) -> WSGIServer:
    """Assemble the WSGI server around the application."""
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=None,
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=arbitration_store,
        convergence_store=convergence_store,
        witness_store=witness_store,
        llm_provider=llm_provider,
    )
    return make_server(DEFAULT_HOST, DEFAULT_PORT, app)


def main() -> None:
    """Entry point: configure, build, serve."""
    configure_logging()
    logger = structlog.get_logger(__name__)
    settings = ApiSettings()
    session_store = SessionStore(directory=Path(DEFAULT_SESSIONS_DIRECTORY))
    arbitrator = ProfileArbitrator()
    arbitration_store = ArbitrationStore(directory=Path(DEFAULT_ARBITRATIONS_DIRECTORY))
    convergence_store = ConvergenceStore(directory=Path(DEFAULT_CONVERGENCES_DIRECTORY))
    witness_store = WitnessStore(directory=Path(DEFAULT_WITNESSES_DIRECTORY))
    server = build_server(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=arbitration_store,
        convergence_store=convergence_store,
        witness_store=witness_store,
        llm_provider=DeterministicMockProvider(),
    )
    logger.info("api_server_started", host=DEFAULT_HOST, port=DEFAULT_PORT)
    server.serve_forever()  # pragma: no cover - the serving loop is the entry point


if __name__ == "__main__":
    main()  # pragma: no cover
