"""PQL translation of the MCC query dialect (spec §17.3, issue #39).

verifypn's query language (PQL) has no ``->`` implication, so the MCC
``a -> b`` is rewritten as ``not (a) or (b)``. Found by the P39 e2e on the
real engine: ``AG (eat_1 == 1 -> AF eat_1 == 0)`` is a parse error on
verifypn, the rewritten form is accepted.

This module is standard-library-only on purpose: the engine images
(``Dockerfile.verifypn``) copy it and run without any third-party
dependency installed.
"""

ARROW = "->"
SPACE = " "
OPEN_PAREN = "("
CLOSE_PAREN = ")"
ARROW_HEAD = ">"


def _left_operand(*, formula: str, arrow_index: int) -> tuple[str, int]:
    """The operand left of the ``->`` at ``arrow_index``, and where it starts.

    Implication is the lowest-precedence connective, so the left operand
    runs back to the enclosing ``(`` or to a previous top-level ``->`` -
    never past either. A parenthesized group is kept whole.
    """
    end = arrow_index
    while end > 0 and formula[end - 1] == SPACE:
        end -= 1
    start = end
    depth = 0
    while start > 0:
        char = formula[start - 1]
        if char == CLOSE_PAREN:
            depth += 1
        elif char == OPEN_PAREN:
            if depth == 0:
                break
            depth -= 1
        elif (
            char == ARROW_HEAD
            and depth == 0
            and start >= len(ARROW)
            and formula[start - len(ARROW) : start] == ARROW
        ):
            break
        start -= 1
    left = formula[start:end]
    stripped = left.lstrip(SPACE)
    return stripped, start + (len(left) - len(stripped))


def pql_formula(*, formula: str) -> str:
    """Translate one MCC formula to verifypn PQL (spec §17.3).

    Arrows are rewritten rightmost-first, so chained implications keep
    their right-associative reading: ``a -> b -> c`` becomes
    ``not (a) or not (b) or c``.
    """
    result = formula
    while ARROW in result:
        arrow = result.rindex(ARROW)
        left, start = _left_operand(formula=result, arrow_index=arrow)
        right = arrow + len(ARROW)
        while right < len(result) and result[right] == SPACE:
            right += 1
        result = f"{result[:start]}not ({left}) or {result[right:]}"
    return result
