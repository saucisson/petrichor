"""Tests of the frozen pilote study materials, version v1 (spec §20.2, §20.10, issue #40).

The pilote runs on one real model outside the Philosophes corpus
(DrinkVendingMachine-PT-02, MCC benchmark). Everything is frozen, dated and
pinned by content hash BEFORE the first session: model, formula catalog,
coverage grid, pre-registration (thresholds + analysis plan) and the session
budget cap (3600 s of tool time). The golden corpus carries the dated
pre-registration trace (spec §15, §20 intro).
"""

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from petrichor.api.app import create_app
from petrichor.api.settings import ApiSettings
from petrichor.model_store import ModelStore
from petrichor.petri_canonical import compute_content_hash
from petrichor.pnml_import import import_pnml
from petrichor.run_store import RunStore
from petrichor.session_store import SessionStore
from petrichor.study import pilote

TEST_API_KEY = "test-key"
AUTH_HEADERS = {"X-API-Key": TEST_API_KEY}
JSON_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/json"}
PNML_HEADERS = {**AUTH_HEADERS, "Content-Type": "application/pnml+xml"}

SHIPPED_CATALOG = Path(pilote.__file__).parent.parent / "elicitation_catalog.v1.json"
EXPECTED_MODEL_HASH = "sha256:10c2c09236fe45d21e203336943e5f9be4d8d2ad648836c6918bbfc3e868946c"
EXPECTED_MODEL_ID = "mdl_10c2c092"


class FakeRunner:
    """A runner that never runs: the study-prep tests need no engine."""

    tool_version = "0.0.0-none"

    def run(self, *, question: object, timeout_seconds: int) -> object:
        raise AssertionError("no engine run is expected in the study-prep tests")


def build_client(tmp_path: Path) -> httpx.Client:
    """Wire the app with fresh stores and a runner that never runs."""
    settings = ApiSettings(api_key=SecretStr(TEST_API_KEY))
    app = create_app(
        settings=settings,
        store=ModelStore(),
        run_store=RunStore(),
        runner=FakeRunner(),
        session_store=SessionStore(directory=tmp_path / "sessions"),
    )
    return httpx.Client(transport=httpx.WSGITransport(app=app), base_url="http://testserver")


class TestFrozenModel:
    def test_model_imports_as_pt_with_pinned_shape_and_hash(self) -> None:
        model = import_pnml(text=pilote.load_model_text())
        assert model.formalism == "pt"
        assert len(model.places) == 24
        assert len(model.transitions) == 72
        assert len(model.arcs) == 440
        assert model.content_hash == EXPECTED_MODEL_HASH
        assert model.content_hash == compute_content_hash(model=model)

    def test_model_imports_through_the_api_with_the_hash_derived_id(self, tmp_path: Path) -> None:
        client = build_client(tmp_path)
        response = client.post("/v1/models", content=pilote.load_model_text(), headers=PNML_HEADERS)
        assert response.status_code == 201
        assert response.json()["id"] == EXPECTED_MODEL_ID

    @pytest.mark.property
    @settings(deadline=None, max_examples=10)
    @given(st.datetimes(timezones=st.just(UTC)))
    def test_content_hash_does_not_depend_on_the_import_clock(self, imported_at: datetime) -> None:
        """The frozen model's hash is stable whatever the import clock says."""
        model = import_pnml(text=pilote.load_model_text(), clock=lambda: imported_at)
        assert model.content_hash == EXPECTED_MODEL_HASH


