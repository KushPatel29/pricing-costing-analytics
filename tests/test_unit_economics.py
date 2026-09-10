"""
Unit economics, break-even and scenario modelling.

The convention tests are the ones worth reading. Markup against margin, and
variable cost against absorbed cost, are the two places where a defensible
reading exists on both sides and only one of them is implemented -- and where
choosing the wrong one produces a plausible number that is quietly wrong
forever.
"""

from __future__ import annotations

import math

import pytest

from pricing import scenario as sc
from pricing import unit_economics as ue

# ==========================================================================
# Markup and margin
# ==========================================================================

class TestMarkupAndMargin:
    @pytest.mark.parametrize(
        "markup,margin",
        [(0.25, 0.20), (1.0, 0.50), (0.3333333333, 0.25), (0.0, 0.0)],
    )
    def test_the_conversion_both_ways(self, markup, margin):
        assert ue.markup_to_margin(markup) == pytest.approx(margin, abs=1e-9)
        assert ue.margin_to_markup(margin) == pytest.approx(markup, abs=1e-9)

    def test_they_are_inverses(self):
        for rate in (0.05, 0.15, 0.25, 0.4, 0.75, 1.5):
            assert ue.markup_to_margin(ue.margin_to_markup(
                ue.markup_to_margin(rate))) == pytest.approx(
                    ue.markup_to_margin(rate))

    def test_the_gap_is_the_whole_point(self):
        """
        A 40% markup applied where a 40% margin was meant delivers 28.6% --
        eleven points light, on every line, forever, with nothing announcing it.
        """
        assert ue.markup_to_margin(0.40) == pytest.approx(0.2857, abs=1e-4)
        assert 0.40 - ue.markup_to_margin(0.40) > 0.11

    def test_a_hundred_percent_margin_has_no_finite_markup(self):
        assert ue.margin_to_markup(1.0) == float("inf")

    def test_price_from_markup_is_not_price_from_margin(self):
        from costing.formulas import calculate_price_from_margin

        assert ue.price_from_markup(10.0, 0.25) == pytest.approx(12.50)
        assert calculate_price_from_margin(10.0, 0.25) == pytest.approx(13.3333, abs=1e-4)

    def test_the_table_lines_the_two_up(self):
        rows = ue.markup_margin_table([0.20, 0.40])
        first = rows[0]
        assert first["markup_needed_for_that_margin"] == pytest.approx(0.25)
        assert first["margin_if_read_as_markup"] == pytest.approx(1 / 6, abs=1e-9)
        assert first["gap"] > 0


# ==========================================================================
# Contribution and cost to serve
# ==========================================================================

class TestContribution:
    def test_contribution_is_price_less_variable_cost(self):
        result = ue.contribution(price=25.0, variable_cost=18.0, quantity=1000)
        assert result["contribution_per_unit"] == pytest.approx(7.0)
        assert result["contribution_ratio"] == pytest.approx(0.28)
        assert result["total_contribution"] == pytest.approx(7000.0)

    def test_a_zero_price_has_no_ratio_rather_than_a_division_error(self):
        assert ue.contribution(price=0.0, variable_cost=5.0)["contribution_ratio"] == 0.0

    def test_cost_to_serve_is_reported_as_a_share_of_price(self):
        """
        The number that matters. Forty cents is nothing on a $340 monitor and
        the whole margin on a $2 pack of pens, and only the share says which.
        """
        result = ue.cost_to_serve(freight_out=0.30, order_handling=0.08,
                                  returns_credits=0.05, price=2.00, quantity=100)
        assert result["cost_to_serve_per_unit"] == pytest.approx(0.43)
        assert result["pct_of_price"] == pytest.approx(0.215)
        assert result["cost_to_serve_total"] == pytest.approx(43.0)


# ==========================================================================
# Break-even
# ==========================================================================

