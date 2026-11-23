"""HTTP API (WSGI) of the Gérer service: models and checks (spec §4.1, §5, §12; ADR-0016).

- ``POST /v1/models`` with ``application/pnml+xml`` imports a net into the
  canonical `petri+json` form: ``201`` when new, ``200`` when the content
  hash is already known (idempotent import, spec §13).
- ``GET /v1/models/{mid}`` serves the canonical document as
  ``application/petri+json; version=1.0``.
- ``POST /v1/checks/{family}`` accepts a check question and answers
  ``202 Accepted`` + ``Location: /v1/runs/{rid}``; the run is idempotent by
  hash of (model, question) and its ``kind`` pattern must belong to the
  family of the endpoint, otherwise ``422`` - never reinterpreted (spec §12.1).
  Every accepted run passes the session budget cap (spec §12.4): when the
  remaining budget cannot cover the run, the request is refused with an
  explicit ``429`` carrying the remaining budget - no carry-over.
- ``GET /v1/runs/{rid}`` polls the run states ``running`` | ``done`` | ``error``;
  engine errors and timeouts are explicit error states, never a default verdict.
  Every run is archived with its command, tool version, timeout, input hash
  and measured cost (spec §12.5) - and no API or code path ever deletes a run.
- ``GET /v1/qualification`` exposes the stored qualification status per
  (adapter, family) to the orchestrator (spec §17.5, issue #22); a done run
  also carries ``engine_qualified``, whether its engine is qualified for the
  family - an unqualified adapter is never alone on a clause.
- Web v1 (issue #206, contract §1/§5): ``GET /v1/catalog/models`` and
  ``GET /v1/models/{mid}`` are public read-only routes of the Découvrir zone;
  every other ``/v1`` route requires a session cookie or a Bearer PAT and
  answers ``401`` ``reason: "session_required"`` without one. Every error is
  an RFC 7807 ``application/problem+json`` payload of the same shape.

The contract document is ``openapi.yaml`` next to this module (ADR-0016).
"""

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterable
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeGuard
from urllib.parse import parse_qs, unquote, urlsplit
from wsgiref.types import StartResponse, WSGIEnvironment

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from petrichor.api.auth import (
    ENVIRON_AUTH_CLAIMS,
    REASON_INVALID_BEARER,
    REASON_MISSING_CREDENTIALS,
    REASON_SESSION_REQUIRED,
    authenticate_request,
)
from petrichor.api.web_auth import (
    SESSION_COOKIE,
    OidcFlowClient,
    PendingLoginStore,
    WebSessionStore,
    code_challenge_s256,
    is_internal_path,
)
from petrichor.arbitration import ArbitrationDecisionKind, ArbitrationStore
from petrichor.archive_ingest import (
    ARCHIVE_CONTENT_TYPES,
    ArchiveError,
    NoPnmlError,
    PnmlCandidate,
    UnsafeArchiveError,
    extract_pnml_candidates,
)
from petrichor.auth import TokenRejectedError
from petrichor.catalog import (
    CatalogQuery,
    CatalogUnavailableError,
    InvalidCatalogPnmlError,
    ModelCatalog,
    QuotaExceededError,
    SourceUnavailableError,
    UnknownModelError,
    parse_sort,
)
from petrichor.checks import (
    FAIRNESS_CAPABLE_TOOLS,
    FAMILY_PATTERNS,
    TOOL_SPECIFIC_SEMANTICS,
    CheckCompleted,
    CheckFamilyName,
    CheckOutcome,
    CheckQuestion,
    CheckRequest,
    CheckRunner,
    QualificationLookup,
    RunId,
    SessionId,
    StaticFirstProber,
    ToolVersion,
    run_key,
)
from petrichor.contract import arbitrated_profile, build_contract, render_markdown
from petrichor.elicitation import (
    QuestionId,
    instantiate_questions,
    load_catalog,
    questions_converge,
)
from petrichor.insitu import build_net_overlay
from petrichor.llm.provider import FormulaText, LLMDegradationError
from petrichor.petri_canonical import (
    Arc,
    CanonicalModel,
    Element,
    ElementId,
    InvalidCanonicalModelError,
    JsonValue,
    ModelId,
    UnknownElementError,
    canonical_json,
    semantic_projection,
    serialize_model,
    with_element_annotation,
)
from petrichor.pnml_import import PnmlImportError, import_pnml
from petrichor.profile import ArbitrationDecision, ProfileArbitrator
from petrichor.run_store import AdmissionResult, BudgetRefused, RunAdmission, RunStore
from petrichor.session_store import SessionStore, StoredSession
from petrichor.state_graph import StateGraphExplorer, StateGraphUnsupportedError
from petrichor.triage import (
    STATIC_COST_SECONDS,
    STATIC_TOOL_VERSION,
    NonVacuityGuard,
    QuestionTriage,
    StaticResolver,
    StaticUndecidableError,
    triage_question,
)
from petrichor.url_import import (
    DownloadFailedError,
    ImportUrl,
    PrivateNetworkError,
    UrlImportConfig,
    UrlNotAllowedError,
)
from petrichor.url_import import (
    QuotaExceededError as UrlQuotaExceededError,
)
from petrichor.url_import import (
    download as download_url,
)
from petrichor.witness import (
    WitnessError,
    WitnessId,
    WitnessStore,
    narrate_steps,
    normalize_witness_output,
    witness_record_of,
)

if TYPE_CHECKING:
    from petrichor.api.settings import ApiSettings
    from petrichor.auth import TokenVerifier
    from petrichor.convergence import ConvergenceStore
    from petrichor.elicitation import GeneratedQuestion
    from petrichor.llm.provider import LLMProvider
    from petrichor.model_store import ModelStore
    from petrichor.profile import ProfileState
    from petrichor.question_edits import FormulaEditStore

logger = structlog.get_logger(__name__)

type WSGIApp = Callable[[WSGIEnvironment, StartResponse], Iterable[bytes]]

MODELS_PATH = "/v1/models"
MODELS_IMPORT_URL_PATH = "/v1/models/import-url"
CATALOG_MODELS_PATH = "/v1/catalog/models"
CATALOG_IMPORT_PATH = "/v1/catalog/import"
MODEL_PATH_PREFIX = "/v1/models/"
CHECKS_PATH_PREFIX = "/v1/checks/"
RUNS_PATH_PREFIX = "/v1/runs/"
WITNESS_SUFFIX = "/witness"
WITNESSES_PATH = "/v1/witnesses"
WITNESSES_PATH_PREFIX = "/v1/witnesses/"
LLM_USAGE_PATH = "/v1/llm/usage"
LLM_CALLS_PATH = "/v1/llm/calls"
QUALIFICATION_PATH = "/v1/qualification"
SESSIONS_PATH = "/v1/sessions"
SESSION_PATH_PREFIX = "/v1/sessions/"
SESSION_JOURNAL_SUFFIX = "/journal"
ARBITRATION_REQUESTS_SUFFIX = "/arbitration-requests"
ARBITRATIONS_SUFFIX = "/arbitrations"
CONVERGENCES_SUFFIX = "/convergences"
SESSION_PROFILE_SUFFIX = "/profile"
SESSION_ARBITRATE_SUFFIX = "/profile/arbitrate"
SESSION_COVERAGE_SUFFIX = "/coverage"
SESSION_STATE_GRAPH_SUFFIX = "/state-graph"
SESSION_ANNOTATIONS_SUFFIX = "/annotations"
SESSION_NET_OVERLAY_SUFFIX = "/net-overlay"
SESSION_CONTRACT_SUFFIX = "/contract"
SESSION_BUDGET_SUFFIX = "/budget"
QUESTIONS_SEGMENT = "/questions/"
FORMULA_SUFFIX = "/formula"
READING_SUFFIX = "/reading"
DEFAULT_SESSIONS_PAGE_LIMIT = 100
MAX_SESSIONS_PAGE_LIMIT = 500
DEFAULT_CONVERGENCES_PAGE_LIMIT = 100
MAX_CONVERGENCES_PAGE_LIMIT = 500


def _parse_pagination(
    *, environ: WSGIEnvironment, default_limit: int, max_limit: int
) -> tuple[int, int] | ProblemPayload:
    """Parse the limit/offset query parameters, or a problem payload (ADR-0016)."""
    query = environ.get("QUERY_STRING", "")
    params: dict[str, str] = {}
    for pair in query.split("&"):
        if "=" in pair:
            key, value = pair.split("=", maxsplit=1)
            params[key] = value
    try:
        limit = min(int(params.get("limit", str(default_limit))), max_limit)
        offset = max(0, int(params.get("offset", "0")))
    except ValueError:
        return _problem(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_pagination",
            detail="The limit and offset query parameters must be integers.",
        )
    return limit, offset


CONTENT_TYPE_PNML = "application/pnml+xml"
MAX_UPLOAD_BYTES = 100 << 20
"""Upload quota of POST /v1/models (PNML or archive), same order as the catalog quota."""
ACCEPTED_UPLOAD_TYPES = frozenset({CONTENT_TYPE_PNML, *ARCHIVE_CONTENT_TYPES})
"""The media types POST /v1/models accepts (spec §21.3)."""
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_PETRI_JSON = "application/petri+json; version=1.0"
CONTENT_TYPE_PROBLEM_JSON = "application/problem+json"
CONTENT_TYPE_CONTRACT_JSON = "application/contract+json; version=1.0"
CONTENT_TYPE_MARKDOWN = "text/markdown; charset=utf-8"
UI_PATH = "/ui/"
UI_INDEX = "index.html"
AUTH_LOGIN_PATH = "/auth/login"
AUTH_CALLBACK_PATH = "/auth/callback"
AUTH_LOGOUT_PATH = "/auth/logout"
AUTH_ME_PATH = "/auth/me"
CONTENT_TYPE_HTML = "text/html; charset=utf-8"
CONTENT_TYPE_JAVASCRIPT = "text/javascript; charset=utf-8"
CONTENT_TYPE_CSS = "text/css; charset=utf-8"
UI_STATIC_DIRECTORY = Path(__file__).parent / "static"
# ADR-0101: the UI v2 is a built SPA (Vite) - assets are hashed files served
# by suffix, not a filename whitelist; extension-less paths are client-side
# routes and get the index.html shell.
UI_CONTENT_TYPES_BY_SUFFIX = {
    ".html": CONTENT_TYPE_HTML,
    ".js": CONTENT_TYPE_JAVASCRIPT,
    ".css": CONTENT_TYPE_CSS,
    ".svg": "image/svg+xml",
}
PROBLEM_TYPE_BASE_URL = "https://petrichor.dev/problems/"
WATCHDOG_GRACE_SECONDS = 5
"""Extra seconds the watchdog waits past the run timeout before forcing the error state."""
ACCEPTABLE_MEDIA_TYPES = frozenset(
    {"*/*", "application/*", "application/petri+json", "application/json"}
)


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


CONTRACT_JSON_MEDIA = "application/contract+json"
CONTRACT_MARKDOWN_MEDIA = "text/markdown"
CONTRACT_NEGOTIABLE_MEDIA = frozenset({CONTRACT_MARKDOWN_MEDIA, CONTRACT_JSON_MEDIA, "*/*"})


def _negotiate_contract_media(*, accept_header: str | None) -> Literal["markdown", "json"] | None:
    """Negotiate the contract representation by q-value, or None when refused (406).

    Standard content negotiation (spec §5): the acceptable entry with the
    highest q-value wins, ties keep the client's order, and an entry with
    q=0 is refused - so ``text/markdown;q=0, application/contract+json``
    negotiates to the JSON document.
    """
    if accept_header is None or not accept_header.strip():
        return "json"
    best_media: str | None = None
    best_quality = 0.0
    for entry in accept_header.split(","):
        parts = entry.split(";")
        media = parts[0].strip().lower()
        if media not in CONTRACT_NEGOTIABLE_MEDIA:
            continue
        quality = 1.0
        for parameter in parts[1:]:
            key, _, value = parameter.strip().partition("=")
            if key == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > best_quality:
            best_quality = quality
            best_media = media
    if best_media is None or best_quality <= 0:
        return None
    return "markdown" if best_media == CONTRACT_MARKDOWN_MEDIA else "json"


