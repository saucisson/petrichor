"""API tests for the opt-in static-first proof (issue #25, spec §18.4).

When ``PETRICHOR_STATIC_FIRST`` is on, a question the embedded mini-engine
could not decide (``dégradé``, typically a net beyond its capacity) is probed
by the reducer before any exhaustive MC run: a decided probe is a static
proof (``technique: static``, no MC run emitted); an undecided probe yields
the reduced net ``model.sr.pnml`` and the projected formula, and the run goes
on the reduced model. Both paths use the same clause states as the §14 triage.
"""

import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog
from api_test_support import accepting_verifier

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.checks import (
    CheckCompleted,
    CheckOutcome,
    CheckQuestion,
    StaticFirstProbe,
    ToolVersion,
)
from petrichor.engines.mini_engine import (
    mini_capacity,
    non_vacuity_guard,
    resolve_static_payload,
)
from petrichor.model_store import ModelStore
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel, JsonValue
    from petrichor.triage import CapacityReport, NonVacuityGuard

FIXTURES = Path(__file__).parent / "fixtures"
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3.pnml").read_text(encoding="utf-8")

TEST_PAT = "test-pat"
AUTH_HEADERS = {"Authorization": f"Bearer {TEST_PAT}"}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")
FAKE_COMMAND = ["fake-tool", "check"]
DONE_OUTPUT = {"verdict": "violated", "witness": None, "cost": {}, "technique": "dummy"}
REDUCER_TOOL_VERSION = ToolVersion("its-tools-10052022-reducer")

CONSERVATION_FORMULA = "AG (fork_1 + fork_2 + fork_3 == 3)"
"""Outside the mini-engine fragment: triaged dégradé, the static-first trigger."""
INVARIANT_FORMULA = "AG (eat_1 <= 1)"
"""Inside the mini-engine fragment: resolved statically, never probed."""
PROJECTED_FORMULA = "AG (fork_1 <= 1)"
REDUCED_MODEL: dict[str, JsonValue] = {"places": [], "transitions": [], "arcs": []}
TERMINAL_POLL_SECONDS = 10.0
POLL_INTERVAL_SECONDS = 0.01


class MiniStaticResolver:
    """The StaticResolver protocol wired to the mini-engine adapters."""

    def capacity(self, *, model: CanonicalModel) -> CapacityReport:
        """Probe the state-graph capacity of the model."""
        return mini_capacity(model=model)

    def resolve_static(
        self, *, model: CanonicalModel, question: CheckQuestion
    ) -> dict[str, JsonValue]:
        """Resolve a statically-triaged question inside the mini-engine fragment."""
        return resolve_static_payload(model=model, question=question)

    def non_vacuity_guard(
        self, *, model: CanonicalModel, family: str, kind: str, formula: str
    ) -> NonVacuityGuard:
        """Build the non-vacuity guard of a question (spec §14.4)."""
        return non_vacuity_guard(model=model, family=family, kind=kind, formula=formula)


class RecordingRunner:
    """A runner recording every question it executes, serving one fixed outcome."""

    tool_version = FAKE_TOOL_VERSION

    def __init__(self) -> None:
        """Start with an empty execution log."""
        self.questions: list[CheckQuestion] = []

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckOutcome:
        """Record the question and answer the fixed completed outcome."""
        self.questions.append(question)
        return CheckCompleted(output=DONE_OUTPUT, command=FAKE_COMMAND)


class FakeProber:
    """A static-first prober serving one fixed probe outcome."""

    def __init__(self, *, probe: StaticFirstProbe) -> None:
        """Serve the given probe outcome on every call."""
        self._probe = probe
        self.calls = 0

    def probe(
        self, *, model: CanonicalModel, question: CheckQuestion, timeout_seconds: int
    ) -> StaticFirstProbe:
        """Count the call and return the fixed probe outcome."""
        self.calls += 1
        return self._probe


def decided_probe() -> StaticFirstProbe:
    """A probe that statically proves the question (no MC run needed)."""
    return StaticFirstProbe(
        verdict="satisfied",
        output={
            "verdict": "true",
            "witness": None,
            "cost": {"tool_seconds": 0.4, "states_explored": 0},
        },
        tool_version=REDUCER_TOOL_VERSION,
    )


def undecided_probe() -> StaticFirstProbe:
    """A probe that cannot decide and yields the reduced net + projected formula."""
    return StaticFirstProbe(
        verdict="unknown",
        reduced_model=REDUCED_MODEL,
        projected_formula=PROJECTED_FORMULA,
        tool_version=REDUCER_TOOL_VERSION,
    )


