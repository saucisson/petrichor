"""Tests for the real Mistral LLM provider and its selection (spec §8, §15, §19.4; issue #36).

- ``MistralProvider`` beside the mock, selected by configuration.
- Every output is validated by its schema; failure → bounded re-request →
  explicit degradation. A verdict-laden output fails validation (guard §19.4).
- The LLM cost (tokens) is exposed apart from the tool-time budget (§12.4).
"""

import json
from typing import TYPE_CHECKING, get_args, get_origin

import httpx
import pytest
import structlog.testing
from pydantic import BaseModel, SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.llm import schemas

if TYPE_CHECKING:
    from collections.abc import Callable
from petrichor.llm.mistral import PROMPT_TEMPLATES, MistralProvider
from petrichor.llm.provider import DeterministicMockProvider, LLMDegradationError
from petrichor.main import select_llm_provider
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import ContentHash
from petrichor.run_store import RunStore

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
MODEL_HASH = ContentHash("sha256:48fb522c37d91504f4dc22ead01e8be6269eed234d62ab6b562a0fc38f9fb316")


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
        sentence = provider.narrate_transition(model_hash=MODEL_HASH, transition="takeL_1")
        assert sentence == "Le philosophe 1 prend la fourchette."
        assert len(requests) == 1
        body = json.loads(requests[0].content.decode("utf-8"))
        assert body["temperature"] == 0
        assert body["response_format"] == {"type": "json_object"}
        assert requests[0].headers["Authorization"] == "Bearer sk-test"
        totals = provider.usage_totals()
        assert totals["provider"] == "mistral"
        assert totals["calls"] == 1
        assert totals["total_tokens"] == 42

    def test_invalid_output_degrades_after_exactly_two_attempts(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json=mistral_response("not json at all"))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError):
            provider.narrate_transition(model_hash=MODEL_HASH, transition="takeL_1")
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
            provider.narrate_transition(model_hash=MODEL_HASH, transition="takeL_1")

    def test_http_error_is_retried_then_degrades(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500, json={"error": "boom"})

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with pytest.raises(LLMDegradationError):
            provider.semantic_profile(model_hash=MODEL_HASH)
        assert calls == 2


class TestPromptGuard:
    def test_every_prompt_template_forbids_verdicts(self) -> None:
        """§19.4: no prompt asks for a verdict; each carries the guard phrase."""
        assert set(PROMPT_TEMPLATES) == {
            "semantic-profile",
            "reading",
            "clarification",
            "cluster",
            "narration",
        }
        for usage, template in PROMPT_TEMPLATES.items():
            lowered = template.lower()
            assert "never" in lowered, f"prompt {usage!r} must carry a never-guard phrase"
            assert '"verdict"' not in lowered, f"prompt {usage!r} must not ask for a verdict key"

    def test_no_llm_schema_has_a_verdict_field(self) -> None:
        """Structural guard: no LLM output schema can carry a verdict key."""

        for name in dir(schemas):
            obj = getattr(schemas, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel):
                assert "verdict" not in obj.model_fields, f"{name} has a verdict field"


# The shape each prompt template advertises, mapped to the pydantic schema that
# validates the real output (issue #176: the drift between the two made every
# real call fail validation). The guard is mechanical in both directions: the
# sample must validate against the schema, and every key of the sample must be
# named in the prompt - change either side and this test fails.
USAGE_SCHEMAS: dict[str, type[BaseModel]] = {
    "semantic-profile": schemas.SemanticProfile,
    "reading": schemas.FormulaReadings,
    "clarification": schemas.Clarification,
    "cluster": schemas.ClusterList,
    "narration": schemas.NarrationCorpus,
}

