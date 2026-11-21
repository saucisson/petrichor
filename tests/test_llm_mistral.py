"""Tests for the real Mistral LLM provider and its selection (spec §8, §15, §19.4; issue #36).

- ``MistralProvider`` beside the mock, selected by configuration.
- Every output is validated by its schema; failure → bounded re-request →
  explicit degradation. A verdict-laden output fails validation (guard §19.4).
- The LLM cost (tokens) is exposed apart from the tool-time budget (§12.4).
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from petrichor.llm import FormulaText, schemas
from petrichor.pnml_import import import_pnml

if TYPE_CHECKING:
    from collections.abc import Callable
from petrichor.llm.mistral import PROMPT_TEMPLATES, MistralProvider
from petrichor.llm.provider import LLMDegradationError

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_MODEL = import_pnml(
    text=(FIXTURES / "philosophes3-maquette.pnml").read_text(encoding="utf-8")
)
MODEL_HASH = PHILOSOPHES3_MODEL.content_hash


def mistral_response(content: str, *, total_tokens: int = 42) -> dict:
    """A well-formed Mistral chat-completion payload."""
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 32, "total_tokens": total_tokens},
    }


def fake_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """An httpx client whose POST is served by the handler."""
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestMistralProvider:
    def test_valid_output_is_schema_validated_and_usage_accumulated(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json=mistral_response(
                    json.dumps({"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}})
                ),
            )

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        corpus = provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        assert corpus.narrations["takeL_1"] == "Le philosophe 1 prend la fourchette."
        assert len(requests) == 1
        body = json.loads(requests[0].content.decode("utf-8"))
        assert body["temperature"] == 0
        # P72 (annexe B §B.1): strict json_schema derived from the pydantic schema.
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["name"] == "narration"
        assert body["response_format"]["json_schema"]["strict"] is True
        expected_schema = schemas.NarrationCorpus.model_json_schema()
        assert body["response_format"]["json_schema"]["schema"] == expected_schema
        assert body["max_tokens"] == 3000  # annexe B §B.4, narration output budget
        assert requests[0].headers["Authorization"] == "Bearer sk-test"
        totals = provider.usage_totals()
        assert totals["provider"] == "mistral"
        assert totals["calls"] == 1
        assert totals["total_tokens"] == 42
        couple = totals["by_usage_model"]
        assert couple == {"narration|mistral-small-latest": {"calls": 1, "total_tokens": 42}}

    def test_invalid_output_degrades_after_exactly_two_attempts(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=mistral_response("not json at all"))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError):
            provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        assert calls == 2

    def test_verdict_laden_output_fails_schema_validation_and_degrades(self) -> None:
        """§19.4 guard: an LLM output carrying a verdict never passes validation."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=mistral_response(
                    json.dumps({"narrations": {"takeL_1": "x"}, "verdict": "satisfied"})
                ),
            )

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError):
            provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))

    def test_http_error_is_retried_then_degrades(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, json={"error": "boom"})

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError):
            provider.semantic_profile(model=PHILOSOPHES3_MODEL)
        assert calls == 2


