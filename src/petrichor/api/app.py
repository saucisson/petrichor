"""HTTP API (WSGI) of the Gérer service: models and asynchronous checks (spec §4.1, §5; ADR-0016).

- ``POST /v1/models`` with ``application/pnml+xml`` imports a net into the
  canonical `petri+json` form: ``201`` when new, ``200`` when the content
  hash is already known (idempotent import, spec §13).
- ``GET /v1/models/{mid}`` serves the canonical document as
  ``application/petri+json; version=1.0``.
- ``POST /v1/checks/{family}`` accepts a check question and answers
  ``202 Accepted`` + ``Location: /v1/runs/{rid}``; the run is idempotent by
  hash of (model, question) and its ``kind`` pattern must belong to the
  family of the endpoint, otherwise ``422`` - never reinterpreted (spec §12.1).
- ``GET /v1/runs/{rid}`` polls the run states ``running`` | ``done`` | ``error``;
  engine errors and timeouts are explicit error states, never a default verdict.
- Every request is authenticated with the ``X-API-Key`` header before it is
  parsed deeply; every error is an RFC 7807 ``application/problem+json``
  payload of the same shape.

The contract document is ``openapi.yaml`` next to this module (ADR-0016).
"""

import hmac
import json
import threading
from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import TYPE_CHECKING, TypeGuard
from wsgiref.types import StartResponse, WSGIEnvironment

import structlog
from pydantic import ValidationError

from petrichor.checks import (
    FAMILY_PATTERNS,
    CheckCompleted,
    CheckFamilyName,
    CheckOutcome,
    CheckQuestion,
    CheckRequest,
    CheckRunner,
    RunId,
    run_key,
)
from petrichor.petri_canonical import InvalidCanonicalModelError, ModelId, serialize_model
from petrichor.pnml_import import PnmlImportError, import_pnml

if TYPE_CHECKING:
    from petrichor.api.settings import ApiSettings
    from petrichor.model_store import ModelStore
    from petrichor.run_store import RunStore

logger = structlog.get_logger(__name__)

type WSGIApp = Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]

MODELS_PATH = "/v1/models"
MODEL_PATH_PREFIX = "/v1/models/"
CHECKS_PATH_PREFIX = "/v1/checks/"
RUNS_PATH_PREFIX = "/v1/runs/"
CONTENT_TYPE_PNML = "application/pnml+xml"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_PETRI_JSON = "application/petri+json; version=1.0"
CONTENT_TYPE_PROBLEM_JSON = "application/problem+json"
HEADER_API_KEY = "X-API-Key"
PROBLEM_TYPE_BASE_URL = "https://petrichor.dev/problems/"
WATCHDOG_GRACE_SECONDS = 5
"""Extra seconds the watchdog waits past the run timeout before forcing the error state."""
ACCEPTABLE_MEDIA_TYPES = frozenset(
    {"*/*", "application/*", "application/petri+json", "application/json"}
)


def _is_authorized(*, environ: WSGIEnvironment, settings: ApiSettings) -> bool:
    """Constant-time comparison of the X-API-Key header against the configured key."""
    provided = environ.get("HTTP_X_API_KEY", "")
    expected = settings.api_key.get_secret_value()
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


def _media_type(*, header_value: str | None) -> str:
    """The media type of a Content-Type header, lowercased, without parameters."""
    if header_value is None:
        return ""
    return header_value.split(";", maxsplit=1)[0].strip().lower()


def _accepts_petri_json(*, accept_header: str | None) -> bool:
    """Whether an Accept header admits the canonical petri+json representation."""
    if accept_header is None or not accept_header.strip():
        return True
    for entry in accept_header.split(","):
        media = entry.split(";", maxsplit=1)[0].strip().lower()
        if media in ACCEPTABLE_MEDIA_TYPES:
            return True
    return False


