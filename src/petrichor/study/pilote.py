"""Frozen materials of the pilote study, version v1 (spec §20.2, §20.10, issue #40).

The pilote runs the full chain on one real model outside the Philosophes
corpus: **DrinkVendingMachine-PT-02** (MCC benchmark, P/T unfolded instance
02). Everything the study relies on is frozen here, dated and pinned by
content hash: the model, the formula catalog, the coverage grid, the
pre-registration (go/no-go thresholds + analysis plan) and the session
budget cap (3600 s of tool time, §20.2 - identical for every condition).

The golden corpus carries the dated pre-registration trace
(``llm/golden/preregistration.json``, spec §20 intro / §15).
"""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from petrichor.elicitation import CoverageGridEntry, ElicitationCatalog, load_catalog
from petrichor.petri_canonical import ContentHash

logger = structlog.get_logger(__name__)

STUDY_ID = "pilote"
STUDY_VERSION = "v1"
PREREGISTRATION_DATE = date(2026, 11, 18)
SESSION_BUDGET_SECONDS = 3600.0
"""Session cap of the study in tool seconds (spec §20.2): measured, never exceeded."""

MATERIALS_DIR = Path(__file__).parent / STUDY_ID / STUDY_VERSION
CONFIG_FILE_NAME = "config.json"
MODEL_FILE_NAME = "model.pnml"
CATALOG_FILE_NAME = "catalogue-formules.v1.json"
GRID_FILE_NAME = "grille-couverture.v1.json"
PREREGISTRATION_FILE_NAME = "preregistre.md"
GOLDEN_PREREGISTRATION = Path(__file__).parent.parent / "llm" / "golden" / "preregistration.json"
"""The dated pre-registration trace carried by the golden corpus (spec §15, §20)."""

HASH_ALGORITHM = "sha256"


class StudyMaterialError(Exception):
    """A frozen study material is missing, invalid, or its content hash drifted."""


class StudyModelProvenance(BaseModel):
    """Where the frozen study model comes from (spec §20.9 reproducibility)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sha256: ContentHash
    source: str = Field(min_length=1)
    source_commit: str = Field(min_length=1)
    retrieved_at: date
    origin: str = Field(min_length=1)


class StudyConfig(BaseModel):
    """The frozen study config: identity, date, budget cap, pinned material hashes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    study_id: str = Field(min_length=1)
    study_version: Literal["v1"]
    preregistered_at: date
    session_budget_seconds: float = Field(gt=0)
    model: StudyModelProvenance
    materials: dict[str, ContentHash]
    """Frozen material file name -> pinned content hash."""


class GoldenPreregistration(BaseModel):
    """The dated pre-registration trace carried by the golden corpus (spec §15, §20)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    study_id: str = Field(min_length=1)
    study_version: Literal["v1"]
    preregistered_at: date
    session_budget_seconds: float = Field(gt=0)
    materials: dict[str, ContentHash]
    materials_dir: str = Field(min_length=1)
    note: str = Field(min_length=1)


def _read_model[ModelT: BaseModel](*, path: Path, model_type: type[ModelT]) -> ModelT:
    """Read one JSON study material into its typed model."""
    try:
        return model_type.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except ValidationError as error:
        raise StudyMaterialError(f"Invalid study material {path.name}: {error}") from error


def _sha256_of(*, path: Path) -> ContentHash:
    """The ``sha256:<hex>`` content hash of one file."""
    return ContentHash(f"{HASH_ALGORITHM}:{hashlib.sha256(path.read_bytes()).hexdigest()}")


def verify_materials(*, config: StudyConfig | None = None) -> dict[str, ContentHash]:
    """Verify every frozen material against the hashes pinned in the config.

    Returns the verified ``{file_name: hash}`` map; raises
    ``StudyMaterialError`` on any missing file or hash mismatch - a frozen
    material that drifted is a new study version, never a silent edit
    (spec §20.2).
    """
    if config is None:
        config = _read_model(path=MATERIALS_DIR / CONFIG_FILE_NAME, model_type=StudyConfig)
    verified: dict[str, ContentHash] = {}
    for file_name, expected_hash in config.materials.items():
        path = MATERIALS_DIR / file_name
        if not path.is_file():
            raise StudyMaterialError(f"Frozen study material {file_name!r} is missing.")
        actual_hash = _sha256_of(path=path)
        if actual_hash != expected_hash:
            raise StudyMaterialError(
                f"Frozen study material {file_name!r} drifted: expected {expected_hash},"
                f" got {actual_hash} (a change is a new study version, spec §20.2)."
            )
        verified[file_name] = actual_hash
    return verified


def load_config() -> StudyConfig:
    """Load and hash-verify the frozen study config (v1)."""
    config = _read_model(path=MATERIALS_DIR / CONFIG_FILE_NAME, model_type=StudyConfig)
    verify_materials(config=config)
    logger.debug(
        "study_materials_loaded",
        study_id=config.study_id,
        study_version=config.study_version,
        preregistered_at=config.preregistered_at.isoformat(),
        session_budget_seconds=config.session_budget_seconds,
        materials=sorted(config.materials),
    )
    return config


def load_model_text() -> str:
    """The PNML text of the frozen study model."""
    return (MATERIALS_DIR / MODEL_FILE_NAME).read_text(encoding="utf-8")


def load_frozen_catalog() -> ElicitationCatalog:
    """The frozen formula catalog (roles x guidewords templates), validated."""
    return load_catalog(path=MATERIALS_DIR / CATALOG_FILE_NAME)


def load_frozen_grid() -> tuple[CoverageGridEntry, ...]:
    """The frozen coverage grid (coverage groups, spec §14.6)."""
    return load_frozen_catalog().coverage_grid


def load_golden_preregistration() -> GoldenPreregistration:
    """The dated pre-registration trace carried by the golden corpus."""
    return _read_model(path=GOLDEN_PREREGISTRATION, model_type=GoldenPreregistration)
