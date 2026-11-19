"""Tests for §16 witnesses (spec §16, issue #32).

- Every engine's witness is normalized to the §16 document and served per
  run, with the step reader consuming it (fired transition highlighted).
- Per-step narrations are LLM propositions labeled ``provenance: llm`` and
  never carry a verdict (guard test over the whole narration corpus).
- A witness shared by several violations is stored once, referenced n times.
"""

import json
import time
from pathlib import Path

import httpx
import pytest
import structlog.testing
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from conftest import fetch_ui_bundle
from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.checks import CheckCompleted, CheckQuestion, RunId, ToolVersion
from petrichor.llm.provider import DeterministicMockProvider
from petrichor.model_store import ModelStore
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore
from petrichor.witness import (
    WitnessError,
    WitnessStep,
    WitnessStore,
    narrate_steps,
    narration_carries_no_verdict,
    normalize_witness_output,
    witness_id_of,
    witness_record_of,
)

FIXTURES = Path(__file__).parent / "fixtures"
# The maquette fixture: its content hash keys the shipped golden corpus.
PHILOSOPHES3_PNML = (FIXTURES / "philosophes3-maquette.pnml").read_text(encoding="utf-8")
GOLDEN_ROOT = Path(__file__).parent.parent / "src" / "petrichor" / "llm" / "golden"

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}

FAKE_TOOL_VERSION = ToolVersion("1.0.0-fake")

WITNESS_OUTPUT = {
    "verdict": "violated",
    "witness": {
        "kind": "counterexample",
        "cyclic": False,
        "back_edge": None,
        "steps": [
            {
                "index": 0,
                "marking": ["think_1", "think_2", "think_3", "fork_1", "fork_2", "fork_3"],
                "fired": None,
            },
            {
                "index": 1,
                "marking": ["hasL_1", "think_2", "think_3", "fork_2", "fork_3"],
                "fired": "takeL_1",
            },
        ],
    },
    "cost": {"tool_seconds": 0.1, "states_explored": 4},
    "technique": "dummy",
}


class FakeRunner:
    """A runner serving a fixed normalized output, recording its calls."""

    tool_version = FAKE_TOOL_VERSION

    def __init__(self, output: dict) -> None:
        self.output = output
        self.calls: list[CheckQuestion] = []

    def run(self, *, question: CheckQuestion, timeout_seconds: int) -> CheckCompleted:
        """Serve the fixed output."""
        self.calls.append(question)
        return CheckCompleted(output=self.output, command=["fake-tool"])


def build_client(tmp_path: Path, output: dict) -> tuple[httpx.Client, RunStore, WitnessStore, str]:
    """Wire the app with the witness store and the mock LLM provider."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    store = ModelStore()
    model, _ = store.save(model=import_pnml(text=PHILOSOPHES3_PNML))
    run_store = RunStore()
    witness_store = WitnessStore(directory=tmp_path / "witnesses")
    app = create_app(
        settings=settings,
        store=store,
        run_store=run_store,
        runner=FakeRunner(output),
        witness_store=witness_store,
        llm_provider=DeterministicMockProvider(),
    )
    client = httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")
    return client, run_store, witness_store, str(model.id)


def post_check(client: httpx.Client, family: str, model_id: str, formula: str) -> str:
    """POST one check and return the run id."""
    response = client.post(
        f"/v1/checks/{family}",
        json={"model_id": model_id, "kind": "deadlock", "formula": formula},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 202
    return response.json()["run_id"]


def wait_terminal(client: httpx.Client, run_id: str, timeout: float = 5.0) -> dict:
    """Poll the run until it leaves the running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/v1/runs/{run_id}", headers=AUTH_HEADERS).json()
        if run["status"] != "running":
            return run
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class TestNormalizeWitness:
    def test_engine_output_normalizes_to_section16_document(self) -> None:
        document = normalize_witness_output(run_id=RunId("run_abc"), output=WITNESS_OUTPUT)
        assert document is not None
        assert document.run_id == "run_abc"
        assert document.kind == "counterexample"
        assert document.cyclic is False
        assert len(document.steps) == 2
        assert document.steps[1].fired == "takeL_1"
        assert document.narrations == ()

    def test_output_without_witness_normalizes_to_none(self) -> None:
        output = {**WITNESS_OUTPUT, "witness": None}
        assert normalize_witness_output(run_id=RunId("run_abc"), output=output) is None

    def test_malformed_witness_raises_a_typed_error(self) -> None:
        output = {**WITNESS_OUTPUT, "witness": {"kind": "counterexample", "steps": [{"index": 0}]}}
        with pytest.raises(WitnessError):
            normalize_witness_output(run_id=RunId("run_abc"), output=output)

    def test_witness_id_is_a_content_hash_of_the_structure(self) -> None:
        steps = (WitnessStep(index=0, marking=["think_1"], fired=None),)
        first = witness_id_of(kind="trace", cyclic=False, back_edge=None, steps=steps)
        again = witness_id_of(kind="trace", cyclic=False, back_edge=None, steps=steps)
        other = witness_id_of(
            kind="trace",
            cyclic=False,
            back_edge=None,
            steps=(WitnessStep(index=0, marking=["eat_1"], fired=None),),
        )
        assert first == again
        assert first != other
        assert first.startswith("wit_")


