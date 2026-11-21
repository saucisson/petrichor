"""The real Mistral LLM provider, beside the deterministic mock (spec §8, §15; issue #36).

``MistralProvider`` implements the unique `LLMProvider` interface over the
Mistral chat API. P73 (annexe B of the 2026-11-20/21 directive): every call
sends TWO messages — a system prompt per usage, verbatim from annexe B §B.3,
in English and free of verdict vocabulary (guard test) — and a canonical
JSON payload ``{"digest": ..., "data": ..., "language": ...}`` carrying the
bounded canonical digest of the net (``petrichor.llm.digest.net_digest``),
the usage data and the session language. Output is strict ``json_schema``
derived from the usage's pydantic schema, validated, with a bounded
re-request on failure (``MAX_LLM_ATTEMPTS``) and then an explicit
degradation — the provider never invents an answer and never emits a
verdict (spec §19.4).

The real provider's `semantic-profile` schema excludes the mock-only
``wrong`` field (annexe B §B.5): an answer carrying it fails validation.

P74 (ADR-0108): the model is routed per usage from a pinned, versioned
routing table (``petrichor.llm.routing.USAGE_MODEL_ROUTING``) — every
version is exact, ``-latest`` aliases are rejected explicitly (replay P46).

Token usage is accumulated per (usage, model) couple and exposed via
``usage_totals``: the LLM cost is displayed apart from the tool-time
budget, which counts only tool seconds (spec §12.4).
"""

import json
from typing import TYPE_CHECKING, TypeVar

import httpx
import structlog
from pydantic import BaseModel, SecretStr, ValidationError

