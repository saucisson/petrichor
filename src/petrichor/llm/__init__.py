"""The LLM layer: typed outputs, the provider interface and the deterministic mock.

Spec §8 (unique ``LLMProvider`` interface) and §15 (four usages, four
typed outputs, golden files ``golden/{model_hash}/{usage}.json``). The
mock serves the Philosophes-3 corpus verbatim and degrades explicitly —
it never invents an answer, and never emits a verdict (spec §8).
"""

from petrichor.llm.provider import (
    DEFAULT_GOLDEN_ROOT,
    MAX_LLM_ATTEMPTS,
    DeterministicMockProvider,
    FormulaText,
    HumanMessage,
    LLMDegradationError,
    LLMError,
    LLMProvider,
)
from petrichor.llm.schemas import (
    Clarification,
    Cluster,
    ClusterCause,
    ClusterList,
    FormulaReadings,
    LlmUsage,
    ProfileEntry,
    Reading,
    SemanticProfile,
)
from petrichor.llm.session import SessionRequest, run_session

__all__ = [
    "DEFAULT_GOLDEN_ROOT",
    "MAX_LLM_ATTEMPTS",
    "Clarification",
    "Cluster",
    "ClusterCause",
    "ClusterList",
    "DeterministicMockProvider",
    "FormulaReadings",
    "FormulaText",
    "HumanMessage",
    "LLMDegradationError",
    "LLMError",
    "LLMProvider",
    "LlmUsage",
    "ProfileEntry",
    "Reading",
    "SemanticProfile",
    "SessionRequest",
    "run_session",
]
