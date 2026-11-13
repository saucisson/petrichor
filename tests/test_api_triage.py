"""API tests for the triage, static resolution and non-vacuity guards (issue #14, spec §14.2/§14.4).

A statically-triaged question is resolved by the embedded mini-engine without
any MC run (``technique: static``, cost zero, no runner needed); a degraded
question displays its degradation on the run state; every question carries its
non-vacuity guard chip.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog.testing
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.checks import CheckCompleted, CheckOutcome, CheckQuestion, CheckRunner, ToolVersion
from petrichor.engines.mini_engine import (
    MAX_PLACES_FOR_INVARIANT_ENUMERATION,
    mini_capacity,
    non_vacuity_guard,
    resolve_static_payload,
)
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import CanonicalModel, build_canonical_model
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore

if TYPE_CHECKING:
    from petrichor.triage import CapacityReport, NonVacuityGuard

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
FAKE_COMMAND = ["fake-tool", "check"]
DONE_OUTPUT = {"verdict": "violated", "witness": None, "cost": {}, "technique": "dummy"}


class MiniStaticResolver:
    """The StaticResolver protocol wired to the mini-engine adapters."""

    def capacity(self, *, model: CanonicalModel) -> CapacityReport:
        return mini_capacity(model=model)

    def resolve_static(
        self, *, model: CanonicalModel, question: CheckQuestion
    ) -> dict[str, object]:
        return resolve_static_payload(model=model, question=question)

    def non_vacuity_guard(
        self, *, model: CanonicalModel, family: str, kind: str, formula: str
    ) -> NonVacuityGuard:
        return non_vacuity_guard(model=model, family=family, kind=kind, formula=formula)


class FakeRunner:
    """A minimal runner serving one fixed completed outcome."""

    tool_version = FAKE_TOOL_VERSION

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        return CheckCompleted(output=DONE_OUTPUT, command=FAKE_COMMAND)


def build_client(
    *, runner: CheckRunner | None = None, with_resolver: bool = True, pnml: str = PHILOSOPHES3_PNML
) -> tuple[httpx.Client, str]:
    """Wire the app with the Philosophes-3 model, a runner and the static resolver."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    model, _ = store.save(model=import_pnml(text=pnml))
    app = create_app(
        settings=settings,
        store=store,
        run_store=RunStore(),
        runner=runner,
        static_resolver=MiniStaticResolver() if with_resolver else None,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, str(model.id)


def post_check(client: httpx.Client, family: str, payload: dict[str, object]) -> httpx.Response:
    return client.post(f"/v1/checks/{family}", json=payload, headers=JSON_HEADERS)


def check_payload(model_id: str, kind: str, formula: str) -> dict[str, object]:
    return {"model_id": model_id, "kind": kind, "formula": formula}


def get_run(client: httpx.Client, run_id: str) -> dict[str, object]:
    response = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS)
    assert response.status_code == 200
    return response.json()


class TestStaticResolution:
    def test_static_question_resolved_without_any_mc_run(self) -> None:
        """A statique question is done at once: technique static, cost zero, no runner wired."""
        client, model_id = build_client(runner=None)
        with structlog.testing.capture_logs() as logs:
            response = post_check(
                client, "safety", check_payload(model_id, "safety.invariant", "AG (eat_1 <= 1)")
            )
        assert response.status_code == 202
        run = get_run(client, response.json()["run_id"])
        assert run["status"] == "done"
        assert run["output"]["technique"] == "static"
        assert run["output"]["verdict"] == "true"
        assert run["measured_seconds"] == 0.0
        assert run["command"] == []
        assert run["tool_version"] == "mini-engine"
        assert run["triage"]["kind"] == "statique"
        assert "spec §14.2" in run["triage"]["justification"]
        triaged = next(e for e in logs if e["event"] == "api_check_triaged")
        assert triaged["triage_kind"] == "statique"
        resolved = next(e for e in logs if e["event"] == "api_check_static_resolved")
        assert resolved["verdict"] == "true"

    def test_static_question_false_verdict_carries_a_witness(self) -> None:
        client, model_id = build_client(runner=None)
        response = post_check(
            client, "safety", check_payload(model_id, "safety.invariant", "AG (eat_1 <= 0)")
        )
        run = get_run(client, response.json()["run_id"])
        assert run["status"] == "done"
        assert run["output"]["verdict"] == "false"
        assert run["output"]["technique"] == "static"
        assert run["output"]["witness"] is not None

    def test_static_resolution_is_idempotent(self) -> None:
        client, model_id = build_client(runner=None)
        payload = check_payload(model_id, "safety.invariant", "AG (eat_1 <= 1)")
        first = post_check(client, "safety", payload)
        second = post_check(client, "safety", payload)
        assert first.json()["run_id"] == second.json()["run_id"]


