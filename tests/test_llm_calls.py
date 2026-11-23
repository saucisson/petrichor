"""Tests of the observable LLM call state (P75, issue #181).

The LLM layer exposes one state per call (usage, model, start, end, error);
the in-progress state is exercised with a provider of simulated latency,
because the mock stays instantaneous.
"""

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import structlog
from api_test_support import accepting_verifier
from pydantic import SecretStr
from tests.test_llm_mistral import fake_client, mistral_response

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.llm.mistral import MistralProvider
from petrichor.llm.provider import (
    MAX_RECORDED_CALL_STATES,
    CallState,
    DeterministicMockProvider,
    FormulaText,
    HumanMessage,
    LLMDegradationError,
    LLMProvider,
    record_call_state,
)
from petrichor.llm.routing import USAGE_MODEL_ROUTING, ModelRoute
from petrichor.llm.schemas import (
    Clarification,
    Cluster,
    FormulaReadings,
    LlmUsage,
    NarrationCorpus,
    SemanticProfilePublic,
)
from petrichor.model_store import ModelStore
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_MODEL = import_pnml(
    text=(FIXTURES / "philosophes3-maquette.pnml").read_text(encoding="utf-8")
)

TEST_PAT = "test-pat"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}

NARRATION_SAMPLE = {"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}}


class SlowTestProvider(LLMProvider):
    """A test provider with simulated latency (P75): the call stays in progress."""

    def __init__(self, *, latency_seconds: float) -> None:
        self._latency_seconds = latency_seconds
        self._states: list[CallState] = []

    def call_states(self) -> tuple[CallState, ...]:
        return tuple(self._states)

    def usage_totals(self) -> dict[str, object]:
        return {"provider": "slow-test", "calls": len(self._states)}

    def _run(self, usage: LlmUsage) -> None:
        started = datetime.now(UTC)
        self._states.append(CallState(usage=usage, model="slow-test", started_at=started))
        time.sleep(self._latency_seconds)
        self._states[-1] = self._states[-1].model_copy(update={"ended_at": datetime.now(UTC)})

    def semantic_profile(self, *, model: CanonicalModel) -> SemanticProfilePublic:
        self._run("semantic-profile")
        raise NotImplementedError

    def reading(
        self, *, model: CanonicalModel, formulas: tuple[FormulaText, ...]
    ) -> FormulaReadings:
        self._run("reading")
        raise NotImplementedError

    def clarification(self, *, model: CanonicalModel, text: HumanMessage) -> Clarification:
        self._run("clarification")
        raise NotImplementedError

    def cluster(self, *, model: CanonicalModel) -> tuple[Cluster, ...]:
        self._run("cluster")
        raise NotImplementedError

    def narrate(self, *, model: CanonicalModel, transitions: tuple[str, ...]) -> NarrationCorpus:
        self._run("narration")
        return NarrationCorpus.model_validate(NARRATION_SAMPLE)


class TestCallState:
    def test_in_progress_state_is_visible_during_a_slow_call(self) -> None:
        provider = SlowTestProvider(latency_seconds=0.2)
        observed: list[bool] = []
        thread = threading.Thread(
            target=lambda: provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        )
        thread.start()
        while not provider.call_states():
            time.sleep(0.01)
        observed.append(provider.call_states()[-1].in_progress)
        thread.join()
        states = provider.call_states()
        assert observed == [True]
        assert states[-1].in_progress is False
        assert states[-1].ended_at is not None
        assert states[-1].error is None
        assert states[-1].usage == "narration"

    def test_mistral_provider_records_start_end_and_model_per_call(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mistral_response(json.dumps(NARRATION_SAMPLE)))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        states = provider.call_states()
        assert len(states) == 1
        state = states[0]
        assert state.usage == "narration"
        assert state.model == "ministral-14b-2512"
        assert state.in_progress is False
        assert state.ended_at is not None and state.ended_at >= state.started_at
        assert state.error is None

    def test_mistral_provider_marks_the_state_in_progress_while_the_request_runs(self) -> None:
        seen_in_progress: list[bool] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_in_progress.append(provider.call_states()[-1].in_progress)
            return httpx.Response(200, json=mistral_response(json.dumps(NARRATION_SAMPLE)))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        assert seen_in_progress == [True]
        assert provider.call_states()[-1].in_progress is False

    def test_degraded_call_records_the_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=mistral_response("not json"))

        provider = MistralProvider(
            api_key=SecretStr("sk-test"),
            http_client=fake_client(handler),
            # No narration fallback: the degradation is recorded on the primary route.
            routing={
                **USAGE_MODEL_ROUTING,
                "narration": ModelRoute(model="ministral-14b-2512", version="ministral-14b-2512"),
            },
        )
        with pytest.raises(LLMDegradationError):
            provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        states = provider.call_states()
        assert states, "every attempt records a state"
        assert all(state.error == "degraded" for state in states)
        assert all(state.in_progress is False for state in states)

    def test_mock_records_finished_states_only(self) -> None:
        provider = DeterministicMockProvider()
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        states = provider.call_states()
        assert len(states) == 1
        assert states[0].in_progress is False
        assert states[0].ended_at is not None

    def test_recorded_call_states_are_bounded(self) -> None:
        """The /v1/llm/calls history keeps only the most recent calls (bounded payload)."""
        states: list[CallState] = []
        now = datetime.now(UTC)
        for _index in range(MAX_RECORDED_CALL_STATES + 10):
            record_call_state(
                states,
                CallState(
                    usage="narration",
                    model="mock",
                    started_at=now,
                    ended_at=now,
                ),
            )
            assert len(states) <= MAX_RECORDED_CALL_STATES


class TestLlmCallsEndpoint:
    def test_calls_are_exposed_with_in_progress_flags(self) -> None:
        provider = DeterministicMockProvider()
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        settings = ApiSettings()
        app = create_app(
            token_verifier=accepting_verifier(),
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            llm_provider=provider,
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        response = client.get("/v1/llm/calls", headers=AUTH_HEADERS)
        assert response.status_code == 200
        calls = response.json()["calls"]
        assert len(calls) == 1
        call = calls[0]
        assert call["usage"] == "narration"
        assert call["model"] == "mock"
        assert call["in_progress"] is False
        assert call["ended_at"] is not None
        assert call["error"] is None

    def test_calls_without_provider_is_503(self) -> None:
        settings = ApiSettings()
        app = create_app(
            token_verifier=accepting_verifier(),
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        response = client.get("/v1/llm/calls", headers=AUTH_HEADERS)
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "llm_not_configured"

    def test_calls_endpoint_debug_event_is_pinned(self) -> None:
        """ADR-0005: the api_llm_calls_read event is part of the narration contract."""
        settings = ApiSettings()
        app = create_app(
            token_verifier=accepting_verifier(),
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            llm_provider=DeterministicMockProvider(),
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        with structlog.testing.capture_logs() as logs:
            response = client.get("/v1/llm/calls", headers=AUTH_HEADERS)
        assert response.status_code == 200
        events = [event["event"] for event in logs]
        assert "api_llm_calls_read" in events

    def test_calls_requires_the_api_key(self) -> None:
        settings = ApiSettings()
        app = create_app(
            token_verifier=accepting_verifier(),
            settings=settings,
            store=ModelStore(),
            run_store=RunStore(),
            runner=None,
            llm_provider=DeterministicMockProvider(),
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        response = client.get("/v1/llm/calls")
        assert response.status_code == 401
