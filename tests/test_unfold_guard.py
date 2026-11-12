"""Guard tests for the unfold module (spec §18.3, §19.4, issue #27).

AST scans that no module outside ``petrichor.engines`` imports the unfold
module directly, and that the module exposes the certified entry point and
its typed errors — an unfold without a certificate is refused by
construction.
"""

import ast
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "petrichor"
ENGINES_PACKAGE = SRC_ROOT / "engines"
UNFOLD_MODULE_NAME = "unfold"
"""The module token the import guard watches for."""
PRUNED_DIR_NAMES = frozenset({"__pycache__", "tests"})
"""Directories never scanned: tests/ legitimately imports the module under test."""


def _python_files(*, root: Path) -> list[Path]:
    """Every Python file under ``root``, pruning dot-dirs, tests/ and caches."""
    collected: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and name not in PRUNED_DIR_NAMES
        )
        collected.extend(Path(dirpath, name) for name in filenames if name.endswith(".py"))
    return sorted(collected)


def _imports_unfold(*, tree: ast.AST, path: Path) -> list[str]:
    """Return the line numbers where the AST imports the unfold module."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module_hit = node.module is not None and UNFOLD_MODULE_NAME in node.module
            alias_hit = any(alias.name == UNFOLD_MODULE_NAME for alias in node.names)
            if module_hit or alias_hit:
                hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if UNFOLD_MODULE_NAME in alias.name:
                    hits.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return hits


def test_no_module_outside_engines_imports_unfold() -> None:
    """Only petrichor.engines may import the unfold module (verdict-lock style)."""
    offenders: list[str] = []
    for path in _python_files(root=REPO_ROOT):
        if ENGINES_PACKAGE in path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        offenders.extend(_imports_unfold(tree=tree, path=path))
    assert offenders == [], f"unfold imported outside engines/: {offenders}"


def test_unfold_module_defines_certified_entry_point() -> None:
    """The unfold module exposes the certified entry point and typed errors."""
    path = ENGINES_PACKAGE / "unfold.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    class_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    assert "unfold_symmetric_to_pt" in function_names
    assert "cross_check_unfold_against_oracle" in function_names
    assert "NotSymmetricError" in class_names
    assert "NegativeArcAnnotationError" in class_names
    assert "UnfoldCertificate" in class_names


def test_unfold_module_rejects_negative_arcs() -> None:
    """The unfold module must raise NegativeArcAnnotationError on negative arcs.

    AST scan across the whole module: at least 1 ``raise
    NegativeArcAnnotationError`` site must exist. The threshold catches
    wholesale removal of the §18.5 guard.
    """
    path = ENGINES_PACKAGE / "unfold.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    raises_negative = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and node.exc is not None:
            exc = node.exc
            if (
                isinstance(exc, ast.Call)
                and isinstance(exc.func, ast.Name)
                and exc.func.id == "NegativeArcAnnotationError"
            ) or (isinstance(exc, ast.Name) and exc.id == "NegativeArcAnnotationError"):
                raises_negative += 1
    assert raises_negative >= 1, (
        f"expected >= 1 raise NegativeArcAnnotationError site, found {raises_negative}"
    )