class ProblemPayload(BaseModel):
    """One RFC 7807 problem+json document (ADR-0016): the typed error record.

    A record instead of a growing parameter list (ADR-0003): every error of
    the API is one of these, serialized with a stable shape.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: HTTPStatus
    reason: str = Field(min_length=1)
    detail: str = Field(min_length=1)
    instance: str = ""
    extensions: dict[str, float | int] = Field(default_factory=dict)

    def body(self) -> bytes:
        """Serialize the payload as problem+json with a stable shape."""
        payload = {
            "type": f"{PROBLEM_TYPE_BASE_URL}{self.reason}",
            "title": self.status.phrase,
            "status": self.status.value,
            "detail": self.detail,
            "instance": self.instance,
            "extensions": {"reason": self.reason, **self.extensions},
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _problem(
    *,
    status: HTTPStatus,
    reason: str,
    detail: str,
    extensions: dict[str, float | int] | None = None,
) -> ProblemPayload:
    """Build one problem payload; the instance is filled at response time."""
    return ProblemPayload(status=status, reason=reason, detail=detail, extensions=extensions or {})


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
    payload: ProblemPayload,
) -> Iterable[bytes]:
    """Respond with the documented problem+json error payload."""
    body = payload.model_copy(update={"instance": environ.get("PATH_INFO", "/")}).body()
    return _respond(
        start_response=start_response,
        status=payload.status,
        content_type=CONTENT_TYPE_PROBLEM_JSON,
        body=body,
    )


def _read_body(
    *, environ: WSGIEnvironment, start_response: StartResponse
) -> bytes | ProblemPayload:
    """Read the request body under the upload quota; ProblemPayload on 400/413."""
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_content_length",
            detail="The Content-Length header is not an integer.",
        )
    if length < 0:
        return _problem(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_content_length",
            detail="The Content-Length header is negative.",
        )
    if length > MAX_UPLOAD_BYTES:
        return _problem(
            status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            reason="quota_exceeded",
            detail=f"The upload exceeds the quota of {MAX_UPLOAD_BYTES} bytes.",
        )
    stream = environ["wsgi.input"]
    # Read at most quota+1 bytes: a lying Content-Length cannot bypass the quota.
    return stream.read(min(length, MAX_UPLOAD_BYTES + 1)) if length else b""


def _store_pnml_text(
    *,
    text: str,
    provenance: dict[str, str],
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
) -> Iterable[bytes]:
    """Import one PNML text into the canonical form and store it (201 new, 200 idempotent)."""
    try:
        model = import_pnml(text=text)
    except (PnmlImportError, InvalidCanonicalModelError) as error:
        logger.warning("api_model_import_rejected", reason=type(error).__name__, detail=str(error))
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY, reason="invalid_pnml", detail=str(error)
            ),
        )
    model = model.model_copy(update={"metadata": {**model.metadata, "provenance": provenance}})
    stored, created = store.save(model=model)
    if not created:
        store.replace(model=model)
    status = HTTPStatus.CREATED if created else HTTPStatus.OK
    logger.info(
        "api_model_stored",
        model_id=stored.id,
        content_hash=stored.content_hash,
        created=created,
        status=status.value,
        provenance_source=provenance.get("source", "unknown"),
    )
    extra_headers = [("Location", f"{MODELS_PATH}/{stored.id}")] if created else []
    return _respond(
        start_response=start_response,
        status=status,
        content_type=CONTENT_TYPE_PETRI_JSON,
        body=serialize_model(model=stored).encode("utf-8"),
        extra_headers=extra_headers,
    )


def _candidates_payload(*, candidates: list[PnmlCandidate]) -> bytes:
    """The typed 409 body listing the PNML candidates of a multi-PNML source."""
    return json.dumps(
        {
            "reason": "multiple_candidates",
            "detail": (
                f"The source contains {len(candidates)} PNML documents;"
                " re-POST with the candidate query parameter to choose one."
            ),
            "candidates": [
                candidate.model_dump(mode="json", exclude={"content"}) for candidate in candidates
            ],
        }
    ).encode("utf-8")


def _import_from_candidates(
    *,
    candidates: list[PnmlCandidate],
    candidate_name: str | None,
    provenance: dict[str, str],
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
) -> Iterable[bytes]:
    """The one-PNML-or-candidates rule (spec §21.3): never a silent multi-import."""
    if candidate_name is not None:
        chosen = next((c for c in candidates if c.name == candidate_name), None)
        if chosen is None:
            return _problem_response(
                start_response=start_response,
                environ=environ,
                payload=_problem(
                    status=HTTPStatus.NOT_FOUND,
                    reason="unknown_candidate",
                    detail=f"No PNML candidate named {candidate_name!r} in the source.",
                ),
            )
    elif len(candidates) == 1:
        chosen = candidates[0]
    else:
        logger.info("api_model_candidates_listed", candidates=len(candidates))
        return _respond(
            start_response=start_response,
            status=HTTPStatus.CONFLICT,
            content_type=CONTENT_TYPE_JSON,
            body=_candidates_payload(candidates=candidates),
        )
    provenance = {**provenance, "candidate": chosen.name, "sha256": chosen.sha256}
    return _store_pnml_text(
        text=chosen.content,
        provenance=provenance,
        environ=environ,
        start_response=start_response,
        store=store,
    )


def _import_archive(
    *,
    body: bytes,
    content_type: str,
    candidate_name: str | None,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
) -> Iterable[bytes]:
    """Extract the PNML candidates of an uploaded archive, then apply the choice rule."""
    try:
        candidates = extract_pnml_candidates(data=body, content_type=content_type)
    except UnsafeArchiveError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="unsafe_archive",
                detail=str(error),
            ),
        )
    except NoPnmlError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY, reason="no_pnml", detail=str(error)
            ),
        )
    except ArchiveError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_archive",
                detail=str(error),
            ),
        )
    provenance = {
        "source": "upload",
        "content_type": content_type,
        "archive_sha256": f"sha256:{hashlib.sha256(body).hexdigest()}",
    }
    return _import_from_candidates(
        candidates=candidates,
        candidate_name=candidate_name,
        provenance=provenance,
        environ=environ,
        start_response=start_response,
        store=store,
    )


_EXTENSION_CONTENT_TYPES: dict[str, str] = {
    ".pnml": CONTENT_TYPE_PNML,
    ".xml": CONTENT_TYPE_PNML,
    ".zip": "application/zip",
    ".tgz": "application/gzip",
    ".tar.gz": "application/gzip",
    ".tar": "application/x-tar",
}
"""The media types of a downloaded document by URL extension (spec §21.3)."""


def _sniff_content_type(*, url: str, data: bytes) -> str:
    """The media type of a downloaded document, by extension then by magic bytes."""
    path = urlsplit(url).path.lower()
    for extension, media_type in _EXTENSION_CONTENT_TYPES.items():
        if path.endswith(extension):
            return media_type
    if data.startswith(b"PK\x03\x04"):
        return "application/zip"
    if data.startswith(b"\x1f\x8b"):
        return "application/gzip"
    if data.lstrip().startswith(b"<"):
        return CONTENT_TYPE_PNML
    return ""


class ModelImportUrlBody(BaseModel):
    """The JSON body of POST /v1/models/import-url."""

    url: ImportUrl
    candidate: str | None = None


def _post_models_import_url(  # noqa: PLR0911 - one typed error per URL-import failure mode
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    url_import: UrlImportConfig,
) -> Iterable[bytes]:
    """Import a PNML or an archive from an allowlisted https URL (spec §21.3, ADR-0008)."""
    body = _read_body(environ=environ, start_response=start_response)
    if isinstance(body, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=body)
    try:
        payload = ModelImportUrlBody.model_validate_json(body)
    except ValidationError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_body",
                detail=str(error),
            ),
        )
    try:
        data = download_url(url=payload.url, config=url_import)
    except UrlNotAllowedError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY, reason="url_not_allowed", detail=str(error)
            ),
        )
    except PrivateNetworkError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="private_network_rejected",
                detail=str(error),
            ),
        )
    except UrlQuotaExceededError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                reason="quota_exceeded",
                detail=str(error),
            ),
        )
    except DownloadFailedError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.BAD_GATEWAY, reason="download_failed", detail=str(error)
            ),
        )
    content_type = _sniff_content_type(url=payload.url, data=data)
    provenance = {
        "source": "url",
        "url": payload.url,
        "archive_sha256": f"sha256:{hashlib.sha256(data).hexdigest()}",
    }
    if content_type == CONTENT_TYPE_PNML:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            return _problem_response(
                start_response=start_response,
                environ=environ,
                payload=_problem(
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                    reason="invalid_pnml",
                    detail=f"The downloaded document is not valid UTF-8: {error}",
                ),
            )
        return _store_pnml_text(
            text=text,
            provenance=provenance,
            environ=environ,
            start_response=start_response,
            store=store,
        )
    if content_type in ARCHIVE_CONTENT_TYPES:
        return _import_archive(
            body=data,
            content_type=content_type,
            candidate_name=payload.candidate,
            environ=environ,
            start_response=start_response,
            store=store,
        )
    return _problem_response(
        start_response=start_response,
        environ=environ,
        payload=_problem(
            status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            reason="unsupported_media_type",
            detail=f"The document at {payload.url} is neither PNML nor a supported archive.",
        ),
    )


def _post_models(
    *, environ: WSGIEnvironment, start_response: StartResponse, store: ModelStore
) -> Iterable[bytes]:
    """Import a PNML document or an archive into the canonical form (201 new, 200 idempotent)."""
    content_type = _media_type(header_value=environ.get("CONTENT_TYPE"))
    if content_type not in ACCEPTED_UPLOAD_TYPES:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                reason="unsupported_media_type",
                detail=(
                    f"POST {MODELS_PATH} expects Content-Type one of"
                    f" {', '.join(sorted(ACCEPTED_UPLOAD_TYPES))}, got {content_type or 'none'}."
                ),
            ),
        )
    body = _read_body(environ=environ, start_response=start_response)
    if isinstance(body, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=body)
    candidate_name = parse_qs(environ.get("QUERY_STRING", "")).get("candidate", [None])[0]
    if content_type in ARCHIVE_CONTENT_TYPES:
        return _import_archive(
            body=body,
            content_type=content_type,
            candidate_name=candidate_name,
            environ=environ,
            start_response=start_response,
            store=store,
        )
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_pnml",
                detail=f"The body is not valid UTF-8: {error}",
            ),
        )
    return _store_pnml_text(
        text=text,
        provenance={"source": "upload", "content_type": content_type},
        environ=environ,
        start_response=start_response,
        store=store,
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
            payload=_problem(
                status=HTTPStatus.NOT_ACCEPTABLE,
                reason="not_acceptable",
                detail=(
                    f"This endpoint serves {CONTENT_TYPE_PETRI_JSON};"
                    " PNML re-export is not supported yet."
                ),
            ),
        )
    model = store.get(model_id=ModelId(model_id))
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {model_id!r}.",
            ),
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

    def __init__(self, *, payload: ProblemPayload) -> None:
        super().__init__(payload.detail)
        self.payload = payload


def _is_check_family(value: str) -> TypeGuard[CheckFamilyName]:
    """Narrow a path segment to a known check family (spec §12.1)."""
    return value in FAMILY_PATTERNS


def _parse_check_request(*, environ: WSGIEnvironment) -> CheckRequest:
    """Read and validate the JSON body of a check request, or raise a problem."""
    content_type = _media_type(header_value=environ.get("CONTENT_TYPE"))
    if content_type != CONTENT_TYPE_JSON:
        raise _ProblemPayloadError(
            payload=_problem(
                status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                reason="unsupported_media_type",
                detail=f"POST {CHECKS_PATH_PREFIX}{{family}} expects Content-Type"
                f" {CONTENT_TYPE_JSON}, got {content_type or 'none'}.",
            )
        )
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        raise _ProblemPayloadError(
            payload=_problem(
                status=HTTPStatus.BAD_REQUEST,
                reason="invalid_content_length",
                detail="The Content-Length header is not an integer.",
            )
        ) from None
    body = environ["wsgi.input"].read(length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise _ProblemPayloadError(
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_json",
                detail=f"The body is not valid JSON: {error}",
            )
        ) from None
    try:
        return CheckRequest.model_validate(payload)
    except ValidationError as error:
        raise _ProblemPayloadError(
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_check_request",
                detail=f"The check request does not conform to the contract: {error}",
            )
        ) from None


def _execute_run(
    *,
    runner: CheckRunner,
    run_store: RunStore,
    run_id: RunId,
    question: CheckQuestion,
    timeout_seconds: int,
    started_monotonic: float,
    qualification: QualificationLookup | None,
    witness_store: WitnessStore | None = None,
) -> None:
    """Run one check on a background thread and record its explicit outcome.

    The cost of the run is the wall-clock tool time measured around the
    runner call (spec §12.4) - never an estimate - and is archived with the
    run together with the command the runner reports (spec §12.5). A witness
    in the output is normalized to §16, stored once and referenced by the
    run (spec §16, issue #32).
    """
    logger.debug("check_run_started", run_id=run_id, family=question.family, kind=question.kind)
    try:
        outcome: CheckOutcome = runner.run(question=question, timeout_seconds=timeout_seconds)
    except Exception as error:  # noqa: BLE001 - a crashed runner must still become an explicit error state, never a run stuck in "running"
        measured_seconds = time.monotonic() - started_monotonic
        logger.error("check_run_crashed", run_id=run_id, error=repr(error))
        run_store.mark_error(
            run_id=run_id,
            kind="engine_error",
            detail=repr(error),
            command=[],
            measured_seconds=measured_seconds,
        )
        return
    measured_seconds = time.monotonic() - started_monotonic
    if isinstance(outcome, CheckCompleted):
        output = outcome.output
        fairness = question.parameters.get("fairness", "none")
        if fairness != "none":
            # No wired tool supports fairness natively (spec §17.5, P29
            # follow-up): the run is labeled tool-specific and the verdict
            # is explicitly NOT verifiable under fairness - never masked.
            output = {
                **output,
                "semantics": TOOL_SPECIFIC_SEMANTICS,
                "fairness": fairness,
                "fairness_supported": runner.tool_version in FAIRNESS_CAPABLE_TOOLS,
            }
            logger.warning(
                "check_run_fairness_tool_specific",
                run_id=run_id,
                fairness=fairness,
                tool_version=runner.tool_version,
            )
        engine_qualified = (
            qualification.is_qualified(family=question.family)
            if qualification is not None
            else None
        )
        logger.debug(
            "check_run_engine_qualification",
            run_id=run_id,
            family=question.family,
            engine_qualified=engine_qualified,
        )
        if witness_store is not None:
            # Stored and referenced BEFORE the terminal state: a poller that
            # sees "done" must already see the witness (spec §16, issue #32).
            try:
                document = normalize_witness_output(run_id=run_id, output=output)
            except WitnessError as error:
                logger.error("check_run_witness_invalid", run_id=run_id, error=str(error))
                document = None
            if document is not None:
                witness_store.store(record=witness_record_of(document=document))
                witness_store.add_reference(witness_id=document.witness_id, run_id=run_id)
        run_store.mark_done(
            run_id=run_id,
            output=output,
            command=outcome.command,
            measured_seconds=measured_seconds,
            engine_qualified=engine_qualified,
        )
        logger.debug(
            "check_run_finished", run_id=run_id, status="done", measured_seconds=measured_seconds
        )
    else:
        run_store.mark_error(
            run_id=run_id,
            kind=outcome.kind,
            detail=outcome.detail,
            command=outcome.command,
            measured_seconds=measured_seconds,
        )
        logger.debug(
            "check_run_finished",
            run_id=run_id,
            status="error",
            error_kind=outcome.kind,
            measured_seconds=measured_seconds,
        )


def _watchdog(
    *, run_store: RunStore, run_id: RunId, timeout_seconds: int, started_monotonic: float
) -> None:
    """Backstop: a runner that ignores its timeout still ends in an explicit error state."""
    run = run_store.get(run_id=run_id)
    if run is not None and run.status == "running":
        measured_seconds = time.monotonic() - started_monotonic
        logger.warning("check_run_watchdog_fired", run_id=run_id, timeout_seconds=timeout_seconds)
        run_store.mark_error(
            run_id=run_id,
            kind="timeout",
            detail=f"runner did not answer within {timeout_seconds}s (watchdog backstop)",
            command=[],
            measured_seconds=measured_seconds,
        )


def _triage_and_resolve_static(
    *,
    static_resolver: StaticResolver | None,
    model: CanonicalModel,
    family: CheckFamilyName,
    request: CheckRequest,
    question: CheckQuestion,
) -> tuple[QuestionTriage, NonVacuityGuard | None, dict[str, JsonValue] | None]:
    """Triage a question and try the static resolution (spec §14.2, issue #14).

    Returns the triage, the non-vacuity guard and the static output when the
    embedded engine decided the question without any MC run. When the static
    proof cannot be found, the triage is explicitly degraded - the failure of
    a heuristic is never read as a verdict - and the output stays None.
    """
    capacity = static_resolver.capacity(model=model) if static_resolver is not None else None
    triage = triage_question(kind=request.kind, capacity=capacity)
    guard = (
        static_resolver.non_vacuity_guard(
            model=model, family=family, kind=request.kind, formula=request.formula
        )
        if static_resolver is not None
        else None
    )
    logger.debug(
        "api_check_triaged",
        family=family,
        kind=request.kind,
        triage_kind=triage.kind,
        justification=triage.justification,
        guard_status=guard.status if guard is not None else None,
    )
    static_output: dict[str, JsonValue] | None = None
    if triage.kind == "statique" and static_resolver is not None:
        try:
            static_output = static_resolver.resolve_static(model=model, question=question)
        except StaticUndecidableError as error:
            triage = QuestionTriage(
                kind="dégradé",
                justification=(
                    f"Preuve statique non trouvée ({error}) — question déclassée MC (spec §14.2)."
                ),
            )
            logger.warning(
                "api_check_static_undecidable", family=family, kind=request.kind, detail=str(error)
            )
    return triage, guard, static_output


def _static_first_probe(
    *,
    static_first_prober: StaticFirstProber,
    model: CanonicalModel,
    family: CheckFamilyName,
    request: CheckRequest,
    question: CheckQuestion,
    triage: QuestionTriage,
    timeout_seconds: int,
) -> tuple[QuestionTriage, dict[str, JsonValue] | None, CheckQuestion, ToolVersion]:
    """Probe a degraded question with the reducer before any MC run (spec §18.4, issue #25).

    A decided probe is a static proof - no MC run is emitted, the triage
    becomes an explicit ``statique`` proof and the output is labeled
    ``technique: static``. An undecided probe yields the reduced net and the
    projected formula: the returned execution question targets the reduced
    model while the caller keeps the original question for the idempotence
    key. Both paths use the same clause states as the §14 triage - a proof or
    an explicit degradation, never a home-made verdict.
    """
    probe = static_first_prober.probe(
        model=model, question=question, timeout_seconds=timeout_seconds
    )
    logger.debug(
        "api_check_static_first_probed",
        family=family,
        kind=request.kind,
        verdict=probe.verdict,
        tool_version=probe.tool_version,
    )
    if probe.verdict != "unknown" and probe.output is not None:
        static_output = {**probe.output, "technique": "static"}
        proved_triage = QuestionTriage(
            kind="statique",
            justification=(
                f"Preuve statique d'abord ({probe.tool_version}) — aucun run MC"
                " (spec §18.4, issue #25)."
            ),
        )
        return proved_triage, static_output, question, probe.tool_version
    if probe.reduced_model is not None:
        reduced_question = CheckQuestion(
            family=family,
            kind=request.kind,
            formula=probe.projected_formula or question.formula,
            parameters={**question.parameters, "model": probe.reduced_model},
        )
        logger.debug(
            "api_check_static_first_reduced",
            family=family,
            kind=request.kind,
            formula=reduced_question.formula,
        )
        return triage, None, reduced_question, STATIC_TOOL_VERSION
    return triage, None, question, STATIC_TOOL_VERSION


def _resolve_tool_version(
    *,
    runner: CheckRunner | None,
    static_output: dict[str, JsonValue] | None,
    static_tool_version: ToolVersion,
) -> ToolVersion | ProblemPayload:
    """The tool version of the run: the static engine when it proved, else the runner's."""
    if static_output is not None:
        return static_tool_version
    if runner is None:
        return _problem(
            status=HTTPStatus.SERVICE_UNAVAILABLE,
            reason="runner_not_configured",
            detail="No check runner is wired into this server yet (real engines: P09+).",
        )
    return runner.tool_version


def _admit_check_run(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    request: CheckRequest,
    run_store: RunStore,
    session_store: SessionStore | None,
    settings: ApiSettings,
    family: CheckFamilyName,
    question: CheckQuestion,
    timeout_seconds: int,
    tool_version: ToolVersion,
    triage: QuestionTriage,
    guard: NonVacuityGuard | None,
    static_output: dict[str, JsonValue] | None,
) -> tuple[SessionId | None, AdmissionResult] | ProblemPayload:
    """Resolve the session budget and admit the run atomically (spec §12.4, issue #33).

    Returns the session id with the admission result, or a problem payload
    (unknown session, or the explicit 429 budget refusal).
    """
    session_budget = _resolve_session_budget(
        request=request, session_store=session_store, settings=settings
    )
    if isinstance(session_budget, ProblemPayload):
        return session_budget
    session_id, session_budget_seconds = session_budget
    admission = RunAdmission(
        key=run_key(model_id=request.model_id, question=question),
        family=family,
        kind=request.kind,
        formula=request.formula,
        model_id=request.model_id,
        timeout_seconds=timeout_seconds,
        tool_version=tool_version,
        session_budget_seconds=session_budget_seconds,
        session_id=session_id,
        fairness=request.fairness,
        triage=triage,
        non_vacuity=guard,
    )
    result = run_store.admit(
        admission=admission,
        reservation_seconds=(
            STATIC_COST_SECONDS
            if static_output is not None
            else timeout_seconds + WATCHDOG_GRACE_SECONDS
        ),
    )
    return session_id, result


def _attach_run_to_session(
    *,
    session_store: SessionStore | None,
    session_id: SessionId | None,
    run_id: RunId,
    created: bool,
) -> None:
    """Attach a newly created run to its session (spec §12.4, issue #33)."""
    if created and session_id is not None and session_store is not None:
        session_store.append_run(session_id=session_id, run_id=run_id)


def _resolve_session_budget(
    *,
    request: CheckRequest,
    session_store: SessionStore | None,
    settings: ApiSettings,
) -> tuple[SessionId | None, float] | ProblemPayload:
    """The (session id, budget cap) a check is charged to (spec §12.4, issue #33).

    Without ``session_id`` the run goes to the global bucket with the server
    cap; with one, the session must exist and its own cap applies.
    """
    if request.session_id is None:
        return None, settings.session_budget_seconds
    if session_store is None:
        return _problem(
            status=HTTPStatus.SERVICE_UNAVAILABLE,
            reason="sessions_not_configured",
            detail="No session store is wired into this server.",
        )
    session = session_store.get(session_id=request.session_id)
    if session is None:
        return _problem(
            status=HTTPStatus.NOT_FOUND,
            reason="session_not_found",
            detail=f"No session with id {request.session_id!r}.",
        )
    return session.session_id, session.budget_seconds


def _post_check(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    run_store: RunStore,
    runner: CheckRunner | None,
    family: CheckFamilyName,
    settings: ApiSettings,
    qualification: QualificationLookup | None,
    static_resolver: StaticResolver | None,
    static_first_prober: StaticFirstProber | None,
    witness_store: WitnessStore | None = None,
    session_store: SessionStore | None = None,
) -> Iterable[bytes]:
    """Accept a check question: 202 + Location, idempotent by (model, question) hash.

    Admission is atomic in the store (spec §12.4): the session budget check
    and the run creation happen under one lock, with in-flight reservations,
    so the cumulative measured cost never exceeds the cap - a run that does
    not fit is refused with an explicit 429 carrying the remaining budget.

    Every question is triaged first (spec §14.2, issue #14): a statically
    triaged question is resolved by the embedded engine without any MC run
    (cost zero, ``technique: static``); when the static proof cannot be found
    the question is explicitly degraded and displayed as such on the run.
    """
    timeout_seconds = settings.check_timeout_seconds
    try:
        request = _parse_check_request(environ=environ)
        if request.kind not in FAMILY_PATTERNS[family]:
            raise _ProblemPayloadError(
                payload=_problem(
                    status=HTTPStatus.UNPROCESSABLE_ENTITY,
                    reason="pattern_not_in_family",
                    detail=(
                        f"Pattern {request.kind!r} does not belong to family {family!r};"
                        " it is rejected, never reinterpreted (spec §12.1)."
                    ),
                )
            )
        model = store.get(model_id=request.model_id)
        if model is None:
            raise _ProblemPayloadError(
                payload=_problem(
                    status=HTTPStatus.NOT_FOUND,
                    reason="model_not_found",
                    detail=f"No model with id {request.model_id!r}.",
                )
            )
    except _ProblemPayloadError as problem:
        return _problem_response(
            start_response=start_response, environ=environ, payload=problem.payload
        )
    # The model travels inside the question parameters: the runner (engines/)
    # ships it to the engine container, which knows no model store (spec §7).
    # Only the semantic projection goes in - it is what the content hash
    # covers, so the idempotence key stays stable across app instances
    # (metadata like imported_at would break it).
    question = CheckQuestion(
        family=family,
        kind=request.kind,
        formula=request.formula,
        parameters={
            **request.parameters,
            "model": json.loads(canonical_json(payload=semantic_projection(model=model))),
            # The fairness parameter is part of the question: each parameter
            # set is a distinct run, billed as such (spec §14.7, issue #35).
            "fairness": request.fairness,
        },
    )
    triage, guard, static_output = _triage_and_resolve_static(
        static_resolver=static_resolver,
        model=model,
        family=family,
        request=request,
        question=question,
    )
    # Static-first (opt-in, spec §18.4, issue #25): a question the embedded
    # engine could not decide (dégradé, typically a net beyond its capacity)
    # is probed by the reducer before any exhaustive MC run. The idempotence
    # key always covers the original question.
    execution_question = question
    static_tool_version = STATIC_TOOL_VERSION
    if (
        static_output is None
        and triage.kind == "dégradé"
        and settings.static_first
        and static_first_prober is not None
    ):
        triage, static_output, execution_question, static_tool_version = _static_first_probe(
            static_first_prober=static_first_prober,
            model=model,
            family=family,
            request=request,
            question=question,
            triage=triage,
            timeout_seconds=timeout_seconds,
        )
    tool_version = _resolve_tool_version(
        runner=runner, static_output=static_output, static_tool_version=static_tool_version
    )
    if isinstance(tool_version, ProblemPayload):
        return _problem_response(
            start_response=start_response, environ=environ, payload=tool_version
        )
    admission_result = _admit_check_run(
        request=request,
        run_store=run_store,
        session_store=session_store,
        settings=settings,
        family=family,
        question=question,
        timeout_seconds=timeout_seconds,
        tool_version=tool_version,
        triage=triage,
        guard=guard,
        static_output=static_output,
    )
    if isinstance(admission_result, ProblemPayload):
        return _problem_response(
            start_response=start_response, environ=environ, payload=admission_result
        )
    session_id, result = admission_result
    if isinstance(result, BudgetRefused):
        logger.warning(
            "api_check_budget_refused",
            remaining_budget_seconds=result.remaining_budget_seconds,
            session_budget_seconds=result.session_budget_seconds,
            consumed_seconds=result.consumed_seconds,
            reserved_seconds=result.reserved_seconds,
            timeout_seconds=result.timeout_seconds,
        )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.TOO_MANY_REQUESTS,
                reason="session_budget_exceeded",
                detail=(
                    f"Session budget exceeded: {result.remaining_budget_seconds:.3f}s of tool time"
                    f" remain (cap {result.session_budget_seconds}s,"
                    f" consumed {result.consumed_seconds:.3f}s,"
                    f" reserved {result.reserved_seconds:.3f}s), but one run can consume up to"
                    f" {timeout_seconds + WATCHDOG_GRACE_SECONDS}s (timeout + watchdog grace)."
                ),
                extensions={
                    "remaining_budget_seconds": result.remaining_budget_seconds,
                    "session_budget_seconds": result.session_budget_seconds,
                    "consumed_seconds": result.consumed_seconds,
                    "reserved_seconds": result.reserved_seconds,
                    "timeout_seconds": result.timeout_seconds,
                },
            ),
        )
    run, created = result.run, result.created
    _attach_run_to_session(
        session_store=session_store, session_id=session_id, run_id=run.run_id, created=created
    )
    if created:
        if static_output is not None:
            run_store.mark_done(
                run_id=run.run_id,
                output=static_output,
                command=[],
                measured_seconds=STATIC_COST_SECONDS,
            )
            logger.debug(
                "api_check_static_resolved",
                run_id=run.run_id,
                family=family,
                kind=request.kind,
                verdict=static_output.get("verdict"),
            )
        else:
            started_monotonic = time.monotonic()
            thread = threading.Thread(
                target=_execute_run,
                kwargs={
                    "runner": runner,
                    "run_store": run_store,
                    "run_id": run.run_id,
                    "question": execution_question,
                    "timeout_seconds": timeout_seconds,
                    "started_monotonic": started_monotonic,
                    "qualification": qualification,
                    "witness_store": witness_store,
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
                    "started_monotonic": started_monotonic,
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
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="run_not_found",
                detail=f"No run with id {run_id!r}.",
            ),
        )
    logger.debug("api_run_read", run_id=run.run_id, status=run.status)
    body = json.dumps(run.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_run_witness(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    run_store: RunStore,
    store: ModelStore,
    witness_store: WitnessStore,
    llm_provider: LLMProvider | None,
    run_id: str,
) -> Iterable[bytes]:
    """The §16 witness document of one run, with its per-step LLM narrations (issue #32).

    The structure comes from the tool (markings, firings, cyclicity,
    back-edge); the narrations are LLM propositions labeled
    ``provenance: llm``, never verdicts (spec §16, §19.4).
    """
    run = run_store.get(run_id=RunId(run_id))
    if run is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="run_not_found",
                detail=f"No run with id {run_id!r}.",
            ),
        )
    if run.output is None or run.output.get("witness") is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="witness_not_found",
                detail=f"Run {run_id!r} carries no witness (status {run.status!r}).",
            ),
        )
    try:
        document = normalize_witness_output(run_id=run.run_id, output=run.output)
    except WitnessError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
                reason="witness_invalid",
                detail=str(error),
            ),
        )
    if document is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="witness_not_found",
                detail=f"Run {run_id!r} carries no normalizable witness.",
            ),
        )
    if llm_provider is not None:
        model = store.get(model_id=run.model_id)
        if model is not None:
            document = document.model_copy(
                update={
                    "narrations": narrate_steps(
                        provider=llm_provider,
                        model=model,
                        steps=document.steps,
                    )
                }
            )
    logger.debug(
        "api_run_witness_read",
        run_id=run.run_id,
        witness_id=document.witness_id,
        steps=len(document.steps),
        narrations=len(document.narrations),
    )
    body = json.dumps(document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_witness(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    witness_store: WitnessStore,
    witness_id: str,
) -> Iterable[bytes]:
    """One stored witness with its referencing runs (stored once, referenced n times, §16)."""
    record = witness_store.get(witness_id=WitnessId(witness_id))
    if record is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="witness_not_found",
                detail=f"No witness with id {witness_id!r}.",
            ),
        )
    references = witness_store.references(witness_id=record.witness_id)
    logger.debug("api_witness_read", witness_id=record.witness_id, references=len(references))
    body = json.dumps(
        {
            "record": record.model_dump(mode="json"),
            "referenced_by": [str(run_id) for run_id in references],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_qualification(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    qualification: QualificationLookup | None,
) -> Iterable[bytes]:
    """Expose the stored qualification status per (adapter, family) (spec §17.5, issue #22)."""
    if qualification is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="qualification_not_configured",
                detail="No qualification lookup is wired into this server yet (issue #22).",
            ),
        )
    snapshot = qualification.snapshot()
    logger.debug("api_qualification_read", engines=len(snapshot))
    body = json.dumps({"engines": snapshot}, sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _dispatch_models(  # noqa: PLR0911 - one typed response per route/method
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    store: ModelStore,
    url_import: UrlImportConfig | None,
) -> Iterable[bytes] | None:
    """Route the /v1/models resources, or return None when the path is not one."""
    if path == MODELS_IMPORT_URL_PATH:
        if method == "POST":
            if url_import is None:
                return _problem_response(
                    start_response=start_response,
                    environ=environ,
                    payload=_problem(
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                        reason="url_import_not_configured",
                        detail="URL import is not configured on this server.",
                    ),
                )
            return _post_models_import_url(
                environ=environ,
                start_response=start_response,
                store=store,
                url_import=url_import,
            )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED,
                reason="method_not_allowed",
                detail=f"{method} is not supported on {MODELS_IMPORT_URL_PATH}.",
            ),
        )
    if path == MODELS_PATH:
        if method == "POST":
            return _post_models(environ=environ, start_response=start_response, store=store)
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED,
                reason="method_not_allowed",
                detail=f"{method} is not supported on {MODELS_PATH}.",
            ),
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
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.NOT_FOUND,
                reason="method_not_allowed" if method != "GET" else "model_not_found",
                detail=f"No resource at {path}.",
            ),
        )
    return None