class TestBreakEven:
    def test_the_textbook_case(self):
        result = ue.break_even(fixed_costs=100_000, price=25.0, variable_cost=15.0)
        assert result["exists"]
        assert result["break_even_units"] == pytest.approx(10_000)
        assert result["break_even_revenue"] == pytest.approx(250_000)

    def test_at_break_even_operating_profit_is_exactly_zero(self):
        """The definition, checked against the implementation."""
        result = ue.break_even(fixed_costs=100_000, price=25.0, variable_cost=15.0)
        at_break_even = ue.break_even(
            fixed_costs=100_000, price=25.0, variable_cost=15.0,
            actual_quantity=result["break_even_units"])
        assert at_break_even["operating_profit"] == pytest.approx(0.0, abs=1e-6)
        assert at_break_even["margin_of_safety"] == pytest.approx(0.0, abs=1e-9)

    def test_zero_contribution_has_no_break_even_rather_than_a_huge_one(self):
        """
        A tool that prints 4,300,000,000 units has told the reader to sell
        harder. There is no volume that fixes a price below variable cost.
        """
        result = ue.break_even(fixed_costs=100_000, price=15.0, variable_cost=15.0)
        assert not result["exists"]
        assert math.isnan(result["break_even_units"])
        assert "no volume covers" in result["reason"]

    def test_negative_contribution_is_refused_too(self):
        result = ue.break_even(fixed_costs=100_000, price=12.0, variable_cost=15.0)
        assert not result["exists"]
        assert result["operating_profit"] < 0

    def test_a_target_profit_moves_the_volume_needed(self):
        base = ue.break_even(fixed_costs=100_000, price=25.0, variable_cost=15.0)
        with_target = ue.break_even(fixed_costs=100_000, price=25.0,
                                    variable_cost=15.0, target_profit=50_000)
        assert with_target["break_even_units"] == pytest.approx(15_000)
        assert with_target["break_even_units"] > base["break_even_units"]

    def test_margin_of_safety_is_how_far_volume_can_fall(self):
        result = ue.break_even(fixed_costs=100_000, price=25.0, variable_cost=15.0,
                               actual_quantity=12_500)
        assert result["margin_of_safety"] == pytest.approx(0.20)
        assert result["margin_of_safety_units"] == pytest.approx(2_500)

    def test_absorbed_cost_gives_a_different_and_wrong_answer(self):
        """
        The distinction the whole module exists for. Absorbing the fixed pool
        into unit cost and then breaking even on it double-counts the fixed
        cost, and the error is in the direction that flatters: it reports a
        *lower* break-even than the truth.
        """
        fixed, price, variable, volume = 100_000, 25.0, 15.0, 20_000
        honest = ue.break_even(fixed_costs=fixed, price=price,
                               variable_cost=variable, actual_quantity=volume)
        absorbed_unit_cost = variable + fixed / volume
        wrong = ue.break_even(fixed_costs=fixed, price=price,
                              variable_cost=absorbed_unit_cost,
                              actual_quantity=volume)
        assert wrong["break_even_units"] > honest["break_even_units"]
        # And the absorbed version reports a *loss* at a volume that is
        # comfortably profitable.
        assert honest["operating_profit"] > 0
        assert wrong["operating_profit"] < honest["operating_profit"]

    def test_operating_leverage_is_undefined_at_break_even(self):
        result = ue.operating_leverage(contribution_total=100_000, operating_profit=0.0)
        assert not result["defined"]
        assert "unbounded" in result["reason"]

    def test_operating_leverage_amplifies_a_volume_move(self):
        """At 4x, a ten percent fall in volume takes forty percent of profit."""
        leverage = ue.operating_leverage(contribution_total=400_000,
                                         operating_profit=100_000)
        assert leverage["leverage"] == pytest.approx(4.0)
        after = ue.break_even(fixed_costs=300_000, price=25.0, variable_cost=15.0,
                              actual_quantity=40_000 * 0.9)
        before = ue.break_even(fixed_costs=300_000, price=25.0, variable_cost=15.0,
                               actual_quantity=40_000)
        assert (after["operating_profit"] / before["operating_profit"] - 1) == \
            pytest.approx(-0.40, abs=1e-9)

    def test_the_curve_marks_the_crossing(self):
        points = ue.break_even_curve(fixed_costs=100_000, price=25.0,
                                     variable_cost=15.0, max_quantity=20_000)
        assert sum(p["is_break_even"] for p in points) == 1
        crossing = next(p for p in points if p["is_break_even"])
        assert crossing["quantity"] == pytest.approx(10_000, abs=300)
        assert crossing["profit"] == pytest.approx(0.0, abs=5_000)

    def test_the_curve_series_are_consistent(self):
        for point in ue.break_even_curve(fixed_costs=50_000, price=10.0,
                                         variable_cost=6.0, max_quantity=30_000):
            assert point["total_cost"] == pytest.approx(
                point["fixed_cost"] + point["variable_cost"])
            assert point["profit"] == pytest.approx(
                point["revenue"] - point["total_cost"])


