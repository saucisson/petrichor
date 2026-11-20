"""Adaptive ``its`` wrapper bridging the engine-server contract to the real ITS-Tools CLI.

Issue #21, spec §17.2, §17.4, §18.1, §19.4. Standard library only, exactly
like ``its_tools_server``: the engine image ships no third-party dependency.

The engine server invokes ``its <model.pnml> -q <query.props>`` for a check
and ``its <model.pnml> --reducer <query.props>`` for the reducer. Those
invocations do not exist in ITS-Tools: the upstream CLI serves the MCC
driver instead, ``its-tools [-Dfr.lip6.binaries.root=<plugins>]
-pnfolder <dir> -examination <Name> [--reduce <ReductionType>]``, over an
examination folder holding ``model.pnml`` plus one property file
``<Name>.xml`` written in the MCC property DAG dialect - the format the
official kits still ship in 2026 (verified against the 2026 submission kit).
``Dockerfile.its-tools`` installs this module as ``/usr/local/bin/its``.

The wrapper parses the textual formula of the spec §17.2 dialect
(comparisons of token counts, boolean connectives, implication, CTL path
quantifiers, LTL temporal operators, the ``EF deadlock`` sentinel),
classifies the examination, writes the DAG XML, then runs the pinned
upstream binary and streams stdout/stderr through with the exit code
intact. No verdict is ever synthesized here: every ``FORMULA ... TRUE|FALSE``
line is printed by ITS-Tools itself.

Examination classification, in order:

- ``EF deadlock`` (any case) is the deadlock-family sentinel and becomes a
  ReachabilityDeadlock examination holding one fixed property;
- any CTL path quantifier (EF/EG/AF/AG/EX/AX, ``A[phi U psi]``, ``E[phi U psi]``)
  decides CTLCardinality;
- temporal operators without a path quantifier (X/F/G/U) decide
  LTLCardinality; mixing the two dialects is rejected rather than mistranslated;
- a bare state formula is read as reachability of that state (``EF``), like
  the MCC ReachabilityCardinality examinations.

In reducer mode the wrapper passes ``--reduce`` with the upstream reduction
type of the examination (DEADLOCK, REACHABILITY, SI_CTL, SI_LTL). When
ITS-Tools cannot decide statically it exports the reduced net and the
projected property into the examination folder
(``model.<pid>.<TYPE>.pnml``, ``<Examination>.<pid>.<TYPE>.xml``); the
wrapper mirrors them next to the server's inputs under the §18.1 names
(``model.sr.pnml``, ``query.sr.props``), renders the projected DAG back to
text so the caller never receives XML where a formula is expected, and
prints a MAYBE line: an undecided reducer is undecided, never a guess.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

ITS_TOOLS_HOME = Path(os.environ.get("ITS_TOOLS_HOME", "/opt/its-tools"))
"""Root of the pinned ITS-Tools product extracted by Dockerfile.its-tools."""

DEADLOCK_SENTINEL = re.compile(r"^EF\s+DEADLOCK$", re.IGNORECASE)
"""The deadlock-family query, mapped upstream to a ReachabilityDeadlock examination."""

CHECK_FLAG = "-q"
REDUCER_FLAG = "--reducer"
MODEL_ARTIFACT = "model.sr.pnml"
"""The §18.1 name of the reduced net the server reads back."""
QUERY_ARTIFACT = "query.sr.props"
"""The §18.1 name of the projected formula the server reads back."""
REDUCED_MODEL_LINE = "Reduced model written to: {path}"
PROJECTED_QUERY_LINE = "Projected query written to: {path}"
MAYBE_LINE = "Query is MAYBE satisfied"
"""The undecided-reducer marker the server maps to the unknown verdict."""
FORMULA_LINE = re.compile(r"FORMULA \S+ (TRUE|FALSE)\b")
REDUCTION_TYPE_BY_EXAMINATION = {
    "ReachabilityDeadlock": "DEADLOCK",
    "ReachabilityCardinality": "REACHABILITY",
    "CTLCardinality": "SI_CTL",
    "LTLCardinality": "SI_LTL",
}
"""The upstream ReductionType of each examination, verified against the CLI."""


class FormulaError(Exception):
    """The textual formula is outside the translatable MCC dialect."""


class WrapperError(Exception):
    """The wrapper invocation does not conform to the server contract."""


type Node = Cmp | Bool | Quantifier | Temporal
"""One parsed formula node (the union is closed)."""


@dataclass(frozen=True)
class Arith:
    """One comparison side: an integer constant or a token-count sum."""

    places: tuple[str, ...] = ()
    constant: int = 0


@dataclass(frozen=True)
class Cmp:
    """A comparison of two arithmetic sides."""

    op: Literal["<", "<=", ">", ">=", "==", "!="]
    left: Arith
    right: Arith


@dataclass(frozen=True)
class Bool:
    """A boolean combination; an implication desugars to or(not(a), b)."""

    op: Literal["and", "or", "not", "imp"]
    children: tuple[Node, ...]


@dataclass(frozen=True)
class Quantifier:
    """A CTL path quantifier applied to one temporal operator."""

    quantifier: Literal["A", "E"]
    op: Literal["F", "G", "X", "U"]
    child: Node | None = None
    before: Node | None = None


@dataclass(frozen=True)
class Temporal:
    """An LTL temporal operator outside any path quantifier."""

    op: Literal["F", "G", "X", "U"]
    child: Node | None = None
    before: Node | None = None


COMPARISON_TAG = {
    "<": "integer-lt",
    "<=": "integer-le",
    ">": "integer-gt",
    ">=": "integer-ge",
    "==": "integer-eq",
    "=": "integer-eq",
    "!=": "integer-neq",
}
_TOKEN = re.compile(
    r"\s*(?:(\d+)|([A-Za-z_][A-Za-z0-9_.]*)|(<=|>=|==|!=|->|=>|&&|\|\|)|([<>=()\[\]+!]))"
)
_CTL_PREFIX: dict[str, tuple[Literal["A", "E"], Literal["F", "G", "X", "U"]]] = {
    "EF": ("E", "F"),
    "EG": ("E", "G"),
    "AF": ("A", "F"),
    "AG": ("A", "G"),
    "EX": ("E", "X"),
    "AX": ("A", "X"),
}
"""The CTL prefix quantifiers, split into (path quantifier, temporal operator)."""
_LTL_PREFIX: dict[str, Literal["F", "G", "X"]] = {"F": "F", "G": "G", "X": "X"}


class _Parser:
    """A recursive-descent parser of the formula dialect into Node trees."""

    def __init__(self, text: str) -> None:
        self.tokens: list[tuple[str, str]] = []
        position = 0
        while position < len(text):
            match = _TOKEN.match(text, position)
            if match is None or match.group().strip() == "":
                rest = text[position:].strip()[:20]
                raise FormulaError(f"offset {position}: cannot tokenize {rest!r}.")
            self.tokens.extend(
                (kind, group)
                for kind, group in zip(("int", "word", "op2", "op1"), match.groups(), strict=False)
                if group is not None
            )
            position = match.end()
        self.index = 0
        self.brackets = 0  # > 0 inside an A[.. U ..] quantifier, where U is structural

    def peek(self) -> tuple[str, str] | None:
        """The next token, or None when exhausted."""
        return self.tokens[self.index] if self.index < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        """Consume and return the next token."""
        token = self.peek()
        if token is None:
            raise FormulaError("unexpected end of formula.")
        self.index += 1
        return token

    def accept_op(self, *ops: str) -> str | None:
        """Consume the next token when it is one of the given operators.

        The boolean connectives are words (and, or, not), everything else is
        a symbol or multi-char operator; both spellings are accepted here.
        """
        token = self.peek()
        if token is None:
            return None
        kind, word = token
        if kind == "word" and word.lower() in ops:
            self.index += 1
            return word
        if kind in ("op1", "op2") and word in ops:
            self.index += 1
            return word
        return None

    def parse_formula(self) -> Node:
        """The root formula, rejecting any trailing token."""
        node = self.parse_implication()
        trailing = self.peek()
        if trailing is not None:
            raise FormulaError(f"unexpected trailing token {trailing[1]!r}.")
        return node

    def parse_implication(self) -> Node:
        """Implication ``a -> b``, right associative, desugared later."""
        left = self.parse_disjunction()
        if self.accept_op("->", "=>") is None:
            return left
        right = self.parse_implication()
        return Bool("imp", (left, right))

    def parse_disjunction(self) -> Node:
        """or, left associative, flattened."""
        node = self.parse_conjunction()
        while self.accept_op("or", "||"):
            rhs = self.parse_conjunction()
            children = (
                (*node.children, rhs) if isinstance(node, Bool) and node.op == "or" else (node, rhs)
            )
            node = Bool("or", children)
        return node

    def parse_conjunction(self) -> Node:
        """and, left associative, flattened."""
        node = self.parse_negation()
        while self.accept_op("and", "&&"):
            rhs = self.parse_negation()
            children = (
                (*node.children, rhs)
                if isinstance(node, Bool) and node.op == "and"
                else (node, rhs)
            )
            node = Bool("and", children)
        return node

    def parse_negation(self) -> Node:
        """Negation."""
        if self.accept_op("not", "!") is not None:
            return Bool("not", (self.parse_negation(),))
        return self.parse_unary()

    def parse_unary(self) -> Node:
        """CTL quantifiers, LTL operators, then U at operand level."""
        token = self.peek()
        if token is not None and token[0] == "word":
            word = token[1]
            upper = word.upper()
            if upper in _CTL_PREFIX:
                self.take()
                quantifier, op = _CTL_PREFIX[upper]
                return Quantifier(quantifier, op, self.parse_negation())
            if upper in ("A", "E"):
                after = self.peek_next()
                if after is not None and after[0] == "op1" and after[1] == "[":
                    return self._parse_until(cast("Literal['A', 'E']", upper))
            if upper in _LTL_PREFIX:
                self.take()
                child = self.parse_unary() if upper == "X" else self.parse_negation()
                return Temporal(_LTL_PREFIX[upper], child)
            if upper == "DEADLOCK":
                raise FormulaError("the deadlock sentinel is only supported as 'EF deadlock'.")
        node = self.parse_until_operand()
        if self.brackets == 0 and self.is_next_word("U"):
            self.take()
            before = self.parse_unary()
            return Temporal("U", before, node)
        return node

    def peek_next(self) -> tuple[str, str] | None:
        """The token after the next one, or None."""
        if self.index + 1 < len(self.tokens):
            return self.tokens[self.index + 1]
        return None

    def is_next_word(self, word: str) -> bool:
        """Whether the next token is the given keyword."""
        token = self.peek()
        return token is not None and token[0] == "word" and token[1].upper() == word

    def _parse_until(self, quantifier: Literal["A", "E"]) -> Node:
        self.take()  # the quantifier word
        self.take()  # the opening bracket
        self.brackets += 1
        try:
            before = self.parse_implication()
            if not self.is_next_word("U"):
                raise FormulaError("expected 'U' inside the A[.. U ..] quantifier.")
            self.take()
            reach = self.parse_implication()
        finally:
            self.brackets -= 1
        if self.accept_op("]") is None:
            raise FormulaError("expected ']' to close the until quantifier.")
        return Quantifier(quantifier, "U", reach, before)

    def parse_until_operand(self) -> Node:
        """A parenthesized formula or a comparison."""
        if self.accept_op("(") is not None:
            node = self.parse_implication()
            if self.accept_op(")") is None:
                raise FormulaError("expected ')' to close the parenthesized formula.")
            return node
        return self.parse_comparison()

    def parse_comparison(self) -> Node:
        """Comparison ``left <op> right`` over token counts and constants."""
        left = self.parse_arith()
        op = self.accept_op("<=", ">=", "==", "!=", "<", ">", "=")
        if op is None:
            raise FormulaError(
                "a bare token count is not a formula: comparisons need one of <= >= == != < >."
            )
        right = self.parse_arith()
        return Cmp(op, left, right)  # type: ignore[arg-type]

    def parse_arith(self) -> Arith:
        """A sum of token counts, or one constant; no mixed sides."""
        places: list[str] = []
        constant = 0
        mixed = False
        terms = 0
        while True:
            token = self.peek()
            if token is None:
                break
            kind, word = token
            if kind == "int":
                self.take()
                constant += int(word)
                mixed = mixed or bool(places)
            elif kind == "word":
                if word.upper() == "DEADLOCK":
                    raise FormulaError("the deadlock sentinel is only supported as 'EF deadlock'.")
                self.take()
                places.append(word)
                mixed = mixed or False
            else:
                break
            terms += 1
            if self.accept_op("+") is None:
                break
        if terms == 0:
            raise FormulaError("expected a token count or an integer constant.")
        if mixed:
            raise FormulaError(
                "the MCC DAG expresses a sum over places or a constant, not both mixed."
            )
        return Arith(tuple(places), constant)


def _escape(place: str) -> str:
    """XML-escape one place name."""
    return place.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _arith_xml(side: Arith) -> str:
    """One comparison side as DAG XML."""
    if side.places:
        places_inner = "".join(f"<place>{_escape(name)}</place>" for name in side.places)
        return f"<tokens-count>{places_inner}</tokens-count>"
    return f"<integer-constant>{side.constant}</integer-constant>"


def _compare_xml(op: str, left: Arith, right: Arith) -> str:
    tag = COMPARISON_TAG[op]
    return f"<{tag}>{_arith_xml(left)}{_arith_xml(right)}</{tag}>"


def _node_xml(node: Node, *, inside_path: bool) -> str:
    """One formula node as DAG XML."""
    if isinstance(node, Cmp):
        return _compare_xml(node.op, node.left, node.right)
    if isinstance(node, Bool):
        if node.op == "not":
            inner = _node_xml(node.children[0], inside_path=inside_path)
            return f"<negation>{inner}</negation>"
        if node.op == "imp":
            left_inner = _node_xml(node.children[0], inside_path=inside_path)
            right_inner = _node_xml(node.children[1], inside_path=inside_path)
            return f"<disjunction><negation>{left_inner}</negation>{right_inner}</disjunction>"
        tag = "conjunction" if node.op == "and" else "disjunction"
        inner = "".join(_node_xml(child, inside_path=inside_path) for child in node.children)
        return f"<{tag}>{inner}</{tag}>"
    if isinstance(node, Quantifier):
        if not inside_path:
            raise FormulaError("CTL path quantifiers cannot nest under an LTL operator.")
        wrapper = "exists-path" if node.quantifier == "E" else "all-paths"
        body = _body_xml(node.op, child=node.child, before=node.before, inside_path=True)
        return f"<{wrapper}>{body}</{wrapper}>"
    if isinstance(node, Temporal):
        if inside_path:
            raise FormulaError(
                "an LTL temporal operator appears under a CTL path quantifier; "
                "mixing the two dialects would mistranslate."
            )
        return _body_xml(node.op, child=node.child, before=node.before, inside_path=False)
    raise FormulaError(f"unsupported node {node!r}.")  # pragma: no cover - closed union


def _body_xml(
    op: str,
    *,
    child: Node | None,
    before: Node | None,
    inside_path: bool,
) -> str:
    """The temporal body of one path quantifier, at path level or LTL level."""
    if op in ("F", "G", "X"):
        if child is None:
            raise FormulaError(f"the {op} operator needs one operand.")
        tag = {"F": "finally", "G": "globally", "X": "next"}[op]
        return f"<{tag}>{_node_xml(child, inside_path=inside_path)}</{tag}>"
    if before is None or child is None:
        raise FormulaError("the until operator needs before and reach operands.")
    left = _node_xml(before, inside_path=inside_path)
    right = _node_xml(child, inside_path=inside_path)
    return f"<until><before>{left}</before><reach>{right}</reach></until>"


def _classify(root: Node) -> tuple[str, Node]:
    """Pick the examination name, wrapping bare state formulas as reachability."""

    def has(node: Node, kind: type) -> bool:
        if isinstance(node, kind):
            return True
        if isinstance(node, Cmp):
            return False
        if isinstance(node, Bool):
            return any(has(child, kind) for child in node.children)
        children = [n for n in (node.child, node.before) if n is not None]
        return any(has(child, kind) for child in children)

    if has(root, Quantifier):
        return "CTLCardinality", root
    if has(root, Temporal):
        return "LTLCardinality", root
    return "ReachabilityCardinality", Quantifier("E", "F", root)


def _property_xml(formula_xml: str) -> str:
    """Wrap one DAG formula into a single-property property-set document."""
    return (
        '<?xml version="1.0"?>\n'
        '<property-set xmlns="http://mcc.lip6.fr/">\n'
        "  <property>\n"
        "    <id>q</id>\n"
        "    <description>petrichor check</description>\n"
        "    <formula>\n"
        f"{formula_xml}\n"
        "    </formula>\n"
        "  </property>\n"
        "</property-set>\n"
    )


DEADLOCK_XML = "<exists-path><finally><deadlock/></finally></exists-path>"
"""The fixed ReachabilityDeadlock property the sentinel maps to."""


def translate_formula(text: str) -> tuple[str, str]:
    """Translate one textual formula into (examination name, DAG XML)."""
    query = text.strip()
    if DEADLOCK_SENTINEL.match(query):
        return "ReachabilityDeadlock", _property_xml(DEADLOCK_XML)
    root = _Parser(query).parse_formula()
    examination, tree = _classify(root)
    # The LTL DAG carries the implicit universal path quantifier at the root;
    # CTL and reachability trees root directly in a path quantifier.
    if examination == "LTLCardinality":
        formula_xml = f"<all-paths>{_node_xml(tree, inside_path=False)}</all-paths>"
    else:
        formula_xml = _node_xml(tree, inside_path=True)
    return examination, _property_xml(formula_xml)


def _local(tag: str) -> str:
    """Strip the MCC namespace from one element tag."""
    return tag.rsplit("}", 1)[-1]


def _render_arith(element: ET.Element) -> str:
    """One DAG comparison side back to text."""
    tag = _local(element.tag)
    if tag == "integer-constant":
        return (element.text or "0").strip()
    if tag == "tokens-count":
        return " + ".join((p.text or "").strip() for p in element)
    raise FormulaError(f"unsupported DAG arithmetic tag {tag!r}.")


_COMPARISON_SYMBOLS = {
    "integer-lt": "<",
    "integer-le": "<=",
    "integer-gt": ">",
    "integer-ge": ">=",
    "integer-eq": "==",
    "integer-neq": "!=",
}
_PREFIX_BY_TAG = {"finally": "F", "globally": "G", "next": "X"}


def _render_comparison(element: ET.Element, tag: str) -> str:
    """One DAG comparison back to text."""
    symbol = _COMPARISON_SYMBOLS[tag]
    return f"{_render_arith(element[0])} {symbol} {_render_arith(element[1])}"


def _render_boolean(element: ET.Element, tag: str) -> str:
    """One conjunction or disjunction back to text."""
    operator = "and" if tag == "conjunction" else "or"
    parts = [_render_element(child) for child in element]
    return f" {operator} ".join(f"({part})" for part in parts)


def _render_until(element: ET.Element) -> str:
    """One DAG until node back to text."""
    before = element.find("before")
    reach = element.find("reach")
    if before is None or reach is None:
        raise FormulaError("an until node needs before and reach children.")
    return f"({_render_element(before)} U {_render_element(reach)})"


def _render_path(element: ET.Element, tag: str) -> str:
    """One DAG path quantifier back to text."""
    quantifier = "E" if tag == "exists-path" else "A"
    body = element[0]
    body_tag = _local(body.tag)
    if body_tag == "until":
        return f"{quantifier} {_render_element(body)}"
    prefix = _PREFIX_BY_TAG.get(body_tag)
    if prefix is None:
        raise FormulaError(f"unsupported path body tag {body_tag!r}.")
    return f"{quantifier}{prefix} ({_render_element(body[0])})"


def _render_element(element: ET.Element) -> str:
    """One DAG node back to the textual dialect."""
    tag = _local(element.tag)
    if tag in _COMPARISON_SYMBOLS:
        text = _render_comparison(element, tag)
    elif tag in ("conjunction", "disjunction"):
        text = _render_boolean(element, tag)
    elif tag == "negation":
        text = f"not ({_render_element(element[0])})"
    elif tag in _PREFIX_BY_TAG:
        text = f"{_PREFIX_BY_TAG[tag]} ({_render_element(element[0])})"
    elif tag == "until":
        text = _render_until(element)
    elif tag in ("exists-path", "all-paths"):
        text = _render_path(element, tag)
    elif tag == "deadlock":
        text = "deadlock"
    else:
        raise FormulaError(f"unsupported DAG tag {tag!r}.")
    return text


def dag_to_text(dag_xml: str) -> str:
    """Render one MCC DAG property document back to the textual dialect."""
    # stdlib ElementTree on Python 3.15 runs on a bundled expat with built-in
    # billion-laughs amplification limits and no external-entity resolution;
    # defusedxml would be a new dependency, which is on the escalation list.
    root = ET.fromstring(dag_xml)  # noqa: S314
    property_element = root.find("{http://mcc.lip6.fr/}property")
    if property_element is None:
        property_element = root.find("property")
    if property_element is None:
        raise FormulaError("the projected property carries no property element.")
    formula = property_element.find("{http://mcc.lip6.fr/}formula")
    if formula is None:
        formula = property_element.find("formula")
    if formula is None or len(formula) == 0:
        raise FormulaError("the projected property carries no formula element.")
    return _render_element(formula[0])


def _binary_argv(home: Path) -> list[str]:
    """The pinned upstream binary and its fixed preamble (native first, JRE fallback)."""
    native = home / "its-tools-native"
    if native.is_file() and os.access(native, os.X_OK):
        return [str(native), f"-Dfr.lip6.binaries.root={home / 'plugins'}"]
    launcher = home / "its-tools"
    if launcher.is_file() and os.access(launcher, os.X_OK):
        return [str(launcher)]
    raise WrapperError(f"no ITS-Tools executable found under {home}.")


def _upstream_argv(home: Path, folder: Path, examination: str, reducer: bool) -> list[str]:
    """The full upstream command line for one examination folder."""
    argv = [*_binary_argv(home), "-pnfolder", str(folder), "-examination", examination]
    if reducer:
        argv += ["--reduce", REDUCTION_TYPE_BY_EXAMINATION[examination]]
    return argv


def _write_examination(folder: Path, model: Path, examination: str, property_xml: str) -> None:
    """Lay out the MCC examination folder: the model plus one property file."""
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(model, folder / "model.pnml")
    (folder / f"{examination}.xml").write_text(property_xml, encoding="utf-8")


def _mirror_reducer_artifacts(*, folder: Path, examination: str, query: Path, stdout: str) -> None:
    """Mirror the exported reduced net and projected property next to the inputs."""
    decided = FORMULA_LINE.search(stdout) is not None
    if decided:
        return
    reduced = [
        candidate
        for candidate in sorted(folder.glob("model.*"))
        if candidate.name != "model.pnml" and candidate.suffix == ".pnml"
    ]
    if reduced:
        target = query.parent / MODEL_ARTIFACT
        shutil.copyfile(reduced[0], target)
        sys.stdout.write(REDUCED_MODEL_LINE.format(path=target) + "\n")
    projected = [
        candidate
        for candidate in sorted(folder.glob(f"{examination}.*"))
        if candidate.name != f"{examination}.xml" and candidate.suffix == ".xml"
    ]
    if projected:
        text = dag_to_text(projected[0].read_text(encoding="utf-8"))
        target = query.parent / QUERY_ARTIFACT
        target.write_text(text + "\n", encoding="utf-8")
        sys.stdout.write(PROJECTED_QUERY_LINE.format(path=target) + "\n")
    sys.stdout.write(MAYBE_LINE + "\n")


CONTRACT_ARGV_LENGTH = 3
"""The server contract passes exactly model, flag, query."""


def _parse_argv(argv: list[str]) -> tuple[Path, Path, bool]:
    """Validate the server-contract invocation; return (model, query, reducer?)."""
    if len(argv) != CONTRACT_ARGV_LENGTH or argv[1] not in (CHECK_FLAG, REDUCER_FLAG):
        raise WrapperError(
            "usage: its <model.pnml> -q <query.props> | its <model.pnml> --reducer <query.props>"
        )
    model, query = Path(argv[0]), Path(argv[2])
    if not model.is_file():
        raise WrapperError(f"model file {model} does not exist.")
    if not query.is_file():
        raise WrapperError(f"query file {query} does not exist.")
    return model, query, argv[1] == REDUCER_FLAG


def run(argv: list[str] | None = None, *, home: Path | None = None) -> int:
    """Execute one server-contract invocation; return the upstream exit code."""
    args = list(sys.argv[1:] if argv is None else argv)
    model, query_path, reducer = _parse_argv(args)
    root = home if home is not None else ITS_TOOLS_HOME
    query = query_path.read_text(encoding="utf-8").strip()
    try:
        examination, property_xml = translate_formula(query)
    except FormulaError as error:
        sys.stderr.write(f"its-tools wrapper: {error}\n")
        return 2
    with tempfile.TemporaryDirectory(prefix="its-cli-") as scratch:
        folder = Path(scratch)
        _write_examination(folder, model, examination, property_xml)
        argv_upstream = _upstream_argv(root, folder, examination, reducer)
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
                argv_upstream, cwd=folder, capture_output=True, text=True, check=False
            )
        except OSError as error:
            sys.stderr.write(f"its-tools wrapper: could not execute ITS-Tools: {error}\n")
            return 127
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
        sys.stderr.write(completed.stderr)
        sys.stderr.flush()
        if reducer:
            _mirror_reducer_artifacts(
                folder=folder, examination=examination, query=query_path, stdout=completed.stdout
            )
        return completed.returncode


if __name__ == "__main__":  # pragma: no cover - the container entry point
    raise SystemExit(run())