class CatalogImportBody(BaseModel):
    """The body of ``POST /v1/catalog/import``: the instance name to import."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)


def _catalog_query_from_environ(*, environ: WSGIEnvironment) -> CatalogQuery | ProblemPayload:
    """Build the listing query from the query string (search, facets, sort, pagination)."""
    pagination = _parse_pagination(environ=environ, default_limit=20, max_limit=100)
    if isinstance(pagination, ProblemPayload):
        return pagination
    limit, offset = pagination
    params = {
        key: values[-1]
        for key, values in parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True).items()
    }
    facets = {
        key: value
        for key, value in params.items()
        if key in ("family", "formalism", "edition", "source", "size")
    }
    sort = parse_sort(value=params.get("sort", "name"))
    if sort is None:
        return _problem(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_sort",
            detail="The sort parameter must be one of name, family, edition, size.",
        )
    return CatalogQuery(
        search=params.get("search", ""),
        facets=facets,
        sort=sort,
        limit=limit,
        offset=offset,
    )


def _get_catalog_models(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    catalog: ModelCatalog,
) -> Iterable[bytes]:
    """Paginated catalog listing with name search and facets (spec §21.2, ADR-0016)."""
    query = _catalog_query_from_environ(environ=environ)
    if isinstance(query, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=query)
    try:
        page = catalog.search(query=query)
    except CatalogUnavailableError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="catalog_unavailable",
                detail=str(error),
            ),
        )
    logger.debug("api_catalog_listed", total=page.total, limit=page.limit, offset=page.offset)
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=page.model_dump_json().encode("utf-8"),
    )


def _post_catalog_import(  # noqa: PLR0911 - one typed error per catalog failure mode
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    catalog: ModelCatalog,
    store: ModelStore,
) -> Iterable[bytes]:
    """Import one catalog instance by name, idempotent on the content hash (spec §21.2)."""
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.BAD_REQUEST,
                reason="invalid_content_length",
                detail="The Content-Length header is not an integer.",
            ),
        )
    try:
        body = CatalogImportBody.model_validate_json(environ["wsgi.input"].read(length))
    except ValidationError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_body",
                detail=str(error),
            ),
        )
    try:
        fetched = catalog.fetch(name=body.name)
    except UnknownModelError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND, reason="unknown_model", detail=str(error)
            ),
        )
    except CatalogUnavailableError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="catalog_unavailable",
                detail=str(error),
            ),
        )
    except SourceUnavailableError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="source_unavailable",
                detail=str(error),
            ),
        )
    except QuotaExceededError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                reason="quota_exceeded",
                detail=str(error),
            ),
        )
    except InvalidCatalogPnmlError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_pnml",
                detail=str(error),
            ),
        )
    try:
        model = import_pnml(text=fetched.pnml_text)
    except (PnmlImportError, InvalidCanonicalModelError) as error:
        logger.warning("api_catalog_import_rejected", name=body.name, detail=str(error))
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY, reason="invalid_pnml", detail=str(error)
            ),
        )
    # Provenance in the model-level annotations section (metadata, hors hachage).
    provenance = {
        "catalog": "mcc",
        "source": fetched.entry.source,
        "edition": fetched.entry.edition,
        "name": fetched.name,
        "url": fetched.url,
        "sha256": fetched.sha256,
    }
    model = model.model_copy(update={"metadata": {**model.metadata, "provenance": provenance}})
    stored, created = store.save(model=model)
    if not created:
        store.replace(model=model)
    status = HTTPStatus.CREATED if created else HTTPStatus.OK
    logger.info(
        "api_catalog_imported",
        name=body.name,
        model_id=stored.id,
        content_hash=stored.content_hash,
        created=created,
        sha256=fetched.sha256,
    )
    extra_headers = [("Location", f"{MODELS_PATH}/{stored.id}")] if created else []
    return _respond(
        start_response=start_response,
        status=status,
        content_type=CONTENT_TYPE_PETRI_JSON,
        body=serialize_model(model=stored).encode("utf-8"),
        extra_headers=extra_headers,
    )


def _dispatch_catalog(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    catalog: ModelCatalog | None,
    store: ModelStore,
) -> Iterable[bytes] | None:
    """Route the /v1/catalog resources, or return None when the path is not one."""
    if catalog is None:
        return None
    if path == CATALOG_MODELS_PATH and method == "GET":
        return _get_catalog_models(environ=environ, start_response=start_response, catalog=catalog)
    if path == CATALOG_IMPORT_PATH and method == "POST":
        return _post_catalog_import(
            environ=environ, start_response=start_response, catalog=catalog, store=store
        )
    if path.startswith("/v1/catalog/"):
        known = path in (CATALOG_MODELS_PATH, CATALOG_IMPORT_PATH)
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED if known else HTTPStatus.NOT_FOUND,
                reason="method_not_allowed" if known else "not_found",
                detail=f"No resource at {path}.",
            ),
        )
    return None


def _get_llm_usage(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    llm_provider: LLMProvider | None,
) -> Iterable[bytes]:
    """The accumulated LLM cost, displayed apart from the tool-time budget (spec §12.4, issue #36).

    The MC budget counts only tool seconds; the LLM cost (tokens) is exposed
    here, separately, never mixed into it.
    """
    if llm_provider is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="llm_not_configured",
                detail="No LLM provider is wired into this server.",
            ),
        )
    totals = llm_provider.usage_totals()
    logger.debug("api_llm_usage_read", provider=totals.get("provider"), calls=totals.get("calls"))
    body = json.dumps(
        {
            "llm": totals,
            "budget_note": "LLM cost is NOT part of the MC tool-time budget (spec §12.4).",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_llm_calls(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    llm_provider: LLMProvider | None,
) -> Iterable[bytes]:
    """The observable state of every LLM call (P75: usage, model, start, end, error).

    The UI polls this while a real call may be in flight, to show the active
    waiting indicator (animated hourglass, usage, elapsed) - never a frozen
    screen without information. The mock is instantaneous: no call is ever
    in progress with it.
    """
    if llm_provider is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="llm_not_configured",
                detail="No LLM provider is wired into this server.",
            ),
        )
    calls = [
        {
            "usage": state.usage,
            "model": state.model,
            "started_at": state.started_at.isoformat(),
            "ended_at": state.ended_at.isoformat() if state.ended_at else None,
            "error": state.error,
            "in_progress": state.in_progress,
        }
        for state in llm_provider.call_states()
    ]
    logger.debug("api_llm_calls_read", calls=len(calls))
    body = json.dumps({"calls": calls}, sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _dispatch_qualification(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    qualification: QualificationLookup | None,
) -> Iterable[bytes] | None:
    """Route /v1/qualification, or return None when the path is not it."""
    if path != QUALIFICATION_PATH:
        return None
    if method == "GET":
        return _get_qualification(
            environ=environ, start_response=start_response, qualification=qualification
        )
    return _problem_response(
        start_response=start_response,
        environ=environ,
        payload=_problem(
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            reason="method_not_allowed",
            detail=f"No resource at {path}.",
        ),
    )


def _dispatch_checks_and_runs(  # noqa: PLR0913, PLR0911 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    settings: ApiSettings,
    store: ModelStore,
    run_store: RunStore,
    runner: CheckRunner | None,
    qualification: QualificationLookup | None,
    static_resolver: StaticResolver | None,
    static_first_prober: StaticFirstProber | None,
    witness_store: WitnessStore | None = None,
    llm_provider: LLMProvider | None = None,
    session_store: SessionStore | None = None,
) -> Iterable[bytes] | None:
    """Route /v1/checks/{family}, /v1/runs/{run_id} and the witnesses, or return None."""
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
                settings=settings,
                session_store=session_store,
                witness_store=witness_store,
                qualification=qualification,
                static_resolver=static_resolver,
                static_first_prober=static_first_prober,
            )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED if method != "POST" else HTTPStatus.NOT_FOUND,
                reason="method_not_allowed" if method != "POST" else "check_family_not_found",
                detail=f"No check family at {path}.",
            ),
        )
    if path.startswith(RUNS_PATH_PREFIX):
        run_id = path.removeprefix(RUNS_PATH_PREFIX)
        if method == "GET" and run_id.endswith(WITNESS_SUFFIX) and witness_store is not None:
            return _get_run_witness(
                environ=environ,
                start_response=start_response,
                run_store=run_store,
                store=store,
                witness_store=witness_store,
                llm_provider=llm_provider,
                run_id=run_id[: -len(WITNESS_SUFFIX)],
            )
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
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.NOT_FOUND,
                reason="method_not_allowed" if method != "GET" else "run_not_found",
                detail=f"No resource at {path}.",
            ),
        )
    if path.startswith(WITNESSES_PATH_PREFIX) and witness_store is not None:
        witness_id = path.removeprefix(WITNESSES_PATH_PREFIX)
        if method == "GET" and witness_id and "/" not in witness_id:
            return _get_witness(
                environ=environ,
                start_response=start_response,
                witness_store=witness_store,
                witness_id=witness_id,
            )
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.NOT_FOUND,
                reason="method_not_allowed" if method != "GET" else "witness_not_found",
                detail=f"No resource at {path}.",
            ),
        )
    return None


def _parse_session_request(
    *, environ: WSGIEnvironment, settings: ApiSettings
) -> tuple[ModelId, float] | ProblemPayload:
    """Parse the POST /v1/sessions body: model id plus optional budget cap (issue #33)."""
    content_type = _media_type(header_value=environ.get("CONTENT_TYPE"))
    if content_type != CONTENT_TYPE_JSON:
        return _problem(
            status=HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            reason="unsupported_media_type",
            detail=f"POST {SESSIONS_PATH} expects Content-Type {CONTENT_TYPE_JSON}.",
        )
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_content_length",
            detail="The Content-Length header is not an integer.",
        )
    body = environ["wsgi.input"].read(length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_json",
            detail=f"The body is not valid JSON: {error}",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("model_id"), str):
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_session_request",
            detail='The body must be a JSON object with a "model_id" string field.',
        )
    budget_seconds = payload.get("budget_seconds", settings.session_budget_seconds)
    if (
        not isinstance(budget_seconds, (int, float))
        or isinstance(budget_seconds, bool)
        or budget_seconds <= 0
    ):
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_session_request",
            detail='The optional "budget_seconds" field must be a positive number.',
        )
    return ModelId(payload["model_id"]), float(budget_seconds)


