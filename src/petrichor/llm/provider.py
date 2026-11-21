"""The LLM provider interface and its deterministic mock (spec §8, §15).

:class:`LLMProvider` is the unique interface of the LLM layer (spec §8).
:class:`DeterministicMockProvider` serves the golden files of the
maquette corpus (``golden/{model_hash}/{usage}.json``) verbatim. Every
output is validated against its JSON schema; a failure triggers a
bounded re-request and then an explicit degradation — the mock never
invents an answer (spec §15).
"""

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, NewType, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from petrichor.llm.schemas import (
    Clarification,
    Cluster,
    ClusterList,
    FormulaReadings,
    LlmUsage,
    NarrationCorpus,
    SemanticProfile,
)

if TYPE_CHECKING:
    from petrichor.petri_canonical import ContentHash

logger = structlog.get_logger(__name__)

MAX_LLM_ATTEMPTS = 2
"""Bounded re-request count before explicit degradation (spec §15: 2 tentatives max)."""

DEFAULT_GOLDEN_ROOT = Path(__file__).parent / "golden"
"""The golden corpus shipped with the product, keyed by model content hash."""

OutputType = TypeVar("OutputType", bound=BaseModel)

FormulaText = NewType("FormulaText", str)
"""A catalogue formula, as written in the spec (spec §15)."""

HumanMessage = NewType("HumanMessage", str)
"""A human proposal in natural language (spec §15)."""


class LLMError(Exception):
    """Base class of every domain error raised by the LLM layer."""


class LLMDegradationError(LLMError):
    """Explicit degradation: no valid scripted answer after the bounded re-requests."""


class LLMProvider(ABC):
    """The unique LLM interface (spec §8): four usages, four typed outputs."""

    @abstractmethod
    def semantic_profile(self, *, model_hash: ContentHash) -> SemanticProfile:
        """Extract the semantic profile of a model."""

    @abstractmethod
    def reading(
        self, *, model_hash: ContentHash, formulas: tuple[FormulaText, ...]
    ) -> FormulaReadings:
        """Read the whole frozen question grid in ONE call (P72, spec §15).

        The document is the grid: one HTTP call per document, never one call
        per formula. The returned document maps each formula to its reading.
        """

    @abstractmethod
    def clarification(self, *, model_hash: ContentHash, text: HumanMessage) -> Clarification:
        """Reformulate a human proposal and propose option chips."""

    @abstractmethod
    def cluster(self, *, model_hash: ContentHash) -> tuple[Cluster, ...]:
        """Cluster the violations of a model with their causes and remediations."""

    @abstractmethod
    def narrate(self, *, model_hash: ContentHash, transitions: tuple[str, ...]) -> NarrationCorpus:
        """Narrate the COMPLETE witness in ONE call (P72, spec §16, issue #32).

        The document is the witness: one HTTP call per document, stored once
        (P32), never one call per transition. The returned corpus maps each
        fired transition to its sentence; a transition absent from the
        corpus simply gets no narration - never an invented one.
        """

    @abstractmethod
    def usage_totals(self) -> dict[str, object]:
        """The accumulated LLM cost of the provider, per (usage, model) couple.

        Displayed apart from the tool-time budget, which counts only tool
        seconds (spec §12.4, issue #36; P72: the breakdown is per couple
        (usage, model) so the routing of P74 is observable).
        """


class DeterministicMockProvider(LLMProvider):
    """Serves the golden files verbatim; never invents an answer (spec §15)."""

    def __init__(self, *, golden_root: Path = DEFAULT_GOLDEN_ROOT) -> None:
        """Bind the provider to a golden corpus root (defaults to the shipped corpus)."""
        self._golden_root = golden_root
        self._calls = 0
        self._calls_by_usage: dict[str, int] = {}

    def usage_totals(self) -> dict[str, object]:
        """The mock costs no tokens; the call count is tracked per usage (spec §12.4, P72)."""
        return {
            "provider": "mock",
            "calls": self._calls,
            "by_usage_model": {
                f"{usage}|mock": {"calls": count, "total_tokens": 0}
                for usage, count in sorted(self._calls_by_usage.items())
            },
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def semantic_profile(self, *, model_hash: ContentHash) -> SemanticProfile:
        """Serve the scripted profile of the corpus."""
        return self._load(
            usage="semantic-profile", model_hash=model_hash, output_type=SemanticProfile
        )

    def reading(
        self, *, model_hash: ContentHash, formulas: tuple[FormulaText, ...]
    ) -> FormulaReadings:
        """Serve the scripted reading document of the whole grid (P72: one load per document).

        The document is served verbatim; a formula absent from it gets no
        reading from the caller - the mock never invents one.
        """
        return self._load(usage="reading", model_hash=model_hash, output_type=FormulaReadings)

    def clarification(self, *, model_hash: ContentHash, text: HumanMessage) -> Clarification:
        """Serve the scripted clarification; the initial corpus scripts none."""
        return self._load(usage="clarification", model_hash=model_hash, output_type=Clarification)

    def cluster(self, *, model_hash: ContentHash) -> tuple[Cluster, ...]:
        """Serve the scripted clusters of the corpus."""
        document = self._load(usage="cluster", model_hash=model_hash, output_type=ClusterList)
        return document.clusters

    def narrate(self, *, model_hash: ContentHash, transitions: tuple[str, ...]) -> NarrationCorpus:
        """Serve the scripted narration document of the whole witness (P72: one load per document).

        The corpus is served verbatim; a transition absent from it gets no
        narration from the caller - the mock never invents a sentence.
        """
        return self._load(usage="narration", model_hash=model_hash, output_type=NarrationCorpus)

    def _load(
        self, *, usage: LlmUsage, model_hash: ContentHash, output_type: type[OutputType]
    ) -> OutputType:
        """Load and validate one golden file, with a bounded re-request on failure."""
        self._calls += 1
        self._calls_by_usage[usage] = self._calls_by_usage.get(usage, 0) + 1
        path = self._golden_root / model_hash / f"{usage}.json"
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            try:
                payload: object = json.loads(path.read_text(encoding="utf-8"))
                output = output_type.model_validate(payload)
            except (OSError, json.JSONDecodeError, ValidationError) as error:
                logger.warning(
                    "llm_golden_load_failed",
                    usage=usage,
                    model_hash=model_hash,
                    attempt=attempt,
                    reason=str(error),
                )
                continue
            logger.debug("llm_golden_served", usage=usage, model_hash=model_hash, attempt=attempt)
            return output
        logger.error("llm_degraded", usage=usage, model_hash=model_hash, attempts=MAX_LLM_ATTEMPTS)
        raise LLMDegradationError(
            f"No valid golden file for usage {usage!r} of model {model_hash}"
            f" after {MAX_LLM_ATTEMPTS} attempts."
        )
