"""Execution of the pilote study session (spec §20, issue #41).

One full elicitation session on the frozen v1 materials: the real model
(DrinkVendingMachine-PT-02) is imported, the session is created with the
pre-registered budget cap (3600 s of tool time), the profile is proposed and
arbitrated, every generated question is arbitrated (accept) and checked, the
converging pairs are fused, and the contract is exported. The cumulative cost
is measured against the cap via ``/v1/sessions/{sid}/budget``, and everything
- session journal, contract exports, report - is written under the output
directory with content hashes (spec §12.5, §20.9).

The runner is engine-agnostic: the CLI wires the real verifypn compose
service; tests wire a fake runner. The verdicts always come from the
``engines`` package (verdict lock, spec §19.4).
"""

import hashlib
import json
import time
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING

import httpx
import structlog
from pydantic import BaseModel, ConfigDict, SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.arbitration import ArbitrationStore
from petrichor.checks import RunId, SessionId
from petrichor.convergence import ConvergenceStore
from petrichor.llm.schemas import ProfileEntry, SemanticProfile
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ContentHash, ModelId
from petrichor.pnml_import import import_pnml
from petrichor.profile import ProfileArbitrator
from petrichor.question_edits import FormulaEditStore
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore
from petrichor.study import pilote
from petrichor.witness import WitnessStore

if TYPE_CHECKING:
    from pathlib import Path

    from petrichor.checks import CheckRunner
    from petrichor.triage import StaticResolver

logger = structlog.get_logger(__name__)

STUDY_API_KEY = "study-p"
AUTH_HEADERS = {"X-API-Key": STUDY_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}
CONTRACT_HEADERS = {**AUTH_HEADERS, "Accept": "application/contract+json; version=1.0"}
MARKDOWN_HEADERS = {**AUTH_HEADERS, "Accept": "text/markdown"}

RUN_POLL_INTERVAL_SECONDS = 0.5
RUN_POLL_TIMEOUT_SECONDS = 600.0

RAPPORT_FILE_NAME = "rapport.json"
CONTRACT_JSON_FILE_NAME = "contrat.json"
CONTRACT_MD_FILE_NAME = "contrat.md"
JOURNAL_FILE_NAME = "journal.json"

PILOTE_PROFILE = SemanticProfile(
    entries=(
        ProfileEntry(
            id="product_slots",
            elements="productSlots_1..2",
            role="ressource partagée",
            conf=0.9,
            evidence="les emplacements produit sont des jetons partages entre les recettes",
        ),
        ProfileEntry(
            id="option_slots",
            elements="optionSlots_1..2",
            role="ressource partagée",
            conf=0.9,
            evidence="les emplacements d'options sont des jetons partages",
        ),
        ProfileEntry(
            id="ready",
            elements="ready_1..8",
            role="section critique",
            conf=0.85,
            evidence="ready_i marque la machine prete a delivrer la boisson i",
        ),
        ProfileEntry(
            id="wait",
            elements="wait_1..8",
            role="état d'automate",
            conf=0.95,
            evidence="wait_i est l'etat d'attente de la commande i",
        ),
    )
)
"""The profile proposed for the pilote model (DrinkVendingMachine-PT-02)."""


class PiloteExecutionError(Exception):
    """The pilote session could not reach its stopping criterion."""


class PiloteBudgetReport(BaseModel):
    """The measured cumulative cost of the pilote session against the cap (§12.4, §20.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cap_seconds: float
    consumed_seconds: float
    reserved_seconds: float
    remaining_seconds: float
    run_count: int
    within_cap: bool


class PiloteRunSummary(BaseModel):
    """One run of the pilote session, as recorded in the report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    family: str
    kind: str
    formula: str
    status: str
    verdict: str | None
    tool_seconds: float | None


class PiloteReport(BaseModel):
    """The execution record of the pilote session (spec §20.9: archived with hashes)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    study_id: str
    study_version: str
    preregistered_at: date
    executed_at: datetime
    model_file: str
    model_sha256: ContentHash
    model_content_hash: ContentHash
    model_id: ModelId
    session_id: SessionId
    engine: str
    engine_version: str
    budget: PiloteBudgetReport
    questions_total: int
    questions_arbitrated: int
    runs: tuple[PiloteRunSummary, ...]
    contract_sha256: ContentHash
    markdown_sha256: ContentHash
    journal_sha256: ContentHash
    materials: dict[str, ContentHash]
    output_dir: str


def _sha256_bytes(*, payload: bytes) -> ContentHash:
    return ContentHash(f"{pilote.HASH_ALGORITHM}:{hashlib.sha256(payload).hexdigest()}")


def _expect(*, response: httpx.Response, status: HTTPStatus) -> None:
    """A study step that fails is a typed error, never a silent continuation."""
    if response.status_code != status.value:
        path = response.request.url.path
        raise PiloteExecutionError(
            f"expected {status.value} from {response.request.method} {path},"
            f" got {response.status_code}: {response.text[:500]}"
        )


def _as_float(*, value: object) -> float:
    """A numeric payload field, narrowed explicitly (no dynamic escape)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PiloteExecutionError(f"expected a number in the payload, got {value!r}")
    return float(value)