def _post_session(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    session_store: SessionStore,
    settings: ApiSettings,
) -> Iterable[bytes]:
    """Create a new session for a model: 201 + Location (spec §4, §12.4 issue #33).

    The optional ``budget_seconds`` body field sets the session's own MC
    budget cap; it defaults to the server setting.
    """
    parsed = _parse_session_request(environ=environ, settings=settings)
    if isinstance(parsed, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=parsed)
    model_id, budget_seconds = parsed
    if store.get(model_id=model_id) is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {model_id!r}.",
            ),
        )
    session = session_store.create(model_id=model_id, budget_seconds=float(budget_seconds))
    logger.debug("api_session_created", session_id=session.session_id, model_id=str(model_id))
    body_out = json.dumps(session.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.CREATED,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
        extra_headers=[("Location", f"{SESSIONS_PATH}/{session.session_id}")],
    )


def _get_sessions(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
) -> Iterable[bytes]:
    """List sessions with limit/offset pagination, in creation order (spec §4, ADR-0016)."""
    pagination = _parse_pagination(
        environ=environ,
        default_limit=DEFAULT_SESSIONS_PAGE_LIMIT,
        max_limit=MAX_SESSIONS_PAGE_LIMIT,
    )
    if isinstance(pagination, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=pagination)
    limit, offset = pagination
    all_sessions = session_store.list_all()
    sessions = all_sessions[offset : offset + limit]
    logger.debug(
        "api_sessions_listed",
        count=len(sessions),
        total=len(all_sessions),
        limit=limit,
        offset=offset,
    )
    body = json.dumps(
        {
            "sessions": [s.model_dump(mode="json") for s in sessions],
            "total": len(all_sessions),
            "limit": limit,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_session(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    session_id: str,
) -> Iterable[bytes]:
    """Read one session with its full serialized state (spec §4)."""
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    logger.debug("api_session_read", session_id=session.session_id)
    body = json.dumps(session.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_session_journal(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    session_id: str,
) -> Iterable[bytes]:
    """Export the append-only timestamped journal of one session (spec §4)."""
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    entries = session_store.journal(session_id=SessionId(session_id))
    logger.debug("api_session_journal_exported", session_id=session_id, entries=len(entries))
    body = json.dumps(
        [e.model_dump(mode="json") for e in entries], sort_keys=True, separators=(",", ":")
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


class FormulaEditBody(BaseModel):
    """The body of ``POST /v1/sessions/{sid}/questions/{qid}/formula`` (spec §9, issue #37)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    formula: str = Field(min_length=1)


def _post_question_formula(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    store: ModelStore,
    arbitrator: ProfileArbitrator | None,
    edit_store: FormulaEditStore,
    llm_provider: LLMProvider | None,
    session_id: str,
    question_id: str,
) -> Iterable[bytes]:
    """Edit the formula of one question card: journaled, re-read, re-verifiable (§9, #37).

    The edit is append-only (old and new formula both kept in the journal -
    the witness of the old formula is never lost), the new formula gets its
    inverse reading (formule → langage, LLM proposition ``provenance: llm``)
    and is re-verified through the standard check chain.
    """
    context = _convergence_context(
        session_store=session_store, store=store, arbitrator=arbitrator, session_id=session_id
    )
    if isinstance(context, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=context)
    session, profile_state, model = context
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.BAD_REQUEST,
                reason="invalid_content_length",
                detail="The Content-Length header is not an integer.",
            ),
        )
    body = environ["wsgi.input"].read(length)
    try:
        payload = FormulaEditBody.model_validate(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_formula_edit",
                detail=f"The formula edit body is invalid: {error}",
            ),
        )
    generated = instantiate_questions(
        catalog=load_catalog(), profile=arbitrated_profile(profile_state=profile_state), model=model
    )
    question = next((q for q in generated if q.question_id == QuestionId(question_id)), None)
    if question is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="question_not_found",
                detail=f"No generated question with id {question_id!r} in session {session_id!r}.",
            ),
        )
    old_formula = edit_store.latest_formula(
        session_id=session.session_id, question_id=question.question_id, original=question.formula
    )
    record = edit_store.edit(
        session_id=session.session_id,
        question_id=question.question_id,
        old_formula=old_formula,
        new_formula=payload.formula,
    )
    session_store.append_journal_entry(
        session_id=session.session_id,
        event="formula_edited",
        detail={
            "question_id": str(question.question_id),
            "old_formula": old_formula,
            "new_formula": payload.formula,
        },
    )
    reading = _read_formula(
        llm_provider=llm_provider,
        model=model,
        formula=payload.formula,
        grid=tuple(FormulaText(q.formula) for q in generated),
    )
    logger.debug(
        "api_question_formula_edited",
        session_id=session_id,
        question_id=question_id,
        old_formula=old_formula,
        new_formula=payload.formula,
    )
    body_out = json.dumps(
        {"record": record.model_dump(mode="json"), "reading": reading},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.CREATED,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _read_formula(
    *,
    llm_provider: LLMProvider | None,
    model: CanonicalModel,
    formula: str,
    grid: tuple[FormulaText, ...] = (),
) -> dict[str, JsonValue] | None:
    """The inverse reading of a formula (formule → langage), an LLM proposition (spec §9).

    P72: the provider reads the WHOLE frozen grid in one call (document
    granularity); ``grid`` is that document. Degrades explicitly (None with
    a note) when no reading is available - never a fabricated reading.
    """
    if llm_provider is None:
        return None
    document_formulas = grid if grid else (FormulaText(formula),)
    try:
        document = llm_provider.reading(model=model, formulas=document_formulas)
        reading = document.readings[FormulaText(formula)]
    except LLMDegradationError, KeyError:
        logger.warning("api_formula_reading_degraded", formula=formula)
        return {"degraded": True, "note": "Aucune lecture scriptée pour cette formule."}
    return {**reading.model_dump(mode="json"), "provenance": "llm", "degraded": False}


def _get_reading(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    store: ModelStore,
    llm_provider: LLMProvider | None,
    session_id: str,
) -> Iterable[bytes]:
    """The inverse reading of any formula of a session (formule → langage, spec §9, issue #37)."""
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    query = environ.get("QUERY_STRING", "")
    params: dict[str, str] = {}
    for pair in query.split("&"):
        if "=" in pair:
            key, value = pair.split("=", maxsplit=1)
            params[key] = unquote(value)
    formula = params.get("formula", "")
    if not formula:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="missing_formula",
                detail='The "formula" query parameter is required.',
            ),
        )
    model = store.get(model_id=session.model_id)
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {session.model_id!r}.",
            ),
        )
    reading = _read_formula(llm_provider=llm_provider, model=model, formula=formula)
    logger.debug("api_reading_read", session_id=session_id, formula=formula)
    body_out = json.dumps(
        {"formula": formula, "reading": reading},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _dispatch_session_questions(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    session_store: SessionStore,
    store: ModelStore,
    arbitrator: ProfileArbitrator | None,
    edit_store: FormulaEditStore,
    llm_provider: LLMProvider | None,
) -> Iterable[bytes] | None:
    """Route the question-editing sub-resources of a session, or None (spec §9, issue #37)."""
    if remainder.endswith(READING_SUFFIX):
        session_id = remainder[: -len(READING_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_reading(
                environ=environ,
                start_response=start_response,
                session_store=session_store,
                store=store,
                llm_provider=llm_provider,
                session_id=session_id,
            )
        return None
    if QUESTIONS_SEGMENT in remainder:
        session_id, _, tail = remainder.partition(QUESTIONS_SEGMENT)
        question_id, _, _ = tail.partition("/")
        if session_id and question_id and tail == question_id + FORMULA_SUFFIX and method == "POST":
            return _post_question_formula(
                environ=environ,
                start_response=start_response,
                session_store=session_store,
                store=store,
                arbitrator=arbitrator,
                edit_store=edit_store,
                llm_provider=llm_provider,
                session_id=session_id,
                question_id=question_id,
            )
    return None


def _get_session_budget(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    run_store: RunStore,
    session_id: str,
) -> Iterable[bytes]:
    """The live MC budget of one session: cap, consumed, reserved, remaining (§12.4, #33).

    The budget lives continuously per session: consumed is the measured tool
    time of the session's terminal runs, reserved the in-flight reservations,
    and the remaining is what the next run of THIS session may still claim -
    never another session's leftover.
    """
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    consumed = run_store.consumed_seconds(session_id=session.session_id)
    reserved = run_store.reserved_seconds(session_id=session.session_id)
    remaining = session.budget_seconds - consumed - reserved
    logger.debug(
        "api_session_budget_read",
        session_id=session.session_id,
        budget_seconds=session.budget_seconds,
        consumed_seconds=consumed,
        reserved_seconds=reserved,
        remaining_seconds=remaining,
        runs=len(session.run_ids),
    )
    body = json.dumps(
        {
            "session_id": str(session.session_id),
            "budget_seconds": session.budget_seconds,
            "consumed_seconds": consumed,
            "reserved_seconds": reserved,
            "remaining_seconds": remaining,
            "run_count": len(session.run_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _get_session_profile(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    arbitrator: ProfileArbitrator,
    session_id: str,
) -> Iterable[bytes]:
    """Read the proposed + arbitrated profile of a session (spec §14)."""
    state = arbitrator.get(session_id=SessionId(session_id))
    if state is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="profile_not_found",
                detail=f"No proposed profile for session {session_id!r}.",
            ),
        )
    logger.debug("api_profile_read", session_id=session_id, arbitrations=len(state.arbitrations))
    body = json.dumps(state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _post_profile_arbitration(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    arbitrator: ProfileArbitrator,
    session_id: str,
) -> Iterable[bytes]:
    """Arbitrate one proposed role: the human accepts or replaces it (spec §14)."""
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.BAD_REQUEST,
                reason="invalid_content_length",
                detail="The Content-Length header is not an integer.",
            ),
        )
    body = environ["wsgi.input"].read(length)
    try:
        payload = json.loads(body.decode("utf-8"))
        decision = ArbitrationDecision.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_arbitration",
                detail=f"The arbitration body is invalid: {error}",
            ),
        )
    state = arbitrator.arbitrate(session_id=SessionId(session_id), decision=decision)
    if state is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="profile_not_found",
                detail=f"No proposed profile for session {session_id!r}.",
            ),
        )
    logger.debug("api_profile_arbitrated", session_id=session_id, entry_id=decision.entry_id)
    body_out = json.dumps(state.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _get_session_coverage(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    arbitrator: ProfileArbitrator,
    session_id: str,
) -> Iterable[bytes]:
    """Coverage groups of a session; uncovered ones are re-seeding targets (spec §14)."""
    state = arbitrator.get(session_id=SessionId(session_id))
    if state is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="profile_not_found",
                detail=f"No proposed profile for session {session_id!r}.",
            ),
        )
    uncovered = state.uncovered_groups()
    logger.debug(
        "api_coverage_read",
        session_id=session_id,
        groups=len(state.groups),
        uncovered=len(uncovered),
    )
    body = json.dumps(
        {
            "groups": [g.model_dump(mode="json") for g in state.groups],
            "uncovered_groups": [g.model_dump(mode="json") for g in uncovered],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


class AnnotationRequest(BaseModel):
    """One in-situ annotation set on one element of the session's model (spec §13, §14)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    element_id: ElementId
    key: str = Field(min_length=1)
    value: JsonValue


def _session_or_problem(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    session_id: str,
) -> StoredSession | Iterable[bytes]:
    """The session, or a ready-to-return 404 problem response."""
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    return session


def _get_session_state_graph(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    session_store: SessionStore,
    state_graph_explorer: StateGraphExplorer | None,
    session_id: str,
) -> Iterable[bytes]:
    """The reachability graph of the session's permanent model, always consultable (P13)."""
    session = _session_or_problem(
        environ=environ,
        start_response=start_response,
        session_store=session_store,
        session_id=session_id,
    )
    if not isinstance(session, StoredSession):
        return session
    if state_graph_explorer is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="state_graph_not_configured",
                detail="No state-graph explorer is wired on this server.",
            ),
        )
    model = store.get(model_id=session.model_id)
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {session.model_id!r}.",
            ),
        )
    try:
        graph_payload, truncated = state_graph_explorer(model=model)
    except StateGraphUnsupportedError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="unsupported_formalism",
                detail=str(error),
            ),
        )
    body = json.dumps(
        {
            "model_id": model.id,
            "formalism": model.formalism,
            "truncated": truncated,
            **graph_payload,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    states = graph_payload["states"]
    logger.debug(
        "api_state_graph_served",
        session_id=session_id,
        state_count=len(states) if isinstance(states, list) else 0,
        truncated=truncated,
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _put_session_annotation(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    session_store: SessionStore,
    session_id: str,
) -> Iterable[bytes]:
    """Set one in-situ annotation on one element; persisted outside the content hash (P13)."""
    session = _session_or_problem(
        environ=environ,
        start_response=start_response,
        session_store=session_store,
        session_id=session_id,
    )
    if not isinstance(session, StoredSession):
        return session
    model = store.get(model_id=session.model_id)
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {session.model_id!r}.",
            ),
        )
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.BAD_REQUEST,
                reason="invalid_content_length",
                detail="The Content-Length header is not an integer.",
            ),
        )
    body = environ["wsgi.input"].read(length)
    try:
        payload = json.loads(body.decode("utf-8"))
        request = AnnotationRequest.model_validate(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_annotation",
                detail=f"The annotation body is invalid: {error}",
            ),
        )
    try:
        updated = with_element_annotation(
            model=model, element_id=request.element_id, key=request.key, value=request.value
        )
    except UnknownElementError as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="element_not_found",
                detail=str(error),
            ),
        )
    store.replace(model=updated)
    logger.debug(
        "api_annotation_set",
        session_id=session_id,
        element_id=request.element_id,
        key=request.key,
        content_hash=updated.content_hash,
    )
    updated_elements: tuple[Element | Arc, ...] = (
        *updated.places,
        *updated.transitions,
        *updated.arcs,
    )
    body_out = json.dumps(
        {
            "model_id": updated.id,
            "element_id": request.element_id,
            "content_hash": updated.content_hash,
            "annotations": next(
                element.annotations
                for element in updated_elements
                if element.id == request.element_id
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _get_session_net_overlay(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    session_store: SessionStore,
    arbitrator: ProfileArbitrator | None,
    session_id: str,
) -> Iterable[bytes]:
    """The coverage overlay of the permanent net: card → elements + greyed coverage (P13)."""
    session = _session_or_problem(
        environ=environ,
        start_response=start_response,
        session_store=session_store,
        session_id=session_id,
    )
    if not isinstance(session, StoredSession):
        return session
    model = store.get(model_id=session.model_id)
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {session.model_id!r}.",
            ),
        )
    state = arbitrator.get(session_id=SessionId(session_id)) if arbitrator is not None else None
    overlay = build_net_overlay(model=model, groups=state.groups if state is not None else ())
    logger.debug(
        "api_net_overlay_read",
        session_id=session_id,
        elements=len(overlay.elements),
        greyed=len(overlay.greyed_ids()),
    )
    body = json.dumps(
        {**overlay.model_dump(mode="json"), "greyed": list(overlay.greyed_ids())},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body.encode("utf-8"),
    )


def _dispatch_session_insitu(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    store: ModelStore,
    session_store: SessionStore,
    arbitrator: ProfileArbitrator | None,
    state_graph_explorer: StateGraphExplorer | None,
) -> Iterable[bytes] | None:
    """Route the in-situ sub-resources of a session (state graph, annotations, overlay), or None."""
    if remainder.endswith(SESSION_STATE_GRAPH_SUFFIX):
        session_id = remainder[: -len(SESSION_STATE_GRAPH_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_session_state_graph(
                environ=environ,
                start_response=start_response,
                store=store,
                session_store=session_store,
                state_graph_explorer=state_graph_explorer,
                session_id=session_id,
            )
    elif remainder.endswith(SESSION_ANNOTATIONS_SUFFIX):
        session_id = remainder[: -len(SESSION_ANNOTATIONS_SUFFIX)]
        if method == "PUT" and session_id and "/" not in session_id:
            return _put_session_annotation(
                environ=environ,
                start_response=start_response,
                store=store,
                session_store=session_store,
                session_id=session_id,
            )
    elif remainder.endswith(SESSION_NET_OVERLAY_SUFFIX):
        session_id = remainder[: -len(SESSION_NET_OVERLAY_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_session_net_overlay(
                environ=environ,
                start_response=start_response,
                store=store,
                session_store=session_store,
                arbitrator=arbitrator,
                session_id=session_id,
            )
    return None


def _dispatch_session_arbitration(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    session_store: SessionStore,
    arbitration_store: ArbitrationStore,
    convergence_store: ConvergenceStore | None = None,
) -> Iterable[bytes] | None:
    """Route the arbitration sub-resources of a session, or None (spec §4.8, issue #16)."""
    if remainder.endswith(ARBITRATION_REQUESTS_SUFFIX):
        session_id = remainder[: -len(ARBITRATION_REQUESTS_SUFFIX)]
        if method == "POST" and session_id and "/" not in session_id:
            return _post_arbitration_request(
                environ=environ,
                start_response=start_response,
                session_store=session_store,
                arbitration_store=arbitration_store,
                convergence_store=convergence_store,
                session_id=session_id,
            )
    elif remainder.endswith(ARBITRATIONS_SUFFIX):
        session_id = remainder[: -len(ARBITRATIONS_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_arbitrations(
                environ=environ,
                start_response=start_response,
                session_store=session_store,
                arbitration_store=arbitration_store,
                session_id=session_id,
            )
    return None


def _dispatch_session_convergence(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    session_store: SessionStore,
    convergence_store: ConvergenceStore,
    store: ModelStore,
    arbitrator: ProfileArbitrator | None,
) -> Iterable[bytes] | None:
    """Route the convergence sub-resources of a session, or None (spec §4.8, §12.2, issue #34)."""
    if remainder.endswith(CONVERGENCES_SUFFIX):
        session_id = remainder[: -len(CONVERGENCES_SUFFIX)]
        if session_id and "/" not in session_id:
            if method == "POST":
                return _post_convergence(
                    environ=environ,
                    start_response=start_response,
                    session_store=session_store,
                    convergence_store=convergence_store,
                    store=store,
                    arbitrator=arbitrator,
                    session_id=session_id,
                )
            if method == "GET":
                return _get_convergences(
                    environ=environ,
                    start_response=start_response,
                    session_store=session_store,
                    convergence_store=convergence_store,
                    session_id=session_id,
                )
    return None


def _dispatch_session_profile(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    arbitrator: ProfileArbitrator,
) -> Iterable[bytes] | None:
    """Route the profile/coverage sub-resources of a session, or None (spec §14)."""
    profile_id = remainder[: -len(SESSION_PROFILE_SUFFIX)]
    if remainder.endswith(SESSION_PROFILE_SUFFIX) and "/" not in profile_id:
        session_id = profile_id
        if method == "GET":
            return _get_session_profile(
                environ=environ,
                start_response=start_response,
                arbitrator=arbitrator,
                session_id=session_id,
            )
    elif remainder.endswith(SESSION_ARBITRATE_SUFFIX):
        session_id = remainder[: -len(SESSION_ARBITRATE_SUFFIX)]
        if method == "POST" and session_id and "/" not in session_id:
            return _post_profile_arbitration(
                environ=environ,
                start_response=start_response,
                arbitrator=arbitrator,
                session_id=session_id,
            )
    elif remainder.endswith(SESSION_COVERAGE_SUFFIX):
        session_id = remainder[: -len(SESSION_COVERAGE_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_session_coverage(
                environ=environ,
                start_response=start_response,
                arbitrator=arbitrator,
                session_id=session_id,
            )
    return None


class ArbitrationRequestBody(BaseModel):
    """The body of ``POST /v1/sessions/{sid}/arbitration-requests`` (spec §4.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: QuestionId = Field(min_length=1)
    decision: ArbitrationDecisionKind


def _post_arbitration_request(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    arbitration_store: ArbitrationStore,
    convergence_store: ConvergenceStore | None,
    session_id: str,
) -> Iterable[bytes]:
    """Accept, refuse or revise one question card; the previous decision stays (spec §12.7).

    A revision also reopens the clauses of the questions covered by
    convergence with this one (spec §12.7, issue #34): the reopening is
    journaled, and the journal keeps the old and the new decision alike.
    """
    if session_store.get(session_id=SessionId(session_id)) is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.BAD_REQUEST,
                reason="invalid_content_length",
                detail="The Content-Length header is not an integer.",
            ),
        )
    body = environ["wsgi.input"].read(length)
    try:
        payload = ArbitrationRequestBody.model_validate(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNPROCESSABLE_ENTITY,
                reason="invalid_arbitration_request",
                detail=f"The arbitration request body is invalid: {error}",
            ),
        )
    record = arbitration_store.decide(
        session_id=SessionId(session_id),
        question_id=payload.question_id,
        decision=payload.decision,
    )
    session_store.append_journal_entry(
        session_id=SessionId(session_id),
        event="arbitration_decided",
        detail={
            "question_id": str(payload.question_id),
            "decision": payload.decision,
            "previous_decision": record.previous_decision,
        },
    )
    if record.previous_decision is not None and convergence_store is not None:
        reopened = [
            fusion.question_id
            for fusion in convergence_store.covered_by_proposition(
                session_id=SessionId(session_id), proposition_id=payload.question_id
            )
            if fusion.timestamp < record.timestamp
        ]
        if reopened:
            session_store.append_journal_entry(
                session_id=SessionId(session_id),
                event="convergence_reopened",
                detail={
                    "proposition_id": str(payload.question_id),
                    "reopened": [str(question_id) for question_id in reopened],
                },
            )
            logger.debug(
                "api_convergence_reopened",
                session_id=session_id,
                proposition_id=str(payload.question_id),
                reopened=len(reopened),
            )
    logger.debug(
        "api_arbitration_decided",
        session_id=session_id,
        question_id=str(payload.question_id),
        decision=payload.decision,
        previous_decision=record.previous_decision,
    )
    history = arbitration_store.for_question(
        session_id=SessionId(session_id), question_id=payload.question_id
    )
    body_out = json.dumps(
        {
            "record": record.model_dump(mode="json"),
            "question_history": [r.model_dump(mode="json") for r in history],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.CREATED,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _get_arbitrations(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    arbitration_store: ArbitrationStore,
    session_id: str,
) -> Iterable[bytes]:
    """The full append-only arbitration history of a session (spec §4.8)."""
    if session_store.get(session_id=SessionId(session_id)) is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    history = arbitration_store.history(session_id=SessionId(session_id))
    logger.debug("api_arbitrations_read", session_id=session_id, decisions=len(history))
    body_out = json.dumps(
        {"decisions": [r.model_dump(mode="json") for r in history]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


class ConvergenceRequestBody(BaseModel):
    """The body of ``POST /v1/sessions/{sid}/convergences`` (spec §4.8, §12.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: QuestionId = Field(min_length=1)
    """The question to cover (never solicited again once fused)."""

    proposition_id: QuestionId = Field(min_length=1)
    """The surviving question the covered one converges with."""


def _post_convergence(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    convergence_store: ConvergenceStore,
    store: ModelStore,
    arbitrator: ProfileArbitrator | None,
    session_id: str,
) -> Iterable[bytes]:
    """Fuse one question into a converging surviving one - a human-only decision (spec §12.2).

    Only Arbitrer merges: the two questions must be generated for the
    session and must actually converge (spec §14.5); anything else is a
    ``422`` - automatic or arbitrary fusion is forbidden.
    """
    context = _convergence_context(
        session_store=session_store, store=store, arbitrator=arbitrator, session_id=session_id
    )
    if isinstance(context, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=context)
    parsed = _parse_convergence_body(environ=environ)
    if isinstance(parsed, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=parsed)
    _, profile_state, model = context
    generated = instantiate_questions(
        catalog=load_catalog(), profile=arbitrated_profile(profile_state=profile_state), model=model
    )
    by_id = {question.question_id: question for question in generated}
    rejection = _fusion_rejection(payload=parsed, by_id=by_id)
    if rejection is not None:
        return _problem_response(start_response=start_response, environ=environ, payload=rejection)
    existing = convergence_store.find_fusion(
        session_id=SessionId(session_id),
        question_id=parsed.question_id,
        proposition_id=parsed.proposition_id,
    )
    if existing is not None:
        logger.debug(
            "api_convergence_already_fused",
            session_id=session_id,
            question_id=str(parsed.question_id),
            proposition_id=str(parsed.proposition_id),
        )
        body_out = json.dumps(
            {"record": existing.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        return _respond(
            start_response=start_response,
            status=HTTPStatus.OK,
            content_type=CONTENT_TYPE_JSON,
            body=body_out.encode("utf-8"),
        )
    record = convergence_store.fuse(
        session_id=SessionId(session_id),
        question_id=parsed.question_id,
        proposition_id=parsed.proposition_id,
    )
    session_store.append_journal_entry(
        session_id=SessionId(session_id),
        event="convergence_fused",
        detail={
            "question_id": str(parsed.question_id),
            "proposition_id": str(parsed.proposition_id),
        },
    )
    logger.debug(
        "api_convergence_fused",
        session_id=session_id,
        question_id=str(parsed.question_id),
        proposition_id=str(parsed.proposition_id),
    )
    body_out = json.dumps(
        {"record": record.model_dump(mode="json")},
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.CREATED,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _convergence_context(
    *,
    session_store: SessionStore,
    store: ModelStore,
    arbitrator: ProfileArbitrator | None,
    session_id: str,
) -> tuple[StoredSession, ProfileState, CanonicalModel] | ProblemPayload:
    """Resolve the session, its arbitrated profile state and its model, or a problem payload."""
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem(
            status=HTTPStatus.NOT_FOUND,
            reason="session_not_found",
            detail=f"No session with id {session_id!r}.",
        )
    profile_state = arbitrator.get(session_id=SessionId(session_id)) if arbitrator else None
    if profile_state is None:
        return _problem(
            status=HTTPStatus.NOT_FOUND,
            reason="profile_not_found",
            detail=f"No proposed profile for session {session_id!r}.",
        )
    model = store.get(model_id=session.model_id)
    if model is None:
        return _problem(
            status=HTTPStatus.NOT_FOUND,
            reason="model_not_found",
            detail=f"No model with id {session.model_id!r}.",
        )
    return session, profile_state, model


def _parse_convergence_body(*, environ: WSGIEnvironment) -> ConvergenceRequestBody | ProblemPayload:
    """Parse the convergence request body, or a problem payload when invalid."""
    try:
        length = int(environ.get("CONTENT_LENGTH", "") or 0)
    except ValueError:
        return _problem(
            status=HTTPStatus.BAD_REQUEST,
            reason="invalid_content_length",
            detail="The Content-Length header is not an integer.",
        )
    body = environ["wsgi.input"].read(length)
    try:
        return ConvergenceRequestBody.model_validate(json.loads(body.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="invalid_convergence_request",
            detail=f"The convergence request body is invalid: {error}",
        )


def _fusion_rejection(
    *, payload: ConvergenceRequestBody, by_id: dict[QuestionId, GeneratedQuestion]
) -> ProblemPayload | None:
    """Why the fusion is rejected, or None when it is a valid human fusion (spec §14.5)."""
    if payload.question_id == payload.proposition_id:
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="convergence_rejected",
            detail="A question cannot cover itself.",
        )
    covered = by_id.get(payload.question_id)
    proposition = by_id.get(payload.proposition_id)
    if covered is None or proposition is None:
        unknown = sorted(
            str(question_id)
            for question_id in (payload.question_id, payload.proposition_id)
            if question_id not in by_id
        )
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="convergence_rejected",
            detail=f"Both question ids must be generated for this session; unknown: {unknown}.",
        )
    if not questions_converge(first=covered, second=proposition):
        return _problem(
            status=HTTPStatus.UNPROCESSABLE_ENTITY,
            reason="convergence_rejected",
            detail="The two questions do not converge (spec §14.5); fusion is human-only.",
        )
    return None


def _get_convergences(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    session_store: SessionStore,
    convergence_store: ConvergenceStore,
    session_id: str,
) -> Iterable[bytes]:
    """List the fusions of a session with limit/offset pagination, in append order (ADR-0016)."""
    if session_store.get(session_id=SessionId(session_id)) is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    pagination = _parse_pagination(
        environ=environ,
        default_limit=DEFAULT_CONVERGENCES_PAGE_LIMIT,
        max_limit=MAX_CONVERGENCES_PAGE_LIMIT,
    )
    if isinstance(pagination, ProblemPayload):
        return _problem_response(start_response=start_response, environ=environ, payload=pagination)
    limit, offset = pagination
    history = convergence_store.history(session_id=SessionId(session_id))
    page = history[offset : offset + limit]
    logger.debug(
        "api_convergences_read",
        session_id=session_id,
        count=len(page),
        total=len(history),
        limit=limit,
        offset=offset,
    )
    body_out = json.dumps(
        {
            "convergences": [r.model_dump(mode="json") for r in page],
            "total": len(history),
            "limit": limit,
            "offset": offset,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_JSON,
        body=body_out.encode("utf-8"),
    )


def _get_session_contract(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    store: ModelStore,
    session_store: SessionStore,
    run_store: RunStore,
    arbitrator: ProfileArbitrator | None,
    arbitration_store: ArbitrationStore | None,
    convergence_store: ConvergenceStore | None,
    session_id: str,
) -> Iterable[bytes]:
    """The contract of a session, negotiated as contract+json or Markdown (spec §4.8)."""
    media = _negotiate_contract_media(accept_header=environ.get("HTTP_ACCEPT"))
    if media is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_ACCEPTABLE,
                reason="not_acceptable",
                detail=(
                    "The contract is served as application/contract+json; version=1.0"
                    " or text/markdown."
                ),
            ),
        )
    session = session_store.get(session_id=SessionId(session_id))
    if session is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="session_not_found",
                detail=f"No session with id {session_id!r}.",
            ),
        )
    profile_state = arbitrator.get(session_id=SessionId(session_id)) if arbitrator else None
    if profile_state is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="profile_not_found",
                detail=f"No proposed profile for session {session_id!r}.",
            ),
        )
    model = store.get(model_id=session.model_id)
    if model is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND,
                reason="model_not_found",
                detail=f"No model with id {session.model_id!r}.",
            ),
        )
    history = (
        arbitration_store.history(session_id=SessionId(session_id))
        if arbitration_store is not None
        else []
    )
    fusions = (
        convergence_store.history(session_id=SessionId(session_id))
        if convergence_store is not None
        else []
    )
    runs = [run for run_id in session.run_ids if (run := run_store.get(run_id=run_id)) is not None]
    document = build_contract(
        session=session,
        profile_state=profile_state,
        model=model,
        catalog=load_catalog(),
        arbitration_history=history,
        runs=runs,
        journal=session_store.journal(session_id=SessionId(session_id)),
        convergence_history=fusions,
    )
    logger.debug(
        "api_contract_exported",
        session_id=session_id,
        media=media,
        questions=len(document.questions),
    )
    if media == "markdown":
        return _respond(
            start_response=start_response,
            status=HTTPStatus.OK,
            content_type=CONTENT_TYPE_MARKDOWN,
            body=render_markdown(document=document).encode("utf-8"),
        )
    body_out = json.dumps(document.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=CONTENT_TYPE_CONTRACT_JSON,
        body=body_out.encode("utf-8"),
    )


def _dispatch_session_read(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    store: ModelStore,
    run_store: RunStore,
    session_store: SessionStore,
    arbitrator: ProfileArbitrator | None,
    state_graph_explorer: StateGraphExplorer | None,
) -> Iterable[bytes] | None:
    """Route the journal, budget, plain-read, in-situ and profile sub-resources, or None."""
    if remainder.endswith(SESSION_JOURNAL_SUFFIX):
        session_id = remainder[: -len(SESSION_JOURNAL_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_session_journal(
                environ=environ,
                start_response=start_response,
                session_store=session_store,
                session_id=session_id,
            )
    elif remainder.endswith(SESSION_BUDGET_SUFFIX):
        session_id = remainder[: -len(SESSION_BUDGET_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_session_budget(
                environ=environ,
                start_response=start_response,
                session_store=session_store,
                run_store=run_store,
                session_id=session_id,
            )
    elif method == "GET" and remainder and "/" not in remainder:
        return _get_session(
            environ=environ,
            start_response=start_response,
            session_store=session_store,
            session_id=remainder,
        )
    result = _dispatch_session_insitu(
        environ=environ,
        start_response=start_response,
        method=method,
        remainder=remainder,
        store=store,
        session_store=session_store,
        arbitrator=arbitrator,
        state_graph_explorer=state_graph_explorer,
    )
    if result is None and arbitrator is not None:
        result = _dispatch_session_profile(
            environ=environ,
            start_response=start_response,
            method=method,
            remainder=remainder,
            arbitrator=arbitrator,
        )
    return result


def _dispatch_session_contract(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    remainder: str,
    store: ModelStore,
    run_store: RunStore,
    session_store: SessionStore,
    arbitrator: ProfileArbitrator | None,
    arbitration_store: ArbitrationStore | None,
    convergence_store: ConvergenceStore | None = None,
) -> Iterable[bytes] | None:
    """Route the contract sub-resource of a session, or None (spec §4.8, issue #17)."""
    if remainder.endswith(SESSION_CONTRACT_SUFFIX):
        session_id = remainder[: -len(SESSION_CONTRACT_SUFFIX)]
        if method == "GET" and session_id and "/" not in session_id:
            return _get_session_contract(
                environ=environ,
                start_response=start_response,
                store=store,
                session_store=session_store,
                run_store=run_store,
                arbitrator=arbitrator,
                arbitration_store=arbitration_store,
                convergence_store=convergence_store,
                session_id=session_id,
            )
    return None


def _dispatch_sessions_root(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    store: ModelStore,
    session_store: SessionStore,
    settings: ApiSettings,
) -> Iterable[bytes] | None:
    """Route the /v1/sessions collection, or None when the path is not it."""
    if method == "POST":
        return _post_session(
            environ=environ,
            start_response=start_response,
            store=store,
            session_store=session_store,
            settings=settings,
        )
    if method == "GET":
        return _get_sessions(
            environ=environ, start_response=start_response, session_store=session_store
        )
    return _problem_response(
        start_response=start_response,
        environ=environ,
        payload=_problem(
            status=HTTPStatus.METHOD_NOT_ALLOWED,
            reason="method_not_allowed",
            detail=f"{method} is not supported on {SESSIONS_PATH}.",
        ),
    )


def _dispatch_sessions(  # noqa: PLR0913 - one keyword per routed dependency (ADR-0003)
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    store: ModelStore,
    run_store: RunStore,
    session_store: SessionStore,
    settings: ApiSettings,
    arbitrator: ProfileArbitrator | None = None,
    state_graph_explorer: StateGraphExplorer | None = None,
    arbitration_store: ArbitrationStore | None = None,
    convergence_store: ConvergenceStore | None = None,
    edit_store: FormulaEditStore | None = None,
    llm_provider: LLMProvider | None = None,
) -> Iterable[bytes] | None:
    """Route the /v1/sessions resources, or return None when the path is not one."""
    result: Iterable[bytes] | None = None
    if path == SESSIONS_PATH:
        result = _dispatch_sessions_root(
            environ=environ,
            start_response=start_response,
            method=method,
            store=store,
            session_store=session_store,
            settings=settings,
        )
    elif path.startswith(SESSION_PATH_PREFIX):
        remainder = path.removeprefix(SESSION_PATH_PREFIX)
        if arbitration_store is not None:
            result = _dispatch_session_arbitration(
                environ=environ,
                start_response=start_response,
                method=method,
                remainder=remainder,
                session_store=session_store,
                arbitration_store=arbitration_store,
                convergence_store=convergence_store,
            )
        if result is None and convergence_store is not None:
            result = _dispatch_session_convergence(
                environ=environ,
                start_response=start_response,
                method=method,
                remainder=remainder,
                session_store=session_store,
                convergence_store=convergence_store,
                store=store,
                arbitrator=arbitrator,
            )
        if result is None and edit_store is not None:
            result = _dispatch_session_questions(
                environ=environ,
                start_response=start_response,
                method=method,
                remainder=remainder,
                session_store=session_store,
                store=store,
                arbitrator=arbitrator,
                edit_store=edit_store,
                llm_provider=llm_provider,
            )
        if result is None:
            result = _dispatch_session_contract(
                environ=environ,
                start_response=start_response,
                method=method,
                remainder=remainder,
                store=store,
                run_store=run_store,
                session_store=session_store,
                arbitrator=arbitrator,
                arbitration_store=arbitration_store,
                convergence_store=convergence_store,
            )
        if result is None:
            result = _dispatch_session_read(
                environ=environ,
                start_response=start_response,
                method=method,
                remainder=remainder,
                store=store,
                run_store=run_store,
                session_store=session_store,
                arbitrator=arbitrator,
                state_graph_explorer=state_graph_explorer,
            )
        if result is None:
            result = _problem_response(
                start_response=start_response,
                environ=environ,
                payload=_problem(
                    status=(
                        HTTPStatus.METHOD_NOT_ALLOWED if method != "GET" else HTTPStatus.NOT_FOUND
                    ),
                    reason=("method_not_allowed" if method != "GET" else "session_not_found"),
                    detail=f"No resource at {path}.",
                ),
            )
    return result


def _dispatch_ui(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
) -> Iterable[bytes] | None:
    """Serve the static elicitation UI (spec §9, issue #18), or None.

    The UI assets carry no data and are public; every data call they make
    goes through the authenticated /v1 API.
    """
    if (path == "/" or path.startswith(UI_PATH)) and method != "GET":
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.METHOD_NOT_ALLOWED,
                reason="method_not_allowed",
                detail=f"{method} is not supported on {path}.",
            ),
        )
    if path == "/":
        return _respond(
            start_response=start_response,
            status=HTTPStatus.FOUND,
            content_type=CONTENT_TYPE_HTML,
            body=b"",
            extra_headers=[("Location", UI_PATH)],
        )
    if path == UI_PATH:
        path = UI_PATH + UI_INDEX
    if path.startswith(UI_PATH):
        name = path.removeprefix(UI_PATH)
        return _serve_ui_asset(start_response=start_response, path=path, name=name)
    return None


def _serve_ui_asset(
    *,
    start_response: StartResponse,
    path: str,
    name: str,
) -> Iterable[bytes] | None:
    """Serve one built UI asset (ADR-0101), or None when it does not exist.

    Known suffixes are served from the static directory; an extension-less
    name is a client-side route of the SPA and gets the index.html shell;
    anything else (unknown suffix, missing file, escape from the static
    directory) is a 404.
    """
    # An absolute name (`/ui//etc/foo.js` -> `/etc/foo.js`) would make
    # Path(static) / name discard the base: reject it, and `..` segments,
    # then verify containment on the resolved path.
    if name.startswith("/") or ".." in name.split("/"):
        return None
    suffix = Path(name).suffix
    content_type = UI_CONTENT_TYPES_BY_SUFFIX.get(suffix)
    if content_type is not None:
        asset = UI_STATIC_DIRECTORY / name
    elif suffix == "":
        asset = UI_STATIC_DIRECTORY / UI_INDEX
        content_type = CONTENT_TYPE_HTML
    else:
        return None
    try:
        asset.resolve().relative_to(UI_STATIC_DIRECTORY.resolve())
    except ValueError:
        return None
    if not asset.is_file():
        return None
    logger.debug("api_ui_asset_served", path=path, bytes=asset.stat().st_size)
    return _respond(
        start_response=start_response,
        status=HTTPStatus.OK,
        content_type=content_type,
        body=asset.read_bytes(),
    )


def _session_id_from_cookie(*, environ: WSGIEnvironment) -> str | None:
    """The ``petrichor-session`` cookie value of the request, if any."""
    raw: str = environ.get("HTTP_COOKIE", "")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE and value:
            return str(value)
    return None


def _session_cookie_header(*, session_id: str, max_age_seconds: int) -> str:
    """The Set-Cookie header of the applicative session (contract §2)."""
    return (
        f"{SESSION_COOKIE}={session_id}; Path=/; HttpOnly; Secure; "
        f"SameSite=Lax; Max-Age={max_age_seconds}"
    )


def _clear_session_cookie_header() -> str:
    """The Set-Cookie header expiring the session cookie."""
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"


def _redirect_uri(*, environ: WSGIEnvironment) -> str:
    """The callback URL of this app, from the request host (or its Host header)."""
    scheme = environ.get("wsgi.url_scheme", "http")
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "localhost")
    return f"{scheme}://{host}{AUTH_CALLBACK_PATH}"


def _dispatch_auth(  # noqa: PLR0911 - one typed response per /auth route
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    method: str,
    path: str,
    oidc_flow: OidcFlowClient | None,
    pending_logins: PendingLoginStore,
    web_sessions: WebSessionStore,
    token_verifier: TokenVerifier | None,
) -> Iterable[bytes] | None:
    """Route the /auth/* endpoints of the web v1 browser login (issue #207).

    These routes are public by design (the login page must be reachable
    without a session); they never carry data of the workshop.
    """
    if path == AUTH_LOGIN_PATH and method == "GET":
        return _auth_login(
            environ=environ,
            start_response=start_response,
            oidc_flow=oidc_flow,
            pending_logins=pending_logins,
        )
    if path == AUTH_CALLBACK_PATH and method == "GET":
        return _auth_callback(
            environ=environ,
            start_response=start_response,
            oidc_flow=oidc_flow,
            pending_logins=pending_logins,
            web_sessions=web_sessions,
            token_verifier=token_verifier,
        )
    if path == AUTH_LOGOUT_PATH and method == "POST":
        return _auth_logout(
            environ=environ,
            start_response=start_response,
            oidc_flow=oidc_flow,
            web_sessions=web_sessions,
        )
    if path == AUTH_ME_PATH and method == "GET":
        session_id = _session_id_from_cookie(environ=environ)
        session = web_sessions.get(session_id=session_id) if session_id else None
        if session is None:
            return _problem_response(
                start_response=start_response,
                environ=environ,
                payload=_problem(
                    status=HTTPStatus.UNAUTHORIZED,
                    reason=REASON_SESSION_REQUIRED,
                    detail="No active session.",
                ),
            )
        body = json.dumps({"sub": session.claims.sub, "iss": session.claims.iss}).encode()
        return _respond(
            start_response=start_response,
            status=HTTPStatus.OK,
            content_type=CONTENT_TYPE_JSON,
            body=body,
        )
    if path.startswith("/auth/"):
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND, reason="not_found", detail=f"No resource at {path}."
            ),
        )
    return None


def _auth_login(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    oidc_flow: OidcFlowClient | None,
    pending_logins: PendingLoginStore,
) -> Iterable[bytes]:
    """GET /auth/login?returnTo=… — start the OIDC flow (issue #207)."""
    if oidc_flow is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="oidc_not_configured",
                detail="OIDC login is not configured on this server (AUTH_ISSUER unset).",
            ),
        )
    params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    return_to = params.get("returnTo", [UI_PATH])[0]
    if not is_internal_path(path=return_to):
        return_to = UI_PATH
    ui_locales = "fr" if "fr" in environ.get("HTTP_ACCEPT_LANGUAGE", "fr") else "en"
    pending = pending_logins.create(return_to=return_to)
    url = oidc_flow.authorize_url(
        redirect_uri=_redirect_uri(environ=environ),
        state=pending.state,
        nonce=pending.nonce,
        code_challenge=code_challenge_s256(code_verifier=pending.code_verifier),
        ui_locales=ui_locales,
    )
    logger.debug("web_auth_login_redirect", return_to=return_to, ui_locales=ui_locales)
    return _respond(
        start_response=start_response,
        status=HTTPStatus.FOUND,
        content_type=CONTENT_TYPE_HTML,
        body=b"",
        extra_headers=[("Location", url)],
    )


def _auth_callback(  # noqa: PLR0911 - one typed response per failure mode
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    oidc_flow: OidcFlowClient | None,
    pending_logins: PendingLoginStore,
    web_sessions: WebSessionStore,
    token_verifier: TokenVerifier | None,
) -> Iterable[bytes]:
    """GET /auth/callback — verify state/nonce, exchange the code, open a session."""
    if oidc_flow is None or token_verifier is None:
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                reason="oidc_not_configured",
                detail="OIDC login is not configured on this server.",
            ),
        )
    params = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
    state = params.get("state", [""])[0]
    code = params.get("code", [""])[0]
    pending = pending_logins.pop(state=state)
    if pending is None or not code:
        logger.warning("web_auth_callback_rejected", reason="unknown_or_expired_state")
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNAUTHORIZED,
                reason="invalid_state",
                detail="Unknown or expired login state.",
            ),
        )
    try:
        tokens = oidc_flow.exchange_code(
            code=code,
            redirect_uri=_redirect_uri(environ=environ),
            code_verifier=pending.code_verifier,
        )
    except Exception as error:  # noqa: BLE001 - the IdP failure is narrated, never swallowed
        logger.warning("web_auth_code_exchange_failed", reason=repr(error))
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNAUTHORIZED,
                reason="code_exchange_failed",
                detail="The authorization code could not be exchanged.",
            ),
        )
    id_token = tokens.get("id_token")
    access_token = tokens.get("access_token")
    if not isinstance(id_token, str) or not isinstance(access_token, str):
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNAUTHORIZED,
                reason="incomplete_token_response",
                detail="The token response carries no id_token/access_token pair.",
            ),
        )
    try:
        claims = token_verifier.verify_access_token(token=id_token)
    except TokenRejectedError as error:
        logger.warning("web_auth_id_token_rejected", reason=error.reason)
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNAUTHORIZED,
                reason=REASON_INVALID_BEARER,
                detail="The id_token was rejected.",
            ),
        )
    if claims.nonce is not None and claims.nonce != pending.nonce:
        logger.warning("web_auth_callback_rejected", reason="nonce_mismatch")
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.UNAUTHORIZED,
                reason="nonce_mismatch",
                detail="The id_token nonce does not match the pending login.",
            ),
        )
    session = web_sessions.create(claims=claims)
    logger.info("web_auth_login_succeeded", sub=claims.sub)
    return _respond(
        start_response=start_response,
        status=HTTPStatus.FOUND,
        content_type=CONTENT_TYPE_HTML,
        body=b"",
        extra_headers=[
            ("Location", pending.return_to),
            (
                "Set-Cookie",
                _session_cookie_header(
                    session_id=session.session_id,
                    max_age_seconds=int(session.expires_at - session.created_at),
                ),
            ),
        ],
    )


