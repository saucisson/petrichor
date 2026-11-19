"""Elicitation catalogue: the frozen role x guideword templates (spec §14.1, issue #15).

The catalogue is a **versioned, frozen configuration file**
(``elicitation_catalog.v1.json`` next to this module): the coverage grid and
the formula templates are pinned before any study (pre-registration,
spec §20.2) - any change is a new catalog version, never a retro-applied edit.

Each productive (role, guideword) couple instantiates one question per
matching profile entry; every question carries a **stable identifier**
(content hash of template + group + formula) and its formula. Non-productive
couples are documented by their absence from the generated set, never silent.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from petrichor.checks import FAMILY_PATTERNS, CheckFamilyName, KindPattern
from petrichor.profile import GroupId
from petrichor.triage import STATIC_KINDS, TriageKind

if TYPE_CHECKING:
    from petrichor.llm.schemas import SemanticProfile
    from petrichor.petri_canonical import CanonicalModel

logger = structlog.get_logger(__name__)

CATALOG_PATH = Path(__file__).with_name("elicitation_catalog.v1.json")
"""The frozen v1 catalog file; a new study version ships a new file."""

QUESTION_ID_PREFIX = "q_"
QUESTION_ID_HEX_DIGITS = 12
PLACEHOLDER_NAMES = ("place", "place_b", "place_c")
"""The formula placeholders ``{place}``, ``{place_b}``, ``{place_c}``."""

QuestionId = NewType("QuestionId", str)
"""A stable generated-question identifier, e.g. ``q_3f9a1c2b7d4e``."""

TemplateId = NewType("TemplateId", str)
"""A catalog template identifier, e.g. ``tpl_res_shared_more``."""

_ELEMENT_RANGE_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)_(\d+)\.\.(\d+)")
_FIRST_TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SYMMETRY_INDEX_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)_\d+")
"""A trailing symmetry index, e.g. the ``_2`` of ``eat_2`` (spec §14.5)."""


class CatalogTemplate(BaseModel):
    """One role x guideword template of the frozen catalog (spec §14.1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: TemplateId
    role: str = Field(min_length=1)
    guideword: str = Field(min_length=1)
    family: CheckFamilyName
    kind: KindPattern = Field(min_length=1)
    risk: int = Field(ge=1, le=5)
    """The base risk of the template (spec §14.3, 1-5)."""

    cost: int = Field(ge=0)
    """The base cost of the pattern (spec §14.3): static 0, EF 1, AG AF 3."""

    scenario: str = Field(min_length=1)
    formula_template: str = Field(min_length=1)
    """The formula with ``{place}`` / ``{place_b}`` / ``{place_c}`` placeholders."""


class CoverageGridEntry(BaseModel):
    """One frozen coverage group of the pre-registered grid (spec §14.6, §20.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: GroupId = Field(min_length=1)
    label: str = Field(min_length=1)
    role: str = Field(min_length=1)


class ElicitationCatalog(BaseModel):
    """The frozen catalog: version, templates and coverage grid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal["v1"]
    templates: tuple[CatalogTemplate, ...]
    coverage_grid: tuple[CoverageGridEntry, ...]


class GeneratedQuestion(BaseModel):
    """One instantiated question: stable id, formula, triage, risk x cost, coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_id: QuestionId
    template_id: TemplateId
    family: CheckFamilyName
    kind: KindPattern
    formula: str = Field(min_length=1)
    triage: TriageKind
    risk: int = Field(ge=1, le=5)
    cost: int = Field(ge=0)
    covers: tuple[GroupId, ...]
    """The coverage groups this question mentions (spec §14.6)."""

    scenario: str = Field(min_length=1)
    guideword: str = Field(min_length=1)
    role: str = Field(min_length=1)
    convergence_hint: QuestionId | None = None
    """The first earlier generated question this one converges with (spec §14.5)."""


def load_catalog(*, path: Path = CATALOG_PATH) -> ElicitationCatalog:
    """Load and validate the frozen catalog file (spec §20.2 pre-registration)."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        catalog = ElicitationCatalog.model_validate(payload)
    except ValidationError as error:
        raise ValueError(f"Invalid elicitation catalog {path}: {error}") from error
    for template in catalog.templates:
        if template.kind not in FAMILY_PATTERNS[template.family]:
            raise ValueError(
                f"Template {template.id}: kind {template.kind!r} is outside family"
                f" {template.family!r} (spec §12.1)."
            )
    logger.debug(
        "elicitation_catalog_loaded",
        version=catalog.version,
        templates=len(catalog.templates),
        coverage_groups=len(catalog.coverage_grid),
    )
    return catalog


def question_id(*, template_id: TemplateId, group_id: GroupId, formula: str) -> QuestionId:
    """The stable identifier of a question: content hash of template + group + formula."""
    material = f"{template_id}\n{group_id}\n{formula}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return QuestionId(f"{QUESTION_ID_PREFIX}{digest[:QUESTION_ID_HEX_DIGITS]}")


def _instantiate_formula(
    *, template: CatalogTemplate, elements: str, model: CanonicalModel
) -> str | None:
    """Fill the formula placeholders from the entry elements, e.g. ``fork_1..3``.

    Returns None when the entry carries fewer elements than the template
    needs: the couple is non-productive and is documented as such by the
    caller (spec §14.1) - never padded with an unrelated place, which would
    produce a formula about the wrong elements.
    """
    range_match = _ELEMENT_RANGE_PATTERN.search(elements)
    if range_match is not None:
        base = range_match.group(1)
        start, end = int(range_match.group(2)), int(range_match.group(3))
        places = [f"{base}_{index}" for index in range(start, end + 1)]
    else:
        token = _FIRST_TOKEN_PATTERN.search(elements)
        places = [token.group(0)] if token is not None else []
    names = [name for name in PLACEHOLDER_NAMES if f"{{{name}}}" in template.formula_template]
    if len(places) < len(names):
        return None
    return template.formula_template.format(**dict(zip(names, places, strict=False)))


def instantiate_questions(
    *,
    catalog: ElicitationCatalog,
    profile: SemanticProfile,
    model: CanonicalModel,
) -> tuple[GeneratedQuestion, ...]:
    """Instantiate every productive (role, guideword) couple over the profile (spec §14.1)."""
    questions: list[GeneratedQuestion] = []
    for template in catalog.templates:
        for entry in profile.entries:
            if not entry.role.startswith(template.role):
                continue
            group = GroupId(
                next(
                    (g.group_id for g in catalog.coverage_grid if g.role == template.role),
                    entry.id,
                )
            )
            formula = _instantiate_formula(template=template, elements=entry.elements, model=model)
            if formula is None:
                logger.debug(
                    "question_generation_skipped",
                    template_id=template.id,
                    entry_id=entry.id,
                    reason="non-productive couple (spec §14.1): not enough elements",
                )
                continue
            questions.append(
                GeneratedQuestion(
                    question_id=question_id(
                        template_id=template.id, group_id=group, formula=formula
                    ),
                    template_id=template.id,
                    family=template.family,
                    kind=template.kind,
                    formula=formula,
                    triage="statique" if template.kind in STATIC_KINDS else "MC",
                    risk=template.risk,
                    cost=template.cost,
                    covers=(group,),
                    scenario=template.scenario,
                    guideword=template.guideword,
                    role=entry.role,
                )
            )
    hints = convergence_hints(questions=tuple(questions))
    if hints:
        questions = [
            question.model_copy(update={"convergence_hint": hints.get(question.question_id)})
            for question in questions
        ]
    logger.debug(
        "elicitation_questions_generated",
        questions=len(questions),
        statique=sum(1 for question in questions if question.triage == "statique"),
        mc=sum(1 for question in questions if question.triage == "MC"),
        convergence_hints=len(hints),
    )
    return tuple(questions)


def normalize_formula(*, formula: str) -> str:
    """Collapse every whitespace run of a formula (the §14.5 normalization)."""
    return " ".join(formula.split())


def _symmetry_normalized_tokens(*, formula: str) -> frozenset[str]:
    """The identifiers of a formula with symmetry indices erased (``eat_1`` → ``eat_*``)."""
    return frozenset(
        _SYMMETRY_INDEX_PATTERN.sub(r"\1_*", token)
        for token in _IDENTIFIER_PATTERN.findall(formula)
    )


def formulas_converge(*, first: str, second: str) -> bool:
    """Whether two formulas converge (spec §14.5).

    Identical after normalization, or same targets up to symmetry-index
    substitution (``eat_1`` ≡ ``eat_2`` under a verified symmetry).
    """
    if normalize_formula(formula=first) == normalize_formula(formula=second):
        return True
    return _symmetry_normalized_tokens(formula=first) == _symmetry_normalized_tokens(formula=second)


def questions_converge(*, first: GeneratedQuestion, second: GeneratedQuestion) -> bool:
    """Whether two questions converge: same ``family.kind`` and converging formulas (spec §14.5)."""
    return (
        first.family == second.family
        and first.kind == second.kind
        and formulas_converge(first=first.formula, second=second.formula)
    )


def convergence_hints(*, questions: tuple[GeneratedQuestion, ...]) -> dict[QuestionId, QuestionId]:
    """Map each question to the first earlier question it converges with (spec §14.5)."""
    hints: dict[QuestionId, QuestionId] = {}
    for index, question in enumerate(questions):
        for earlier in questions[:index]:
            if questions_converge(first=question, second=earlier):
                hints[question.question_id] = earlier.question_id
                break
    logger.debug(
        "elicitation_convergence_hints",
        questions=len(questions),
        hints=len(hints),
    )
    return hints


def order_questions(
    *,
    questions: tuple[GeneratedQuestion, ...],
    uncovered_groups: frozenset[GroupId] | set[GroupId],
) -> tuple[GeneratedQuestion, ...]:
    """Order the solicitation: holes first, then risk desc x cost asc (§14.3/§14.6)."""
    ordered = sorted(
        questions,
        key=lambda question: (
            0 if any(group in uncovered_groups for group in question.covers) else 1,
            -question.risk,
            question.cost,
            question.question_id,
        ),
    )
    logger.debug(
        "elicitation_questions_ordered",
        questions=len(ordered),
        uncovered=len(uncovered_groups),
    )
    return tuple(ordered)
