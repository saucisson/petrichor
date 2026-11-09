"""Composition root of the Petrichor HTTP API (ADR-0002).

Wires logging, settings, the model store and the WSGI app, then serves with
the stdlib `wsgiref` server - no adapter is constructed anywhere else.
Run with ``PETRICHOR_API_KEY=... python -m petrichor.main``.
"""

import logging
from wsgiref.simple_server import WSGIServer, make_server

import structlog

from petrichor.api import create_app
from petrichor.api.settings import ApiSettings
from petrichor.model_store import ModelStore

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080


def configure_logging() -> None:
    """Console rendering locally; the composition root is the only place that configures it."""
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer()],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
    )


def build_server(*, settings: ApiSettings, store: ModelStore) -> WSGIServer:
    """Assemble the WSGI server around the application."""
    app = create_app(settings=settings, store=store)
    return make_server(DEFAULT_HOST, DEFAULT_PORT, app)


def main() -> None:
    """Entry point: configure, build, serve."""
    configure_logging()
    logger = structlog.get_logger(__name__)
    settings = ApiSettings()
    server = build_server(settings=settings, store=ModelStore())
    logger.info("api_server_started", host=DEFAULT_HOST, port=DEFAULT_PORT)
    server.serve_forever()  # pragma: no cover - the serving loop is the entry point


if __name__ == "__main__":
    main()  # pragma: no cover
