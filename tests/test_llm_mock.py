"""Tests for the deterministic mock LLM provider and the golden corpus (issue #8, spec §15-16).

The acceptance criteria are pinned directly: the mock serves the
Philosophes-3 corpus (profile with its deliberately false role, the
readings of the 7 catalogue formulas, clusters A/B), every output is
validated by its JSON schema with a bounded re-request then an explicit
degradation, two executions of a session produce an identical journal
(spec §19.4), and log narration is asserted (ADR-0005).
"""

import json
from typing import TYPE_CHECKING

import pytest
import structlog.testing

from petrichor.llm import (
    DEFAULT_GOLDEN_ROOT,
    MAX_LLM_ATTEMPTS,
    DeterministicMockProvider,
    LLMDegradationError,
    LLMError,
    SessionRequest,
    run_session,
)
from petrichor.petri_canonical import ContentHash

if TYPE_CHECKING:
    from pathlib import Path

PHILOSOPHES3_CONTENT_HASH = ContentHash(
    "sha256:48fb522c37d91504f4dc22ead01e8be6269eed234d62ab6b562a0fc38f9fb316"
)
"""Canonical content hash of the Philosophes-3 maquette net (docs/ui-mockup)."""

ARROW = "\u2192"
LOGICAL_OR = "\u2228"
NOT_SIGN = "\u00ac"
LOGICAL_AND = "\u2227"
"""Unicode operators of the catalogue formulas, as escapes to stay lint-clean."""

CATALOGUE_FORMULAS = (
    "EF (eat_1)",
    "AG EF (fork_1 = 1)",
    "AG AF (eat_1)",
    f"AG (eat_1 {ARROW} AF think_1)",
    f"AG (hasL_1 {ARROW} AF (eat_1 {LOGICAL_OR} think_1))",
    "EF DEADLOCK",
    f"AG {NOT_SIGN}(eat_1 {LOGICAL_AND} eat_2)",
)


@pytest.fixture
def provider() -> DeterministicMockProvider:
    return DeterministicMockProvider()


class TestGoldenCorpusBinding:
    def test_golden_directory_is_keyed_by_the_philosophes3_content_hash(self) -> None:
        assert (DEFAULT_GOLDEN_ROOT / PHILOSOPHES3_CONTENT_HASH).is_dir()

    def test_corpus_ships_the_three_scripted_usages(self) -> None:
        corpus = DEFAULT_GOLDEN_ROOT / PHILOSOPHES3_CONTENT_HASH
        assert (corpus / "semantic-profile.json").is_file()
        assert (corpus / "reading.json").is_file()
        assert (corpus / "cluster.json").is_file()


class TestSemanticProfile:
    def test_profile_serves_the_seven_maquette_entries(
        self, provider: DeterministicMockProvider
    ) -> None:
        profile = provider.semantic_profile(model_hash=PHILOSOPHES3_CONTENT_HASH)
        assert len(profile.entries) == 7

    def test_profile_carries_the_deliberately_false_role(
        self, provider: DeterministicMockProvider
    ) -> None:
        profile = provider.semantic_profile(model_hash=PHILOSOPHES3_CONTENT_HASH)
        think = next(entry for entry in profile.entries if entry.id == "think")
        assert think.role == "file d'attente"
        assert think.wrong is True
        assert think.options == ("état d'automate (attente)", "compteur", "ressource")


class TestReadings:
    def test_all_seven_catalogue_formulas_are_scripted(
        self, provider: DeterministicMockProvider
    ) -> None:
        for formula in CATALOGUE_FORMULAS:
            reading = provider.reading(model_hash=PHILOSOPHES3_CONTENT_HASH, formula=formula)
            assert reading.reading
            assert reading.not_says
            assert reading.pattern
            assert reading.cross

    def test_deadlock_reading_matches_the_maquette(
        self, provider: DeterministicMockProvider
    ) -> None:
        reading = provider.reading(model_hash=PHILOSOPHES3_CONTENT_HASH, formula="EF DEADLOCK")
        assert reading.pattern == "atteignabilité d'impasse"
        assert "plus aucune transition n'est activable" in reading.reading

    def test_unknown_formula_degrades_explicitly(self, provider: DeterministicMockProvider) -> None:
        with pytest.raises(LLMDegradationError, match="No scripted reading"):
            provider.reading(model_hash=PHILOSOPHES3_CONTENT_HASH, formula="AG (eat_1)")


