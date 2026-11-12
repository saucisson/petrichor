"""Unit tests for certified reductions and witness replay (spec §18.2, issue #24)."""

import pytest
import structlog.testing
from philosophes3 import build_philosophes3
from pydantic import ValidationError

from petrichor.engines.contract import Witness, WitnessStep
from petrichor.engines.reduction import (
    TOKEN_ELIMINATION_RULE_NAME,
    ElementMap,
    ReductionCertificate,
    ReductionError,
    UncertifiedReductionError,
    WitnessReplayError,
    reduce_marking_token_elimination,
    reformat_witness_for_original,
    replay_witness_on_original,
)
from petrichor.petri_canonical import ElementName


class TestTokenElimination:
    """Token Elimination reduces the marking only; the net stays intact."""

    def test_reduces_marking_and_keeps_structure(self) -> None:
        model = build_philosophes3()
        reduced = reduce_marking_token_elimination(
            model=model,
            reduced_marking={ElementName("think_1"): 0, ElementName("fork_1"): 0},
        )
        assert reduced.parent_id == model.id
        assert reduced.model.parent == model.id
        # Net structure intact: same places, transitions, arcs
        assert len(reduced.model.places) == len(model.places)
        assert len(reduced.model.transitions) == len(model.transitions)
        assert len(reduced.model.arcs) == len(model.arcs)
        # Marking reduced on the two targeted places
        place_by_name = {place.name: place for place in reduced.model.places}
        assert place_by_name[ElementName("think_1")].initial_marking == 0
        assert place_by_name[ElementName("fork_1")].initial_marking == 0
        # Untouched places keep their marking
        assert place_by_name[ElementName("think_2")].initial_marking == 1

    def test_element_map_is_identity(self) -> None:
        model = build_philosophes3()
        reduced = reduce_marking_token_elimination(
            model=model,
            reduced_marking={ElementName("think_1"): 0},
        )
        element_map = reduced.certificate.element_map
        for place in model.places:
            assert element_map.to_original(name=place.name) == place.name
        for transition in model.transitions:
            assert element_map.to_original(name=transition.name) == transition.name

    def test_certificate_exposes_rules_and_saved_cost(self) -> None:
        model = build_philosophes3()
        reduced = reduce_marking_token_elimination(
            model=model,
            reduced_marking={ElementName("think_1"): 0, ElementName("think_2"): 0},
        )
        certificate = reduced.certificate
        assert certificate.technique == TOKEN_ELIMINATION_RULE_NAME
        assert len(certificate.rules) == 1
        assert certificate.rules[0].name == TOKEN_ELIMINATION_RULE_NAME
        assert certificate.rules[0].elements_affected == 2
        # Original total marking = 6 (3 think + 3 fork); reduced = 4
        assert certificate.saved_states == 2

    def test_rejects_unknown_place(self) -> None:
        model = build_philosophes3()
        with pytest.raises(UncertifiedReductionError, match="unknown place"):
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("nonexistent"): 0},
            )

    def test_rejects_negative_marking(self) -> None:
        model = build_philosophes3()
        with pytest.raises(UncertifiedReductionError, match="negative marking"):
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("think_1"): -1},
            )

    def test_rejects_marking_above_original(self) -> None:
        model = build_philosophes3()
        with pytest.raises(UncertifiedReductionError, match="exceeds original"):
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("think_1"): 3},
            )

    def test_rejection_warning_events_pinned(self) -> None:
        model = build_philosophes3()
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UncertifiedReductionError),
        ):
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("ghost"): 0},
            )
        event_names = [entry["event"] for entry in logs]
        assert "reduction_rejected_unknown_place" in event_names

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UncertifiedReductionError),
        ):
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("think_1"): -1},
            )
        event_names = [entry["event"] for entry in logs]
        assert "reduction_rejected_negative_marking" in event_names

        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(UncertifiedReductionError),
        ):
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("think_1"): 5},
            )
        event_names = [entry["event"] for entry in logs]
        assert "reduction_rejected_marking_increase" in event_names

    def test_empty_reduction_is_allowed(self) -> None:
        model = build_philosophes3()
        reduced = reduce_marking_token_elimination(model=model, reduced_marking={})
        assert reduced.certificate.saved_states == 0
        assert reduced.certificate.rules[0].elements_affected == 0

    def test_derived_model_has_reduction_derivation(self) -> None:
        model = build_philosophes3()
        reduced = reduce_marking_token_elimination(
            model=model,
            reduced_marking={ElementName("think_1"): 0},
        )
        derivations = list(reduced.model.derivations)
        assert len(derivations) == 1
        derivation = derivations[0]
        assert isinstance(derivation, dict)
        assert derivation["kind"] == "reduction"
        assert derivation["rule"] == TOKEN_ELIMINATION_RULE_NAME

    def test_narration_events_pinned(self) -> None:
        model = build_philosophes3()
        with structlog.testing.capture_logs() as logs:
            reduce_marking_token_elimination(
                model=model,
                reduced_marking={ElementName("think_1"): 0},
            )
        event_names = [entry["event"] for entry in logs]
        assert "reduction_token_elimination_applied" in event_names
        applied = next(
            entry for entry in logs if entry["event"] == "reduction_token_elimination_applied"
        )
        assert applied["parent_id"] == model.id
        assert applied["saved_states"] == 1


