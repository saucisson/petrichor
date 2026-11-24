"""Tests of the ITS-Tools wrapper CLI (issue #21, spec §17.2, §17.4, §18.1).

``its`` inside the engine image is ``petrichor.engines.its_tools_cli``: the
pinned upstream CLI speaks MCC examinations (``-pnfolder/-examination`` over
DAG XML property files), not the server's textual query contract. These
tests pin the translation of the §17.2 formula dialect and the wrapper
contract against a fake binary; the real pinned capture was exercised in the
pre-registration smoke on the pilot DVM model (``EF deadlock`` FALSE,
``EF (ready_1 >= 1)`` TRUE, ``AG`` conservation TRUE, LTL stutter-test FALSE).
"""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.engines.its_tools_cli import (
    MAYBE_LINE,
    PROJECTED_QUERY_LINE,
    REDUCED_MODEL_LINE,
    FormulaError,
    WrapperError,
    dag_to_text,
    run,
    translate_formula,
)

if TYPE_CHECKING:
    from pathlib import Path

MODEL_BYTES = b'<pnml><net id="dvm"/></pnml>'

FAKE_BINARY = """#!/usr/bin/env python3
import os
import sys

# Record the argv, the laid-out model, and the examination property file:
# the examination folder is a scratch directory deleted upon return.
with open(os.environ["ITS_FAKE_ARGS"], "w", encoding="utf-8") as f:
    f.write("\\n".join(sys.argv))
    folder = sys.argv[sys.argv.index("-pnfolder") + 1]
    examination = sys.argv[sys.argv.index("-examination") + 1]
    model = open(f"{folder}/model.pnml", "rb").read().decode()
    f.write(f"\\n=== model ===\\n{model}")
    with open(f"{folder}/{examination}.xml", encoding="utf-8") as source:
        f.write(f"\\n=== property ===\\n{source.read()}")
mode = os.environ["ITS_FAKE_MODE"]
if mode == "fail":
    sys.exit(3)
print(f"examining {examination} in {os.path.basename(folder)}")
if mode == "undecided" and "--reduce" in sys.argv:
    kind = sys.argv[-1]
    with open(f"{folder}/model.1234.{kind}.pnml", "wb") as reduced:
        reduced.write(b"<pnml-reduced/>\\n")
    with open(f"{folder}/{examination}.xml", encoding="utf-8") as source:
        projected = source.read()
    with open(f"{folder}/{examination}.1234.{kind}.xml", "w", encoding="utf-8") as out:
        out.write(projected)
    sys.exit(0)
print("FORMULA q TRUE TECHNIQUES SEQUENTIAL_PROCESSES")
"""

_PLACES = ("ready_1", "wait_2", "productSlots_1", "theOptions")
_OPS = ("<", "<=", ">", ">=", "==", "!=")
_CTL_PREFIXES = ("EF ", "AG ", "AF ", "EX ", "EG ", "AX ")


def _fake_home(tmp_path: Path, name: str = "its-tools-native") -> Path:
    """A fake pinned product holding one executable under test."""
    home = tmp_path / "home"
    home.mkdir()
    binary = home / name
    binary.write_text(FAKE_BINARY, encoding="utf-8")
    binary.chmod(stat.S_IRWXU)
    return home


def _inputs(tmp_path: Path, query: str) -> tuple[Path, Path, list[str]]:
    """A model file and a query file, plus the server-contract argv."""
    model = tmp_path / "model.pnml"
    model.write_bytes(MODEL_BYTES)
    query_file = tmp_path / "query.props"
    query_file.write_text(query + "\n", encoding="utf-8")
    return model, query_file, [str(model), "-q", str(query_file)]


def _fake_record(tmp_path: Path) -> tuple[list[str], str, str]:
    """What the fake binary saw: (argv, laid-out model, examination property)."""
    raw = (tmp_path / "fake-args.txt").read_text(encoding="utf-8")
    argv, _, rest = raw.partition("=== model ===\n")
    model, _, property_xml = rest.partition("=== property ===\n")
    return argv.splitlines(), model.strip("\n"), property_xml