def _as_int(*, value: object) -> int:
    """An integer payload field, narrowed explicitly."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise PiloteExecutionError(f"expected an integer in the payload, got {value!r}")
    return value


def _as_object_list(*, value: object) -> list[dict[str, object]]:
    """A JSON array of objects, narrowed explicitly."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise PiloteExecutionError(f"expected an array of objects in the payload, got {value!r}")
    return value


def _get_json(*, client: httpx.Client, path: str) -> dict[str, object]:
    """GET one JSON resource; a non-200 is a typed PiloteExecutionError."""
    response = client.get(path, headers=AUTH_HEADERS)
    _expect(response=response, status=HTTPStatus.OK)
    payload: dict[str, object] = response.json()
    return payload


def _wait_terminal(*, client: httpx.Client, run_id: str) -> dict[str, object]:
    """Poll one run until it leaves the running state (real engines are slow)."""
    deadline = time.monotonic() + RUN_POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        run = _get_json(client=client, path=f"/v1/runs/{run_id}")
        if run["status"] != "running":
            return run
        time.sleep(RUN_POLL_INTERVAL_SECONDS)
    raise PiloteExecutionError(f"run {run_id} did not reach a terminal state")


def _amorcage(*, client: httpx.Client, config: pilote.StudyConfig) -> tuple[str, str]:
    """Import the frozen model, create the session with the study budget cap."""
    imported = client.post("/v1/models", content=pilote.load_model_text(), headers=PNML_HEADERS)
    _expect(response=imported, status=HTTPStatus.CREATED)
    model_id = str(imported.json()["id"])
    created = client.post(
        "/v1/sessions",
        json={"model_id": model_id, "budget_seconds": config.session_budget_seconds},
        headers=JSON_HEADERS,
    )
    _expect(response=created, status=HTTPStatus.CREATED)
    return model_id, str(created.json()["session_id"])


def _elicitation(
    *, client: httpx.Client, session_id: str, arbitrator: ProfileArbitrator
) -> list[dict[str, object]]:
    """Propose the profile, arbitrate every entry, then arbitrate every question card."""
    arbitrator.propose(session_id=SessionId(session_id), profile=PILOTE_PROFILE)
    for entry in PILOTE_PROFILE.entries:
        response = client.post(
            f"/v1/sessions/{session_id}/profile/arbitrate",
            json={"entry_id": entry.id, "accepted": True, "chosen_role": entry.role},
            headers=JSON_HEADERS,
        )
        _expect(response=response, status=HTTPStatus.OK)
    response = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS)
    _expect(response=response, status=HTTPStatus.OK)
    contract: dict[str, object] = response.json()
    questions = _as_object_list(value=contract["questions"])
    if not questions:
        raise PiloteExecutionError("the arbitrated profile generated no question")
    for question in questions:
        response = client.post(
            f"/v1/sessions/{session_id}/arbitration-requests",
            json={"question_id": question["question_id"], "decision": "accept"},
            headers=JSON_HEADERS,
        )
        _expect(response=response, status=HTTPStatus.CREATED)
    return questions


def _checks(
    *, client: httpx.Client, model_id: str, session_id: str, questions: list[dict[str, object]]
) -> list[PiloteRunSummary]:
    """Run every question (static or MC) against the session budget."""
    runs: list[PiloteRunSummary] = []
    for question in questions:
        posted = client.post(
            f"/v1/checks/{question['family']}",
            json={
                "model_id": model_id,
                "kind": question["kind"],
                "formula": question["formula"],
                "session_id": session_id,
            },
            headers=JSON_HEADERS,
        )
        _expect(response=posted, status=HTTPStatus.ACCEPTED)
        run = _wait_terminal(client=client, run_id=str(posted.json()["run_id"]))
        if run["status"] != "done":
            raise PiloteExecutionError(f"run {run['run_id']} failed: {run.get('error')}")
        output = run["output"]
        cost = output.get("cost") if isinstance(output, dict) else None
        runs.append(
            PiloteRunSummary(
                run_id=RunId(str(run["run_id"])),
                family=str(question["family"]),
                kind=str(question["kind"]),
                formula=str(question["formula"]),
                status=str(run["status"]),
                verdict=output.get("verdict") if isinstance(output, dict) else None,
                tool_seconds=float(cost["tool_seconds"]) if isinstance(cost, dict) else None,
            )
        )
    return runs


def _convergences(
    *, client: httpx.Client, session_id: str, questions: list[dict[str, object]]
) -> None:
    """Fuse every converging pair (human-only fusion, spec §14.5)."""
    for question in questions:
        hint = question.get("convergence_hint")
        if hint is None:
            continue
        fused = client.post(
            f"/v1/sessions/{session_id}/convergences",
            json={"question_id": question["question_id"], "proposition_id": hint},
            headers=JSON_HEADERS,
        )
        _expect(response=fused, status=HTTPStatus.CREATED)


