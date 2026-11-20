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
import os
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

import structlog

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.catalog_sources import (
    DEFAULT_CONFIG_PATH,
    CatalogService,
    load_catalog_config,
)
from petrichor.convergence import ConvergenceStore
from petrichor.llm.mistral import MistralProvider
from petrichor.llm.provider import DeterministicMockProvider, LLMProvider
from petrichor.model_store import ModelStore
from petrichor.profile import ProfileArbitrator
from petrichor.question_edits import FormulaEditStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore
from petrichor.witness import WitnessStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
HOST_ENVIRONMENT_VARIABLE = "PETRICHOR_HOST"
PORT_ENVIRONMENT_VARIABLE = "PETRICHOR_PORT"
"""Container deployment (ADR-0102): the API listens on 0.0.0.0 inside the
container so the published port can reach it; locally it stays on loopback."""
DEFAULT_SESSIONS_DIRECTORY = ".petrichor/sessions"
DEFAULT_ARBITRATIONS_DIRECTORY = ".petrichor/arbitrations"
DEFAULT_CONVERGENCES_DIRECTORY = ".petrichor/convergences"
DEFAULT_WITNESSES_DIRECTORY = ".petrichor/witnesses"
DEFAULT_FORMULA_EDITS_DIRECTORY = ".petrichor/formula_edits"
DEFAULT_CATALOG_CACHE_DIRECTORY = ".petrichor/catalog-cache"


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
    llm_provider: LLMProvider,
    edit_store: FormulaEditStore,
    catalog: CatalogService | None = None,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
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
        edit_store=edit_store,
        catalog=catalog,
    )
    return make_server(host, port, app)


def select_llm_provider(*, settings: ApiSettings) -> LLMProvider:
    """Select the LLM provider by configuration (spec §8, issue #36).

    ``mock`` (default) serves the golden corpus; ``mistral`` calls the real
    API and fails at startup when the key is missing - never open by default.
    """
    if settings.llm_provider == "mistral":
        if settings.mistral_api_key is None:
            raise SystemExit("PETRICHOR_LLM_PROVIDER=mistral requires MISTRAL_API_KEY to be set.")
        return MistralProvider(api_key=settings.mistral_api_key, model=settings.llm_model)
    return DeterministicMockProvider()


class ConfigurationError(RuntimeError):
    """A typed startup error for invalid environment configuration (ADR-0102)."""


def _parse_port(*, raw_value: str) -> int:
    """Parse PETRICHOR_PORT, raising a typed error on a non-integer value."""
    try:
        return int(raw_value)
    except ValueError as error:
        raise ConfigurationError(
            f"{PORT_ENVIRONMENT_VARIABLE}={raw_value!r} is not a valid port number."
        ) from error


def main() -> None:
    """Entry point: configure, build, serve."""
    configure_logging()
    logger = structlog.get_logger(__name__)
    host = os.environ.get(HOST_ENVIRONMENT_VARIABLE, DEFAULT_HOST)
    port = _parse_port(raw_value=os.environ.get(PORT_ENVIRONMENT_VARIABLE, str(DEFAULT_PORT)))
    settings = ApiSettings()
    session_store = SessionStore(directory=Path(DEFAULT_SESSIONS_DIRECTORY))
    arbitrator = ProfileArbitrator()
    arbitration_store = ArbitrationStore(directory=Path(DEFAULT_ARBITRATIONS_DIRECTORY))
    convergence_store = ConvergenceStore(directory=Path(DEFAULT_CONVERGENCES_DIRECTORY))
    witness_store = WitnessStore(directory=Path(DEFAULT_WITNESSES_DIRECTORY))
    edit_store = FormulaEditStore(directory=Path(DEFAULT_FORMULA_EDITS_DIRECTORY))
    catalog_config = load_catalog_config(path=DEFAULT_CONFIG_PATH)
    catalog = CatalogService(
        config=catalog_config, cache_directory=Path(DEFAULT_CATALOG_CACHE_DIRECTORY)
    )
    catalog.start_background_refresh()
    server = build_server(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        session_store=session_store,
        arbitrator=arbitrator,
        arbitration_store=arbitration_store,
        convergence_store=convergence_store,
        witness_store=witness_store,
        llm_provider=select_llm_provider(settings=settings),
        edit_store=edit_store,
        catalog=catalog,
        host=host,
        port=port,
    )
    logger.info("api_server_started", host=host, port=port)
    server.serve_forever()  # pragma: no cover - the serving loop is the entry point


if __name__ == "__main__":
    main()  # pragma: no cover
