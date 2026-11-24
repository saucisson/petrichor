"""The LLM layer: typed outputs, the provider interface and the deterministic mock.

Spec §8 (unique ``LLMProvider`` interface) and §15 (four usages, four
typed outputs, golden files ``golden/{model_hash}/{usage}.json``). The
mock serves the Philosophes-3 corpus verbatim and degrades explicitly —
it never invents an answer, and never emits a verdict (spec §8).
"""

from petrichor.llm.digest import (
    DIGEST_TOKEN_CEILING,
    TRACE_MAX_STEPS,
    AcceptedRole,
    DigestError,
    net_digest,
)
from petrichor.llm.provider import (
    DEFAULT_GOLDEN_ROOT,
    MAX_LLM_ATTEMPTS,
    DeterministicMockProvider,
    FormulaText,
    HumanMessage,
    LLMDegradationError,
    LLMError,
    LLMProvider,
    LLMTokenRequiredError,
    MistralToken,
    MistralTokenInvalidError,
)
from petrichor.llm.schemas import (
    Clarification,
    Cluster,
    ClusterCause,
    ClusterList,
    FormulaReadings,
    LlmUsage,
    ProfileEntry,
    ProfileEntryPublic,
    Reading,
    SemanticProfile,
    SemanticProfilePublic,
)
from petrichor.llm.session import SessionRequest, run_session

__all__ = [
    "DEFAULT_GOLDEN_ROOT",
    "DIGEST_TOKEN_CEILING",
    "MAX_LLM_ATTEMPTS",
    "TRACE_MAX_STEPS",
    "AcceptedRole",
    "Clarification",
    "Cluster",
    "ClusterCause",
    "ClusterList",
    "DeterministicMockProvider",
    "DigestError",
    "FormulaReadings",
    "FormulaText",
    "HumanMessage",
    "LLMDegradationError",
    "LLMError",
    "LLMProvider",
    "LLMTokenRequiredError",
    "LlmUsage",
    "MistralToken",
    "MistralTokenInvalidError",
    "ProfileEntry",
    "ProfileEntryPublic",
    "Reading",
    "SemanticProfile",
    "SemanticProfilePublic",
    "SessionRequest",
    "net_digest",
    "run_session",
]