def _synthese(
    *, client: httpx.Client, session_id: str, output_dir: Path
) -> tuple[bytes, bytes, bytes, dict[str, object], list[dict[str, object]]]:
    """Export the contract (JSON + Markdown), read the budget and the journal."""
    contract_response = client.get(f"/v1/sessions/{session_id}/contract", headers=CONTRACT_HEADERS)
    _expect(response=contract_response, status=HTTPStatus.OK)
    final_contract = contract_response.json()
    markdown_response = client.get(f"/v1/sessions/{session_id}/contract", headers=MARKDOWN_HEADERS)
    _expect(response=markdown_response, status=HTTPStatus.OK)
    markdown = markdown_response.text
    budget = _get_json(client=client, path=f"/v1/sessions/{session_id}/budget")
    journal_response = client.get(f"/v1/sessions/{session_id}/journal", headers=AUTH_HEADERS)
    _expect(response=journal_response, status=HTTPStatus.OK)
    journal: list[dict[str, object]] = journal_response.json()
    contract_bytes = json.dumps(final_contract, sort_keys=True, separators=(",", ":")).encode()
    markdown_bytes = markdown.encode("utf-8")
    journal_bytes = json.dumps(journal, sort_keys=True, separators=(",", ":")).encode()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / CONTRACT_JSON_FILE_NAME).write_bytes(contract_bytes)
    (output_dir / CONTRACT_MD_FILE_NAME).write_bytes(markdown_bytes)
    (output_dir / JOURNAL_FILE_NAME).write_bytes(journal_bytes)
    return contract_bytes, markdown_bytes, journal_bytes, budget, journal


def run_pilote(
    *,
    output_dir: Path,
    runner: CheckRunner,
    static_resolver: StaticResolver,
    engine_name: str,
    engine_version: str,
    check_timeout_seconds: int = 300,
) -> PiloteReport:
    """Run the full pilote session on the frozen v1 materials; archive everything.

    The session runs until the stopping criterion: every generated question
    arbitrated (full coverage) - or an explicit refusal, never a silent stop.
    The static resolver is injected (verdict lock, spec §19.4): it lives in
    ``petrichor.engines`` and this module never imports that package.
    """
    config = pilote.load_config()
    materials = pilote.verify_materials(config=config)
    model_content_hash = import_pnml(text=pilote.load_model_text()).content_hash
    settings = ApiSettings(
        api_key=SecretStr(STUDY_API_KEY),
        check_timeout_seconds=check_timeout_seconds,
    )
    arbitrator = ProfileArbitrator()
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=runner,
        session_store=SessionStore(directory=output_dir / "sessions"),
        arbitrator=arbitrator,
        static_resolver=static_resolver,
        arbitration_store=ArbitrationStore(directory=output_dir / "arbitrations"),
        convergence_store=ConvergenceStore(directory=output_dir / "convergences"),
        witness_store=WitnessStore(directory=output_dir / "witnesses"),
        edit_store=FormulaEditStore(directory=output_dir / "formula_edits"),
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    model_id, session_id = _amorcage(client=client, config=config)
    questions = _elicitation(client=client, session_id=session_id, arbitrator=arbitrator)
    runs = _checks(client=client, model_id=model_id, session_id=session_id, questions=questions)
    _convergences(client=client, session_id=session_id, questions=questions)
    contract_bytes, markdown_bytes, journal_bytes, budget, _journal = _synthese(
        client=client, session_id=session_id, output_dir=output_dir
    )
    report = PiloteReport(
        study_id=config.study_id,
        study_version=config.study_version,
        preregistered_at=config.preregistered_at,
        executed_at=datetime.now(UTC),
        model_file=pilote.MODEL_FILE_NAME,
        model_sha256=materials[pilote.MODEL_FILE_NAME],
        model_content_hash=model_content_hash,
        model_id=ModelId(model_id),
        session_id=SessionId(session_id),
        engine=engine_name,
        engine_version=engine_version,
        budget=PiloteBudgetReport(
            cap_seconds=_as_float(value=budget["budget_seconds"]),
            consumed_seconds=_as_float(value=budget["consumed_seconds"]),
            reserved_seconds=_as_float(value=budget["reserved_seconds"]),
            remaining_seconds=_as_float(value=budget["remaining_seconds"]),
            run_count=_as_int(value=budget["run_count"]),
            within_cap=_as_float(value=budget["remaining_seconds"]) >= 0.0,
        ),
        questions_total=len(questions),
        questions_arbitrated=len(questions),
        runs=tuple(runs),
        contract_sha256=_sha256_bytes(payload=contract_bytes),
        markdown_sha256=_sha256_bytes(payload=markdown_bytes),
        journal_sha256=_sha256_bytes(payload=journal_bytes),
        materials=materials,
        output_dir=str(output_dir),
    )
    (output_dir / RAPPORT_FILE_NAME).write_text(report.model_dump_json(indent=2), encoding="utf-8")
    logger.info(
        "pilote_session_executed",
        session_id=session_id,
        questions=len(questions),
        runs=len(runs),
        consumed_seconds=report.budget.consumed_seconds,
        cap_seconds=report.budget.cap_seconds,
        within_cap=report.budget.within_cap,
    )
    return report