USAGE_SAMPLES: dict[str, dict[str, object]] = {
    "semantic-profile": {
        "entries": [
            {
                "id": "p1",
                "elements": "M1",
                "role": "ressource",
                "conf": 0.9,
                "evidence": "place M1 is consumed by takeL",
                "options": ["jeton"],
                "wrong": False,
            }
        ]
    },
    "reading": {
        "readings": {
            "AG(philo0 thinking)": {
                "reading": "toujours vrai",
                "not_says": "vivacite",
                "pattern": "safety",
                "cross": "q2",
            }
        }
    },
    "clarification": {"reform": "verifier que ...", "options": ["lecture A", "lecture B"]},
    "cluster": {"clusters": [{"cause": "model", "fix": "ajouter une place", "members": ["c1"]}]},
    "narration": {"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}},
}


def _annotation_keys(annotation: object) -> set[str]:
    """The nested schema keys reachable through a field annotation."""
    origin = get_origin(annotation)
    if origin is not None:
        keys: set[str] = set()
        for argument in get_args(annotation):
            keys |= _annotation_keys(argument)
        return keys
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return _schema_keys(annotation)
    return set()


def _schema_keys(model: type[BaseModel]) -> set[str]:
    """Every JSON key of a pydantic model, recursively (validation aliases win)."""
    keys: set[str] = set()
    for name, field in model.model_fields.items():
        alias = field.validation_alias if isinstance(field.validation_alias, str) else name
        keys.add(alias)
        keys |= _annotation_keys(field.annotation)
    return keys


class TestPromptSchemaParity:
    def test_every_usage_prompt_matches_its_pydantic_schema(self) -> None:
        """Issue #176 guard: the JSON shape advertised by each prompt template
        validates against the usage's pydantic schema, and the prompt names
        every key of that schema - prompt/schema drift fails here."""
        assert set(USAGE_SCHEMAS) == set(PROMPT_TEMPLATES)
        for usage, schema in USAGE_SCHEMAS.items():
            schema.model_validate(USAGE_SAMPLES[usage])
            template = PROMPT_TEMPLATES[usage]
            for key in _schema_keys(schema):
                assert f'"{key}"' in template, (
                    f"prompt {usage!r} does not name the schema key {key!r}"
                )


class TestProviderSelection:
    def test_mock_is_the_default(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        assert isinstance(select_llm_provider(settings=settings), DeterministicMockProvider)

    def test_mistral_without_key_fails_at_startup(self) -> None:
        settings = ApiSettings(
            api_key=SecretStr(TEST_API_KEY), llm_provider="mistral", mistral_api_key=None
        )
        with pytest.raises(SystemExit):
            select_llm_provider(settings=settings)

    def test_mistral_with_key_is_selected(self) -> None:
        settings = ApiSettings(
            api_key=SecretStr(TEST_API_KEY),
            llm_provider="mistral",
            mistral_api_key=SecretStr("sk-test"),
        )
        provider = select_llm_provider(settings=settings)
        assert isinstance(provider, MistralProvider)
        provider.close()


class TestLlmUsageEndpoint:
    def test_llm_usage_is_exposed_apart_from_the_tool_budget(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        app = create_app(
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            llm_provider=DeterministicMockProvider(),
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        response = client.get("/v1/llm/usage", headers=AUTH_HEADERS)
        assert response.status_code == 200
        payload = response.json()
        assert payload["llm"]["provider"] == "mock"
        assert "NOT part of the MC tool-time budget" in payload["budget_note"]

    def test_llm_usage_without_provider_is_503(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        app = create_app(settings=settings, store=ModelStore(), run_store=RunStore(), runner=None)
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        response = client.get("/v1/llm/usage", headers=AUTH_HEADERS)
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "llm_not_configured"


class TestLlmLogNarration:
    """The LLM layer's log events are part of the narration contract (ADR-0005)."""

    def test_mistral_failure_and_degradation_events_are_pinned(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mistral_response("not json"))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        with structlog.testing.capture_logs() as logs, pytest.raises(LLMDegradationError):
            provider.narrate_transition(model_hash=MODEL_HASH, transition="takeL_1")
        events = [event["event"] for event in logs]
        assert events.count("llm_mistral_call_failed") == 2
        assert "llm_degraded" in events

    def test_llm_usage_endpoint_event_is_pinned(self) -> None:
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        app = create_app(
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            llm_provider=DeterministicMockProvider(),
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        with structlog.testing.capture_logs() as logs:
            response = client.get("/v1/llm/usage", headers=AUTH_HEADERS)
            assert response.status_code == 200
        events = [event["event"] for event in logs]
        assert "api_llm_usage_read" in events