class TestPortfolioBreakEven:
    ROWS = [
        {"price": 30.0, "variable_cost": 20.0, "quantity": 1000},
        {"price": 10.0, "variable_cost": 8.0, "quantity": 5000},
    ]

    def test_it_uses_a_weighted_ratio_not_a_per_unit_contribution(self):
        """
        A monitor and a pack of pens are both one unit and nothing else about
        them is the same, so a per-unit average is meaningless. The answer is a
        break-even *revenue*.
        """
        result = ue.portfolio_break_even(self.ROWS, fixed_costs=10_000)
        revenue = 30 * 1000 + 10 * 5000
        variable = 20 * 1000 + 8 * 5000
        assert result["revenue"] == pytest.approx(revenue)
        assert result["contribution_ratio"] == pytest.approx(
            (revenue - variable) / revenue)
        assert result["break_even_revenue"] == pytest.approx(
            10_000 / result["contribution_ratio"])

    def test_it_only_holds_while_the_mix_holds(self):
        """Shifting the mix toward the thin product raises break-even revenue,
        with no price or cost having changed."""
        richer = [{**self.ROWS[0], "quantity": 5000}, {**self.ROWS[1], "quantity": 1000}]
        thin = ue.portfolio_break_even(self.ROWS, fixed_costs=10_000)
        rich = ue.portfolio_break_even(richer, fixed_costs=10_000)
        assert rich["break_even_revenue"] < thin["break_even_revenue"]

    def test_a_book_that_does_not_cover_variable_cost_has_no_break_even(self):
        losing = [{"price": 5.0, "variable_cost": 7.0, "quantity": 100}]
        result = ue.portfolio_break_even(losing, fixed_costs=1_000)
        assert not result["exists"]
        assert math.isnan(result["break_even_revenue"])


# ==========================================================================
# Scenario modelling
# ==========================================================================

