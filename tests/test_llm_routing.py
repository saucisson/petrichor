"""Tests of the usage → model routing with pinned versions (P74, ADR-0108)."""

import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from tests.test_llm_mistral import USAGE_SAMPLES, fake_client, mistral_response

from petrichor.llm.mistral import MistralProvider
from petrichor.llm.routing import (
    ALL_USAGES,
    USAGE_MODEL_ROUTING,
    ModelRoute,
    RoutingError,
    route_for,
    validate_routing,
)
from petrichor.pnml_import import import_pnml

if TYPE_CHECKING:
    from petrichor.llm.schemas import LlmUsage

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_MODEL = import_pnml(
    text=(FIXTURES / "philosophes3-maquette.pnml").read_text(encoding="utf-8")
)


class TestRoutingTable:
    def test_covers_exactly_the_five_usages(self) -> None:
        assert (
            set(USAGE_MODEL_ROUTING)
            == set(ALL_USAGES)
            == {
                "semantic-profile",
                "reading",
                "clarification",
                "cluster",
                "narration",
            }
        )

    def test_routing_matches_annexe_b_b4(self) -> None:
        """ADR-0108: semantic-profile/cluster → Large 4, reading/clarification → Small 4,
        narration → Ministral 3 14B with a Small 4 fallback."""
        assert USAGE_MODEL_ROUTING["semantic-profile"].model == "mistral-large-4-2510"
        assert USAGE_MODEL_ROUTING["cluster"].model == "mistral-large-4-2510"
        assert USAGE_MODEL_ROUTING["reading"].model == "mistral-small-2603"
        assert USAGE_MODEL_ROUTING["clarification"].model == "mistral-small-2603"
        narration = USAGE_MODEL_ROUTING["narration"]
        assert narration.model == "ministral-14b-2512"
        assert narration.fallback is not None
        assert narration.fallback.model == "mistral-small-2603"

    def test_every_identifier_is_pinned_exact(self) -> None:
        """Guard: no `-latest` alias anywhere in the versioned configuration."""
        for usage in ALL_USAGES:
            route = USAGE_MODEL_ROUTING[usage]
            assert "-latest" not in route.model
            assert "-latest" not in route.version
            if route.fallback is not None:
                assert "-latest" not in route.fallback.model
                assert "-latest" not in route.fallback.version

    def test_model_route_rejects_a_latest_alias(self) -> None:
        with pytest.raises(ValidationError, match="-latest"):
            ModelRoute(model="mistral-small-latest", version="mistral-small-latest")

    def test_validate_routing_rejects_a_latest_alias_with_a_typed_error(self) -> None:
        # model_construct bypasses the field validator to exercise validate_routing itself.
        aliased = ModelRoute.model_construct(
            model="mistral-small-2603", version="mistral-small-latest"
        )
        routing: dict[LlmUsage, ModelRoute] = {**USAGE_MODEL_ROUTING, "reading": aliased}
        with pytest.raises(RoutingError, match="-latest"):
            validate_routing(routing)

    def test_validate_routing_rejects_a_missing_usage(self) -> None:
        routing: dict[LlmUsage, ModelRoute] = dict(USAGE_MODEL_ROUTING)
        del routing["cluster"]
        with pytest.raises(RoutingError, match="cluster"):
            validate_routing(routing)

    def test_route_for_is_exhaustive(self) -> None:
        for usage in ALL_USAGES:
            assert route_for(USAGE_MODEL_ROUTING, usage=usage) is USAGE_MODEL_ROUTING[usage]


class TestProviderRouting:
    def test_each_usage_is_sent_to_its_pinned_model(self) -> None:
        """One request per usage; the request body carries the routed pinned model."""
        sent: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            usage = body["response_format"]["json_schema"]["name"]
            sent[usage] = body["model"]
            return httpx.Response(200, json=mistral_response(json.dumps(USAGE_SAMPLES[usage])))

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        provider.semantic_profile(model=PHILOSOPHES3_MODEL)
        provider.reading(model=PHILOSOPHES3_MODEL, formulas=("AG(philo0 thinking)",))
        provider.clarification(model=PHILOSOPHES3_MODEL, text="un texte")
        provider.cluster(model=PHILOSOPHES3_MODEL)
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        assert sent == {
            "semantic-profile": "mistral-large-4-2510",
            "reading": "mistral-small-2603",
            "clarification": "mistral-small-2603",
            "cluster": "mistral-large-4-2510",
            "narration": "ministral-14b-2512",
        }

    def test_archive_and_totals_carry_the_routed_model_and_version(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=mistral_response(
                    json.dumps({"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}})
                ),
            )

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        entry = provider.archived_calls()[0]
        assert entry["model"] == "ministral-14b-2512"
        assert entry["version"] == "ministral-14b-2512"
        totals = provider.usage_totals()
        assert totals["by_usage_model"] == {
            "narration|ministral-14b-2512": {"calls": 1, "total_tokens": 42}
        }
        assert totals["routing"]["narration"] == "ministral-14b-2512"

    def test_narration_falls_back_to_the_pinned_small_model_on_degradation(self) -> None:
        """ADR-0108: narration repli = Small 4 when the primary model degrades."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode("utf-8"))
            seen.append(body["model"])
            if body["model"] == "ministral-14b-2512":
                return httpx.Response(500, json={"error": "model unavailable"})
            return httpx.Response(
                200,
                json=mistral_response(
                    json.dumps({"narrations": {"takeL_1": "Le philosophe 1 prend la fourchette."}})
                ),
            )

        provider = MistralProvider(api_key=SecretStr("sk-test"), http_client=fake_client(handler))
        corpus = provider.narrate(model=PHILOSOPHES3_MODEL, transitions=("takeL_1",))
        assert "takeL_1" in corpus.narrations
        # The primary model was tried (bounded re-requests), then the pinned fallback.
        assert "ministral-14b-2512" in seen
        assert seen[-1] == "mistral-small-2603"
        archive = provider.archived_calls()
        assert archive[-1]["model"] == "mistral-small-2603"
        assert archive[-1]["version"] == "mistral-small-2603"

    def test_provider_rejects_an_incomplete_routing(self) -> None:
        routing: dict[LlmUsage, ModelRoute] = dict(USAGE_MODEL_ROUTING)
        del routing["cluster"]
        with pytest.raises(RoutingError, match="cluster"):
            MistralProvider(api_key=SecretStr("sk-test"), routing=routing)
