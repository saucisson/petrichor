"""HTTP entry point of the dummy engine container (spec §19.4).

Standard library only, on purpose: the engine image (``Dockerfile.dummy``)
ships no third-party dependency, so this module must not import pydantic or
structlog. The API-side tests import the pure handlers from here; the
container runs ``python -m petrichor.engines.dummy_server``.
"""

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None
"""Any JSON-compatible value (local alias: this module must stay stdlib-only)."""

DUMMY_TECHNIQUE = "dummy"
DUMMY_VERDICT = "unknown"
SERVER_HOST = "0.0.0.0"  # noqa: S104 - the container must accept the loopback-published port
SERVER_PORT = 8080
HEALTH_PATH = "/health"
CHECK_PATH = "/check"
ENGINE_TIMEOUT_VARIABLE = "ENGINE_TIMEOUT"
DEFAULT_ENGINE_TIMEOUT_SECONDS = 30
MAX_BODY_BYTES = 1_048_576


def dummy_output(*, tool_seconds: float) -> dict[str, Json]:
    """The deterministic normalized output of the dummy engine.

    The dummy makes no semantic claim (``verdict: unknown``): it exists to
    exercise the driver contract end to end - verdict, witness, cost and
    technique are all present in the normalized shape.
    """
    return {
        "verdict": DUMMY_VERDICT,
        "witness": {
            "kind": "trace",
            "cyclic": False,
            "back_edge": None,
            "steps": [{"index": 0, "marking": [], "fired": None}],
        },
        "cost": {"tool_seconds": tool_seconds, "states_explored": 0},
        "technique": DUMMY_TECHNIQUE,
    }


def handle_health() -> tuple[int, bytes]:
    """Answer the compose healthcheck."""
    return 200, json.dumps({"status": "ok"}).encode("utf-8")


def handle_check(*, body: bytes, started: float) -> tuple[int, bytes]:
    """Validate a question payload and answer with the normalized output."""
    try:
        payload: Json = json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return 422, json.dumps({"detail": "body is not valid JSON"}).encode("utf-8")
    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("family"), str)
        or not isinstance(payload.get("formula"), str)
        or not payload["formula"]
    ):
        detail = json.dumps({"detail": "question needs a family and a non-empty formula"})
        return 422, detail.encode("utf-8")
    tool_seconds = max(0.0, time.monotonic() - started)
    output = dummy_output(tool_seconds=tool_seconds)
    return 200, json.dumps(output, sort_keys=True, separators=(",", ":")).encode("utf-8")


class DummyHandler(BaseHTTPRequestHandler):
    """Routes /health and /check to the pure handlers."""

    server_version = "petrichor-dummy"

    def do_GET(self) -> None:
        """Serve the healthcheck endpoint."""
        if self.path != HEALTH_PATH:
            self._answer(404, json.dumps({"detail": "not found"}).encode("utf-8"))
            return
        self._answer(*handle_health())

    def do_POST(self) -> None:
        """Serve the check endpoint with the engine timeout on the socket."""
        if self.path != CHECK_PATH:
            self._answer(404, json.dumps({"detail": "not found"}).encode("utf-8"))
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            self._answer(413, json.dumps({"detail": "body too large"}).encode("utf-8"))
            return
        body = self.rfile.read(length)
        self._answer(*handle_check(body=body, started=time.monotonic()))

    def _answer(self, status: int, body: bytes) -> None:
        """Write one JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Route http.server's access log into logging (no stdout writes, ADR-0005)."""
        logger.debug("dummy_engine_request", extra={"message": format % args})


def main() -> None:  # pragma: no cover - the container serving loop
    """Serve the dummy engine until the container stops."""
    timeout = float(os.environ.get(ENGINE_TIMEOUT_VARIABLE, DEFAULT_ENGINE_TIMEOUT_SECONDS))
    DummyHandler.timeout = timeout
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), DummyHandler)
    server.daemon_threads = True
    logger.info(
        "dummy_engine_listening",
        extra={"host": SERVER_HOST, "port": SERVER_PORT, "timeout": timeout},
    )
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