class TestFrozenMaterials:
    def test_frozen_catalog_is_byte_identical_to_the_shipped_catalog(self) -> None:
        frozen = pilote.MATERIALS_DIR / pilote.CATALOG_FILE_NAME
        assert frozen.read_bytes() == SHIPPED_CATALOG.read_bytes()
        shipped = json.loads(SHIPPED_CATALOG.read_text("utf-8"))
        assert pilote.load_frozen_catalog().model_dump(mode="json") == shipped

    def test_frozen_grid_is_the_shipped_coverage_grid(self) -> None:
        shipped = json.loads(SHIPPED_CATALOG.read_text("utf-8"))
        grid = [entry.model_dump(mode="json") for entry in pilote.load_frozen_grid()]
        assert grid == shipped["coverage_grid"]
        assert len(grid) == 7

    def test_config_pins_version_date_and_budget_cap(self) -> None:
        config = pilote.load_config()
        assert config.study_id == "pilote"
        assert config.study_version == "v1"
        assert config.preregistered_at == date(2026, 11, 18)
        assert config.session_budget_seconds == 3600.0
        assert pilote.SESSION_BUDGET_SECONDS == 3600.0
        assert date(2026, 11, 18) == pilote.PREREGISTRATION_DATE

    def test_verify_materials_returns_the_pinned_hashes(self) -> None:
        config = pilote.load_config()
        assert pilote.verify_materials(config=config) == config.materials

    def test_drifted_material_is_a_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A frozen material whose hash drifted raises, never silently passes."""
        config = pilote.load_config()
        drifted = tmp_path / "v1"
        drifted.mkdir()
        (drifted / pilote.CONFIG_FILE_NAME).write_text(config.model_dump_json(), encoding="utf-8")
        (drifted / pilote.MODEL_FILE_NAME).write_text("tampered", encoding="utf-8")
        monkeypatch.setattr(pilote, "MATERIALS_DIR", drifted)
        with pytest.raises(pilote.StudyMaterialError, match="drifted"):
            pilote.verify_materials()

    def test_invalid_material_is_a_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A material that does not validate against its schema raises, never passes."""
        invalid = tmp_path / "v1"
        invalid.mkdir()
        (invalid / pilote.CONFIG_FILE_NAME).write_text('{"study_id": 42}', encoding="utf-8")
        monkeypatch.setattr(pilote, "MATERIALS_DIR", invalid)
        with pytest.raises(pilote.StudyMaterialError, match="Invalid study material"):
            pilote.load_config()

    def test_malformed_json_material_is_a_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Malformed JSON raises StudyMaterialError, never a raw JSONDecodeError."""
        invalid = tmp_path / "v1"
        invalid.mkdir()
        (invalid / pilote.CONFIG_FILE_NAME).write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(pilote, "MATERIALS_DIR", invalid)
        with pytest.raises(pilote.StudyMaterialError, match="Unreadable study material"):
            pilote.load_config()

    def test_missing_config_is_a_typed_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing config.json raises StudyMaterialError, not FileNotFoundError."""
        monkeypatch.setattr(pilote, "MATERIALS_DIR", tmp_path / "empty")
        with pytest.raises(pilote.StudyMaterialError, match="Unreadable study material"):
            pilote.load_config()
        with pytest.raises(pilote.StudyMaterialError, match="Unreadable study material"):
            pilote.load_model_text()
        with pytest.raises(pilote.StudyMaterialError, match="Unreadable study material"):
            pilote.load_preregistration_text()

    def test_preregistration_document_is_hash_pinned(self) -> None:
        """The pre-registration document itself is a pinned frozen material."""
        config = pilote.load_config()
        assert pilote.PREREGISTRATION_FILE_NAME in config.materials
        text = pilote.load_preregistration_text()
        assert "Pré-enregistrement" in text
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert config.materials[pilote.PREREGISTRATION_FILE_NAME] == f"sha256:{digest}"

    def test_materials_loaded_event_is_pinned(self) -> None:
        with structlog.testing.capture_logs() as logs:
            pilote.load_config()
        events = [event for event in logs if event["event"] == "study_materials_loaded"]
        assert len(events) == 1
        assert events[0]["study_id"] == "pilote"
        assert events[0]["study_version"] == "v1"
        assert events[0]["preregistered_at"] == "2026-11-18"
        assert events[0]["session_budget_seconds"] == 3600.0


class TestGoldenPreregistrationTrace:
    def test_golden_corpus_carries_the_dated_trace_matching_the_frozen_materials(self) -> None:
        trace = pilote.load_golden_preregistration()
        assert trace.study_id == "pilote"
        assert trace.study_version == "v1"
        assert trace.preregistered_at == date(2026, 11, 18)
        assert trace.session_budget_seconds == 3600.0
        assert trace.materials == pilote.verify_materials(config=pilote.load_config())

    def test_trace_hashes_are_real_sha256_of_the_files(self) -> None:
        trace = pilote.load_golden_preregistration()
        for file_name, pinned in trace.materials.items():
            digest = hashlib.sha256((pilote.MATERIALS_DIR / file_name).read_bytes()).hexdigest()
            assert pinned == f"sha256:{digest}"


class TestStudyBudgetCap:
    def test_session_created_with_the_study_cap_exposes_it(self, tmp_path: Path) -> None:
        """The 3600 s cap of §20.2 is the one the pilote sessions are created with."""
        client = build_client(tmp_path)
        imported = client.post("/v1/models", content=pilote.load_model_text(), headers=PNML_HEADERS)
        assert imported.status_code == 201
        created = client.post(
            "/v1/sessions",
            json={
                "model_id": imported.json()["id"],
                "budget_seconds": pilote.SESSION_BUDGET_SECONDS,
            },
            headers=JSON_HEADERS,
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        budget = client.get(f"/v1/sessions/{session_id}/budget", headers=AUTH_HEADERS).json()
        assert budget["budget_seconds"] == 3600.0
        assert budget["consumed_seconds"] == 0.0
        assert budget["remaining_seconds"] == 3600.0
