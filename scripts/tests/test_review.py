"""Tests for the review bundle scripts/review.py (ADR-0008 H1)."""

from typing import TYPE_CHECKING

import pytest

from scripts import review
from scripts._shared import EXIT_CODE_CRITERIA_UNMET

if TYPE_CHECKING:
    from pathlib import Path


def _write_component(directory: Path, name: str, content: str) -> Path:
    """Write one bundle component file into the temporary directory."""
    component_path = directory / name
    component_path.write_text(content, encoding="utf-8")
    return component_path


def test_render_bundle_includes_engineering_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The engineering rules document sits between the rules and the ADRs."""
    rules_path = _write_component(tmp_path, name="rules.md", content="RULES")
    engineering_path = _write_component(
        tmp_path, name="engineering-rules.md", content="ENGINEERING"
    )
    monkeypatch.setattr(review, "RULES_DOCUMENT_PATH", rules_path)
    monkeypatch.setattr(review, "ENGINEERING_RULES_DOCUMENT_PATH", engineering_path)
    bundle = review.render_bundle(
        diff_text="DIFF",
        description_text="SPECIFICATION",
        decisions=[("adr 0008", "DECISION")],
    )
    assert bundle == "\n\n---\n\n".join(
        [
            "## RULES AND CHECKLIST\n\nRULES",
            "## ENGINEERING RULES AND CHECKLIST\n\nENGINEERING",
            "## DECISION - adr 0008\n\nDECISION",
            "## PULL REQUEST SPECIFICATION\n\nSPECIFICATION",
            "## DIFF UNDER REVIEW\n\nDIFF",
        ]
    )


def test_render_bundle_refuses_a_missing_engineering_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing engineering rules document fails loudly instead of skipping."""
    rules_path = _write_component(tmp_path, name="rules.md", content="RULES")
    monkeypatch.setattr(review, "RULES_DOCUMENT_PATH", rules_path)
    monkeypatch.setattr(review, "ENGINEERING_RULES_DOCUMENT_PATH", tmp_path / "missing.md")
    with pytest.raises(SystemExit) as exit_information:
        review.render_bundle(diff_text="DIFF", description_text="SPECIFICATION", decisions=[])
    assert exit_information.value.code == EXIT_CODE_CRITERIA_UNMET