class TestWitnessStore:
    def test_store_deduplicates_and_references_accumulate(self, tmp_path: Path) -> None:
        store = WitnessStore(directory=tmp_path)
        document = normalize_witness_output(run_id=RunId("run_a"), output=WITNESS_OUTPUT)
        assert document is not None
        record = witness_record_of(document=document)
        assert store.store(record=record) == record
        assert store.store(record=record) == record
        assert len(store) == 1
        store.add_reference(witness_id=record.witness_id, run_id=RunId("run_a"))
        store.add_reference(witness_id=record.witness_id, run_id=RunId("run_b"))
        store.add_reference(witness_id=record.witness_id, run_id=RunId("run_a"))
        assert store.references(witness_id=record.witness_id) == [RunId("run_a"), RunId("run_b")]

    def test_store_reloads_from_disk(self, tmp_path: Path) -> None:
        document = normalize_witness_output(run_id=RunId("run_a"), output=WITNESS_OUTPUT)
        assert document is not None
        record = witness_record_of(document=document)
        store = WitnessStore(directory=tmp_path)
        store.store(record=record)
        store.add_reference(witness_id=record.witness_id, run_id=RunId("run_a"))
        reloaded = WitnessStore(directory=tmp_path)
        assert len(reloaded) == 1
        assert reloaded.get(witness_id=record.witness_id) == record
        assert reloaded.references(witness_id=record.witness_id) == [RunId("run_a")]


class TestNarration:
    def test_steps_are_narrated_by_the_mock_with_llm_provenance(self) -> None:
        model = import_pnml(text=PHILOSOPHES3_PNML)
        document = normalize_witness_output(run_id=RunId("run_a"), output=WITNESS_OUTPUT)
        assert document is not None
        narrations = narrate_steps(
            provider=DeterministicMockProvider(),
            model_hash=model.content_hash,
            steps=document.steps,
        )
        assert len(narrations) == 1
        assert narrations[0].step_index == 1
        assert narrations[0].provenance == "llm"
        assert "fourchette" in narrations[0].text

    def test_unscripted_transition_degrades_without_inventing_a_sentence(self) -> None:
        model = import_pnml(text=PHILOSOPHES3_PNML)
        steps = (WitnessStep(index=1, marking=["x"], fired="unknown_transition"),)
        narrations = narrate_steps(
            provider=DeterministicMockProvider(),
            model_hash=model.content_hash,
            steps=steps,
        )
        assert narrations == ()

    def test_guard_the_narration_corpus_never_carries_a_verdict(self) -> None:
        """§19.4 guard: no scripted narration contains a verdict term."""
        corpus_files = sorted(GOLDEN_ROOT.glob("*/narration.json"))
        assert corpus_files, "the shipped corpus must script narrations"
        for path in corpus_files:
            corpus = json.loads(path.read_text(encoding="utf-8"))
            for transition, sentence in corpus["narrations"].items():
                assert narration_carries_no_verdict(text=sentence), (
                    f"narration of {transition!r} in {path.name} carries a verdict term"
                )
        assert not narration_carries_no_verdict(text="The formula is satisfied.")
        assert not narration_carries_no_verdict(text="verdict: violated")