def _auth_logout(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    oidc_flow: OidcFlowClient | None,
    web_sessions: WebSessionStore,
) -> Iterable[bytes]:
    """POST /auth/logout — revoke, destroy the session, expire the cookie."""
    session_id = _session_id_from_cookie(environ=environ)
    if session_id is not None:
        web_sessions.destroy(session_id=session_id)
    scheme = environ.get("wsgi.url_scheme", "http")
    host = environ.get("HTTP_HOST") or environ.get("SERVER_NAME", "localhost")
    post_logout = f"{scheme}://{host}{UI_PATH}"
    if oidc_flow is not None:
        oidc_flow.revoke(token=session_id or "")
    logger.debug("web_auth_logout", had_session=session_id is not None)
    return _respond(
        start_response=start_response,
        status=HTTPStatus.FOUND,
        content_type=CONTENT_TYPE_HTML,
        body=b"",
        extra_headers=[
            ("Location", post_logout),
            ("Set-Cookie", _clear_session_cookie_header()),
        ],
    )


def _is_public_read_route(*, method: str, path: str) -> bool:
    """The web v1 public read-only routes (issue #206, contract §1/§5).

    ``GET /v1/catalog/models`` and ``GET /v1/models/{mid}`` serve the public
    Découvrir zone. Every other API route requires a session cookie or a PAT;
    the gate is adapted to this public/private split, not just complemented.
    """
    if method != "GET":
        return False
    if path == CATALOG_MODELS_PATH:
        return True
    if path.startswith(MODEL_PATH_PREFIX):
        model_id = path.removeprefix(MODEL_PATH_PREFIX)
        return bool(model_id) and "/" not in model_id
    return False


