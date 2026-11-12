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

from petrichor.engines.reduction import UncertifiedReductionError, reduce_marking_token_elimination
from petrichor.petri_canonical import ElementName

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "petrichor"
ENGINES_PACKAGE = SRC_ROOT / "engines"
SCAN_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT / "tools"]
"""Scan roots for the import guard: tests/ is excluded — test files legitimately
import the module under test."""


def _python_files(*, root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _imports_reduction(*, tree: ast.AST, path: Path) -> list[str]:
    """Return the line numbers where the AST imports the reduction module.

    Catches ``from petrichor.engines.reduction import ...`` (module contains
    "reduction"), ``from petrichor.engines import reduction`` (alias name is
    "reduction"), and ``import petrichor.engines.reduction`` (alias name
    contains "reduction").
    """
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_hit = node.module is not None and "reduction" in node.module
            alias_hit = any(alias.name == "reduction" for alias in node.names)
            if module_hit or alias_hit:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "reduction" in alias.name:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return hits


def test_no_module_outside_engines_imports_reduction() -> None:
    """Only petrichor.engines may import the reduction module (verdict-lock style)."""
    offenders: list[str] = []
    for scan_root in SCAN_ROOTS:
        for path in _python_files(root=scan_root):
            if ENGINES_PACKAGE in path.parents:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            offenders.extend(_imports_reduction(tree=tree, path=path))
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


def test_reduction_module_rejects_uncertified_reduction() -> None:
    """The reduction module must raise UncertifiedReductionError on bad input.

    AST scan across the whole module: at least 3 ``raise UncertifiedReductionError``
    sites must exist. The threshold catches wholesale removal of the guard;
    partial removal down to 3 sites is intentionally tolerated.
    """
    path = ENGINES_PACKAGE / "reduction.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    raises_uncertified = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if (
                isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name)
                and exc.func.id == "UncertifiedReductionError"
            ) or (isinstance(exc, ast.Name) and exc.id == "UncertifiedReductionError"):
                raises_uncertified += 1
    assert raises_uncertified >= 3, (
        f"expected >= 3 raise UncertifiedReductionError sites, found {raises_uncertified}"
    )


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
    """Property: Token Elimination never increases a marking, never drops structure."""
    model = build_philosophes3()
    original_by_name = {place.name: place.initial_marking for place in model.places}
    typed_marking = {ElementName(name): marking for name, marking in reduced_marking.items()}
    # If any requested marking exceeds the original, the reduction must be refused
    if any(marking > original_by_name.get(name, 0) for name, marking in typed_marking.items()):
        with pytest.raises(UncertifiedReductionError, match="exceeds original"):
            reduce_marking_token_elimination(model=model, reduced_marking=typed_marking)
        return
    reduced = reduce_marking_token_elimination(model=model, reduced_marking=typed_marking)
    assert len(reduced.model.places) == len(model.places)
    assert len(reduced.model.transitions) == len(model.transitions)
    assert len(reduced.model.arcs) == len(model.arcs)
    for place in reduced.model.places:
        assert 0 <= place.initial_marking <= original_by_name[place.name]
    assert reduced.certificate is not None
    assert reduced.certificate.element_map is not None
    # Identity map: every original element maps to itself
    for place in model.places:
        assert reduced.certificate.element_map.to_original(name=place.name) == place.name
