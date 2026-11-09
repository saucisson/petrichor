"""Deterministic session execution over an LLM provider (spec §15, §19.4).

A session is an ordered list of LLM requests; running it produces a
journal — the canonical JSON serialization of every output, in order.
With the deterministic mock, two executions of the same session produce
an identical journal, which the tests pin (spec §19.4).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from petrichor.llm.provider import FormulaText, HumanMessage, LLMError, LLMProvider
from petrichor.petri_canonical import canonical_json

if TYPE_CHECKING:
    from petrichor.llm.schemas import LlmUsage
    from petrichor.petri_canonical import ContentHash, JsonValue

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SessionRequest:
    """One LLM request of a session: a usage and its parameters."""

    usage: LlmUsage
    formula: FormulaText | None = None
    text: HumanMessage | None = None


def run_session(
    *, provider: LLMProvider, model_hash: ContentHash, requests: tuple[SessionRequest, ...]
) -> tuple[str, ...]:
    """Execute a session and return its journal: one canonical JSON entry per request."""
    journal: list[str] = []
    for index, request in enumerate(requests):
        payload = _dispatch(provider=provider, model_hash=model_hash, request=request)
        journal.append(canonical_json(payload=payload))
        logger.debug("llm_session_step", index=index, usage=request.usage, model_hash=model_hash)
    logger.debug("llm_session_completed", steps=len(journal), model_hash=model_hash)
    return tuple(journal)


def _dispatch(
    *, provider: LLMProvider, model_hash: ContentHash, request: SessionRequest
) -> JsonValue:
    """Dispatch one request to the provider and return its JSON-serializable output."""
    match request.usage:
        case "semantic-profile":
            return provider.semantic_profile(model_hash=model_hash).model_dump(mode="json")
        case "reading":
            if request.formula is None:
                raise LLMError("A reading request requires a formula.")
            return provider.reading(model_hash=model_hash, formula=request.formula).model_dump(
                mode="json"
            )
        case "clarification":
            if request.text is None:
                raise LLMError("A clarification request requires a text.")
            return provider.clarification(model_hash=model_hash, text=request.text).model_dump(
                mode="json"
            )
        case "cluster":
            clusters = provider.cluster(model_hash=model_hash)
            return [cluster.model_dump(mode="json") for cluster in clusters]
