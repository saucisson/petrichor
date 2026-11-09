"""Guard tests on the golden corpus and the LLM layer (issue #8, spec §8, §15).

The LLM layer is forbidden to emit verdicts (spec §8, interdits): no
golden file of the corpus may contain a verdict, and no module of
``petrichor.llm`` may import the engines package — the only producer of
verdict values (spec §19.4).
"""

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING

from petrichor.llm import DEFAULT_GOLDEN_ROOT

if TYPE_CHECKING:
    from petrichor.petri_canonical import JsonValue

LLM_PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "petrichor" / "llm"
VERDICT_KEY = "verdict"


def _assert_no_verdict_key(payload: JsonValue, *, path: str) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            assert key.lower() != VERDICT_KEY, f"verdict key {key!r} at {path}"
            _assert_no_verdict_key(value, path=f"{path}.{key}")
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            _assert_no_verdict_key(item, path=f"{path}[{index}]")


class TestGoldenCorpusContainsNoVerdict:
    def test_corpus_is_not_empty(self) -> None:
        golden_files = list(DEFAULT_GOLDEN_ROOT.rglob("*.json"))
        assert len(golden_files) >= 3

    def test_no_golden_file_contains_a_verdict(self) -> None:
        for golden_file in DEFAULT_GOLDEN_ROOT.rglob("*.json"):
            payload: JsonValue = json.loads(golden_file.read_text(encoding="utf-8"))
            _assert_no_verdict_key(payload, path=golden_file.name)


class TestLlmLayerNeverTouchesVerdicts:
    def test_no_llm_module_imports_the_engines_package(self) -> None:
        modules = sorted(LLM_PACKAGE_ROOT.rglob("*.py"))
        assert modules
        for module in modules:
            tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    assert all(
                        not name.name.startswith("petrichor.engines") for name in node.names
                    ), f"{module} imports the engines package"
                elif isinstance(node, ast.ImportFrom):
                    assert node.module is None or not node.module.startswith("petrichor.engines"), (
                        f"{module} imports the engines package"
                    )