class TestTriageOnRuns:
    def test_mc_question_keeps_the_runner_path_and_mc_triage(self) -> None:
        client, model_id = build_client(runner=FakeRunner())
        response = post_check(
            client, "deadlock", check_payload(model_id, "deadlock", "EF DEADLOCK")
        )
        assert response.status_code == 202
        run = get_run(client, response.json()["run_id"])
        assert run["status"] == "done"
        assert run["output"]["technique"] == "dummy"
        assert run["tool_version"] == str(FAKE_TOOL_VERSION)
        assert run["triage"]["kind"] == "MC"
        assert "run MC" in run["triage"]["justification"]

    def test_mc_question_without_runner_is_still_503(self) -> None:
        client, model_id = build_client(runner=None)
        response = post_check(
            client, "deadlock", check_payload(model_id, "deadlock", "EF DEADLOCK")
        )
        assert response.status_code == 503
        assert response.json()["extensions"]["reason"] == "runner_not_configured"

    def test_degraded_question_displays_the_degradation(self) -> None:
        """No capacity probe wired: a static pattern degrades explicitly, never silently."""
        client, model_id = build_client(runner=FakeRunner(), with_resolver=False)
        with structlog.testing.capture_logs() as logs:
            response = post_check(
                client, "safety", check_payload(model_id, "safety.invariant", "AG (eat_1 <= 1)")
            )
        assert response.status_code == 202
        run = get_run(client, response.json()["run_id"])
        assert run["triage"]["kind"] == "dégradé"
        assert "déclassée MC" in run["triage"]["justification"]
        triaged = next(e for e in logs if e["event"] == "api_check_triaged")
        assert triaged["triage_kind"] == "dégradé"

    def test_beyond_capacity_model_degrades(self) -> None:
        """A model beyond the invariant cap degrades the static question (spec §14.2)."""
        places = [
            {
                "id": f"pl_{index:03d}",
                "name": f"p_{index}",
                "initial_marking": 1 if index == 0 else 0,
                "layout": {"x": index, "y": 0},
                "annotations": {},
            }
            for index in range(MAX_PLACES_FOR_INVARIANT_ENUMERATION + 1)
        ]
        payload = {
            "id": "mdl_big",
            "formalism": "pt",
            "places": places,
            "transitions": [
                {"id": "tr_000", "name": "loop", "layout": {"x": 0, "y": 1}, "annotations": {}}
            ],
            "arcs": [
                {
                    "id": "arc_in",
                    "source": "pl_000",
                    "target": "tr_000",
                    "weight": 1,
                    "annotations": {},
                },
                {
                    "id": "arc_out",
                    "source": "tr_000",
                    "target": "pl_000",
                    "weight": 1,
                    "annotations": {},
                },
            ],
            "metadata": {},
        }
        settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
        store = ModelStore()
        model, _ = store.save(model=build_canonical_model(payload=payload))
        app = create_app(
            settings=settings,
            store=store,
            run_store=RunStore(),
            runner=FakeRunner(),
            static_resolver=MiniStaticResolver(),
        )
        client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
        response = post_check(
            client, "safety", check_payload(str(model.id), "safety.invariant", "AG (p_0 <= 1)")
        )
        assert response.status_code == 202
        run = get_run(client, response.json()["run_id"])
        assert run["triage"]["kind"] == "dégradé"
        assert "Preuve statique non trouvée" in run["triage"]["justification"]


class TestNonVacuityGuards:
    def test_ef_guard_ok_on_the_corpus(self) -> None:
        client, model_id = build_client(runner=FakeRunner())
        response = post_check(
            client, "reachability", check_payload(model_id, "reachability.ef", "EF (eat_1 > 0)")
        )
        run = get_run(client, response.json()["run_id"])
        assert run["non_vacuity"]["guard"] == "ef_phi"
        assert run["non_vacuity"]["status"] == "ok"

    def test_ef_guard_vacuous_when_initial_already_satisfies(self) -> None:
        client, model_id = build_client(runner=FakeRunner())
        response = post_check(
            client, "reachability", check_payload(model_id, "reachability.ef", "EF (think_1 > 0)")
        )
        run = get_run(client, response.json()["run_id"])
        assert run["non_vacuity"]["guard"] == "ef_phi"
        assert run["non_vacuity"]["status"] == "vacuous"

    def test_ag_guard_vacuous_when_initial_violates(self) -> None:
        client, model_id = build_client(runner=FakeRunner())
        response = post_check(
            client, "safety", check_payload(model_id, "safety.invariant", "AG (think_1 <= 0)")
        )
        run = get_run(client, response.json()["run_id"])
        assert run["non_vacuity"]["guard"] == "ag_psi"
        assert run["non_vacuity"]["status"] == "vacuous"

    def test_deadlock_formula_has_no_ef_guard(self) -> None:
        client, model_id = build_client(runner=FakeRunner())
        response = post_check(
            client, "deadlock", check_payload(model_id, "deadlock", "EF DEADLOCK")
        )
        run = get_run(client, response.json()["run_id"])
        assert run["non_vacuity"]["status"] == "not_applicable"

    def test_guard_is_none_without_resolver(self) -> None:
        client, model_id = build_client(runner=FakeRunner(), with_resolver=False)
        response = post_check(
            client, "deadlock", check_payload(model_id, "deadlock", "EF DEADLOCK")
        )
        run = get_run(client, response.json()["run_id"])
        assert run["non_vacuity"] is None