class TestOneDocumentOneCall:
    """P72 guard: one HTTP call per document, never one per element."""

    def test_one_reading_call_for_the_whole_grid_is_one_http_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            readings = {
                formula: {
                    "reading": "r",
                    "not_says": "n",
                    "pattern": "p",
                    "cross": "c",
                }
                for formula in ("EF (eat_1)", "AG EF (fork_1 = 1)", "EF DEADLOCK")
            }
            return httpx.Response(200, json=mistral_response(json.dumps({"readings": readings})))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        document = provider.reading(
            model=PHILOSOPHES3_MODEL,
            formulas=tuple(
                FormulaText(f) for f in ("EF (eat_1)", "AG EF (fork_1 = 1)", "EF DEADLOCK")
            ),
        )
        assert len(document.readings) == 3
        assert len(requests) == 1
        body = json.loads(requests[0].content.decode("utf-8"))
        assert body["max_tokens"] == 6000  # annexe B §B.4, reading output budget
        assert body["response_format"]["json_schema"]["name"] == "reading"
        totals = provider.usage_totals()
        couple = totals["by_usage_model"]
        assert couple == {"reading|mistral-small-latest": {"calls": 1, "total_tokens": 42}}

    def test_one_narrate_call_for_the_whole_witness_is_one_http_request(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            narrations = {f"t{i}": f"step {i}" for i in range(5)}
            payload = mistral_response(json.dumps({"narrations": narrations}))
            return httpx.Response(200, json=payload)

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        corpus = provider.narrate(
            model=PHILOSOPHES3_MODEL, transitions=tuple(f"t{i}" for i in range(5))
        )
        assert len(corpus.narrations) == 5
        assert len(requests) == 1

    def test_every_request_and_response_is_archived_with_the_model_fingerprint(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=mistral_response(
                    json.dumps({"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}})
                ),
            )

        provider = MistralProvider(
            api_key=SecretStr("sk-test"),
            model="mistral-small-2603",
            http_client=fake_client(handler),
        )
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        archive = provider.archived_calls()
        assert len(archive) == 1
        entry = archive[0]
        assert entry["usage"] == "narration"
        assert entry["model"] == "mistral-small-2603"
        assert entry["version"] == provider.model_version == "mistral-small-2603"
        assert "request" in entry and "response" in entry
        assert entry["request"]["model"] == "mistral-small-2603"  # rejeu P46


FORBIDDEN_VERDICT_WORDS = (
    "verified",
    "holds",
    "violated",
    "proved",
    "true",
    "false",
    "correct",
    "incorrect",
)
"""The verdict vocabulary forbidden in the real prompts (annexe B §B.3, P73)."""

# A distinctive verbatim fragment of each annexe B §B.3 system prompt (P73:
# the prompts are pinned verbatim, not paraphrased).
VERBATIM_FRAGMENTS: dict[str, str] = {
    "semantic-profile": "propose a role hypothesis and a confidence",
    "reading": "the closest stronger reading the",
    "clarification": "Never choose for the engineer",
    "cluster": "never an automated change",
    "narration": "never its desirability",
}


class TestCanonicalPayload:
    """P73 (annexe B §B.1): each call is two messages - the system prompt, then a
    canonical JSON payload {digest, data, language}."""

    def test_user_message_is_the_canonical_json_payload_with_the_net_digest(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json=mistral_response(
                    json.dumps({"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}})
                ),
            )

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        body = json.loads(requests[0].content.decode("utf-8"))
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == PROMPT_TEMPLATES["narration"]
        assert body["messages"][1]["role"] == "user"
        payload = json.loads(body["messages"][1]["content"])
        assert payload["language"] == "fr"
        assert payload["digest"]["level"] == "trace"
        assert payload["digest"]["steps"][0]["transition"]["id"] == "takeL_1"
        assert payload["data"] == {"transitions": ["takeL_1"]}

    def test_reading_payload_carries_the_places_only_digest_and_the_grid(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            readings = {
                "EF (eat_1)": {"reading": "r", "not_says": "n", "pattern": "p", "cross": "c"}
            }
            return httpx.Response(200, json=mistral_response(json.dumps({"readings": readings})))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        provider.reading(model=PHILOSOPHES3_MODEL, formulas=(FormulaText("EF (eat_1)"),))
        body = json.loads(requests[0].content.decode("utf-8"))
        payload = json.loads(body["messages"][1]["content"])
        assert payload["digest"]["level"] == "places-only"
        assert [place["name"] for place in payload["digest"]["places"]] == ["eat_1"]
        assert payload["data"]["grid"] == [{"formula": "EF (eat_1)"}]
        assert payload["data"]["relations"] == []


class TestNoInvention:
    """P73: any output data absent from the input data is an invention - the
    provider degrades explicitly on an injected counterexample."""

    def test_reading_document_answering_an_unrequested_formula_degrades(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            readings = {
                "EF (eat_1)": {"reading": "r", "not_says": "n", "pattern": "p", "cross": "c"},
                "EF DEADLOCK": {"reading": "r", "not_says": "n", "pattern": "p", "cross": "c"},
            }
            return httpx.Response(200, json=mistral_response(json.dumps({"readings": readings})))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError, match="absent from the grid"):
            provider.reading(model=PHILOSOPHES3_MODEL, formulas=(FormulaText("EF (eat_1)"),))

    def test_narration_corpus_covering_an_unfired_transition_degrades(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            narrations = {"takeL_1": "a", "takeL_2": "b"}
            payload = mistral_response(json.dumps({"narrations": narrations}))
            return httpx.Response(200, json=payload)

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError, match="absent from the trace"):
            provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))

    def test_profile_answer_carrying_the_mock_wrong_field_degrades(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            entry = {
                "id": "think",
                "elements": "S1",
                "role": "file d'attente",
                "conf": 0.9,
                "evidence": "e",
                "wrong": True,
            }
            return httpx.Response(200, json=mistral_response(json.dumps({"entries": [entry]})))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError):
            provider.semantic_profile(model=PHILOSOPHES3_MODEL)


class TestPromptGuard:
    def test_every_prompt_template_forbids_verdicts(self) -> None:
        """P73 guard: no verdict word in any real prompt, outside its own guard sentence."""
        assert set(PROMPT_TEMPLATES) == {
            "semantic-profile",
            "reading",
            "clarification",
            "cluster",
            "narration",
        }
        for usage, template in PROMPT_TEMPLATES.items():
            # The guard sentence itself names the forbidden words; strip it first.
            # "violated clause(s)" is the annexe B input noun phrase (the given
            # data), not a verdict the model is asked to produce.
            body = template.split("Never use the words", maxsplit=1)[0].lower()
            body = body.replace("violated clauses", "").replace("violated clause", "")
            for word in FORBIDDEN_VERDICT_WORDS:
                assert word not in body, f"prompt {usage!r} carries the verdict word {word!r}"
            assert "never use the words" in template.lower(), (
                f"prompt {usage!r} must carry the verbatim guard sentence"
            )

    def test_prompts_are_the_verbatim_annexe_b_texts(self) -> None:
        """P73: each system prompt is the annexe B §B.3 text, pinned by a verbatim fragment."""
        for usage, fragment in VERBATIM_FRAGMENTS.items():
            assert fragment in PROMPT_TEMPLATES[usage], (
                f"prompt {usage!r} is not the verbatim annexe B text"
            )

    def test_no_llm_schema_has_a_verdict_field(self) -> None:
        """Structural guard: no LLM output schema can carry a verdict key."""

        for name in dir(schemas):
            obj = getattr(schemas, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel):
                assert "verdict" not in obj.model_fields, f"{name} has a verdict field"

    def test_real_profile_schema_excludes_the_mock_wrong_field(self) -> None:
        """P73 (annexe B §B.5): the real provider's schema has no `wrong` field."""
        assert "wrong" not in schemas.SemanticProfilePublic.model_fields
        assert "wrong" not in schemas.ProfileEntryPublic.model_fields
        with pytest.raises(ValidationError):
            schemas.SemanticProfilePublic.model_validate(
                {
                    "entries": [
                        {
                            "id": "p",
                            "elements": "M1",
                            "role": "r",
                            "conf": 0.5,
                            "evidence": "e",
                            "wrong": True,
                        }
                    ]
                }
            )