def _auth_gate(
    *,
    environ: WSGIEnvironment,
    start_response: StartResponse,
    settings: ApiSettings,
    token_verifier: TokenVerifier | None,
    web_sessions: WebSessionStore,
    method: str,
    path: str,
) -> Iterable[bytes] | None:
    """The PT-2 auth gate: a uniform 401 problem response on rejection, else None.

    Public read-only routes (issue #206) pass without credentials; on every
    other route a valid Bearer token exposes its validated claims to the
    handlers as ``environ["petrichor.auth_claims"]``. A missing session answers
    ``401`` with ``reason: "session_required"`` (contract §1); the exact
    rejection reason goes to the structured log, never to the response body.
    """
    if _is_public_read_route(method=method, path=path):
        logger.debug("api_public_read_allowed", method=method, path=path)
        return None
    # Web v1 (issue #207): the server-side session cookie authenticates the
    # browser; its claims are exposed like a validated Bearer token.
    cookie_session_id = _session_id_from_cookie(environ=environ)
    if cookie_session_id is not None:
        cookie_session = web_sessions.get(session_id=cookie_session_id)
        if cookie_session is not None:
            environ[ENVIRON_AUTH_CLAIMS] = cookie_session.claims
            logger.debug("api_session_cookie_allowed", path=path)
            return None
    decision = authenticate_request(
        environ=environ, settings=settings, token_verifier=token_verifier
    )
    if decision.allowed:
        if decision.claims is not None:
            environ[ENVIRON_AUTH_CLAIMS] = decision.claims
        return None
    logger.warning(
        "api_request_unauthorized", method=method, path=path, reason=decision.rejection_reason
    )
    problem_reason = (
        REASON_INVALID_BEARER
        if decision.rejection_reason not in (None, REASON_MISSING_CREDENTIALS)
        else REASON_SESSION_REQUIRED
    )
    return _problem_response(
        start_response=start_response,
        environ=environ,
        payload=_problem(
            status=HTTPStatus.UNAUTHORIZED,
            reason=problem_reason,
            detail="A session cookie or a Bearer PAT is required for this route.",
        ),
    )


