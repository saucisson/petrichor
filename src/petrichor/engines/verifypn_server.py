"""HTTP entry point of the verifypn (Tapaal) engine container (spec §19.4; issue #9).

Standard library only, on purpose: the engine image ships no third-party
dependency, so this module must not import pydantic or structlog. The
API-side tests import the pure handlers from here; the container runs
``python -m petrichor.engines.verifypn_server``.

Socle minimal (issue #9): the ``deadlock`` family (``EF deadlock``,
GlobalProperties.ReachabilityDeadlock) and the ``reachability`` family
(ReachabilityCardinality, the formula is PQL as-is). The model travels in
``parameters["model"]`` as the canonical `petri+json` document; it is
exported to PNML, the question to a PQL query file, and verifypn v4.4.2
answers with exit code 0 on success - the verdict is read from the
``FORMULA <file> TRUE|FALSE`` line on stdout (MAYBE maps to ``unknown``).
Any non-zero exit code is an engine failure and maps to HTTP 500. The
XML trace on stderr is replayed on the model to produce the §16 witness.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from petrichor.engines.pnml_export import PnmlExportError, export_pnml

logger = logging.getLogger(__name__)

type Json = bool | int | float | str | list[Json] | dict[str, Json] | None
"""Any JSON-compatible value (local alias: this module must stay stdlib-only)."""

VERIFYPN_VERSION = "4.4.2"
"""The pinned verifypn release this image is built from (Dockerfile.verifypn)."""

VERIFYPN_TECHNIQUE = f"verifypn-{VERIFYPN_VERSION}"
VERIFYPN_BINARY = os.environ.get("VERIFYPN_BINARY", "/usr/local/bin/verifypn")
SERVER_HOST = "0.0.0.0"  # noqa: S104 - the container must accept the loopback-published port
SERVER_PORT = 8080
HEALTH_PATH = "/health"
CHECK_PATH = "/check"
ENGINE_TIMEOUT_VARIABLE = "ENGINE_TIMEOUT"
DEFAULT_ENGINE_TIMEOUT_SECONDS = 30
MAX_BODY_BYTES = 1_048_576
MODEL_PARAMETER = "model"
SUPPORTED_FAMILIES = frozenset({"deadlock", "reachability"})
DEADLOCK_PQL = "EF deadlock"
FORMULA_VERDICT_PATTERN = re.compile(r"FORMULA \S+ (TRUE|FALSE)\b")
MAYBE_PATTERN = re.compile(r"Query is MAYBE satisfied")
STATES_EXPLORED_PATTERN = re.compile(r"explored states:\s+(\d+)")
STATE_SPACE_PATTERN = re.compile(r"STATE_SPACE STATES (\d+)")
TRACE_BLOCK_PATTERN = re.compile(r"<trace>(.*?)</trace>", re.DOTALL)
TRACE_TRANSITION_PATTERN = re.compile(r'<transition id="([^"]+)"')


class QuestionError(Exception):
    """The question payload does not conform to the socle-minimal contract."""


def build_pql_query(*, family: str, formula: str) -> str:
    """Translate the formal question into a PQL query file body (spec §17.3)."""
    if family == "deadlock":
        return DEADLOCK_PQL
    if family == "reachability":
        return formula
    raise QuestionError(
        f"Family {family!r} is outside the socle minimal scope {sorted(SUPPORTED_FAMILIES)}."
    )


def parse_verdict(*, stdout: str) -> str:
    """The normalized verdict from the verifypn stdout (v4.4.2 prints it, the exit code stays 0).

    The ``FORMULA <file> TRUE|FALSE`` line carries the verdict; a MAYBE
    answer maps to ``unknown``. Anything else is a contract violation.
    """
    if MAYBE_PATTERN.search(stdout):
        return "unknown"
    match = FORMULA_VERDICT_PATTERN.search(stdout)
    if match is None:
        raise QuestionError("verifypn stdout carries no FORMULA verdict line.")
    return "satisfied" if match.group(1) == "TRUE" else "violated"


def parse_states_explored(*, stdout: str) -> int:
    """The explored-state count from the verifypn statistics, 0 if absent."""
    match = STATES_EXPLORED_PATTERN.search(stdout) or STATE_SPACE_PATTERN.search(stdout)
    return int(match.group(1)) if match else 0


def parse_trace_transitions(*, stderr: str) -> list[str]:
    """The ordered fired-transition ids of the XML trace on stderr."""
    block = TRACE_BLOCK_PATTERN.search(stderr)
    if block is None:
        return []
    return TRACE_TRANSITION_PATTERN.findall(block.group(1))


def _element_name(*, element: Json, kind: str) -> str:
    """The semantic name of a place or transition payload, validated."""
    if not isinstance(element, dict):
        raise QuestionError(f"Every {kind} must be a JSON object.")
    name = element.get("name")
    if not isinstance(name, str) or not name:
        raise QuestionError(f"Every {kind} needs a non-empty name.")
    return name


def _pre_post_sets(*, model: dict[str, Json]) -> dict[str, tuple[dict[str, int], dict[str, int]]]:
    """Per transition name, the weighted pre- and post-sets over place names.

    The model is the semantic projection: arcs bind element names directly.
    """
    places = model.get("places")
    transitions = model.get("transitions")
    arcs = model.get("arcs")
    if (
        not isinstance(places, list)
        or not isinstance(transitions, list)
        or not isinstance(arcs, list)
    ):
        raise QuestionError("The model payload needs places, transitions and arcs lists.")
    place_names = {_element_name(element=place, kind="place") for place in places}
    pre_post: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
    for transition in transitions:
        pre_post[_element_name(element=transition, kind="transition")] = ({}, {})
    for arc in arcs:
        if not isinstance(arc, dict):
            raise QuestionError("Every arc must be a JSON object.")
        source = arc.get("source")
        target = arc.get("target")
        weight = arc.get("weight", 1)
        if not isinstance(source, str) or not isinstance(target, str):
            raise QuestionError("Every arc needs string source and target names.")
        if source not in pre_post and source not in place_names:
            raise QuestionError(f"Arc source {source!r} is an unknown element name.")
        if target not in pre_post and target not in place_names:
            raise QuestionError(f"Arc target {target!r} is an unknown element name.")
        if not isinstance(weight, int) or isinstance(weight, bool) or weight < 1:
            raise QuestionError("Every arc needs a positive integer weight.")
        if source in place_names:
            pre, post = pre_post[target]
            pre[source] = pre.get(source, 0) + weight
            pre_post[target] = (pre, post)
        else:
            pre, post = pre_post[source]
            post[target] = post.get(target, 0) + weight
            pre_post[source] = (pre, post)
    return pre_post


def _initial_marking(*, model: dict[str, Json]) -> tuple[list[str], dict[str, int]]:
    """The place names and initial marking of the semantic projection."""
    places = model.get("places")
    if not isinstance(places, list):
        raise QuestionError("The model payload needs a places list.")
    place_names: list[str] = []
    marking: dict[str, int] = {}
    for place in places:
        name = _element_name(element=place, kind="place")
        initial = place.get("initial_marking", 0) if isinstance(place, dict) else 0
        if not isinstance(initial, int) or isinstance(initial, bool) or initial < 0:
            raise QuestionError(f"Place {name!r} needs a non-negative integer initial_marking.")
        place_names.append(name)
        marking[name] = initial
    return place_names, marking


def _sparse(*, place_names: list[str], marking: dict[str, int]) -> list[Json]:
    """The sparse, sorted (place repeated per token) view of a marking."""
    result: list[Json] = []
    for name in place_names:
        result.extend([name] * marking[name])
    result.sort()
    return result


def _fire(
    *,
    marking: dict[str, int],
    pre_post: dict[str, tuple[dict[str, int], dict[str, int]]],
    transition: str,
) -> None:
    """Fire one transition on the marking, rejecting unknown or disabled firings."""
    if transition not in pre_post:
        raise QuestionError(f"Trace fires unknown transition {transition!r}.")
    pre, post = pre_post[transition]
    if any(marking[place] < weight for place, weight in pre.items()):
        raise QuestionError(f"Trace fires {transition!r} in a marking where it is disabled.")
    for place, weight in pre.items():
        marking[place] -= weight
    for place, weight in post.items():
        marking[place] += weight


def replay_trace(*, model: dict[str, Json], fired: list[str]) -> list[Json]:
    """Replay a fired-transition sequence on the model into §16 witness steps."""
    place_names, marking = _initial_marking(model=model)
    pre_post = _pre_post_sets(model=model)
    first: dict[str, Json] = {
        "index": 0,
        "marking": _sparse(place_names=place_names, marking=marking),
        "fired": None,
    }
    steps: list[Json] = [first]
    for position, transition in enumerate(fired, start=1):
        _fire(marking=marking, pre_post=pre_post, transition=transition)
        step: dict[str, Json] = {
            "index": position,
            "marking": _sparse(place_names=place_names, marking=marking),
            "fired": transition,
        }
        steps.append(step)
    return steps


def build_normalized_output(
    *,
    stdout: str,
    stderr: str,
    model: dict[str, Json],
    tool_seconds: float,
) -> dict[str, Json]:
    """Assemble the normalized driver output from one verifypn execution."""
    verdict = parse_verdict(stdout=stdout)
    fired = parse_trace_transitions(stderr=stderr)
    witness: dict[str, Json] | None = None
    if verdict == "satisfied" and fired:
        witness = {
            "kind": "trace",
            "cyclic": False,
            "back_edge": None,
            "steps": replay_trace(model=model, fired=fired),
        }
    return {
        "verdict": verdict,
        "witness": witness,
        "cost": {
            "tool_seconds": tool_seconds,
            "states_explored": parse_states_explored(stdout=stdout),
        },
        "technique": VERIFYPN_TECHNIQUE,
    }


def run_verifypn(
    *,
    model_pnml: str,
    query_pql: str,
    timeout_seconds: int,
    binary: str | None = None,
) -> tuple[int, str, str, float]:
    """Run one verifypn execution in a temp dir; return (exit, stdout, stderr, seconds)."""
    executable = binary or os.environ.get("VERIFYPN_BINARY", VERIFYPN_BINARY)
    with tempfile.TemporaryDirectory(prefix="verifypn-") as workdir:
        model_path = Path(workdir) / "model.pnml"
        query_path = Path(workdir) / "query.pql"
        model_path.write_text(model_pnml, encoding="utf-8")
        query_path.write_text(query_pql + "\n", encoding="utf-8")
        started = time.monotonic()
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [executable, str(model_path), str(query_path), "-t", "2", "-n", "1"],
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


def _parse_question(*, body: bytes) -> tuple[dict[str, Json], str, str]:
    """Validate the question payload; return (model, family, formula)."""
    try:
        payload: Json = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HttpError(status=422, detail="body is not valid JSON") from error
    if not isinstance(payload, dict):
        raise HttpError(status=422, detail="question must be a JSON object")
    family = payload.get("family")
    formula = payload.get("formula")
    parameters = payload.get("parameters", {})
    model = parameters.get(MODEL_PARAMETER) if isinstance(parameters, dict) else None
    if not isinstance(family, str) or not isinstance(formula, str) or not formula:
        raise HttpError(status=422, detail="question needs a family and a non-empty formula")
    if family not in SUPPORTED_FAMILIES:
        raise HttpError(status=422, detail=f"family {family!r} is outside the socle minimal scope")
    if not isinstance(model, dict):
        raise HttpError(
            status=422, detail=f"parameters.{MODEL_PARAMETER} must be the canonical model"
        )
    return model, family, formula


def handle_check(*, body: bytes, timeout_seconds: int) -> tuple[int, bytes]:
    """Validate a question payload, run verifypn, answer with the normalized output."""
    try:
        model, family, formula = _parse_question(body=body)
        try:
            query_pql = build_pql_query(family=family, formula=formula)
            model_pnml = export_pnml(model=model)
        except (QuestionError, PnmlExportError) as error:
            raise HttpError(status=422, detail=str(error)) from error
        try:
            returncode, stdout, stderr, tool_seconds = run_verifypn(
                model_pnml=model_pnml, query_pql=query_pql, timeout_seconds=timeout_seconds
            )
        except subprocess.TimeoutExpired as error:
            raise HttpError(status=504, detail=f"verifypn exceeded {timeout_seconds}s") from error
        except OSError as error:
            detail = f"verifypn could not be executed: {error}"
            raise HttpError(status=500, detail=detail) from error
        if returncode != 0:
            raise HttpError(status=500, detail=f"verifypn failed: {stderr.strip()[:500]}")
        try:
            output = build_normalized_output(
                stdout=stdout, stderr=stderr, model=model, tool_seconds=tool_seconds
            )
        except QuestionError as error:
            # Exit 0 with malformed stdout (no FORMULA line, inconsistent
            # trace): an engine contract violation, never a dropped connection.
            raise HttpError(status=500, detail=f"verifypn output invalid: {error}") from error
    except HttpError as error:
        return error.status, json.dumps({"detail": error.detail}).encode("utf-8")
    return 200, json.dumps(output, sort_keys=True, separators=(",", ":")).encode("utf-8")


class VerifypnHandler(BaseHTTPRequestHandler):
    """Routes /health and /check to the pure handlers."""

    server_version = "petrichor-verifypn"

    def do_GET(self) -> None:
        """Serve the healthcheck endpoint."""
        if self.path != HEALTH_PATH:
            self._answer(404, json.dumps({"detail": "not found"}).encode("utf-8"))
            return
        self._answer(*handle_health())

    def do_POST(self) -> None:
        """Serve the check endpoint with the engine timeout."""
        if self.path != CHECK_PATH:
            self._answer(404, json.dumps({"detail": "not found"}).encode("utf-8"))
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_BODY_BYTES:
            self._answer(413, json.dumps({"detail": "body too large"}).encode("utf-8"))
            return
        body = self.rfile.read(length)
        timeout = getattr(self.server, "engine_timeout", DEFAULT_ENGINE_TIMEOUT_SECONDS)
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
        logger.debug("verifypn_engine_request", extra={"message": format % args})


def main() -> None:  # pragma: no cover - the container serving loop
    """Serve the verifypn engine until the container stops."""
    timeout = float(os.environ.get(ENGINE_TIMEOUT_VARIABLE, DEFAULT_ENGINE_TIMEOUT_SECONDS))
    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), VerifypnHandler)
    server.engine_timeout = timeout  # type: ignore[attr-defined]
    server.daemon_threads = True
    logger.info(
        "verifypn_engine_listening",
        extra={"host": SERVER_HOST, "port": SERVER_PORT, "timeout": timeout},
    )
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    main()
