"""PQL translation of the MCC dialect (spec §17.3, issue #39).

verifypn's PQL has no ``->`` implication: ``a -> b`` is rewritten as
``not (a) or (b)``. The rewrite is rightmost-first, so chained implications
keep their right-associative reading, and the left operand runs back to the
enclosing parenthesis - a comparison like ``eat_1 == 1`` is negated whole,
never its right-hand side alone.
"""

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from petrichor.pql import pql_formula


class TestPqlFormula:
    def test_implication_is_rewritten_as_not_or(self) -> None:
        assert (
            pql_formula(formula="AG (eat_1 == 1 -> AF eat_1 == 0)")
            == "AG (not (eat_1 == 1) or AF eat_1 == 0)"
        )

    def test_disjunctive_implication_keeps_its_right_side(self) -> None:
        assert (
            pql_formula(formula="AG (eat_1 == 1 -> AF (eat_1 == 0 or eat_2 == 1))")
            == "AG (not (eat_1 == 1) or AF (eat_1 == 0 or eat_2 == 1))"
        )

    def test_chained_implication_is_right_associative(self) -> None:
        assert pql_formula(formula="a -> b -> c") == "not (a) or not (b) or c"

    def test_parenthesized_left_operand_is_negated_whole(self) -> None:
        assert pql_formula(formula="(a and b) -> c") == "not ((a and b)) or c"

    def test_formula_without_implication_is_unchanged(self) -> None:
        assert pql_formula(formula="AG EF (eat_1 > 0)") == "AG EF (eat_1 > 0)"

    @pytest.mark.property
    @settings(deadline=None)
    @given(formula=st.text(alphabet="abeAGF()=<>- ", min_size=1, max_size=40))
    def test_property_rewrite_never_leaves_an_arrow(self, formula: str) -> None:
        """After translation, no ``->`` remains (PQL has no implication)."""
        assert "->" not in pql_formula(formula=formula)