def create_app(  # noqa: PLR0913, C901 - one keyword per wired dependency (ADR-0003), one dispatch per resource family
    *,
    settings: ApiSettings,
    store: ModelStore,
    run_store: RunStore,
    runner: CheckRunner | None,
    session_store: SessionStore | None = None,
    arbitrator: ProfileArbitrator | None = None,
    qualification: QualificationLookup | None = None,
    state_graph_explorer: StateGraphExplorer | None = None,
    static_resolver: StaticResolver | None = None,
    static_first_prober: StaticFirstProber | None = None,
    arbitration_store: ArbitrationStore | None = None,
    convergence_store: ConvergenceStore | None = None,
    witness_store: WitnessStore | None = None,
    llm_provider: LLMProvider | None = None,
    edit_store: FormulaEditStore | None = None,
    catalog: ModelCatalog | None = None,
    url_import: UrlImportConfig | None = None,
    token_verifier: TokenVerifier | None = None,
    oidc_flow: OidcFlowClient | None = None,
    pending_logins: PendingLoginStore | None = None,
    web_sessions: WebSessionStore | None = None,
) -> WSGIApp:
    """Build the WSGI application: auth first, then routing and negotiation.

    ``token_verifier`` wires the PT-2 middleware: a valid ``Authorization:
    Bearer`` JWT (PAT, validated by the PT-1 brick) is accepted; the web
    session cookie is checked by the gate from ``web_sessions``. The legacy
    ``X-API-Key`` is gone (issue #207). ``oidc_flow``/``pending_logins`` wire
    the browser login routes ``/auth/*``.
    """
    pending_logins = pending_logins if pending_logins is not None else PendingLoginStore()
    web_sessions = web_sessions if web_sessions is not None else WebSessionStore()

    def app(environ: WSGIEnvironment, start_response: StartResponse) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        logger.debug("api_request_received", method=method, path=path)
        # The static UI assets carry no data and are public (spec §9, issue #18).
        routed = _dispatch_ui(
            environ=environ, start_response=start_response, method=method, path=path
        )
        if routed is not None:
            return routed
        # The web v1 login routes are public by design (issue #207).
        routed = _dispatch_auth(
            environ=environ,
            start_response=start_response,
            method=method,
            path=path,
            oidc_flow=oidc_flow,
            pending_logins=pending_logins,
            web_sessions=web_sessions,
            token_verifier=token_verifier,
        )
        if routed is not None:
            return routed
        rejected = _auth_gate(
            environ=environ,
            start_response=start_response,
            settings=settings,
            token_verifier=token_verifier,
            web_sessions=web_sessions,
            method=method,
            path=path,
        )
        if rejected is not None:
            return rejected
        routed = _dispatch_models(
            environ=environ,
            start_response=start_response,
            method=method,
            path=path,
            store=store,
            url_import=url_import,
        )
        if routed is None:
            routed = _dispatch_catalog(
                environ=environ,
                start_response=start_response,
                method=method,
                path=path,
                catalog=catalog,
                store=store,
            )
        if routed is None:
            routed = _dispatch_qualification(
                environ=environ,
                start_response=start_response,
                method=method,
                path=path,
                qualification=qualification,
            )
        if routed is None and method == "GET" and path in (LLM_USAGE_PATH, LLM_CALLS_PATH):
            handler = _get_llm_usage if path == LLM_USAGE_PATH else _get_llm_calls
            routed = handler(
                environ=environ, start_response=start_response, llm_provider=llm_provider
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
                qualification=qualification,
                static_resolver=static_resolver,
                static_first_prober=static_first_prober,
                witness_store=witness_store,
                llm_provider=llm_provider,
                session_store=session_store,
            )
        if routed is None and session_store is not None:
            routed = _dispatch_sessions(
                environ=environ,
                start_response=start_response,
                method=method,
                path=path,
                store=store,
                run_store=run_store,
                session_store=session_store,
                settings=settings,
                arbitrator=arbitrator,
                state_graph_explorer=state_graph_explorer,
                arbitration_store=arbitration_store,
                convergence_store=convergence_store,
                edit_store=edit_store,
                llm_provider=llm_provider,
            )
        if routed is not None:
            return routed
        return _problem_response(
            start_response=start_response,
            environ=environ,
            payload=_problem(
                status=HTTPStatus.NOT_FOUND, reason="not_found", detail=f"No resource at {path}."
            ),
        )

    return app