class TestWitnessReplay:
    """A witness from the reduced net is replayed and validated on the original."""

    def _sample_witness(self) -> Witness:
        return Witness(
            kind="counterexample",
            cyclic=True,
            back_edge=1,
            steps=[
                WitnessStep(index=0, marking=["think_1", "think_2", "fork_1"], fired=None),
                WitnessStep(index=1, marking=["hasL_1", "think_2", "fork_2"], fired="takeL_1"),
            ],
        )

    def test_replay_valid_witness_returns_true(self) -> None:
        model = build_philosophes3()
        reduced = reduce_marking_token_elimination(
            model=model,
            reduced_marking={ElementName("think_1"): 0},
        )
        witness = self._sample_witness()
        assert (
            replay_witness_on_original(
                witness=witness,
                original_model=model,
                element_map=reduced.certificate.element_map,
            )
            is True
        )

    def test_replay_rejects_unmapped_place(self) -> None:
        model = build_philosophes3()
        element_map = ElementMap(mapping={})
        witness = Witness(
            kind="counterexample",
            cyclic=False,
            back_edge=None,
            steps=[WitnessStep(index=0, marking=["ghost_place"], fired=None)],
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(WitnessReplayError, match="unmapped place"),
        ):
            replay_witness_on_original(
                witness=witness,
                original_model=model,
                element_map=element_map,
            )
        event_names = [entry["event"] for entry in logs]
        assert "witness_replay_failed_unmapped_place" in event_names

    def test_replay_rejects_unmapped_transition(self) -> None:
        model = build_philosophes3()
        element_map = ElementMap(mapping={ElementName("think_1"): ElementName("think_1")})
        witness = Witness(
            kind="counterexample",
            cyclic=False,
            back_edge=None,
            steps=[WitnessStep(index=0, marking=["think_1"], fired="ghost_transition")],
        )
        with (
            structlog.testing.capture_logs() as logs,
            pytest.raises(WitnessReplayError, match="unmapped transition"),
        ):
            replay_witness_on_original(
                witness=witness,
                original_model=model,
                element_map=element_map,
            )
        event_names = [entry["event"] for entry in logs]
        assert "witness_replay_failed_unmapped_transition" in event_names

    def test_reformat_witness_maps_names(self) -> None:
        element_map = ElementMap(
            mapping={
                ElementName("reduced_think"): ElementName("think_1"),
                ElementName("reduced_take"): ElementName("takeL_1"),
            }
        )
        witness = Witness(
            kind="trace",
            cyclic=False,
            back_edge=None,
            steps=[
                WitnessStep(index=0, marking=["reduced_think"], fired=None),
                WitnessStep(index=1, marking=["reduced_think"], fired="reduced_take"),
            ],
        )
        reformatted = reformat_witness_for_original(witness=witness, element_map=element_map)
        assert reformatted.steps[0].marking == ["think_1"]
        assert reformatted.steps[1].fired == "takeL_1"

    def test_replay_narration_pinned(self) -> None:
        model = build_philosophes3()
        element_map = ElementMap(mapping={ElementName("think_1"): ElementName("think_1")})
        witness = Witness(
            kind="counterexample",
            cyclic=False,
            back_edge=None,
            steps=[WitnessStep(index=0, marking=["think_1"], fired=None)],
        )
        with structlog.testing.capture_logs() as logs:
            replay_witness_on_original(
                witness=witness,
                original_model=model,
                element_map=element_map,
            )
        event_names = [entry["event"] for entry in logs]
        assert "witness_replay_validated" in event_names


class TestReductionErrorHierarchy:
    """Typed domain errors, causes preserved."""

    def test_uncertified_is_reduction_error(self) -> None:
        assert issubclass(UncertifiedReductionError, ReductionError)

    def test_witness_replay_is_reduction_error(self) -> None:
        assert issubclass(WitnessReplayError, ReductionError)

    def test_certificate_is_frozen(self) -> None:
        certificate = ReductionCertificate(
            rules=(),
            saved_states=0,
            saved_seconds=0.0,
            element_map=ElementMap(mapping={}),
        )
        with pytest.raises(ValidationError):
            # frozen pydantic model: assignment is a deliberate type error to
            # prove the model is immutable (ADR-0006 inline justification)
            certificate.saved_states = 5  # type: ignore[misc]
