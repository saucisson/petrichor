"""Guard test: only the `engines/` package may produce verdict values (spec §19.4).

The verrou de verdict is mechanical: this test parses every product module
under ``src/petrichor`` and fails if any module outside ``engines/``
imports the engines package or defines a verdict type. The positive
control makes the test non-vacuous: the verdict types must exist inside
``engines/verdict.py``.
"""

import ast
from pathlib import Path

PRODUCT_ROOT = Path(__file__).parents[1] / "src" / "petrichor"
ENGINES_DIR = PRODUCT_ROOT / "engines"
VERDICT_TYPES = {"Verdict", "VerdictValue", "Witness", "PathWitness", "CycleWitness"}
ENGINES_PACKAGE_PREFIX = "petrichor.engines"


def _product_files() -> list[Path]:
    """Every Python module of the product package."""
    return sorted(PRODUCT_ROOT.rglob("*.py"))


def _outside_engines(path: Path) -> bool:
    """Whether a module lives outside the engines package."""
    return ENGINES_DIR not in path.parents


def _imported_modules(tree: ast.AST) -> list[str]:
    """Every module name imported by a syntax tree."""
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def _defined_classes(tree: ast.AST) -> list[str]:
    """Every class name defined by a syntax tree."""
    return [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]


def test_verdict_types_exist_inside_engines_verdict() -> None:
    """Positive control: the verdict contract is defined in engines/verdict.py."""
    tree = ast.parse((ENGINES_DIR / "verdict.py").read_text(encoding="utf-8"))
    type_alias_names = {node.name.id for node in ast.walk(tree) if isinstance(node, ast.TypeAlias)}
    assert set(_defined_classes(tree)) | type_alias_names >= VERDICT_TYPES


def test_no_module_outside_engines_defines_a_verdict_type() -> None:
    """No module outside engines/ may define a verdict type."""
    offenders = [
        str(path)
        for path in _product_files()
        if _outside_engines(path)
        for name in _defined_classes(ast.parse(path.read_text(encoding="utf-8")))
        if name in VERDICT_TYPES
    ]
    assert offenders == []


def test_no_module_outside_engines_imports_the_engines_package() -> None:
    """No module outside engines/ may import petrichor.engines (verrou de verdict)."""
    offenders = [
        f"{path}: imports {module}"
        for path in _product_files()
        if _outside_engines(path)
        for module in _imported_modules(ast.parse(path.read_text(encoding="utf-8")))
        if module.startswith(ENGINES_PACKAGE_PREFIX)
    ]
    assert offenders == []
