"""The real Mistral LLM provider, beside the deterministic mock (spec §8, §15; issue #36).

``MistralProvider`` implements the unique `LLMProvider` interface over the
Mistral chat API: one prompt template per usage, JSON output validated
against the usage's pydantic schema, a bounded re-request on failure
(``MAX_LLM_ATTEMPTS``) and then an explicit degradation - the provider never
invents an answer and never emits a verdict (spec §19.4: the prompt
templates carry no verdict vocabulary, pinned by a guard test).

Token usage is accumulated per call and exposed via ``usage_totals``: the
LLM cost is displayed apart from the tool-time budget, which counts only
tool seconds (spec §12.4).
"""

import json
from typing import TYPE_CHECKING, TypeVar

import httpx
import structlog
from pydantic import BaseModel, SecretStr, ValidationError

from petrichor.llm.provider import (
    MAX_LLM_ATTEMPTS,
    FormulaText,
    HumanMessage,
    LLMDegradationError,
    LLMError,
    LLMProvider,
)
from petrichor.llm.schemas import (
    Clarification,
    Cluster,
    ClusterList,
    FormulaReadings,
    NarrationCorpus,
    Reading,
    SemanticProfile,
)

if TYPE_CHECKING:
    from petrichor.petri_canonical import ContentHash

logger = structlog.get_logger(__name__)

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
DEFAULT_MISTRAL_MODEL = "mistral-small-latest"
"""The default Mistral model of the real provider (configurable, spec §8)."""

OutputType = TypeVar("OutputType", bound=BaseModel)

PROMPT_TEMPLATES: dict[str, str] = {
    "semantic-profile": (
        "You are a Petri-net reading assistant. Given the places and transitions"
        " of a P/T net, propose a semantic role for each group of places."
        " Answer ONLY with a JSON object of this exact shape:"
        ' {"entries": [{"id": "str", "elements": "str", "role": "str",'
        ' "conf": 0.0, "evidence": "str", "options": ["str"], "wrong": false}]}.'
        " elements and evidence are plain strings, never arrays."
        " Describe roles and evidence; never judge whether a property holds."
    ),
    "reading": (
        "You are a formal-methods explainer. Read the given check formula in"
        " plain language and state what it does NOT say."
        " Answer ONLY with a JSON object of this exact shape:"
        ' {"readings": {"<formula>": {"reading": "str", "not_says": "str",'
        ' "pattern": "str", "cross": "str"}}}. Never state a verdict.'
    ),
    "clarification": (
        "You are a reformulation assistant. Reformulate the given human"
        " proposal about a Petri net and propose option chips."
        " Answer ONLY with a JSON object of this exact shape:"
        ' {"reform": "str", "options": ["str"]}. Never state a verdict.'
    ),
    "cluster": (
        "You are a diagnostic assistant. Group the given violations of a Petri"
        " net by cause (model or specification) with a remediation each."
        " Answer ONLY with a JSON object of this exact shape:"
        ' {"clusters": [{"cause": "model|specification", "fix": "str",'
        ' "members": ["str"]}]}. Never state a verdict.'
    ),
    "narration": (
        "You are a step narrator for a witness trace of a Petri net. Describe"
        " in one sentence what the given fired transition does to the marking."
        ' Answer ONLY with a JSON object of this exact shape:'
        ' {"narrations": {"<transition>": "<sentence>"}}.'
        " Describe the step; never state a verdict."
    ),
}
"""The prompt templates per usage; they carry no verdict vocabulary (guard test, §19.4)."""


class MistralProvider(LLMProvider):
    """The real provider over the Mistral chat API, with bounded retries and usage tracking."""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str = DEFAULT_MISTRAL_MODEL,
        http_client: httpx.Client | None = None,
        api_url: str = MISTRAL_API_URL,
    ) -> None:
        """Bind the provider to a key, a model and an HTTP client (injectable for tests)."""
        self._api_key = api_key
        self._model = model
        self._api_url = api_url
        self._client = http_client or httpx.Client(timeout=60.0)
        self._owns_client = http_client is None
        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0

    def usage_totals(self) -> dict[str, object]:
        """The accumulated LLM cost, displayed apart from the tool budget (spec §12.4)."""
        return {
            "provider": "mistral",
            "model": self._model,
            "calls": self._calls,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._total_tokens,
        }

    def _complete(
        self, *, usage: str, user_content: str, output_type: type[OutputType]
    ) -> OutputType:
        """One chat completion with bounded re-request and schema validation."""
        prompt = PROMPT_TEMPLATES[usage]
        last_error: Exception | None = None
        for attempt in range(1, MAX_LLM_ATTEMPTS + 1):
            try:
                response = self._client.post(
                    self._api_url,
                    headers={
                        "Authorization": f"Bearer {self._api_key.get_secret_value()}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": prompt},
                            {"role": "user", "content": user_content},
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                content = payload["choices"][0]["message"]["content"]
                usage_payload = payload.get("usage", {})
                self._calls += 1
                self._prompt_tokens += int(usage_payload.get("prompt_tokens", 0))
                self._completion_tokens += int(usage_payload.get("completion_tokens", 0))
                self._total_tokens += int(usage_payload.get("total_tokens", 0))
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

    def semantic_profile(self, *, model_hash: ContentHash) -> SemanticProfile:
        """Extract the semantic profile of a model via Mistral."""
        return self._complete(
            usage="semantic-profile",
            user_content=f"Model content hash: {model_hash}. Propose the roles.",
            output_type=SemanticProfile,
        )

    def reading(self, *, model_hash: ContentHash, formula: FormulaText) -> Reading:
        """Read one formula in natural language via Mistral."""
        document = self._complete(
            usage="reading",
            user_content=f"Model {model_hash}. Formula: {formula}",
            output_type=FormulaReadings,
        )
        if formula not in document.readings:
            raise LLMDegradationError(f"Mistral returned no reading for formula {formula!r}.")
        return document.readings[formula]

    def clarification(self, *, model_hash: ContentHash, text: HumanMessage) -> Clarification:
        """Reformulate a human proposal via Mistral."""
        return self._complete(
            usage="clarification",
            user_content=f"Model {model_hash}. Proposal: {text}",
            output_type=Clarification,
        )

    def cluster(self, *, model_hash: ContentHash) -> tuple[Cluster, ...]:
        """Cluster the violations of a model via Mistral."""
        document = self._complete(
            usage="cluster",
            user_content=f"Model {model_hash}. Cluster its violations.",
            output_type=ClusterList,
        )
        return document.clusters

    def narrate_transition(self, *, model_hash: ContentHash, transition: str) -> str:
        """Narrate one fired transition via Mistral (spec §16, issue #32)."""
        document = self._complete(
            usage="narration",
            user_content=f"Model {model_hash}. Fired transition: {transition}",
            output_type=NarrationCorpus,
        )
        sentence = document.narrations.get(transition)
        if sentence is None:
            raise LLMDegradationError(f"Mistral returned no narration for {transition!r}.")
        return sentence

    def close(self) -> None:
        """Close the owned HTTP client, if any."""
        if self._owns_client:
            self._client.close()


__all__ = [
    "DEFAULT_MISTRAL_MODEL",
    "MISTRAL_API_URL",
    "PROMPT_TEMPLATES",
    "LLMError",
    "MistralProvider",
]