# --- Translation: classification and DAG emission ---


@pytest.mark.parametrize("query", ["EF deadlock", "ef DEADLOCK", "EF  deadlock "])
def test_translate_deadlock_sentinel_maps_to_reachability_deadlock(query: str) -> None:
    """The deadlock sentinel becomes the fixed ReachabilityDeadlock property."""
    examination, dag = translate_formula(query)
    assert examination == "ReachabilityDeadlock"
    assert "<exists-path><finally><deadlock/></finally></exists-path>" in dag


def test_translate_state_formula_becomes_reachability() -> None:
    """A bare state formula is read as reachability, like the MCC examinations."""
    examination, dag = translate_formula("wait_1 >= 1")
    assert examination == "ReachabilityCardinality"
    assert "<exists-path><finally><integer-ge>" in dag
    assert "<tokens-count><place>wait_1</place></tokens-count>" in dag
    assert "<integer-constant>1</integer-constant></integer-ge>" in dag


def test_translate_ctl_cardinality() -> None:
    """A CTL path quantifier (nested or not) decides CTLCardinality."""
    examination, dag = translate_formula("AG EF (productSlots_1 + productSlots_2 <= 2)")
    assert examination == "CTLCardinality"
    assert "<all-paths><globally><exists-path><finally><integer-le>" in dag.replace("\n", "")
    tokens = (
        "<tokens-count><place>productSlots_1</place><place>productSlots_2</place></tokens-count>"
    )
    assert tokens in dag


def test_translate_implication_desugars_to_negation_disjunction() -> None:
    """The DAG has no implication tag; ``->`` desugars upstream of the output."""
    _, dag = translate_formula("AG (wait_1 == 1 -> AF wait_1 == 0)")
    assert "<disjunction><negation>" in dag.replace("\n", "")
    assert "<all-paths><finally>" in dag.replace("\n", "")
    assert "->" not in dag


def test_translate_ltl_wraps_in_universal_path() -> None:
    """A temporal-only formula decides LTLCardinality under an implicit A."""
    examination, dag = translate_formula("G (optionSlots_1 >= 1 -> F ready_2 >= 1)")
    assert examination == "LTLCardinality"
    flat = dag.replace("\n", "")
    assert "<all-paths><globally>" in flat
    assert "<finally>" in flat


def test_translate_ctl_until_brackets() -> None:
    """The A[.. U ..] quantifier renders before/reach children in order."""
    examination, dag = translate_formula("A [ wait_1 >= 1 U ready_1 >= 1 ]")
    assert examination == "CTLCardinality"
    flat = dag.replace("\n", "")
    assert "<all-paths><until><before>" in flat
    assert "<place>wait_1</place>" in dag
    assert "<reach>" in flat


@pytest.mark.parametrize(
    "query",
    [
        "EF (p1 + 3 <= 2)",  # a side mixes a sum and a constant
        "AG (wait_1 <= 1) or",  # trailing garbage
        "AG deadlock",  # the sentinel is only supported as a whole formula
        "EG G (p1 <= 1)",  # LTL operator under a CTL path quantifier
        "G EF (p1 <= 1)",  # CTL path quantifier under an LTL operator
    ],
)
def test_translate_rejects_out_of_dialect_formulas(query: str) -> None:
    """Out-of-dialect formulas fail closed instead of mistranslating."""
    with pytest.raises(FormulaError):
        translate_formula(query)


def test_dag_to_text_round_trips_the_projected_dialect() -> None:
    """The reducer reads the projected property back as text, never XML."""
    assert dag_to_text(translate_formula("EF (ready_1 >= 1)")[1]) == "EF (ready_1 >= 1)"
    assert dag_to_text(translate_formula("EF (1 <= wait_1)")[1]) == "EF (1 <= wait_1)"
    assert (
        dag_to_text(translate_formula("EF (productSlots_1 + productSlots_2 <= 2)")[1])
        == "EF (productSlots_1 + productSlots_2 <= 2)"
    )
    assert dag_to_text(translate_formula("AG (wait_1 == 1 -> AF wait_1 == 0)")[1]) == (
        "AG ((not (wait_1 == 1)) or (AF (wait_1 == 0)))"
    )