def build_client(
    *, runner: RecordingRunner, prober: FakeProber, static_first: bool
) -> tuple[httpx.Client, str]:
    """Wire the app with the Philosophes-3 model, the recording runner and the prober."""
    settings = ApiSettings(static_first=static_first)
    store = ModelStore()
    model, _ = store.save(model=import_pnml(text=PHILOSOPHES3_PNML))
    app = create_app(
        token_verifier=accepting_verifier(),
        settings=settings,
        store=store,
        run_store=RunStore(),
        runner=runner,
        static_resolver=MiniStaticResolver(),
        static_first_prober=prober,
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, str(model.id)


def post_check(client: httpx.Client, family: str, payload: dict[str, object]) -> httpx.Response:
    """POST one check question."""
    return client.post(f"/v1/checks/{family}", json=payload, headers=JSON_HEADERS)


def check_payload(model_id: str, kind: str, formula: str) -> dict[str, object]:
    """The minimal check request body."""
    return {"model_id": model_id, "kind": kind, "formula": formula}


def wait_terminal(client: httpx.Client, run_id: str) -> dict[str, object]:
    """Poll GET /v1/runs/{rid} until the run leaves the running state."""
    deadline = time.monotonic() + TERMINAL_POLL_SECONDS
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(POLL_INTERVAL_SECONDS)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class TestStaticFirst:
    def test_decided_probe_is_a_static_proof_without_any_mc_run(self) -> None:
        """A decided reducer probe resolves the question: technique static, no runner call."""
        runner = RecordingRunner()
        prober = FakeProber(probe=decided_probe())
        client, model_id = build_client(runner=runner, prober=prober, static_first=True)
        with structlog.testing.capture_logs() as logs:
            payload = check_payload(model_id, "safety.conservation", CONSERVATION_FORMULA)
            response = post_check(client, "safety", payload)
        assert response.status_code == 202
        run = client.get(f"/v1/runs/{response.json()['run_id']}", headers=AUTH_HEADERS).json()
        assert run["status"] == "done"
        assert run["output"]["technique"] == "static"
        assert run["output"]["verdict"] == "true"
        assert run["triage"]["kind"] == "statique"
        assert "Preuve statique d'abord" in run["triage"]["justification"]
        assert run["tool_version"] == REDUCER_TOOL_VERSION
        assert runner.questions == [], "a decided probe emits no MC run"
        assert prober.calls == 1
        probed = next(e for e in logs if e["event"] == "api_check_static_first_probed")
        assert probed["verdict"] == "satisfied"
        assert probed["tool_version"] == REDUCER_TOOL_VERSION

    def test_undecided_probe_runs_mc_on_the_reduced_net(self) -> None:
        """An undecided probe: the run goes on model.sr.pnml with the projected formula."""
        runner = RecordingRunner()
        prober = FakeProber(probe=undecided_probe())
        client, model_id = build_client(runner=runner, prober=prober, static_first=True)
        with structlog.testing.capture_logs() as logs:
            payload = check_payload(model_id, "safety.conservation", CONSERVATION_FORMULA)
            response = post_check(client, "safety", payload)
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        run = wait_terminal(client, run_id)
        assert run["status"] == "done"
        assert run["output"] == DONE_OUTPUT
        assert run["triage"]["kind"] == "dégradé", "the clause state stays the §14 degradation"
        assert len(runner.questions) == 1
        executed = runner.questions[0]
        assert executed.formula == PROJECTED_FORMULA
        assert executed.parameters["model"] == REDUCED_MODEL
        assert prober.calls == 1
        reduced = next(e for e in logs if e["event"] == "api_check_static_first_reduced")
        assert reduced["formula"] == PROJECTED_FORMULA
        # The idempotence key covers the original question, not the reduced one.
        replayed = post_check(
            client, "safety", check_payload(model_id, "safety.conservation", CONSERVATION_FORMULA)
        )
        assert replayed.status_code == 202
        assert replayed.json()["run_id"] == run_id
        assert len(runner.questions) == 1, "the replay is idempotent, no second run"

    def test_static_first_disabled_never_calls_the_prober(self) -> None:
        """Opt-out (default): a degraded question goes straight to the runner, unprobed."""
        runner = RecordingRunner()
        prober = FakeProber(probe=undecided_probe())
        client, model_id = build_client(runner=runner, prober=prober, static_first=False)
        payload = check_payload(model_id, "safety.conservation", CONSERVATION_FORMULA)
        response = post_check(client, "safety", payload)
        assert response.status_code == 202
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "done"
        assert run["triage"]["kind"] == "dégradé"
        assert prober.calls == 0
        assert len(runner.questions) == 1
        assert runner.questions[0].formula == CONSERVATION_FORMULA

    def test_resolvable_static_question_is_never_probed(self) -> None:
        """A question the mini-engine resolves is done statically; the prober stays idle."""
        runner = RecordingRunner()
        prober = FakeProber(probe=undecided_probe())
        client, model_id = build_client(runner=runner, prober=prober, static_first=True)
        response = post_check(
            client, "safety", check_payload(model_id, "safety.invariant", INVARIANT_FORMULA)
        )
        assert response.status_code == 202
        run = client.get(f"/v1/runs/{response.json()['run_id']}", headers=AUTH_HEADERS).json()
        assert run["status"] == "done"
        assert run["output"]["technique"] == "static"
        assert run["triage"]["kind"] == "statique"
        assert prober.calls == 0
        assert runner.questions == []

    def test_undecided_probe_without_reduced_model_falls_back_to_the_original(self) -> None:
        """An unknown probe carrying no reduced net runs the MC on the original question."""
        runner = RecordingRunner()
        prober = FakeProber(
            probe=StaticFirstProbe(verdict="unknown", tool_version=REDUCER_TOOL_VERSION)
        )
        client, model_id = build_client(runner=runner, prober=prober, static_first=True)
        payload = check_payload(model_id, "safety.conservation", CONSERVATION_FORMULA)
        response = post_check(client, "safety", payload)
        assert response.status_code == 202
        run = wait_terminal(client, response.json()["run_id"])
        assert run["status"] == "done"
        assert run["triage"]["kind"] == "dégradé"
        assert prober.calls == 1
        assert len(runner.questions) == 1
        assert runner.questions[0].formula == CONSERVATION_FORMULA
