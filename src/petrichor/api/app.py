"""HTTP API (WSGI) of the Gérer service: model import and read (spec §4.1, §5; ADR-0016).

- ``POST /v1/models`` with ``application/pnml+xml`` imports a net into the
  canonical `petri+json` form: ``201`` when new, ``200`` when the content
  hash is already known (idempotent import, spec §13).
- ``GET /v1/models/{mid}`` serves the canonical document as
  ``application/petri+json; version=1.0``.
- Every request is authenticated with the ``X-API-Key`` header before it is
  parsed deeply; every error is an RFC 7807 ``application/problem+json``
  payload of the same shape.

The contract document is ``openapi.yaml`` next to this module (ADR-0016).
"""

import hmac
import json
from collections.abc import Callable, Iterable
from http import HTTPStatus
from typing import TYPE_CHECKING
from wsgiref.types import StartResponse, WSGIEnvironment

import structlog

from petrichor.petri_canonical import InvalidCanonicalModelError, ModelId, serialize_model
from petrichor.pnml_import import PnmlImportError, import_pnml

if TYPE_CHECKING:
    from petrichor.api.settings import ApiSettings
    from petrichor.model_store import ModelStore

logger = structlog.get_logger(__name__)

type WSGIApp = Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]

MODELS_PATH = "/v1/models"
MODEL_PATH_PREFIX = "/v1/models/"
CONTENT_TYPE_PNML = "application/pnml+xml"
CONTENT_TYPE_PETRI_JSON = "application/petri+json; version=1.0"
CONTENT_TYPE_PROBLEM_JSON = "application/problem+json"
HEADER_API_KEY = "X-API-Key"
PROBLEM_TYPE_BASE_URL = "https://petrichor.dev/problems/"
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


def create_app(*, settings: ApiSettings, store: ModelStore) -> WSGIApp:
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
        return _problem_response(
            start_response=start_response,
            environ=environ,
            status=HTTPStatus.NOT_FOUND,
            reason="not_found",
            detail=f"No resource at {path}.",
        )

    return app