class TestClusters:
    def test_clusters_ab_are_served_with_causes_and_members(
        self, provider: DeterministicMockProvider
    ) -> None:
        clusters = provider.cluster(model_hash=PHILOSOPHES3_CONTENT_HASH)
        assert [cluster.cause for cluster in clusters] == ["model", "specification"]
        assert clusters[0].members == ("M1", "M2", "M5")
        assert clusters[1].members == ("M3",)


class TestBoundedRetryAndDegradation:
    def test_missing_golden_file_degrades_after_exactly_two_attempts(self, tmp_path: Path) -> None:
        provider = DeterministicMockProvider(golden_root=tmp_path)
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(LLMDegradationError, match="after 2 attempts"),
        ):
            provider.clarification(model_hash=PHILOSOPHES3_CONTENT_HASH, text="un texte")
        failures = [event for event in events if event["event"] == "llm_golden_load_failed"]
        assert len(failures) == MAX_LLM_ATTEMPTS
        assert any(event["event"] == "llm_degraded" for event in events)

    def test_invalid_golden_file_degrades_after_exactly_two_attempts(self, tmp_path: Path) -> None:
        corpus = tmp_path / PHILOSOPHES3_CONTENT_HASH
        corpus.mkdir(parents=True)
        broken = '{"entries": [{"conf": 42}]}'
        (corpus / "semantic-profile.json").write_text(broken, encoding="utf-8")
        provider = DeterministicMockProvider(golden_root=tmp_path)
        with (
            structlog.testing.capture_logs() as events,
            pytest.raises(LLMDegradationError),
        ):
            provider.semantic_profile(model_hash=PHILOSOPHES3_CONTENT_HASH)
        failures = [event for event in events if event["event"] == "llm_golden_load_failed"]
        assert len(failures) == MAX_LLM_ATTEMPTS

    def test_unknown_model_hash_degrades_explicitly(
        self, provider: DeterministicMockProvider
    ) -> None:
        unknown = ContentHash("sha256:" + "0" * 64)
        with pytest.raises(LLMDegradationError):
            provider.semantic_profile(model_hash=unknown)


class TestSessionDeterminism:
    def test_two_executions_of_a_session_produce_an_identical_journal(
        self, provider: DeterministicMockProvider
    ) -> None:
        requests = (
            SessionRequest(usage="semantic-profile"),
            SessionRequest(usage="reading", formula="EF DEADLOCK"),
            SessionRequest(usage="cluster"),
        )
        first = run_session(
            provider=provider, model_hash=PHILOSOPHES3_CONTENT_HASH, requests=requests
        )
        second = run_session(
            provider=provider, model_hash=PHILOSOPHES3_CONTENT_HASH, requests=requests
        )
        assert first == second
        assert len(first) == 3
        profile_entry = json.loads(first[0])
        assert len(profile_entry["entries"]) == 7

    def test_reading_request_without_formula_is_rejected(
        self, provider: DeterministicMockProvider
    ) -> None:
        with pytest.raises(LLMError, match="requires a formula"):
            run_session(
                provider=provider,
                model_hash=PHILOSOPHES3_CONTENT_HASH,
                requests=(SessionRequest(usage="reading"),),
            )

    def test_clarification_request_without_text_is_rejected(
        self, provider: DeterministicMockProvider
    ) -> None:
        with pytest.raises(LLMError, match="requires a text"):
            run_session(
                provider=provider,
                model_hash=PHILOSOPHES3_CONTENT_HASH,
                requests=(SessionRequest(usage="clarification"),),
            )

    def test_clarification_step_is_served_from_a_scripted_golden(self, tmp_path: Path) -> None:
        corpus = tmp_path / PHILOSOPHES3_CONTENT_HASH
        corpus.mkdir(parents=True)
        scripted = {"reform": "Le système ne se fige jamais.", "options": ["jamais", "parfois"]}
        (corpus / "clarification.json").write_text(json.dumps(scripted), encoding="utf-8")
        provider = DeterministicMockProvider(golden_root=tmp_path)
        journal = run_session(
            provider=provider,
            model_hash=PHILOSOPHES3_CONTENT_HASH,
            requests=(SessionRequest(usage="clarification", text="le système se fige"),),
        )
        assert json.loads(journal[0]) == scripted


class TestLogNarration:
    def test_serving_a_golden_file_narrates_a_debug_event(
        self, provider: DeterministicMockProvider
    ) -> None:
        with structlog.testing.capture_logs() as events:
            provider.cluster(model_hash=PHILOSOPHES3_CONTENT_HASH)
        served = [event for event in events if event["event"] == "llm_golden_served"]
        assert len(served) == 1
        assert served[0]["usage"] == "cluster"
        assert served[0]["model_hash"] == PHILOSOPHES3_CONTENT_HASH
