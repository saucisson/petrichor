"""Tests for the frozen elicitation catalogue (issue #15, spec §14.1, §14.3, §14.6).

The 11 role x guideword templates instantiate on Philosophes-3 with stable
question ids and formulas; the generation targets uncovered coverage groups
first, then risk descending x cost ascending. The catalogue is a frozen,
versioned config file (pre-registration, spec §20.2).
"""

import json
from typing import TYPE_CHECKING

import pytest
from philosophes3 import build_philosophes3

if TYPE_CHECKING:
    from pathlib import Path

from petrichor.checks import FAMILY_PATTERNS
from petrichor.elicitation import (
    CATALOG_PATH,
    ElicitationCatalog,
    instantiate_questions,
    load_catalog,
    order_questions,
    question_id,
)
from petrichor.llm.schemas import ProfileEntry, SemanticProfile

PHILOSOPHES3_PROFILE = SemanticProfile(
    entries=(
        ProfileEntry(
            id="think",
            elements="think_1..3",
            role="état d'automate (attente)",
            conf=0.9,
            evidence="état d'attente du philosophe",
        ),
        ProfileEntry(
            id="hasL",
            elements="hasL_1..3",
            role="détention intermédiaire",
            conf=0.9,
            evidence="détention du jeton gauche",
        ),
        ProfileEntry(
            id="eat",
            elements="eat_1..3",
            role="section critique",
            conf=0.95,
            evidence="repas du philosophe",
        ),
        ProfileEntry(
            id="fork",
            elements="fork_1..3",
            role="ressource partagée (capacité 1)",
            conf=0.97,
            evidence="fourchette partagée",
        ),
        ProfileEntry(
            id="take",
            elements="takeL_i / takeR_i",
            role="acquisition (2 phases)",
            conf=0.95,
            evidence="acquisition des deux fourchettes",
        ),
        ProfileEntry(
            id="rel",
            elements="rel_i",
            role="libération",
            conf=0.97,
            evidence="restitution des fourchettes",
        ),
        ProfileEntry(
            id="motif",
            elements="motif global",
            role="motif global (symétrie d'ordre 3)",
            conf=0.93,
            evidence="anneau de processus",
        ),
        ProfileEntry(
            id="flow",
            elements="takeL_i / takeR_i",
            role="flux critique",
            conf=0.9,
            evidence="chemin d'acquisition",
        ),
    )
)


class TestFrozenCatalog:
    def test_catalog_is_version_v1_with_11_templates_and_a_coverage_grid(self) -> None:
        catalog = load_catalog()
        assert catalog.version == "v1"
        assert len(catalog.templates) == 11
        assert len(catalog.coverage_grid) == 7
        assert CATALOG_PATH.name == "elicitation_catalog.v1.json"

    def test_every_template_kind_belongs_to_its_family(self) -> None:
        """The closed-set guard of spec §12.1 holds for the whole catalogue."""
        catalog = load_catalog()
        for template in catalog.templates:
            assert template.kind in FAMILY_PATTERNS[template.family]

    def test_catalog_rejects_a_kind_outside_its_family(self, tmp_path: Path) -> None:
        payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        payload["templates"][0]["kind"] = "deadlock"
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(ValueError, match="outside family"):
            load_catalog(path=bad)


class TestInstantiationOnPhilosophes3:
    def test_eleven_templates_instantiate_with_stable_ids_and_formulas(self) -> None:
        catalog = load_catalog()
        model = build_philosophes3()
        questions = instantiate_questions(
            catalog=catalog, profile=PHILOSOPHES3_PROFILE, model=model
        )
        assert len(questions) == 11
        assert len({q.question_id for q in questions}) == 11
        for question in questions:
            assert question.question_id.startswith("q_")
            assert question.formula
            assert question.kind in FAMILY_PATTERNS[question.family]
        statique = [q for q in questions if q.triage == "statique"]
        mc = [q for q in questions if q.triage == "MC"]
        assert len(statique) == 6
        assert len(mc) == 5

    def test_instantiation_is_deterministic(self) -> None:
        catalog = load_catalog()
        model = build_philosophes3()
        first = instantiate_questions(catalog=catalog, profile=PHILOSOPHES3_PROFILE, model=model)
        second = instantiate_questions(catalog=catalog, profile=PHILOSOPHES3_PROFILE, model=model)
        assert [q.question_id for q in first] == [q.question_id for q in second]
        assert [q.formula for q in first] == [q.formula for q in second]

    def test_formula_placeholders_are_filled_from_the_entry_elements(self) -> None:
        catalog = load_catalog()
        model = build_philosophes3()
        questions = instantiate_questions(
            catalog=catalog, profile=PHILOSOPHES3_PROFILE, model=model
        )
        by_template = {q.template_id: q for q in questions}
        assert by_template["tpl_res_shared_more"].formula == "AG (fork_1 <= 1)"
        assert by_template["tpl_res_shared_aswell"].formula == "AG (fork_1 + fork_2 + fork_3 == 3)"
        assert by_template["tpl_global_no"].formula == "EF DEADLOCK"

    def test_question_id_is_a_content_hash(self) -> None:
        identifier = question_id(
            template_id="tpl_global_no", group_id="motif", formula="EF DEADLOCK"
        )
        assert identifier == question_id(
            template_id="tpl_global_no", group_id="motif", formula="EF DEADLOCK"
        )
        assert identifier != question_id(
            template_id="tpl_global_no", group_id="motif", formula="EF (eat_1 > 0)"
        )


class TestSolicitationOrder:
    def test_uncovered_groups_are_targeted_first(self) -> None:
        catalog = load_catalog()
        model = build_philosophes3()
        questions = instantiate_questions(
            catalog=catalog, profile=PHILOSOPHES3_PROFILE, model=model
        )
        ordered = order_questions(questions=questions, uncovered_groups={"grp_repas"})
        assert ordered[0].covers == ("grp_repas",)
        assert ordered[0].template_id == "tpl_critsec_no"

    def test_then_risk_descending_and_cost_ascending(self) -> None:
        catalog = load_catalog()
        model = build_philosophes3()
        questions = instantiate_questions(
            catalog=catalog, profile=PHILOSOPHES3_PROFILE, model=model
        )
        ordered = order_questions(questions=questions, uncovered_groups=set())
        keys = [(-q.risk, q.cost, q.question_id) for q in ordered]
        assert keys == sorted(keys)

    def test_catalog_model_roundtrip(self) -> None:
        catalog = load_catalog()
        assert ElicitationCatalog.model_validate(catalog.model_dump(mode="json")) == catalog
