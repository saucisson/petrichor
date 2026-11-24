"""Deterministic session execution over an LLM provider (spec §15, §19.4).

A session is an ordered list of LLM requests; running it produces a
journal — the canonical JSON serialization of every output, in order.
With the deterministic mock, two executions of the same session produce
an identical journal, which the tests pin (spec §19.4).
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from petrichor.llm.provider import (
    FormulaText,
    HumanMessage,
    LLMDegradationError,
    LLMError,
    LLMProvider,
)
from petrichor.petri_canonical import CanonicalModel, canonical_json

if TYPE_CHECKING:
    from petrichor.llm.schemas import LlmUsage
    from petrichor.petri_canonical import JsonValue

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SessionRequest:
    """One LLM request of a session: a usage and its parameters."""

    usage: LlmUsage
    formula: FormulaText | None = None
    text: HumanMessage | None = None


def run_session(
    *, provider: LLMProvider, model: CanonicalModel, requests: tuple[SessionRequest, ...]
) -> tuple[str, ...]:
    """Execute a session and return its journal: one canonical JSON entry per request."""
    journal: list[str] = []
    for index, request in enumerate(requests):
        payload = _dispatch(provider=provider, model=model, request=request)
        journal.append(canonical_json(payload=payload))
        logger.debug(
            "llm_session_step", index=index, usage=request.usage, model_hash=model.content_hash
        )
    logger.debug("llm_session_completed", steps=len(journal), model_hash=model.content_hash)
    return tuple(journal)


def _dispatch_reading(
    *, provider: LLMProvider, model: CanonicalModel, formula: FormulaText
) -> JsonValue:
    """One document call for the reading; the journal entry stays the single reading (§19.4).

    A formula absent from the served document degrades explicitly - never an
    invented reading.
    """
    document = provider.reading(model=model, formulas=(formula,))
    try:
        return document.readings[formula].model_dump(mode="json")
    except KeyError:
        raise LLMDegradationError(
            f"No reading for formula {formula!r} in the served document."
        ) from None


def _dispatch_narration(
    *, provider: LLMProvider, model: CanonicalModel, transition: str
) -> JsonValue:
    """One document call for the witness; the journal entry stays the pair (§19.4)."""
    corpus = provider.narrate(model=model, transitions=(transition,))
    try:
        narration = corpus.narrations[transition]
    except KeyError:
        raise LLMDegradationError(
            f"No narration for transition {transition!r} in the served document."
        ) from None
    return {"transition": transition, "narration": narration}


def _dispatch(
    *, provider: LLMProvider, model: CanonicalModel, request: SessionRequest
) -> JsonValue:
    """Dispatch one request to the provider and return its JSON-serializable output."""
    match request.usage:
        case "semantic-profile":
            return provider.semantic_profile(model=model).model_dump(mode="json")
        case "reading":
            if request.formula is None:
                raise LLMError("A reading request requires a formula.")
            # P72: the provider reads the document (one call).
            return _dispatch_reading(provider=provider, model=model, formula=request.formula)
        case "clarification":
            if request.text is None:
                raise LLMError("A clarification request requires a text.")
            return provider.clarification(model=model, text=request.text).model_dump(mode="json")
        case "cluster":
            clusters = provider.cluster(model=model)
            return [cluster.model_dump(mode="json") for cluster in clusters]
        case "narration":
            if request.text is None:
                raise LLMError("A narration request requires a transition text.")
            # P72: one document call for the witness.
            return _dispatch_narration(provider=provider, model=model, transition=str(request.text))