from petrichor.llm.digest import AcceptedRole, net_digest
from petrichor.llm.provider import (
    MAX_LLM_ATTEMPTS,
    FormulaText,
    HumanMessage,
    LLMDegradationError,
    LLMError,
    LLMProvider,
)
from petrichor.llm.routing import (
    USAGE_MODEL_ROUTING,
    ModelRoute,
    route_for,
    validate_routing,
)
from petrichor.llm.schemas import (
    Clarification,
    Cluster,
    ClusterList,
    FormulaReadings,
    LlmUsage,
    NarrationCorpus,
    SemanticProfilePublic,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from petrichor.petri_canonical import CanonicalModel, JsonValue

logger = structlog.get_logger(__name__)

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"

DEFAULT_SESSION_LANGUAGE = "fr"
"""The default session language of the payloads (annexe B §B.1: ``language: "fr"``)."""

MAX_TOKENS_PER_USAGE: dict[str, int] = {
    "semantic-profile": 2000,
    "reading": 6000,
    "clarification": 500,
    "cluster": 2000,
    "narration": 3000,
}
"""The output token budget per usage (annexe B, § B.4 of the 2026-11-20/21 directive)."""

OutputType = TypeVar("OutputType", bound=BaseModel)

PROMPT_TEMPLATES: dict[str, str] = {
    "semantic-profile": (
        "You are a Petri net reading assistant. You receive a canonical digest "
        "of a Petri net: places, transitions, incidence, groupings, layout. For "
        "each meaningful place group, propose a role hypothesis and a confidence "
        "between 0 and 1. When unsure, list plausible alternative roles in "
        '"options". Cite evidence from the given data using element ids or '
        'names verbatim. Answer in the language given by "language" as a single '
        "JSON object matching the provided schema. Write no text outside the "
        'JSON. Never use the words "verified", "holds", "violated", "proved", '
        '"true", "false", "correct" or "incorrect". Base every statement only '
        "on the given data."
    ),
    "reading": (
        "You are a formal-methods tutor. You receive a frozen grid of "
        "verification questions about a Petri net, a digest of the places "
        "involved, the accepted semantic roles of those places, and static "
        'relations between questions. For each question: "reading" states what '
        "the formula says about the system, naming places through their "
        'accepted roles; "not_says" states the closest stronger reading the '
        'formula does NOT entail; "pattern" restates the family given in the '
        'grid; "cross" states the given static relations to other questions, '
        "citing question ids verbatim. Restate relations only as given: never "
        "invent a relation, a verdict or a question status. Answer in the "
        'language given by "language" as a single JSON object matching the '
        "provided schema. Write no text outside the JSON. Never use the words "
        '"verified", "holds", "violated", "proved", "true", "false", "correct" '
        'or "incorrect". Cite element ids and names verbatim. Base every '
        "statement only on the given data."
    ),
    "clarification": (
        "You are a clarification assistant in a Petri net elicitation session. "
        "You receive one raw human proposal about a question, the current "
        "question (id, formula, reading), the accepted roles of the places "
        'mentioned, and a bounded history. "reform" rephrases the proposal as a '
        "check-style statement of intent that keeps the ambiguity visible; it "
        'never resolves the ambiguity. "options" contains at most 4 short chips '
        "of 5 to 8 words each, one per distinct plausible reading of the "
        "proposal. Never choose for the engineer. Answer in the language given "
        'by "language" as a single JSON object matching the provided schema. '
        "Write no text outside the JSON. Never use the words "
        '"verified", "holds", "violated", "proved", "true", "false", "correct" or '
        '"incorrect". Cite element ids and names verbatim. Base every statement '
        "only on the given data."
    ),
    "cluster": (
        "You are a verification triage assistant. You receive the set of "
        "violated clauses from a verification pass, each with per-tool verdicts "
        "and techniques, a concordance status, a short witness summary and the "
        "semantic roles involved. Group the clauses by their most probable "
        'cause: "model" or "specification". "fix" proposes a remediation '
        'direction, never an automated change. "members" lists clause ids. You '
        "may restate the given per-tool verdicts as reported facts, but never "
        "produce a new verdict of your own. Answer in the language given by "
        '"language" as a single JSON object matching the provided schema. '
        "Write no text outside the JSON. Never use the words "
        '"verified", "holds", "violated", "proved", "true", "false", "correct" or '
        '"incorrect". Cite element ids and names verbatim. Base every statement '
        "only on the given data."
    ),
    "narration": (
        "You are a Petri net trace narrator. You receive the complete fired "
        "trace of a witness, the violated clause as context, and a reported "
        "cyclicity/back-edge signal. Produce exactly one sentence per fired "
        "transition: what the firing does to the marking, naming places through "
        "their accepted semantic roles, with the exact element id in "
        "parentheses; steps that close a cycle are noted as such. Describe the "
        "step, never its desirability. Answer in the language given by "
        '"language" as a single JSON object matching the provided schema. '
        "Write no text outside the JSON. Never use the words "
        '"verified", "holds", "violated", "proved", "true", "false", "correct" or '
        '"incorrect". Base every statement only on the given data.'
    ),
}
"""The system prompts per usage, verbatim from annexe B §B.3 (P73).

They carry no verdict vocabulary outside their own guard sentence —
pinned by the guard test (spec §19.4).
"""


class MistralProvider(LLMProvider):
    """The real provider over the Mistral chat API, with bounded retries and usage tracking."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        http_client: httpx.Client | None = None,
        api_url: str = MISTRAL_API_URL,
        language: str = DEFAULT_SESSION_LANGUAGE,
        routing: Mapping[LlmUsage, ModelRoute] | None = None,
    ) -> None:
        """Bind the provider to a key, an HTTP client, a session language and a routing.

        P74 (ADR-0108): the model is routed per usage from the pinned,
        versioned routing table; any ``-latest`` alias in the configuration
        is rejected explicitly (guard test, replay P46).
        """
        self._api_key = api_key
        self._routing: dict[LlmUsage, ModelRoute] = dict(routing or USAGE_MODEL_ROUTING)
        validate_routing(self._routing)
        self._api_url = api_url
        self._language = language
        self._client = http_client or httpx.Client(timeout=60.0)
        self._owns_client = http_client is None
        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._by_usage_model: dict[tuple[str, str], dict[str, int]] = {}
        self._archive: list[dict[str, object]] = []

    def routing_fingerprint(self) -> dict[str, str]:
        """The exact pinned version per usage (replay P46, P74/ADR-0108)."""
        return {usage: route.version for usage, route in sorted(self._routing.items())}

    def archived_calls(self) -> tuple[dict[str, object], ...]:
        """Archived request/response pairs with their (model, version) fingerprint (P72, P46)."""
        return tuple(self._archive)

    def usage_totals(self) -> dict[str, object]:
        """The accumulated LLM cost per (usage, model) couple (spec §12.4, P72)."""
        return {
            "provider": "mistral",
            "routing": self.routing_fingerprint(),
            "calls": self._calls,
            "by_usage_model": {
                f"{usage}|{model}": dict(stats)
                for (usage, model), stats in sorted(self._by_usage_model.items())
            },
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._total_tokens,
        }

    def _complete(
        self,
        *,
        usage: LlmUsage,
        digest: dict[str, JsonValue],
        data: dict[str, JsonValue],
        output_type: type[OutputType],
        route: ModelRoute | None = None,
    ) -> OutputType:
        """One chat completion (two messages) with bounded re-request and schema validation.

        P73 (annexe B §B.1): message 1 is the system prompt of the usage,
        verbatim; message 2 is the canonical JSON payload
        ``{"digest": ..., "data": ..., "language": ...}``.
        P74 (ADR-0108): the model of the request is the pinned route of the
        usage, and the archive carries its exact (model, version).
        """
        route = route or route_for(self._routing, usage=usage)
        prompt = PROMPT_TEMPLATES[usage]
        user_content = json.dumps(
            {"digest": digest, "data": data, "language": self._language},
            ensure_ascii=False,
            sort_keys=True,
        )
        last_error: Exception | None = None
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            try:
                request_body = {
                    "model": route.model,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_content},
                    ],
                    # P72 (annexe B §B.1): strict json_schema derived from the
                    # pydantic schema of the usage, not a bare json_object.
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": usage,
                            "schema": output_type.model_json_schema(),
                            "strict": True,
                        },
                    },
                    "temperature": 0,
                    "max_tokens": MAX_TOKENS_PER_USAGE[usage],
                }
                response = self._client.post(
                    self._api_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json=request_body,
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                usage_payload = payload.get("usage", {})
                self._calls += 1
                prompt_tokens = int(usage_payload.get("prompt_tokens", 0))
                completion_tokens = int(usage_payload.get("completion_tokens", 0))
                total_tokens = int(usage_payload.get("total_tokens", 0))
                self._prompt_tokens += prompt_tokens
                self._completion_tokens += completion_tokens
                self._total_tokens += total_tokens
                couple = self._by_usage_model.setdefault(
                    (usage, route.model), {"calls": 0, "total_tokens": 0}
                )
                couple["calls"] += 1
                couple["total_tokens"] += total_tokens
                # P72: archive the request/response with the (model, version)
                # fingerprint for the replay of P46.
                self._archive.append(
                    {
                        "usage": usage,
                        "model": route.model,
                        "version": route.version,
                        "request": request_body,
                        "response": payload,
                    }
                )
                logger.debug(
                    "llm_mistral_call_archived",
                    usage=usage,
                    model=route.model,
                    version=route.version,
                    attempt=attempt,
                    total_tokens=total_tokens,
                )
                return output_type.model_validate(json.loads(content))
            except (
                httpx.HTTPError,
                KeyError,
                TypeError,
                json.JSONDecodeError,
                ValidationError,
            ) as error:
                last_error = error
                logger.warning(
                    "llm_mistral_call_failed",
                    usage=usage,
                    attempt=attempt,
                    reason=repr(error),
                )
                continue
        logger.error("llm_degraded", provider="mistral", usage=usage, attempts=MAX_LLM_ATTEMPTS)
        raise LLMDegradationError(
            f"Mistral provider: no valid answer for usage {usage!r}"
            f" after {MAX_LLM_ATTEMPTS} attempts."
        ) from last_error

    def semantic_profile(self, *, model: CanonicalModel) -> SemanticProfilePublic:
        """Extract the semantic profile of a model via Mistral (digest `profile`)."""
        digest = net_digest(model, "profile")
        return self._complete(
            usage="semantic-profile",
            digest=digest,
            data={},
            output_type=SemanticProfilePublic,
        )

    def reading(
        self,
        *,
        model: CanonicalModel,
        formulas: tuple[FormulaText, ...],
        roles: dict[str, AcceptedRole] | None = None,
        relations: tuple[str, ...] = (),
    ) -> FormulaReadings:
        """Read the whole question grid in ONE call (P72) with the `places-only` digest (P73).

        The `cross` field only restates the static relations given in
        ``relations``; the document may only answer the requested grid —
        any extra key is an invention and degrades explicitly.
        """
        formula_texts = tuple(str(formula) for formula in formulas)
        digest = net_digest(model, "places-only", formulas=formula_texts, roles=roles or {})
        data: dict[str, JsonValue] = {
            "grid": [{"formula": formula} for formula in formula_texts],
            "relations": list(relations),
        }
        document = self._complete(
            usage="reading", digest=digest, data=data, output_type=FormulaReadings
        )
        invented = set(document.readings) - set(formula_texts)
        if invented:
            raise LLMDegradationError(
                f"Reading document answers formulas absent from the grid: {sorted(invented)}."
            )
        return document

    def clarification(self, *, model: CanonicalModel, text: HumanMessage) -> Clarification:
        """Reformulate a human proposal via Mistral (one call per proposal, `places-only`)."""
        digest = net_digest(model, "places-only", formulas=(str(text),))
        data: dict[str, JsonValue] = {"proposal": str(text)}
        return self._complete(
            usage="clarification", digest=digest, data=data, output_type=Clarification
        )

    def cluster(self, *, model: CanonicalModel) -> tuple[Cluster, ...]:
        """Cluster the violations of a model via Mistral (digest `profile`)."""
        digest = net_digest(model, "profile")
        document = self._complete(usage="cluster", digest=digest, data={}, output_type=ClusterList)
        return document.clusters

    def narrate(self, *, model: CanonicalModel, transitions: tuple[str, ...]) -> NarrationCorpus:
        """Narrate the COMPLETE witness in ONE call (P72, P32) with the `trace` digest (P73).

        The corpus may only cover the requested transitions — any extra key
        is an invention and degrades explicitly.
        """
        digest = net_digest(model, "trace", trace=transitions)
        data: dict[str, JsonValue] = {"transitions": list(transitions)}
        route = route_for(self._routing, usage="narration")
        try:
            corpus = self._complete(
                usage="narration",
                digest=digest,
                data=data,
                output_type=NarrationCorpus,
                route=route,
            )
        except LLMDegradationError:
            if route.fallback is None:
                raise
            logger.warning(
                "llm_narration_fallback",
                primary=route.model,
                fallback=route.fallback.model,
            )
            corpus = self._complete(
                usage="narration",
                digest=digest,
                data=data,
                output_type=NarrationCorpus,
                route=route.fallback,
            )
        invented = set(corpus.narrations) - set(transitions)
        if invented:
            raise LLMDegradationError(
                f"Narration corpus covers transitions absent from the trace: {sorted(invented)}."
            )
        return corpus

    def close(self) -> None:
        """Close the owned HTTP client, if any."""
        if self._owns_client:
            self._client.close()


__all__ = [
    "DEFAULT_SESSION_LANGUAGE",
    "MAX_TOKENS_PER_USAGE",
    "MISTRAL_API_URL",
    "PROMPT_TEMPLATES",
    "LLMError",
    "MistralProvider",
]