class TestScenario:
    BASE = dict(base_price=25.0, base_volume=100_000, base_unit_cost=16.0,
                base_discount=0.12, fixed_costs=400_000, elasticity=-1.8)

    def test_the_base_case_reproduces_its_inputs(self):
        result = sc.evaluate(**self.BASE)
        assert result["net_price"] == pytest.approx(25.0 * 0.88)
        assert result["volume"] == pytest.approx(100_000)
        assert result["revenue"] == pytest.approx(25.0 * 0.88 * 100_000)
        assert result["operating_profit"] == pytest.approx(
            result["contribution"] - 400_000)

    def test_a_price_move_applies_the_elasticity(self):
        result = sc.evaluate(**{**self.BASE, "price_change": 0.10})
        assert result["volume"] < 100_000
        assert result["volume"] == pytest.approx(100_000 * 1.10 ** -1.8, rel=1e-9)

    def test_elasticity_can_be_switched_off(self):
        result = sc.evaluate(**{**self.BASE, "price_change": 0.10,
                                "apply_elasticity": False})
        assert result["volume"] == pytest.approx(100_000)

    def test_underlying_volume_stacks_on_top_of_the_elasticity(self):
        """
        The two are kept apart so a scenario can say "we raise four points and
        lose the one big account" without the model counting the same volume
        twice.
        """
        both = sc.evaluate(**{**self.BASE, "price_change": 0.10,
                              "volume_change": -0.20})
        elastic_only = sc.evaluate(**{**self.BASE, "price_change": 0.10})
        assert both["volume"] == pytest.approx(elastic_only["volume"] * 0.80)

    def test_a_discount_past_the_cap_is_clamped(self):
        result = sc.evaluate(**{**self.BASE, "discount_change": 5.0})
        assert result["discount"] == pytest.approx(0.95)
        assert result["net_price"] > 0

    def test_compare_always_includes_the_base_first(self):
        rows = sc.compare(self.BASE, {"Up": {"price_change": 0.05}})
        assert rows[0]["scenario"] == "Base"
        assert rows[0]["delta"] == 0.0

    def test_three_point_moves_each_input_to_its_own_good_end(self):
        ranges = {"price_change": (-0.05, 0.05), "unit_cost_change": (-0.05, 0.10)}
        rows = {r["scenario"]: r for r in sc.three_point(self.BASE, ranges)}
        # Good is price up and cost down; bad is the reverse.
        assert rows["Best case"]["list_price"] > rows["Base"]["list_price"]
        assert rows["Best case"]["unit_cost"] < rows["Base"]["unit_cost"]
        assert rows["Worst case"]["unit_cost"] > rows["Base"]["unit_cost"]
        assert (rows["Best case"]["operating_profit"]
                > rows["Base"]["operating_profit"]
                > rows["Worst case"]["operating_profit"])

    def test_three_point_says_it_is_not_an_interval(self):
        rows = sc.three_point(self.BASE, {"price_change": (-0.05, 0.05)})
        best = next(r for r in rows if r["scenario"] == "Best case")
        assert "stress test" in best["note"]

    def test_the_tornado_moves_one_input_at_a_time(self):
        ranges = {"price_change": (-0.05, 0.05), "unit_cost_change": (-0.05, 0.10),
                  "volume_change": (-0.10, 0.10)}
        rows = sc.tornado(self.BASE, ranges)
        for row in rows:
            single = sc.evaluate(**{**self.BASE, row["key"]: row["high_value"]})
            assert single["operating_profit"] in (row["upside"], row["downside"])

    def test_the_tornado_is_ranked_and_shares_sum_to_one(self):
        ranges = {"price_change": (-0.05, 0.05), "unit_cost_change": (-0.05, 0.10),
                  "volume_change": (-0.10, 0.10), "fixed_cost_change": (-0.05, 0.05)}
        rows = sc.tornado(self.BASE, ranges)
        swings = [r["swing"] for r in rows]
        assert swings == sorted(swings, reverse=True)
        assert sum(r["share_of_swing"] for r in rows) == pytest.approx(1.0)
        assert rows[-1]["cumulative_share"] == pytest.approx(1.0)

    def test_the_grid_is_the_product_of_its_axes(self):
        grid = sc.sensitivity_grid(
            self.BASE, x_input="price_change", x_values=[-0.02, 0.0, 0.02],
            y_input="unit_cost_change", y_values=[0.0, 0.05])
        assert len(grid) == 6
        centre = next(c for c in grid if c["x"] == 0.0 and c["y"] == 0.0)
        assert centre["delta"] == pytest.approx(0.0)
        assert centre["sign"] == "Unchanged"

    def test_the_break_even_input_finds_the_crossing(self):
        result = sc.break_even_input(self.BASE, input_name="unit_cost_change")
        assert result["exists"]
        at_threshold = sc.evaluate(
            **{**self.BASE, "unit_cost_change": result["threshold"]})
        assert at_threshold["operating_profit"] == pytest.approx(0.0, abs=1.0)

    def test_no_crossing_is_reported_as_good_news_not_as_a_bound(self):
        """A business that stays profitable across the whole range has no
        threshold, and reporting a number there would invent one."""
        comfortable = {**self.BASE, "fixed_costs": 1_000}
        result = sc.break_even_input(comfortable, input_name="fixed_cost_change")
        assert not result["exists"]
        assert "does not cross zero" in result["reason"]