@given(
    prefix=st.sampled_from(_CTL_PREFIXES),
    place=st.sampled_from(_PLACES),
    extra=st.one_of(st.none(), st.sampled_from(_PLACES)),
    op=st.sampled_from(_OPS),
    constant=st.integers(min_value=0, max_value=4),
    constant_left=st.booleans(),
)
@settings(max_examples=40, deadline=None)
def test_translate_then_dag_to_text_is_idempotent(
    prefix: str, place: str, extra: str | None, op: str, constant: int, constant_left: bool
) -> None:
    """Rendering the DAG and re-translating it yields the same examination and DAG."""
    sums = place + (f" + {extra}" if extra is not None else "")
    if constant_left:
        text = f"{prefix}({constant} {op} {sums})"
    else:
        text = f"{prefix}({sums} {op} {constant})"
    examination, dag = translate_formula(text)
    rendered = dag_to_text(dag)
    assert rendered == text
    assert translate_formula(rendered) == (examination, dag)


# --- Wrapper: the server-contract invocation against a fake binary ---


class TestRunCheck:
    """``its <model.pnml> -q <query.props>`` against the pinned CLI contract."""

    def test_runs_the_native_with_the_mcc_examination(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wrapper lays out an examination folder and calls the native binary."""
        monkeypatch.setenv("ITS_FAKE_MODE", "decided")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path)
        _, _, argv = _inputs(tmp_path, "AG (wait_1 <= 1)")
        assert run(argv, home=home) == 0
        argv_upstream, model_upstream, property_upstream = _fake_record(tmp_path)
        assert argv_upstream[0] == str(home / "its-tools-native")
        assert argv_upstream[1] == f"-Dfr.lip6.binaries.root={home / 'plugins'}"
        assert argv_upstream[2:6] == [
            "-pnfolder",
            argv_upstream[3],
            "-examination",
            "CTLCardinality",
        ]
        assert model_upstream == MODEL_BYTES.decode()
        assert "<all-paths><globally>" in property_upstream.replace("\n", "")
        out = capsys.readouterr().out
        assert "FORMULA q TRUE TECHNIQUES SEQUENTIAL_PROCESSES" in out
        assert MAYBE_LINE not in out

    def test_falls_back_to_the_eclipse_launcher(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the native image, the launcher binary runs without the -D flag."""
        monkeypatch.setenv("ITS_FAKE_MODE", "decided")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path, name="its-tools")
        _, _, argv = _inputs(tmp_path, "EF deadlock")
        assert run(argv, home=home) == 0
        argv_upstream, _, _ = _fake_record(tmp_path)
        assert argv_upstream[0] == str(home / "its-tools")
        assert argv_upstream[1:2] == ["-pnfolder"]
        assert argv_upstream[4] == "ReachabilityDeadlock"

    def test_passes_the_upstream_exit_code_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing engine is a failing engine: the wrapper invents nothing."""
        monkeypatch.setenv("ITS_FAKE_MODE", "fail")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path)
        _, _, argv = _inputs(tmp_path, "EF deadlock")
        assert run(argv, home=home) == 3

    def test_formula_error_exits_two_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """An untranslatable formula fails closed without invoking upstream."""
        home = _fake_home(tmp_path)
        _, _, argv = _inputs(tmp_path, "EF (p1 + 3 <= 2)")
        assert run(argv, home=home) == 2
        assert "its-tools wrapper:" in capsys.readouterr().err
        assert not (tmp_path / "fake-args.txt").exists()

    def test_missing_binary_raises(self, tmp_path: Path) -> None:
        """An empty home is a wrapper-level error, never an upstream failure."""
        _, _, argv = _inputs(tmp_path, "EF deadlock")
        with pytest.raises(WrapperError):
            run(argv, home=tmp_path / "empty")


class TestRunReducer:
    """``its <model.pnml> --reducer <query.props>`` for the §18.1 +red flow."""

    def _reducer_argv(self, tmp_path: Path, query: str) -> list[str]:
        """The server-contract argv of a reducer invocation."""
        _, _, argv = _inputs(tmp_path, query)
        argv[1] = "--reducer"
        return argv

    def test_decided_reducer_leaves_no_artifact(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A statically decided reduction prints the verdict, nothing else."""
        monkeypatch.setenv("ITS_FAKE_MODE", "decided")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path)
        argv = self._reducer_argv(tmp_path, "AG (wait_1 <= 1)")
        assert run(argv, home=home) == 0
        out = capsys.readouterr().out
        assert "FORMULA q TRUE" in out
        assert MAYBE_LINE not in out
        assert not (tmp_path / "model.sr.pnml").exists()
        assert not (tmp_path / "query.sr.props").exists()

    def test_undecided_reducer_mirrors_artifacts_and_maybe(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An undecided reduction mirrors model.sr.pnml and query.sr.props, MAYBE."""
        monkeypatch.setenv("ITS_FAKE_MODE", "undecided")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path)
        argv = self._reducer_argv(tmp_path, "AG (wait_1 <= 1)")
        assert run(argv, home=home) == 0
        argv_upstream, _, _ = _fake_record(tmp_path)
        assert argv_upstream[-2:] == ["--reduce", "SI_CTL"]
        assert (tmp_path / "model.sr.pnml").read_text(encoding="utf-8") == "<pnml-reduced/>\n"
        assert (tmp_path / "query.sr.props").read_text(encoding="utf-8") == ("AG (wait_1 <= 1)\n")
        out = capsys.readouterr().out
        assert REDUCED_MODEL_LINE.format(path=tmp_path / "model.sr.pnml") in out
        assert PROJECTED_QUERY_LINE.format(path=tmp_path / "query.sr.props") in out
        assert MAYBE_LINE in out

    def test_ltl_reducer_uses_si_ltl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The upstream reduction type follows the examination classification."""
        monkeypatch.setenv("ITS_FAKE_MODE", "undecided")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path)
        argv = self._reducer_argv(tmp_path, "G (optionSlots_1 >= 1)")
        assert run(argv, home=home) == 0
        argv_upstream, _, _ = _fake_record(tmp_path)
        assert argv_upstream[-2:] == ["--reduce", "SI_LTL"]

    def test_crashed_reducer_is_a_failure_not_a_maybe(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-zero upstream exit in reducer mode: exit code through, no MAYBE."""
        monkeypatch.setenv("ITS_FAKE_MODE", "fail")
        monkeypatch.setenv("ITS_FAKE_ARGS", str(tmp_path / "fake-args.txt"))
        home = _fake_home(tmp_path)
        argv = self._reducer_argv(tmp_path, "AG (wait_1 <= 1)")
        assert run(argv, home=home) == 3
        out = capsys.readouterr().out
        assert MAYBE_LINE not in out
        assert not (tmp_path / "model.sr.pnml").exists()
        assert not (tmp_path / "query.sr.props").exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["model.pnml", "--bogus", "query.props"],
        ["model.pnml", "-q"],
        ["missing.pnml", "-q", "query.props"],
        ["model.pnml", "-q", "missing.props"],
    ],
)
def test_run_rejects_out_of_contract_invocations(argv: list[str], tmp_path: Path) -> None:
    """The wrapper accepts exactly the two server spellings, on existing files."""
    roots = {"model.pnml": tmp_path / "model.pnml", "query.props": tmp_path / "query.props"}
    _inputs(tmp_path, "EF deadlock")
    resolved = [str(roots[token]) if token in roots else token for token in argv]
    with pytest.raises(WrapperError):
        run(resolved, home=_fake_home(tmp_path))
