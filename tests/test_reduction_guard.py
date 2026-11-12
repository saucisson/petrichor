"""Guard tests for the reduction module (spec §18.2, §19.4, issue #24).

AST scans that no module outside ``petrichor.engines`` imports the reduction
module directly, and that every reduction call site goes through the
certified entry point (``reduce_marking_token_elimination``) — a reduction
without a certificate is refused by construction.
"""

import ast
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from philosophes3 import build_philosophes3

from petrichor.engines.reduction import reduce_marking_token_elimination
from petrichor.petri_canonical import ElementName

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "petrichor"
ENGINES_PACKAGE = SRC_ROOT / "engines"


def _python_files(*, root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def test_no_module_outside_engines_imports_reduction() -> None:
    """Only petrichor.engines may import the reduction module (verdict-lock style)."""
    offenders: list[str] = []
    for path in _python_files(root=SRC_ROOT):
        if ENGINES_PACKAGE in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "reduction" in node.module:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "reduction" in alias.name:
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], f"reduction imported outside engines/: {offenders}"


def test_reduction_module_defines_certified_entry_point() -> None:
    """The reduction module exposes the certified entry point and typed errors."""
    path = ENGINES_PACKAGE / "reduction.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "reduce_marking_token_elimination" in function_names
    assert "UncertifiedReductionError" in class_names
    assert "ReductionCertificate" in class_names
    assert "replay_witness_on_original" in function_names


def test_no_silent_reduction_call_without_certificate() -> None:
    """Every call to reduce_marking_token_elimination in tests must assert a certificate."""
    test_path = REPO_ROOT / "tests" / "test_reduction.py"
    source = test_path.read_text(encoding="utf-8")
    assert "certificate" in source
    assert "UncertifiedReductionError" in source


PLACE_NAMES = [
    "think_1",
    "think_2",
    "think_3",
    "hasL_1",
    "hasL_2",
    "hasL_3",
    "eat_1",
    "eat_2",
    "eat_3",
    "fork_1",
    "fork_2",
    "fork_3",
]


@pytest.mark.property
@given(
    reduced_marking=st.dictionaries(
        keys=st.sampled_from(PLACE_NAMES),
        values=st.integers(min_value=0, max_value=3),
        max_size=5,
    )
)
@settings(max_examples=50)
def test_reduction_invariants_hold_on_arbitrary_markings(
    reduced_marking: dict[str, int],
) -> None:
    """Property: Token Elimination never produces a negative marking or drops structure."""
    model = build_philosophes3()
    typed_marking = {ElementName(name): marking for name, marking in reduced_marking.items()}
    reduced = reduce_marking_token_elimination(model=model, reduced_marking=typed_marking)
    assert len(reduced.model.places) == len(model.places)
    assert len(reduced.model.transitions) == len(model.transitions)
    assert len(reduced.model.arcs) == len(model.arcs)
    for place in reduced.model.places:
        assert place.initial_marking >= 0
    assert reduced.certificate is not None
    assert reduced.certificate.element_map is not None
    # Identity map: every original element maps to itself
    for place in model.places:
        assert reduced.certificate.element_map.to_original(name=place.name) == place.name