class TestWitnessApi:
    def test_run_witness_is_served_with_narrations_and_stored_once(self, tmp_path: Path) -> None:
        client, _, witness_store, model_id = build_client(tmp_path, WITNESS_OUTPUT)
        run_id = post_check(client, "deadlock", model_id, "EF DEADLOCK")
        wait_terminal(client, run_id)

        response = client.get(f"/v1/runs/{run_id}/witness", headers=AUTH_HEADERS)
        assert response.status_code == 200
        document = response.json()
        assert document["run_id"] == run_id
        assert document["kind"] == "counterexample"
        assert len(document["steps"]) == 2
        assert document["narrations"], "the mock LLM narrates the fired step"
        assert all(n["provenance"] == "llm" for n in document["narrations"])

        stored = client.get(f"/v1/witnesses/{document['witness_id']}", headers=AUTH_HEADERS)
        assert stored.status_code == 200
        assert stored.json()["referenced_by"] == [run_id]
        assert len(witness_store) == 1

    def test_shared_witness_is_stored_once_and_referenced_n_times(self, tmp_path: Path) -> None:
        client, _, witness_store, model_id = build_client(tmp_path, WITNESS_OUTPUT)
        first = post_check(client, "deadlock", model_id, "EF DEADLOCK")
        second = post_check(client, "deadlock", model_id, "EF (eat_1)")
        wait_terminal(client, first)
        wait_terminal(client, second)

        first_witness = client.get(f"/v1/runs/{first}/witness", headers=AUTH_HEADERS).json()
        second_witness = client.get(f"/v1/runs/{second}/witness", headers=AUTH_HEADERS).json()
        assert first_witness["witness_id"] == second_witness["witness_id"]
        assert len(witness_store) == 1
        stored = client.get(
            f"/v1/witnesses/{first_witness['witness_id']}", headers=AUTH_HEADERS
        ).json()
        assert sorted(stored["referenced_by"]) == sorted([first, second])

    def test_witness_404s_are_truthful(self, tmp_path: Path) -> None:
        client, _, _, model_id = build_client(tmp_path, {**WITNESS_OUTPUT, "witness": None})
        run_id = post_check(client, "deadlock", model_id, "EF DEADLOCK")
        wait_terminal(client, run_id)

        assert client.get("/v1/runs/run_nope/witness", headers=AUTH_HEADERS).status_code == 404
        response = client.get(f"/v1/runs/{run_id}/witness", headers=AUTH_HEADERS)
        assert response.status_code == 404
        assert response.json()["extensions"]["reason"] == "witness_not_found"
        assert client.get("/v1/witnesses/wit_nope", headers=AUTH_HEADERS).status_code == 404

    def test_ui_bundle_renders_the_step_reader(self, tmp_path: Path) -> None:
        client, _, _, _ = build_client(tmp_path, WITNESS_OUTPUT)
        script = fetch_ui_bundle(client)
        assert "/witness" in script
        assert "fired-transition" in script
        assert "witness-narration" in script
        assert "provenance" in script


_NAME = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]*", fullmatch=True)

STEP_STRATEGY = st.builds(
    WitnessStep,
    index=st.integers(min_value=0, max_value=50),
    marking=st.lists(_NAME, max_size=8),
    fired=st.one_of(st.none(), _NAME),
)


