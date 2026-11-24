"""HTTP entry point of the SMPT engine container (spec §19.4, §18.1; issue #26).

Standard library only, on purpose: the engine image ships no third-party
dependency, so this module must not import pydantic or structlog. The
API-side tests import the pure handlers from here; the container runs
``python -m petrichor.engines.smpt_server``.

SMPT (N. Amat, Dal Zilio, Le Botlan — LAAS) is the third technological
differential of spec §17.4: reachability by SMT (Z3) with SMT witnesses, and
polyhedral invariants (generalized invariants via polyhedral abstraction).
It covers **no CTL/LTL family** (spec §17.2): only the ``reachability``
family is accepted on ``/check``, everything else is a 422.

Endpoints:
- ``/health``: compose healthcheck.
- ``/check``: one reachability question; the model travels in
  ``parameters["model"]`` as the canonical `petri+json` document, exported
  to PNML; the formula is passed as the SMT query file. The verdict is read
  from the ``FORMULA <file> TRUE|FALSE`` line on stdout (MAYBE maps to
  ``unknown``); the XML trace on stderr is replayed on the model into the
  §16 witness.
- ``/invariants``: the polyhedral invariants of the model, normalized in a
  format **distinct from the mini-engine semi-flows** (spec §18.1): a
  semi-flow is a non-negative integer vector over all places, a polyhedral
  invariant is a rational coefficient vector over a named place subset with
  a bound, and its coefficients may be negative or fractional (serialized
  as an int when integral, else as the canonical ``"p/q"`` string).
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

from petrichor.engines.its_tools_server import detect_cyclicity, replay_trace
from petrichor.engines.pnml_export import PnmlExportError, export_pnml

logger = logging.getLogger(__name__)

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None
"""Any JSON-compatible value (local alias: this module must stay stdlib-only)."""

SMPT_VERSION = "1.0"
"""The pinned SMPT release this image is built from (Dockerfile.smpt)."""

SMPT_TECHNIQUE = f"smpt-{SMPT_VERSION}"
SMPT_BINARY = os.environ.get("SMPT_BINARY", "/usr/local/bin/smpt")
SERVER_HOST = "0.0.0.0"  # noqa: S104 - the container must accept the loopback-published port
SERVER_PORT = 8080
HEALTH_PATH = "/health"
CHECK_PATH = "/check"
INVARIANTS_PATH = "/invariants"
ENGINE_TIMEOUT_VARIABLE = "ENGINE_TIMEOUT"
DEFAULT_ENGINE_TIMEOUT_SECONDS = 30
MAX_BODY_BYTES = 1_048_576
MODEL_PARAMETER = "model"
SUPPORTED_FAMILIES = frozenset({"reachability"})
"""SMPT covers reachability only - no CTL/LTL family (spec §17.2)."""
FORMULA_VERDICT_PATTERN = re.compile(r"FORMULA \S+ (TRUE|FALSE)\b")
MAYBE_PATTERN = re.compile(r"Query is MAYBE satisfied")
STATES_EXPLORED_PATTERN = re.compile(r"States: (\d+)")
TRACE_BLOCK_PATTERN = re.compile(r"<trace>(.*?)</trace>", re.DOTALL)
TRACE_TRANSITION_PATTERN = re.compile(r'<transition id="([^"]+)"')
INVARIANT_LINE_PATTERN = re.compile(r"INVARIANT places=(\S+) coeffs=(\S+) bound=(-?\d+)")
MODEL_FILE_NAME = "model.pnml"
QUERY_FILE_NAME = "query.smt2"
TEMP_DIR_PREFIX = "smpt-"
TRACE_XML_ARGUMENT = "2"
"""SMPT ``-t 2``: emit the witness trace as XML on stderr."""
CHECK_ARGUMENTS = ["-q", QUERY_FILE_NAME, "-t", TRACE_XML_ARGUMENT]
"""The SMPT argv of one reachability check (query file + XML trace)."""
INVARIANTS_ARGUMENTS = ["--invariants"]
"""The SMPT argv of one polyhedral-invariant computation."""


class QuestionError(Exception):
    """The question payload does not conform to the contract."""


Verdict = Literal["satisfied", "violated", "unknown"]
"""The closed set of normalized verdicts (ADR-0006)."""


def parse_verdict(*, stdout: str) -> Verdict:
    """The normalized verdict from the SMPT stdout."""
    if MAYBE_PATTERN.search(stdout):
        return "unknown"
    match = FORMULA_VERDICT_PATTERN.search(stdout)
    if match is None:
        raise QuestionError("SMPT stdout carries no FORMULA verdict line.")
    return "satisfied" if match.group(1) == "TRUE" else "violated"


def parse_states_explored(*, stdout: str) -> int:
    """The explored-state count from the SMPT statistics, 0 if absent."""
    match = STATES_EXPLORED_PATTERN.search(stdout)
    return int(match.group(1)) if match else 0


def parse_trace_transitions(*, stderr: str) -> list[str]:
    """The ordered fired-transition ids of the XML trace on stderr."""
    block = TRACE_BLOCK_PATTERN.search(stderr)
    if block is None:
        return []
    return TRACE_TRANSITION_PATTERN.findall(block.group(1))


def _parse_coefficient(*, text: str) -> Json:
    """One rational coefficient: int when integral, else the canonical "p/q" string."""
    try:
        value = Fraction(text)
    except (ValueError, ZeroDivisionError) as error:
        raise QuestionError(f"Malformed coefficient {text!r} in an INVARIANT line.") from error
    if value.denominator == 1:
        return value.numerator
    return f"{value.numerator}/{value.denominator}"


def parse_polyhedral_invariants(*, stdout: str) -> list[Json]:
    """The normalized polyhedral invariants from the SMPT stdout.

    Each ``INVARIANT places=p1,p2 coeffs=c1,c2 bound=b`` line becomes a
    ``polyhedral_invariant`` object: rational coefficients over a named place
    subset with a bound. Unlike the mini-engine semi-flows (non-negative
    integer vectors over all places), coefficients may be negative or
    fractional here; a coefficient is serialized as an int when integral,
    else as the canonical ``"p/q"`` string.
    """
    invariants: list[Json] = []
    for match in INVARIANT_LINE_PATTERN.finditer(stdout):
        places: list[Json] = [name for name in match.group(1).split(",") if name]
        coefficients: list[Json] = [
            _parse_coefficient(text=value) for value in match.group(2).split(",") if value
        ]
        if not places or len(places) != len(coefficients):
            raise QuestionError(f"Malformed INVARIANT line: {match.group(0)!r}")
        invariants.append(
            {
                "kind": "polyhedral_invariant",
                "places": places,
                "coefficients": coefficients,
                "bound": int(match.group(3)),
            }
        )
    return invariants


def build_normalized_output(
    *,
    stdout: str,
    stderr: str,
    model: dict[str, Json],
    tool_seconds: float,
) -> dict[str, Json]:
    """Assemble the normalized driver output from one SMPT reachability execution."""
    verdict = parse_verdict(stdout=stdout)
    fired = parse_trace_transitions(stderr=stderr)
    witness: dict[str, Json] | None = None
    if fired:
        steps = replay_trace(model=model, fired=fired)
        cyclic, back_edge = detect_cyclicity(steps=steps)
        if verdict == "satisfied":
            witness = {
                "kind": "trace",
                "cyclic": cyclic,
                "back_edge": back_edge,
                "steps": steps,
            }
        else:
            # A violated verdict with a trace is a counterexample (spec §16)
            witness = {
                "kind": "counterexample",
                "cyclic": cyclic,
                "back_edge": back_edge,
                "steps": steps,
            }
    return {
        "verdict": verdict,
        "witness": witness,
        "cost": {
            "tool_seconds": tool_seconds,
            "states_explored": parse_states_explored(stdout=stdout),
        },
        "technique": SMPT_TECHNIQUE,
    }


def build_invariants_output(*, stdout: str, tool_seconds: float) -> dict[str, Json]:
    """Assemble the normalized polyhedral-invariants output (spec §18.1, issue #26)."""
    return {
        "invariants": parse_polyhedral_invariants(stdout=stdout),
        "cost": {
            "tool_seconds": tool_seconds,
            "states_explored": parse_states_explored(stdout=stdout),
        },
        "technique": SMPT_TECHNIQUE,
    }


def run_smpt(
    *,
    model_pnml: str,
    arguments: list[str],
    timeout_seconds: int,
    query: str | None = None,
    binary: str | None = None,
) -> tuple[int, str, str, float]:
    """Run one SMPT execution in a temp dir; return (exit, stdout, stderr, seconds)."""
    executable = binary or os.environ.get("SMPT_BINARY", SMPT_BINARY)
    with tempfile.TemporaryDirectory(prefix=TEMP_DIR_PREFIX) as workdir:
        model_path = Path(workdir) / MODEL_FILE_NAME
        model_path.write_text(model_pnml, encoding="utf-8")
        if query is not None:
            (Path(workdir) / QUERY_FILE_NAME).write_text(query + "\n", encoding="utf-8")
        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [executable, str(model_path), *arguments],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        measured = time.monotonic() - started
    return completed.returncode, completed.stdout, completed.stderr, measured


def handle_health() -> tuple[int, bytes]:
    """Answer the compose healthcheck."""
    return 200, json.dumps({"status": "ok"}).encode("utf-8")


class HttpError(Exception):
    """An HTTP error response of the engine server, with its status and detail."""

    def __init__(self, *, status: int, detail: str) -> None:
        """Carry one status + detail pair."""
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _parse_payload(*, body: bytes) -> dict[str, Json]:
    """Decode the JSON body and require a top-level object."""
    try:
        payload: Json = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HttpError(status=422, detail="body is not valid JSON") from error
    if not isinstance(payload, dict):
        raise HttpError(status=422, detail="question must be a JSON object")
    return payload


def _model_of(*, payload: dict[str, Json]) -> dict[str, Json]:
    """The canonical model carried in parameters, validated."""
    parameters = payload.get("parameters", {})
    model = parameters.get(MODEL_PARAMETER) if isinstance(parameters, dict) else None
    if not isinstance(model, dict):
        raise HttpError(
            status=422, detail=f"parameters.{MODEL_PARAMETER} must be the canonical model"
        )
    return model


def _parse_model(*, body: bytes) -> dict[str, Json]:
    """Validate the payload and return the canonical model from parameters."""
    return _model_of(payload=_parse_payload(body=body))


def _parse_question(*, body: bytes) -> tuple[dict[str, Json], str, str]:
    """Validate the question payload; return (model, family, formula)."""
    payload = _parse_payload(body=body)
    model = _model_of(payload=payload)
    family = payload.get("family")
    formula = payload.get("formula")
    if not isinstance(family, str) or not isinstance(formula, str) or not formula:
        raise HttpError(status=422, detail="question needs a family and a non-empty formula")
    if family not in SUPPORTED_FAMILIES:
        raise HttpError(
            status=422,
            detail=(
                f"family {family!r} is outside the SMPT scope {sorted(SUPPORTED_FAMILIES)}"
                " (no CTL/LTL, spec §17.2)"
            ),
        )
    return model, family, formula


def _run_smpt_or_raise(
    *, model_pnml: str, arguments: list[str], timeout_seconds: int, query: str | None = None
) -> tuple[str, str, float]:
    """Run SMPT and map failures to typed HTTP errors; return (stdout, stderr, seconds)."""
    try:
        returncode, stdout, stderr, tool_seconds = run_smpt(
            model_pnml=model_pnml,
            arguments=arguments,
            timeout_seconds=timeout_seconds,
            query=query,
        )
    except subprocess.TimeoutExpired as error:
        detail = f"SMPT exceeded {timeout_seconds}s"
        raise HttpError(status=504, detail=detail) from error
    except OSError as error:
        detail = f"SMPT could not be executed: {error}"
        raise HttpError(status=500, detail=detail) from error
    logger.debug(
        "smpt_run_completed",
        extra={"returncode": returncode, "tool_seconds": tool_seconds},
    )
    if returncode != 0:
        detail = f"SMPT failed: {stderr.strip()[:500]}"
        raise HttpError(status=500, detail=detail)
    return stdout, stderr, tool_seconds


def handle_check(*, body: bytes, timeout_seconds: int) -> tuple[int, bytes]:
    """Validate a reachability question, run SMPT, answer with the normalized output."""
    logger.debug("smpt_check_received", extra={"body_bytes": len(body)})
    try:
        model, family, formula = _parse_question(body=body)
        logger.debug("smpt_question_parsed", extra={"family": family})
        try:
            model_pnml = export_pnml(model=model)
        except PnmlExportError as error:
            raise HttpError(status=422, detail=str(error)) from error
        stdout, stderr, tool_seconds = _run_smpt_or_raise(
            model_pnml=model_pnml,
            arguments=CHECK_ARGUMENTS,
            timeout_seconds=timeout_seconds,
            query=formula,
        )
        try:
            output = build_normalized_output(
                stdout=stdout, stderr=stderr, model=model, tool_seconds=tool_seconds
            )
        except QuestionError as error:
            detail = f"SMPT output invalid: {error}"
            raise HttpError(status=500, detail=detail) from error
    except HttpError as error:
        return error.status, json.dumps({"detail": error.detail}).encode("utf-8")
    return 200, json.dumps(output, sort_keys=True, separators=(",", ":")).encode("utf-8")


def handle_invariants(*, body: bytes, timeout_seconds: int) -> tuple[int, bytes]:
    """Run the polyhedral-invariant computation and answer the normalized invariants."""
    logger.debug("smpt_invariants_received", extra={"body_bytes": len(body)})
    try:
        model = _parse_model(body=body)
        try:
            model_pnml = export_pnml(model=model)
        except PnmlExportError as error:
            raise HttpError(status=422, detail=str(error)) from error
        stdout, _, tool_seconds = _run_smpt_or_raise(
            model_pnml=model_pnml,
            arguments=INVARIANTS_ARGUMENTS,
            timeout_seconds=timeout_seconds,
        )
        try:
            output = build_invariants_output(stdout=stdout, tool_seconds=tool_seconds)
        except QuestionError as error:
            detail = f"SMPT invariants output invalid: {error}"
            raise HttpError(status=500, detail=detail) from error
    except HttpError as error:
        return error.status, json.dumps({"detail": error.detail}).encode("utf-8")
    return 200, json.dumps(output, sort_keys=True, separators=(",", ":")).encode("utf-8")


class SmptHandler(BaseHTTPRequestHandler):
    """Routes /health, /check and /invariants to the pure handlers."""

    server_version = "petrichor-smpt"

    def do_GET(self) -> None:
        """Serve the healthcheck endpoint."""
        if self.path != HEALTH_PATH:
            self._answer(404, json.dumps({"detail": "not found"}).encode("utf-8"))
            return
        self._answer(*handle_health())

    def do_POST(self) -> None:
        """Serve the check and invariants endpoints with the engine timeout."""
        if self.path not in (CHECK_PATH, INVARIANTS_PATH):
            self._answer(404, json.dumps({"detail": "not found"}).encode("utf-8"))
            return
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError:
            detail = json.dumps({"detail": "Content-Length is not an integer"}).encode("utf-8")
            self._answer(422, detail)
            return
        if length < 0:
            detail = json.dumps({"detail": "Content-Length is negative"}).encode("utf-8")
            self._answer(422, detail)
            return
        if length > MAX_BODY_BYTES:
            self._answer(413, json.dumps({"detail": "body too large"}).encode("utf-8"))
            return
        body = self.rfile.read(length)
        timeout = getattr(self.server, "engine_timeout", DEFAULT_ENGINE_TIMEOUT_SECONDS)
        if self.path == INVARIANTS_PATH:
            self._answer(*handle_invariants(body=body, timeout_seconds=timeout))
            return
        self._answer(*handle_check(body=body, timeout_seconds=timeout))

    def _answer(self, status: int, body: bytes) -> None:
        """Write one JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        """Route http.server's access log into logging (no stdout writes, ADR-0005)."""
        logger.debug("smpt_engine_request", extra={"access_log": format % args})


def main() -> None:  # pragma: no cover - the container serving loop
    """Serve the SMPT engine until the container stops."""
    timeout = float(os.environ.get(ENGINE_TIMEOUT_VARIABLE, DEFAULT_ENGINE_TIMEOUT_SECONDS))
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), SmptHandler)
    # ThreadingHTTPServer declares no engine_timeout attribute; the handler
    # reads it back with getattr(self.server, "engine_timeout", default).
    server.engine_timeout = timeout  # type: ignore[attr-defined]
    server.daemon_threads = True
    logger.info(
        "smpt_engine_listening",
        extra={"host": SERVER_HOST, "port": SERVER_PORT, "timeout": timeout},
    )
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