def _problem_body(*, instance: str, status: HTTPStatus, reason: str, detail: str) -> bytes:
    """Serialize an RFC 7807 problem+json payload with a stable shape."""
    payload = {
        "type": f"{PROBLEM_TYPE_BASE_URL}{reason}",
        "title": status.phrase,
        "status": status.value,
        "detail": detail,
        "instance": instance,
        "extensions": {"reason": reason},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _respond(
    *,
    start_response: StartResponse,
    status: HTTPStatus,
    content_type: str,
    body: bytes,
    extra_headers: list[tuple[str, str]] | None = None,
) -> Iterable[bytes]:
    """Start the WSGI response with truthful status and typed content."""
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(len(body))),
        *(extra_headers or []),
    ]
    start_response(f"{status.value} {status.phrase}", headers)
    return [body]


def _problem_response(
    *,
    start_response: StartResponse,
    environ: WSGIEnvironment,
    status: HTTPStatus,
    reason: str,
    detail: str,
) -> Iterable[bytes]:
    """Respond with the documented problem+json error payload."""
    body = _problem_body(
        instance=environ.get("PATH_INFO", "/"), status=status, reason=reason, detail=detail
    )
    return _respond(
        start_response=start_response,
        status=status,
        content_type=CONTENT_TYPE_PROBLEM_JSON,
        body=body,
    )


def _post_models(
    *, environ: WSGIEnvironment, start_response: StartResponse, store: ModelStore
) -> Iterable[bytes]:
    """Import a PNML document into the canonical form (201 new, 200 idempotent)."""
    content_type = _media_type(header_value=environ.get("CONTENT_TYPE"))
    if content_type != CONTENT_TYPE_PNML:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            reason="unsupported_media_type",
            detail=(
                f"POST {MODELS_PATH} expects Content-Type {CONTENT_TYPE_PNML},"
                f" got {content_type or 'none'}."
            ),
        )
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_content_length",
            detail="The Content-Length header is not an integer.",
        )
    body = environ["wsgi.input"].read(length)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_pnml",
            detail=f"The body is not valid UTF-8: {error}",
        )
    try:
        model = import_pnml(text=text)
    except (PnmlImportError, InvalidCanonicalModelError) as error:
        logger.warning("api_model_import_rejected", reason=type(error).__name__, detail=str(error))
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_pnml",
            detail=str(error),
        )
    stored, created = store.save(model=model)
    status = HTTPStatus.CREATED if created else HTTPStatus.OK
    logger.debug(
        "api_model_stored",
        model_id=stored.id,
        content_hash=stored.content_hash,
        created=created,
        status=status.value,
    )
    extra_headers = [("Location", f"{MODELS_PATH}/{stored.id}")] if created else []
    return _respond(
        start_response=start_response,
        status=status,
        content_type=CONTENT_TYPE_PETRI_JSON,
        body=serialize_model(model=stored).encode("utf-8"),
        extra_headers=extra_headers,
    )


def _get_model(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    model_id: str,
) -> Iterable[bytes]:
    """Serve the canonical document of one model."""
    if not _accepts_petri_json(accept_header=environ.get("HTTP_ACCEPT")):
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.NOT_ACCEPTABLE,
            reason="not_acceptable",
            detail=(
                f"This endpoint serves {CONTENT_TYPE_PETRI_JSON};"
                " PNML re-export is not supported yet."
            ),
        )
    model = store.get(model_id=ModelId(model_id))
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.NOT_FOUND,
            reason="model_not_found",
            detail=f"No model with id {model_id!r}.",
        )
    logger.debug("api_model_read", model_id=model.id, content_hash=model.content_hash)
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_PETRI_JSON,
        body=serialize_model(model=model).encode("utf-8"),
    )