class TestWitnessIdProperty:
    @pytest.mark.property
    @settings(deadline=None)
    @given(
        steps=st.lists(STEP_STRATEGY, min_size=1, max_size=10),
        kind=st.sampled_from(["counterexample", "trace"]),
        cyclic=st.booleans(),
        back_edge=st.one_of(st.none(), st.integers(min_value=0, max_value=50)),
    )
    def test_property_identical_structure_yields_identical_witness_id(
        self, steps: list[WitnessStep], kind: str, cyclic: bool, back_edge: int | None
    ) -> None:
        """The dedup key is a pure function of the witness structure (spec §16)."""
        first = witness_id_of(kind=kind, cyclic=cyclic, back_edge=back_edge, steps=tuple(steps))
        again = witness_id_of(kind=kind, cyclic=cyclic, back_edge=back_edge, steps=tuple(steps))
        assert first == again

    @pytest.mark.property
    @settings(deadline=None)
    @given(
        marking=st.lists(_NAME, min_size=1, max_size=8),
        other=st.lists(_NAME, min_size=1, max_size=8),
    )
    def test_property_different_markings_yield_different_witness_ids(
        self, marking: list[str], other: list[str]
    ) -> None:
        """Distinct structures never collide on the content hash."""
        assume(marking != other)
        first = witness_id_of(
            kind="trace",
            cyclic=False,
            back_edge=None,
            steps=(WitnessStep(index=0, marking=marking, fired=None),),
        )
        second = witness_id_of(
            kind="trace",
            cyclic=False,
            back_edge=None,
            steps=(WitnessStep(index=0, marking=other, fired=None),),
        )
        assert first != second

    @pytest.mark.property
    @settings(deadline=None)
    @given(steps=st.lists(STEP_STRATEGY, min_size=1, max_size=10))
    def test_property_normalize_round_trip_keeps_the_witness_id(
        self, steps: list[WitnessStep]
    ) -> None:
        """Normalizing a witness twice yields the same document and id."""
        output = {
            "verdict": "violated",
            "witness": {
                "kind": "counterexample",
                "cyclic": False,
                "back_edge": None,
                "steps": [step.model_dump(mode="json") for step in steps],
            },
            "cost": {"tool_seconds": 0.1, "states_explored": 1},
            "technique": "dummy",
        }
        first = normalize_witness_output(run_id=RunId("run_a"), output=output)
        assert first is not None
        second = normalize_witness_output(run_id=RunId("run_b"), output=output)
        assert second is not None
        assert first.witness_id == second.witness_id
        assert first.steps == second.steps


class TestLogNarration:
    """The module's log events are part of the narration contract: pinned here (ADR-0005)."""

    def test_witness_store_and_normalization_events_are_pinned(self, tmp_path: Path) -> None:
        with structlog.testing.capture_logs() as logs:
            store = WitnessStore(directory=tmp_path)
            document = normalize_witness_output(run_id=RunId("run_a"), output=WITNESS_OUTPUT)
            assert document is not None
            record = witness_record_of(document=document)
            store.store(record=record)
            store.add_reference(witness_id=record.witness_id, run_id=RunId("run_a"))
        events = [event["event"] for event in logs]
        assert "witness_store_reloaded" in events
        assert "witness_normalized" in events
        assert "witness_stored" in events
        assert "witness_referenced" in events

    def test_narration_events_are_pinned(self) -> None:
        model = import_pnml(text=PHILOSOPHES3_PNML)
        document = normalize_witness_output(run_id=RunId("run_a"), output=WITNESS_OUTPUT)
        assert document is not None
        with structlog.testing.capture_logs() as logs:
            narrations = narrate_steps(
                provider=DeterministicMockProvider(),
                model_hash=model.content_hash,
                steps=document.steps,
            )
            narrate_steps(
                provider=DeterministicMockProvider(),
                model_hash=model.content_hash,
                steps=(WitnessStep(index=1, marking=["x"], fired="unknown_transition"),),
            )
        assert len(narrations) == 1
        events = [event["event"] for event in logs]
        assert "witness_narrated" in events
        assert "witness_narration_degraded" in events
        assert "llm_narration_not_scripted" in events

    def test_api_witness_events_are_pinned(self, tmp_path: Path) -> None:
        client, _, _, model_id = build_client(tmp_path, WITNESS_OUTPUT)
        run_id = post_check(client, "deadlock", model_id, "EF DEADLOCK")
        wait_terminal(client, run_id)
        with structlog.testing.capture_logs() as logs:
            response = client.get(f"/v1/runs/{run_id}/witness", headers=AUTH_HEADERS)
            assert response.status_code == 200
            witness_id = response.json()["witness_id"]
            stored = client.get(f"/v1/witnesses/{witness_id}", headers=AUTH_HEADERS)
            assert stored.status_code == 200
        events = [event["event"] for event in logs]
        assert "api_run_witness_read" in events
        assert "api_witness_read" in events

    def test_invalid_witness_output_is_narrated_as_an_error(self, tmp_path: Path) -> None:
        bad_output = {
            **WITNESS_OUTPUT,
            "witness": {"kind": "counterexample", "steps": [{"index": 0}]},
        }
        client, _, _, model_id = build_client(tmp_path, bad_output)
        with structlog.testing.capture_logs() as logs:
            run_id = post_check(client, "deadlock", model_id, "EF DEADLOCK")
            wait_terminal(client, run_id)
        events = [event["event"] for event in logs]
        assert "check_run_witness_invalid" in events
