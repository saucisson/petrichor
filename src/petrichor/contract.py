"""Contrat d'élicitation et export Markdown du livrable (spec §4.8, issue #17).

The contract aggregates the whole session state into one document: the
**arbitrated profile**, the **proposed hypotheses**, the **questions grouped
by family with their status**, the **proof links** of arbitrated questions
(static or mock runs) and the **append-only journal**. The document is built
purely from the session state, so the export is **replayable**: building it
twice from the same state yields byte-identical output (pinned by a test).

Two serializations of the same document are served by
``GET /v1/sessions/{sid}/contract``: ``application/contract+json; version=1.0``
(the structured contract) and ``text/markdown`` (the deliverable export).
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import CheckFamilyName, KindPattern, RunId, RunStatus, ToolVersion
from petrichor.elicitation import (
    ElicitationCatalog,
    GeneratedQuestion,
    QuestionId,
    TemplateId,
    instantiate_questions,
    order_questions,
)
from petrichor.examinations import (
    CounterVariant,
    ExaminationName,
    examination_for,
    safety_counter_variants,
)
from petrichor.llm.schemas import SemanticProfile
from petrichor.petri_canonical import JsonValue, ModelId
from petrichor.profile import EntryId, ProfileState
from petrichor.session_store import SessionId
from petrichor.triage import TriageKind

if TYPE_CHECKING:
    from petrichor.arbitration import ArbitrationRecord
    from petrichor.petri_canonical import CanonicalModel
    from petrichor.run_store import StoredRun
    from petrichor.session_store import JournalEntry, StoredSession

logger = structlog.get_logger(__name__)

CONTRACT_VERSION: Literal["1.0"] = "1.0"
"""The version of the contract document served as ``application/contract+json``."""

RUN_LINK_PREFIX = "/v1/runs/"
"""The API link prefix of one proof run."""

RunLink = NewType("RunLink", str)
"""The API link of one proof run, e.g. ``/v1/runs/run_7c2a9f``."""

type ContractQuestionStatus = Literal["pending", "accept", "refuse", "verify-first"]
"""The status of a question card: its latest arbitration, or pending."""

type CrossStatus = Literal["unverified", "verified_by_n", "controversial"]
"""The cross-verification status of a clause (spec §17.4, issue #31).

``verified_by_n`` when every done proof agrees on one verdict;
``controversial`` when done proofs disagree — reported, never resolved here;
``unverified`` while no proof is done.
"""


class ContractProof(BaseModel):
    """One available proof of a question: a run of the session, by link (spec §4.8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: RunId
    link: RunLink
    status: RunStatus
    triage: TriageKind
    verdict: str | None = None
    """The verdict of a done run, when the run output carries one."""

    tool_version: ToolVersion
    """The pinned version of the tool that executed the run (spec §12.5)."""

    technique: str | None = None
    """The technique reported by the run output, when present."""

    cost_tool_seconds: float | None = Field(default=None, ge=0)
    """The measured tool time of the run, when the output carries a cost."""


class ContractQuestion(BaseModel):
    """One question of the contract, with its status and its proof links."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: QuestionId
    template_id: TemplateId
    family: CheckFamilyName
    kind: KindPattern
    formula: str = Field(min_length=1)
    triage: TriageKind
    risk: int = Field(ge=1, le=5)
    cost: int = Field(ge=0)
    scenario: str = Field(min_length=1)
    role: str = Field(min_length=1)
    status: ContractQuestionStatus
    proofs: tuple[ContractProof, ...] = ()
    examination: ExaminationName
    """The MCC examination of the question (spec §17.3, issue #30)."""
    counter_variant: CounterVariant | None = None
    """The independent complement verification of a safety question (spec §17.3)."""
    cross_status: CrossStatus
    """The computed cross-verification status of the clause (spec §17.4, issue #31)."""
    verified_by: int = Field(ge=0)
    """How many done proofs agree on the verdict; 0 when unverified or controversial."""


class ContractProfileEntry(BaseModel):
    """One profile entry as exported: proposed hypothesis plus arbitration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: EntryId
    elements: str = Field(min_length=1)
    proposed_role: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(min_length=1)
    accepted: bool
    chosen_role: str | None = None
    """The role the human chose; None while the entry is not arbitrated."""


class ContractDocument(BaseModel):
    """The whole contract of one session (spec §4.8): the exportable deliverable."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["1.0"] = CONTRACT_VERSION
    session_id: SessionId
    model_id: ModelId
    profile: tuple[ContractProfileEntry, ...]
    """The arbitrated profile: every proposed entry with its arbitration."""

    questions: tuple[ContractQuestion, ...]
    """The questions of the session, ordered by the solicitation order (§14.3)."""

    journal: tuple[dict[str, JsonValue], ...]
    """The append-only journal entries, serialized as plain JSON objects."""


def arbitrated_profile(*, profile_state: ProfileState) -> SemanticProfile:
    """The arbitrated profile: only accepted entries, with the chosen roles."""
    chosen = {
        arbitration.entry_id: arbitration.chosen_role
        for arbitration in profile_state.arbitrations
        if arbitration.accepted
    }
    return SemanticProfile(
        entries=tuple(
            entry.model_copy(update={"role": chosen[EntryId(entry.id)]})
            for entry in profile_state.proposed.entries
            if entry.id in chosen
        )
    )


def _latest_decisions(
    *, arbitration_history: list[ArbitrationRecord]
) -> dict[QuestionId, ContractQuestionStatus]:
    """The latest decision of each question, in append order (last wins)."""
    latest: dict[QuestionId, ContractQuestionStatus] = {}
    for record in arbitration_history:
        latest[record.question_id] = record.decision
    return latest


def _proofs_for_question(
    *, question: GeneratedQuestion, runs: list[StoredRun]
) -> tuple[ContractProof, ...]:
    """The proof links of one question: the session runs on the same formula."""
    proofs = []
    for run in runs:
        if run.formula != question.formula:
            continue
        verdict = run.output.get("verdict") if run.output is not None else None
        technique = run.output.get("technique") if run.output is not None else None
        cost = run.output.get("cost") if run.output is not None else None
        cost_tool_seconds = cost.get("tool_seconds") if isinstance(cost, dict) else None
        proofs.append(
            ContractProof(
                run_id=run.run_id,
                link=RunLink(f"{RUN_LINK_PREFIX}{run.run_id}"),
                status=run.status,
                triage=run.triage.kind,
                verdict=verdict if isinstance(verdict, str) else None,
                tool_version=run.tool_version,
                technique=technique if isinstance(technique, str) else None,
                cost_tool_seconds=(
                    float(cost_tool_seconds)
                    if isinstance(cost_tool_seconds, (int, float))
                    else None
                ),
            )
        )
    return tuple(proofs)


def cross_status_for_proofs(*, proofs: tuple[ContractProof, ...]) -> tuple[CrossStatus, int]:
    """The cross-verification status computed from the done proofs (spec §17.4, issue #31).

    Every done proof agreeing on one verdict → ``verified_by_n`` with the
    count of agreeing proofs. Done proofs disagreeing → ``controversial``
    (reported to the engineer, never resolved here). No done proof →
    ``unverified``.
    """
    verdicts = [
        proof.verdict for proof in proofs if proof.status == "done" and proof.verdict is not None
    ]
    if not verdicts:
        return "unverified", 0
    if len(set(verdicts)) == 1:
        return "verified_by_n", len(verdicts)
    return "controversial", 0


def build_contract(
    *,
    session: StoredSession,
    profile_state: ProfileState,
    model: CanonicalModel,
    catalog: ElicitationCatalog,
    arbitration_history: list[ArbitrationRecord],
    runs: list[StoredRun],
    journal: list[JournalEntry],
) -> ContractDocument:
    """Build the contract document purely from the session state (replayable)."""
    arbitrated = arbitrated_profile(profile_state=profile_state)
    generated = instantiate_questions(catalog=catalog, profile=arbitrated, model=model)
    ordered = order_questions(
        questions=generated,
        uncovered_groups={group.group_id for group in profile_state.uncovered_groups()},
    )
    latest = _latest_decisions(arbitration_history=arbitration_history)

    def _with_cross_status(*, question: GeneratedQuestion) -> ContractQuestion:
        """Build one contract question with its computed cross-verification status."""
        proofs = _proofs_for_question(question=question, runs=runs)
        cross_status, verified_by = cross_status_for_proofs(proofs=proofs)
        return ContractQuestion(
            question_id=question.question_id,
            template_id=question.template_id,
            family=question.family,
            kind=question.kind,
            formula=question.formula,
            triage=question.triage,
            risk=question.risk,
            cost=question.cost,
            scenario=question.scenario,
            role=question.role,
            status=latest.get(question.question_id, "pending"),
            proofs=proofs,
            examination=examination_for(family=question.family, kind=question.kind),
            counter_variant=(
                safety_counter_variants(kind=question.kind, formula=question.formula)[1]
                if question.family == "safety"
                else None
            ),
            cross_status=cross_status,
            verified_by=verified_by,
        )

    questions = tuple(_with_cross_status(question=question) for question in ordered)
    arbitrated_ids = {
        arbitration.entry_id: arbitration for arbitration in profile_state.arbitrations
    }
    profile = tuple(
        ContractProfileEntry(
            entry_id=EntryId(entry.id),
            elements=entry.elements,
            proposed_role=entry.role,
            confidence=entry.confidence,
            evidence=entry.evidence,
            accepted=EntryId(entry.id) in arbitrated_ids
            and arbitrated_ids[EntryId(entry.id)].accepted,
            chosen_role=(
                arbitrated_ids[EntryId(entry.id)].chosen_role
                if EntryId(entry.id) in arbitrated_ids
                else None
            ),
        )
        for entry in profile_state.proposed.entries
    )
    document = ContractDocument(
        session_id=session.session_id,
        model_id=session.model_id,
        profile=profile,
        questions=questions,
        journal=tuple(entry.model_dump(mode="json") for entry in journal),
    )
    logger.debug(
        "contract_built",
        session_id=session.session_id,
        questions=len(questions),
        arbitrated=sum(1 for entry in profile if entry.accepted),
        journal_entries=len(journal),
    )
    return document


def _iso_timestamp(timestamp: float) -> str:
    """Render a journal timestamp as a deterministic ISO-8601 UTC string."""
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()


def _render_cross_status(*, question: ContractQuestion) -> str:
    """The human-readable cross-verification status of one question (spec §17.4)."""
    if question.cross_status == "verified_by_n":
        return f"vérifiée par {question.verified_by} outils"
    if question.cross_status == "controversial":
        return "controversée"
    return "non vérifiée"


def render_markdown(*, document: ContractDocument) -> str:
    """Render the contract document as the Markdown deliverable (spec §4.8)."""
    lines = [
        f"# Contrat d'élicitation — session `{document.session_id}`",
        "",
        f"- Modèle : `{document.model_id}`",
        f"- Version du contrat : {document.version}",
        "- Export rejouable depuis l'état de session.",
        "",
        "## Profil arbitré",
        "",
        "| Entrée | Éléments | Rôle proposé | Rôle retenu | Statut |",
        "| --- | --- | --- | --- | --- |",
    ]
    for entry in document.profile:
        status = "accepté" if entry.accepted else ("refusé" if entry.chosen_role else "en attente")
        chosen = entry.chosen_role if entry.chosen_role is not None else "—"
        lines.append(
            f"| `{entry.entry_id}` | {entry.elements} | {entry.proposed_role}"
            f" | {chosen} | {status} |"
        )
    lines += [
        "",
        "## Hypothèses proposées",
        "",
        "| Entrée | Rôle proposé | Confiance | Preuve |",
        "| --- | --- | --- | --- |",
    ]
    for entry in document.profile:
        lines.append(
            f"| `{entry.entry_id}` | {entry.proposed_role}"
            f" | {entry.confidence:.2f} | {entry.evidence} |"
        )
    families = sorted({question.family for question in document.questions})
    lines += ["", "## Questions par famille", ""]
    if not families:
        lines.append("Aucune question générée (profil non arbitré).")
    for family in families:
        lines += [
            f"### {family}",
            "",
            "| ID | Formule | Triage | Risque | Coût | Statut | Vérif. croisée | Preuves |",
        ]
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for question in (q for q in document.questions if q.family == family):
            proofs = (
                ", ".join(f"[{proof.run_id}]({proof.link})" for proof in question.proofs)
                if question.proofs
                else "—"
            )
            cross = _render_cross_status(question=question)
            lines.append(
                f"| `{question.question_id}` | {question.formula} | {question.triage}"
                f" | {question.risk} | {question.cost} | {question.status} | {cross} | {proofs} |"
            )
            if question.cross_status == "controversial":
                camps = " ; ".join(
                    f"{proof.tool_version} → {proof.verdict}"
                    for proof in question.proofs
                    if proof.status == "done" and proof.verdict is not None
                )
                lines.append(f"  - Controverse remontée à l'ingénieur — camps : {camps}.")
        lines.append("")
    lines += ["## Journal", "", "| Horodatage | Événement | Détail |", "| --- | --- | --- |"]
    for journal_entry in document.journal:
        timestamp = journal_entry.get("timestamp")
        rendered = _iso_timestamp(timestamp) if isinstance(timestamp, (int, float)) else "—"
        event = str(journal_entry.get("event", ""))
        detail_value = journal_entry.get("detail")
        detail = (
            ", ".join(f"{key}={value}" for key, value in sorted(detail_value.items()))
            if isinstance(detail_value, dict)
            else ""
        )
        lines.append(f"| {rendered} | {event} | {detail} |")
    lines.append("")
    markdown = "\n".join(lines)
    logger.debug(
        "contract_markdown_rendered",
        session_id=document.session_id,
        lines=len(lines),
        bytes=len(markdown.encode("utf-8")),
    )
    return markdown