class _ProblemPayloadError(Exception):
    """An internal early-exit carrying an RFC 7807 payload to the caller."""

    def __init__(self, *, status: HTTPStatus, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.reason = reason
        self.detail = detail


def _is_check_family(value: str) -> TypeGuard[CheckFamilyName]:
    """Narrow a path segment to a known check family (spec §12.1)."""
    return value in FAMILY_PATTERNS


def _parse_check_request(*, environ: WSGIEnvironment) -> CheckRequest:
    """Read and validate the JSON body of a check request, or raise a problem."""
    content_type = _media_type(header_value=environ.get("CONTENT_TYPE"))
    if content_type != CONTENT_TYPE_JSON:
        raise _ProblemPayloadError(
            status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            reason="unsupported_media_type",
            detail=f"POST {CHECKS_PATH_PREFIX}{{family}} expects Content-Type {CONTENT_TYPE_JSON},"
            f" got {content_type or 'none'}.",
        )
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        raise _ProblemPayloadError(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_content_length",
            detail="The Content-Length header is not an integer.",
        ) from None
    body = environ["wsgi.input"].read(length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ProblemPayloadError(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_json",
            detail=f"The body is not valid JSON: {error}",
        ) from None
    try:
        return CheckRequest.model_validate(payload)
    except ValidationError as error:
        raise _ProblemPayloadError(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_check_request",
            detail=f"The check request does not conform to the contract: {error}",
        ) from None


def _execute_run(
    *,
    runner: CheckRunner,
    run_store: RunStore,
    run_id: RunId,
    question: CheckQuestion,
    timeout_seconds: int,
) -> None:
    """Run one check on a background thread and record its explicit outcome."""
    logger.debug("check_run_started", run_id=run_id, family=question.family, kind=question.kind)
    try:
        outcome: CheckOutcome = runner.run(question=question, timeout_seconds=timeout_seconds)
    except Exception as error:  # noqa: BLE001 - a crashed runner must still become an explicit error state, never a run stuck in "running"
        logger.error("check_run_crashed", run_id=run_id, error=repr(error))
        run_store.mark_error(run_id=run_id, kind="engine_error", detail=repr(error))
        return
    if isinstance(outcome, CheckCompleted):
        run_store.mark_done(run_id=run_id, output=outcome.output)
        logger.debug("check_run_finished", run_id=run_id, status="done")
    else:
        run_store.mark_error(run_id=run_id, kind=outcome.kind, detail=outcome.detail)
        logger.debug("check_run_finished", run_id=run_id, status="error", error_kind=outcome.kind)


def _watchdog(*, run_store: RunStore, run_id: RunId, timeout_seconds: int) -> None:
    """Backstop: a runner that ignores its timeout still ends in an explicit error state."""
    run = run_store.get(run_id=run_id)
    if run is not None and run.status == "running":
        logger.warning("check_run_watchdog_fired", run_id=run_id, timeout_seconds=timeout_seconds)
        run_store.mark_error(
            run_id=run_id,
            kind="timeout",
            detail=f"runner did not answer within {timeout_seconds}s (watchdog backstop)",
        )


def _post_check(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    run_store: RunStore,
    runner: CheckRunner | None,
    family: CheckFamilyName,
    timeout_seconds: int,
) -> Iterable[bytes]:
    """Accept a check question: 202 + Location, idempotent by (model, question) hash."""
    try:
        request = _parse_check_request(environ=environ)
        if request.kind not in FAMILY_PATTERNS[family]:
            raise _ProblemPayloadError(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="pattern_not_in_family",
                detail=(
                    f"Pattern {request.kind!r} does not belong to family {family!r};"
                    " it is rejected, never reinterpreted (spec §12.1)."
                ),
            )
        if store.get(model_id=request.model_id) is None:
            raise _ProblemPayloadError(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {request.model_id!r}.",
            )
        if runner is None:
            raise _ProblemPayloadError(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="runner_not_configured",
                detail="No check runner is wired into this server yet (real engines: P09+).",
            )
    except _ProblemPayloadError as problem:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=problem.status,
            reason=problem.reason,
            detail=problem.detail,
        )
    question = CheckQuestion(
        family=family, kind=request.kind, formula=request.formula, parameters=request.parameters
    )
    key = run_key(model_id=request.model_id, question=question)
    run, created = run_store.create_or_get(
        key=key,
        family=family,
        kind=request.kind,
        formula=request.formula,
        model_id=request.model_id,
    )
    if created:
        thread = threading.Thread(
            target=_execute_run,
            kwargs={
                "runner": runner,
                "run_store": run_store,
                "run_id": run.run_id,
                "question": question,
                "timeout_seconds": timeout_seconds,
            },
            daemon=True,
        )
        thread.start()
        watchdog = threading.Timer(
            timeout_seconds + WATCHDOG_GRACE_SECONDS,
            _watchdog,
            kwargs={
                "run_store": run_store,
                "run_id": run.run_id,
                "timeout_seconds": timeout_seconds,
            },
        )
        watchdog.daemon = True
        watchdog.start()
    logger.debug(
        "api_check_accepted",
        run_id=run.run_id,
        family=family,
        kind=request.kind,
        model_id=request.model_id,
        created=created,
        status=HTTPStatus.ACCEPTED.value,
    )
    body = json.dumps(run.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.ACCEPTED,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
        extra_headers=[("Location", f"{RUNS_PATH_PREFIX}{run.run_id}")],
    )


def _get_run(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    run_store: RunStore,
    run_id: str,
) -> Iterable[bytes]:
    """Poll one run: running, done (with output) or error (explicit, no verdict)."""
    run = run_store.get(run_id=RunId(run_id))
    if run is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.NOT_FOUND,
            reason="run_not_found",
            detail=f"No run with id {run_id!r}.",
        )
    logger.debug("api_run_read", run_id=run.run_id, status=run.status)
    body = json.dumps(run.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _dispatch_models(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    store: ModelStore,
) -> Iterable[bytes] | None:
    """Route the /v1/models resources, or return None when the path is not one."""
    if path == MODELS_PATH:
        if method == "POST":
            return _post_models(environ=environ, start_response=start_response, store=store)
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            reason="method_not_allowed",
            detail=f"{method} is not supported on {MODELS_PATH}.",
        )
    if path.startswith(MODEL_PATH_PREFIX):
        model_id = path.removeprefix(MODEL_PATH_PREFIX)
        if method == "GET" and model_id and "/" not in model_id:
            return _get_model(
                environ=environ,
                start_response=start_response,
                store=store,
                model_id=model_id,
            )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.NOT_FOUND,
            reason="method_not_allowed" if method != "GET" else "model_not_found",
            detail=f"No resource at {path}.",
        )
    return None


def _dispatch_checks_and_runs(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    settings: ApiSettings,
    store: ModelStore,
    run_store: RunStore,
    runner: CheckRunner | None,
) -> Iterable[bytes] | None:
    """Route /v1/checks/{family} and /v1/runs/{run_id}, or return None."""
    if path.startswith(CHECKS_PATH_PREFIX):
        family = path.removeprefix(CHECKS_PATH_PREFIX)
        if method == "POST" and "/" not in family and _is_check_family(family):
            return _post_check(
                environ=environ,
                start_response=start_response,
                store=store,
                run_store=run_store,
                runner=runner,
                family=family,
                timeout_seconds=settings.check_timeout_seconds,
            )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.METHOD_NOT_ALLOWED if method != "POST" else HTTPStatus.NOT_FOUND,
            reason="method_not_allowed" if method != "POST" else "check_family_not_found",
            detail=f"No check family at {path}.",
        )
    if path.startswith(RUNS_PATH_PREFIX):
        run_id = path.removeprefix(RUNS_PATH_PREFIX)
        if method == "GET" and run_id and "/" not in run_id:
            return _get_run(
                environ=environ,
                start_response=start_response,
                run_store=run_store,
                run_id=run_id,
            )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.NOT_FOUND,
            reason="method_not_allowed" if method != "GET" else "run_not_found",
            detail=f"No resource at {path}.",
        )
    return None


def create_app(
    *,
    settings: ApiSettings,
    store: ModelStore,
    run_store: RunStore,
    runner: CheckRunner | None,
) -> WSGIApp:
    """Build the WSGI application: auth first, then routing and negotiation."""

    def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        logger.debug("api_request_received", method=method, path=path)
        if not _is_authorized(environ=environ, settings=settings):
            logger.warning("api_request_unauthorized", method=method, path=path)
            return _problem_response(
                start_response=start_response,
                environ=environ,
                status=HTTPStatus.UNAUTHORIZED,
                reason="missing_or_invalid_api_key",
                detail=f"Provide a valid {HEADER_API_KEY} header.",
            )
        routed = _dispatch_models(
            environ=environ, start_response=start_response, method=method, path=path, store=store
        )
        if routed is None:
            routed = _dispatch_checks_and_runs(
                environ=environ,
                start_response=start_response,
                method=method,
                path=path,
                settings=settings,
                store=store,
                run_store=run_store,
                runner=runner,
            )
        if routed is not None:
            return routed
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.NOT_FOUND,
            reason="not_found",
            detail=f"No resource at {path}.",
        )

    return app
