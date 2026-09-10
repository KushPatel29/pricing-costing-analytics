"""
The pricing arithmetic, asserted directly.

Pricing maths that is quietly wrong does not crash. It produces a plausible
number, the tool keeps working, and the gross margin just comes in light. So
the tests that matter here are the ones that pin a *convention* -- additive
versus compounding discounts, margin on pocket versus on list, which side of a
variance is unfavourable -- because those are the ones where two defensible
readings exist and only one is implemented.
"""

from __future__ import annotations

import math

import pytest

from pricing import bundles, competitive, elasticity, guardrails, segmentation, variance
from pricing.waterfall import (
    ALL_DEDUCTIONS,
    aggregate_waterfall,
    build_waterfall,
    invoice_price,
    leakage_percent,
    net_price,
    pocket_margin_percent,
    pocket_price,
    waterfall_steps,
)

# ==========================================================================
# Waterfall
# ==========================================================================

class TestWaterfall:
    def test_discounts_are_additive_on_list_not_compounding(self):
        """
        10% then 5% is 15% off list here, not 14.5%. Both conventions exist;
        quoting systems use the additive one because it is what a salesperson
        means by "another five points". Mixing them moves every price by a
        fraction of a percent, which is exactly the size of error that nobody
        catches and everybody pays.
        """
        deductions = {"volume_discount": 1.00, "contract_discount": 0.50}
        assert invoice_price(10.0, deductions) == pytest.approx(8.50)
        # The compounding reading would give 10 * 0.9 * 0.95 = 8.55.
        assert invoice_price(10.0, deductions) != pytest.approx(8.55)

    def test_the_four_levels_descend(self):
        result = build_waterfall(
            list_price=20.0, final_cost=11.0,
            deductions={"volume_discount": 1.5, "rebate": 0.4, "freight_out": 0.25},
        )
        assert (result["list_price"] > result["invoice_price"]
                > result["net_price"] > result["pocket_price"])

    def test_margin_is_expressed_on_pocket_not_on_list(self):
        """
        A margin quoted on list is the number that lets a deal look healthy
        while losing money. At 20% leakage a 15%-on-list margin is under 6% on
        what is actually kept.
        """
        deductions = {"volume_discount": 2.0}
        pocket = pocket_price(10.0, deductions)
        assert pocket == 8.0
        assert pocket_margin_percent(10.0, deductions, 6.0) == pytest.approx((8 - 6) / 8)
        # On list it would look like (10-6)/10 = 40%, which is not what we keep.
        assert pocket_margin_percent(10.0, deductions, 6.0) < 0.40

    def test_a_stack_of_discounts_past_a_hundred_percent_clamps_at_zero(self):
        """
        A negative price would propagate into every margin downstream as a
        plausible-looking positive number the moment it met a negative cost.
        """
        assert invoice_price(10.0, {"volume_discount": 14.0}) == 0.0
        assert net_price(10.0, {"volume_discount": 14.0, "rebate": 1.0}) == 0.0

    def test_leakage_is_the_whole_distance_from_list_to_pocket(self):
        deductions = {k: 0.1 for k in ALL_DEDUCTIONS}
        assert leakage_percent(10.0, deductions) == pytest.approx(len(ALL_DEDUCTIONS) * 0.1 / 10)

    def test_missing_and_junk_deductions_are_zero_not_an_error(self):
        assert pocket_price(10.0, {"volume_discount": None, "rebate": "n/a"}) == 10.0
        assert pocket_price(10.0, None) == 10.0

    def test_steps_reconcile_to_the_levels(self):
        deductions = {"volume_discount": 1.0, "rebate": 0.3, "order_handling": 0.2}
        steps = waterfall_steps(list_price=12.0, final_cost=7.0, deductions=deductions)
        pocket = next(s for s in steps if s["label"] == "Pocket price")["amount"]
        assert pocket == pytest.approx(pocket_price(12.0, deductions))
        margin = next(s for s in steps if s["label"] == "Pocket margin")["amount"]
        assert margin == pytest.approx(pocket - 7.0)

    def test_a_subtotal_bar_moves_the_running_total_by_nothing(self):
        """
        A waterfall plots each bar as a step from the running total. A subtotal
        carrying its absolute value is added on top of the total it summarises,
        and the chart closes at roughly twice the real figure -- drawn, scaled
        and wrong, with nothing raised.
        """
        steps = waterfall_steps(list_price=12.0, final_cost=7.0,
                                deductions={"volume_discount": 1.0})
        for step in steps:
            if step["kind"] == "total" and step["label"] != "List price":
                assert step["delta"] == pytest.approx(0.0), step["label"]
        assert steps[0]["delta"] == pytest.approx(12.0)

    def test_the_deltas_sum_to_the_closing_figure(self):
        """Which is the whole property a waterfall is drawn to show."""
        steps = waterfall_steps(list_price=12.0, final_cost=7.0,
                                deductions={"volume_discount": 1.0, "rebate": 0.3})
        assert sum(s["delta"] for s in steps) == pytest.approx(steps[-1]["running"])

    def test_the_amount_a_reader_recognises_is_left_alone(self):
        """Charts read `delta`; cards and tables read `amount`, which stays the
        number someone would recognise from an invoice."""
        steps = waterfall_steps(list_price=12.0, final_cost=7.0,
                                deductions={"volume_discount": 1.0})
        pocket = next(s for s in steps if s["label"] == "Pocket price")
        assert pocket["amount"] == pytest.approx(11.0)
        assert pocket["delta"] == pytest.approx(0.0)

    def test_zero_deductions_are_dropped_from_the_chart(self):
        """A waterfall with nine invisible bars is unreadable, and a deduction
        a customer does not take is not information."""
        steps = waterfall_steps(list_price=10.0, deductions={"volume_discount": 1.0,
                                                             "rebate": 0.0})
        assert not any(s["label"] == "Rebate accrual" for s in steps)

    def test_aggregation_is_volume_weighted_not_an_average_of_averages(self):
        """
        The two differ whenever the big customers get the big discounts, which
        is always. A small line at a small discount and a huge line at a huge
        one must not average to the middle.
        """
        rows = [
            {"list_price": 10.0, "final_cost": 6.0, "quantity": 1.0, "volume_discount": 0.0},
            {"list_price": 10.0, "final_cost": 6.0, "quantity": 99.0, "volume_discount": 2.0},
        ]
        result = aggregate_waterfall(rows)
        assert result["pocket_price"] == pytest.approx(10 - (0 * 1 + 2 * 99) / 100)
        assert result["pocket_price"] == pytest.approx(8.02)
        # The unweighted mean of the two per-unit pockets would be 9.00.
        assert result["pocket_price"] < 9.0

    def test_an_empty_book_does_not_divide_by_zero(self):
        assert aggregate_waterfall([])["pocket_price"] == 0.0


# ==========================================================================
# Elasticity
# ==========================================================================

class TestElasticity:
    def test_a_known_demand_curve_is_recovered(self):
        """Generate q = 1000 * p**-1.8 exactly and read the exponent back."""
        prices = [8 + i * 0.4 for i in range(20)]
        quantities = [1000 * p ** -1.8 for p in prices]
        fit = elasticity.estimate_elasticity(prices, quantities)
        assert fit["elasticity"] == pytest.approx(-1.8, abs=1e-6)
        assert fit["r_squared"] == pytest.approx(1.0, abs=1e-9)
        assert fit["usable"]

    def test_a_flat_price_is_refused_rather_than_fitted(self):
        fit = elasticity.estimate_elasticity([10.0] * 30, [500 + i for i in range(30)])
        assert not fit["usable"]
        assert "nothing to regress" in fit["reason"]

    def test_too_few_points_is_refused(self):
        fit = elasticity.estimate_elasticity([9, 10, 11], [110, 100, 92])
        assert not fit["usable"]
        assert "observations" in fit["reason"]

    def test_an_upward_slope_is_reported_as_not_a_demand_curve(self):
        prices = [8 + i * 0.4 for i in range(20)]
        quantities = [100 * p ** 1.2 for p in prices]
        fit = elasticity.estimate_elasticity(prices, quantities)
        assert not fit["usable"]
        assert "downward" in fit["reason"]

    def test_a_trend_control_changes_the_answer(self):
        """
        A control is not decoration. With volume trending up while price
        trends up, the uncontrolled slope is attenuated toward zero; holding
        the trend constant recovers the real one.

        Price needs movement of its *own* here, not only the trend, or ln(p)
        is an affine function of t and the two regressors are collinear -- the
        fit is then rank-deficient and the coefficient is split between them
        arbitrarily. That is not a contrivance for the test: it is why the
        generator staggers price reviews across the catalogue instead of
        moving every price on the same quarter.
        """
        prices = [10 * (1.01 ** t) * (1 + 0.07 * math.sin(2.3 * t)) for t in range(36)]
        # True elasticity -2, plus a +1.5%/period demand trend.
        quantities = [500 * (p / 10) ** -2 * (1.015 ** t) for t, p in enumerate(prices)]
        naive = elasticity.estimate_elasticity(prices, quantities)
        controlled = elasticity.estimate_elasticity(
            prices, quantities, controls={"trend": list(range(36))}
        )
        assert naive["elasticity"] > -1.6            # attenuated by the trend
        assert controlled["elasticity"] == pytest.approx(-2.0, abs=0.02)

    def test_a_price_that_only_moves_with_time_cannot_be_told_apart_from_it(self):
        """
        The failure mode the test above is constructed to avoid, asserted
        directly: when every price move is the trend, the fit is rank-deficient
        and the price coefficient means nothing. The r-squared stays high, so
        nothing about the output announces it -- which is why the generator
        staggers its price reviews.
        """
        prices = [10 * (1.01 ** t) for t in range(36)]
        quantities = [500 * (p / 10) ** -2 * (1.015 ** t) for t, p in enumerate(prices)]
        controlled = elasticity.estimate_elasticity(
            prices, quantities, controls={"trend": list(range(36))}
        )
        assert controlled["r_squared"] == pytest.approx(1.0, abs=1e-6)
        assert controlled["elasticity"] != pytest.approx(-2.0, abs=0.5)

    def test_deseasonalising_removes_a_seasonal_swing(self):
        months = [(i % 12) + 1 for i in range(36)]
        season = {m: 1.0 + 0.3 * math.cos(2 * math.pi * m / 12) for m in range(1, 13)}
        base = [100 * season[m] for m in months]
        flat = elasticity.deseasonalise(base, months)
        assert max(flat) / min(flat) == pytest.approx(1.0, abs=1e-9)

    def test_seasonal_factors_are_computed_in_logs(self):
        """
        A December 30% above average and a February 30% below average average
        to 1.0 in logs and to 1.045 in levels. The second leaves a slow drift
        in the deseasonalised series that a trend term then eats.
        """
        factors = elasticity.seasonal_factors([130, 70] * 12, [12, 2] * 12)
        assert factors[12] * factors[2] == pytest.approx(1.0, abs=1e-12)

    @pytest.mark.parametrize(
        "e,cost,expected", [(-2.0, 10.0, 20.0), (-3.0, 6.0, 9.0), (-1.5, 4.0, 12.0)]
    )
    def test_the_optimal_price_follows_the_markup_rule(self, e, cost, expected):
        result = elasticity.optimal_price(cost, e)
        assert result["exists"]
        assert result["price"] == pytest.approx(expected)
        assert result["implied_margin"] == pytest.approx(1 / -e)

    def test_inelastic_demand_has_no_interior_optimum(self):
        """
        At e = -0.6 the formula returns a *negative* price. A tool that prints
        it has just advised paying customers to take the product.
        """
        result = elasticity.optimal_price(10.0, -0.6)
        assert not result["exists"]
        assert not result["actionable"]
        assert "inelastic" in result["reason"]

    def test_an_optimum_near_minus_one_is_reported_but_not_actionable(self):
        """
        Mathematically correct and worthless: constant elasticity was a local
        approximation, and nothing about a fit around today's price licenses an
        extrapolation to fifteen times it.
        """
        result = elasticity.optimal_price(10.0, -1.07)
        assert result["exists"]
        assert not result["actionable"]
        assert result["markup"] > 10
        assert "not a recommendation" in result["reason"]

    @pytest.mark.parametrize(
        "cut,margin,hurdle",
        [(-0.05, 0.30, 0.20), (-0.05, 0.15, 0.50), (-0.10, 0.40, 1 / 3)],
    )
    def test_the_break_even_volume_hurdle(self, cut, margin, hurdle):
        assert elasticity.break_even_volume_change(cut, margin) == pytest.approx(hurdle)

    def test_a_price_rise_reports_the_volume_it_can_afford_to_lose(self):
        assert elasticity.break_even_volume_change(0.05, 0.30) == pytest.approx(-1 / 7)

    def test_a_cut_past_variable_cost_can_never_be_made_up_on_volume(self):
        assert elasticity.break_even_volume_change(-0.35, 0.30) == float("inf")

    def test_the_break_even_hurdle_and_the_forecast_agree_at_the_boundary(self):
        """
        At exactly the break-even elasticity the margin change must be zero.
        This is the round trip that catches a sign error in either function.
        """
        impact = elasticity.price_change_impact(
            base_quantity=1000, base_price=10.0, unit_cost=7.0,
            price_change_pct=-0.05, elasticity=0.0,
        )
        hurdle = impact["break_even_volume_change_pct"]
        matched = elasticity.price_change_impact(
            base_quantity=1000, base_price=10.0, unit_cost=7.0, price_change_pct=-0.05,
            elasticity=math.log(1 + hurdle) / math.log(0.95),
        )
        assert matched["margin_change"] == pytest.approx(0.0, abs=1e-6)

    def test_the_response_curve_marks_today_and_the_profit_maximum(self):
        points = elasticity.price_response_curve(
            base_quantity=1000, base_price=10.0, unit_cost=6.0, elasticity=-2.0,
        )
        assert sum(p["is_current"] for p in points) == 1
        assert sum(p["is_profit_max"] for p in points) == 1
        current = next(p for p in points if p["is_current"])
        assert current["price"] == pytest.approx(10.0)

    def test_cross_elasticity_names_the_relationship(self):
        prices = [8 + i * 0.4 for i in range(20)]
        rival_volume = [50 * p ** 0.9 for p in prices]
        result = elasticity.cross_elasticity(prices, rival_volume)
        assert result["relationship"] == "substitutes"
        assert result["usable"]


# ==========================================================================
# Competitive
# ==========================================================================

class TestCompetitive:
    def test_the_market_price_defaults_to_the_median(self):
        """
        One clearance price shifts a five-observation mean by two points and
        the median not at all. Competitive files contain clearance prices.
        """
        observations = [{"price": p} for p in (10, 10.2, 10.1, 9.9, 2.0)]
        assert competitive.market_price(observations) == pytest.approx(10.0)

    def test_weights_are_used_when_supplied(self):
        observations = [{"price": 10.0, "share": 1.0}, {"price": 20.0, "share": 3.0}]
        assert competitive.market_price(
            observations, weight_key="share") == pytest.approx(17.5)

    def test_a_stale_observation_counts_for_less(self):
        assert competitive.freshness_weight(0) == 1.0
        assert competitive.freshness_weight(45) == pytest.approx(0.5)
        assert competitive.freshness_weight(90) == pytest.approx(0.25)
        # A future-dated scrape weighs 1, not more than 1.
        assert competitive.freshness_weight(-30) == 1.0

    def test_the_index_reports_the_age_of_the_data_it_used(self):
        observations = [
            {"price": 10.0, "competitor": "A", "age": 3},
            {"price": 11.0, "competitor": "B", "age": 120},
        ]
        result = competitive.price_index(10.5, observations, age_key="age")
        assert result["max_age_days"] == 120
        assert result["competitors"] == 2

    def test_no_coverage_is_reported_rather_than_assumed_to_be_parity(self):
        """
        Treating "no observation" as "at parity" is the most common way a price
        index lies.
        """
        result = competitive.price_index(10.0, [])
        assert math.isnan(result["index"])
        assert result["position"] == "No coverage"

    @pytest.mark.parametrize(
        "index,band",
        [(80, "Deep discount"), (92, "Below market"), (100, "At market"),
         (110, "Premium"), (130, "Super-premium")],
    )
    def test_position_bands(self, index, band):
        assert competitive.market_position(index) == band

    def test_gaps_are_ranked_by_revenue_not_by_the_size_of_the_gap(self):
        """
        A fourteen-point premium on a product nobody buys is a curiosity;
        three points on the top line is the whole conversation.
        """
        rows = [
            {"index": 130, "revenue": 100},
            {"index": 116, "revenue": 900_000},
        ]
        result = competitive.competitive_gaps(rows)
        assert result["overpriced"][0]["revenue"] == 900_000

    @staticmethod
    def _index(steps: list[float], base: float = 100.0) -> list[float]:
        """Compound a list of period-over-period changes into a level series."""
        out = [base]
        for step in steps:
            out.append(out[-1] * (1 + step))
        return out

    # A varying series, not a smooth compound: a constant growth rate has no
    # variation to regress against, which is what MIN_INDEX_VARIATION refuses.
    STEPS = [0.03, -0.01, 0.02, 0.05, -0.02, 0.01, 0.04, -0.03,
             0.02, 0.06, -0.01, 0.00, 0.03, -0.04, 0.02, 0.01]

    def test_full_passthrough_is_recovered_from_a_clean_series(self):
        costs = self._index(self.STEPS)
        prices = self._index(self.STEPS, base=50.0)
        result = competitive.passthrough_ratio(costs, prices)
        assert result["passthrough"] == pytest.approx(1.0, abs=1e-9)
        assert result["r_squared"] == pytest.approx(1.0, abs=1e-9)

    def test_partial_passthrough_is_recovered(self):
        costs = self._index(self.STEPS)
        prices = self._index([0.6 * s for s in self.STEPS], base=50.0)
        result = competitive.passthrough_ratio(costs, prices)
        assert result["passthrough"] == pytest.approx(0.6, abs=1e-9)

    def test_a_constant_growth_rate_carries_no_passthrough_estimate(self):
        """
        A series that moved by the same amount every period is a constant
        regressor, and the slope is then whatever floating-point noise survives
        in the denominator -- on a smooth 2%-a-month series that came out as
        -0.25, which reads as a seller cutting price into a rising market.
        """
        costs = [100 * 1.02 ** t for t in range(30)]
        prices = [50 * 1.012 ** t for t in range(30)]
        result = competitive.passthrough_ratio(costs, prices)
        assert math.isnan(result["passthrough"])

    def test_the_best_lag_is_chosen_on_fit_not_on_coefficient_size(self):
        """The question is *when* cost reaches price; picking the biggest
        coefficient would just find the noisiest lag."""
        costs = [100 * (1.03 if t == 5 else 1.0) ** 1 for t in range(20)]
        costs = [100.0] * 20
        costs[5] = 105.0
        prices = [50.0] * 20
        prices[7] = 52.5                     # the same move, two periods later
        result = competitive.best_passthrough_lag(costs, prices, max_lag=4)
        assert result["lag"] == 2

    def test_an_index_linked_clause_prices_the_move_at_its_stated_share(self):
        assert competitive.indexed_price(10.0, 110.0, 100.0, 1.0) == pytest.approx(11.0)
        assert competitive.indexed_price(10.0, 110.0, 100.0, 0.6) == pytest.approx(10.6)
        assert competitive.indexed_price(10.0, 110.0, 100.0, 0.0) == pytest.approx(10.0)


# ==========================================================================
# Variance
# ==========================================================================

class TestVariance:
    def test_positive_is_unfavourable_everywhere(self):
        """The one convention that has to hold across the whole module."""
        overspend = variance.purchase_price_variance(
            actual_price=11.0, standard_price=10.0, actual_quantity=100)
        underspend = variance.purchase_price_variance(
            actual_price=9.0, standard_price=10.0, actual_quantity=100)
        assert overspend["variance"] == 100 and overspend["verdict"] == "Unfavourable"
        assert underspend["variance"] == -100 and underspend["verdict"] == "Favourable"

    def test_purchase_price_variance_is_priced_on_actual_quantity(self):
        """PPV is a buying result. Charging it on the quantity actually bought
        keeps the usage decision out of it."""
        result = variance.purchase_price_variance(
            actual_price=8.50, standard_price=8.00, actual_quantity=1200)
        assert result["variance"] == pytest.approx(600.0)
        assert result["quantity"] == 1200

    def test_yield_variance_prices_excess_input_at_standard(self):
        """
        68% standard recovery on 1,000 lb of output allows 1,470 lb of input.
        Consuming 1,540 is 70 lb of loss at standard cost -- and it is a
        conversation with production, not with purchasing, which is why it is
        valued at standard price rather than at what was actually paid.
        """
        result = variance.yield_variance(
            output_units=1000, actual_input_units=1540,
            standard_sellable_rate=0.68, standard_input_price=5.0)
        assert result["standard_input_units"] == pytest.approx(1000 / 0.68)
        assert result["excess_units"] == pytest.approx(1540 - 1000 / 0.68)
        assert result["variance"] == pytest.approx(result["excess_units"] * 5.0)
        assert result["verdict"] == "Unfavourable"

    def test_yield_variance_accepts_recovery_as_a_percentage_or_a_fraction(self):
        as_fraction = variance.yield_variance(
            output_units=1000, actual_input_units=1500,
            standard_sellable_rate=0.68, standard_input_price=5.0)
        as_percent = variance.yield_variance(
            output_units=1000, actual_input_units=1500,
            standard_sellable_rate=68, standard_input_price=5.0)
        assert as_fraction["variance"] == pytest.approx(as_percent["variance"])

    def test_overhead_volume_variance_is_not_overspending(self):
        """
        Nobody overspent -- the plant ran below the volume the rate was set on,
        and the unabsorbed pool lands in cost of sales anyway.
        """
        spending = variance.overhead_spending_variance(
            actual_overhead=100_000, budgeted_rate=0.20, actual_driver=500_000)
        volume = variance.overhead_volume_variance(
            budgeted_driver=600_000, actual_driver=500_000, budgeted_rate=0.20)
        assert spending["variance"] == pytest.approx(0.0)
        assert volume["variance"] == pytest.approx(20_000)
        assert volume["verdict"] == "Unfavourable"

    def test_budget_variance_interprets_the_sign_per_line(self):
        """Revenue over budget is favourable; cost over budget is not."""
        revenue = variance.budget_variance(actual=110, budget=100)
        cost = variance.budget_variance(actual=110, budget=100, higher_is_better=False)
        assert revenue["verdict"] == "Favourable"
        assert cost["verdict"] == "Unfavourable"
        assert revenue["variance"] == cost["variance"] == 10

    def test_the_components_sum_to_the_total(self):
        components = [
            variance.purchase_price_variance(
                actual_price=11, standard_price=10, actual_quantity=100),
            variance.labour_rate_variance(
                actual_rate=21, standard_rate=20, actual_hours=50),
        ]
        total = variance.total_cost_variance(components)
        assert total["variance"] == pytest.approx(150)
        assert len(total["components"]) == 2


class TestBridge:
    @staticmethod
    def _rows(spec):
        return [{"product_id": pid, "quantity": q, "price": p, "cost": c}
                for pid, q, p, c in spec]

    def test_the_effects_sum_to_the_change_exactly(self):
        prior = self._rows([("A", 100, 10.0, 6.0), ("B", 50, 20.0, 14.0),
                            ("C", 30, 5.0, 3.0)])
        current = self._rows([("A", 120, 10.5, 6.4), ("B", 40, 21.0, 15.2),
                              ("D", 25, 8.0, 5.0)])
        bridge = variance.margin_bridge(prior, current)
        assert bridge["residual"] == pytest.approx(0.0, abs=1e-9)
        assert bridge["explained"] == pytest.approx(bridge["margin_change"])

    def test_a_pure_price_move_lands_entirely_in_price(self):
        prior = self._rows([("A", 100, 10.0, 6.0)])
        current = self._rows([("A", 100, 11.0, 6.0)])
        effects = {e["effect"]: e["amount"] for e in
                   variance.margin_bridge(prior, current)["effects"]}
        assert effects["Price"] == pytest.approx(100.0)
        assert effects["Cost"] == pytest.approx(0.0)
        assert effects["Volume"] == pytest.approx(0.0)
        assert effects["Mix"] == pytest.approx(0.0)

    def test_a_pure_volume_move_lands_entirely_in_volume(self):
        prior = self._rows([("A", 100, 10.0, 6.0), ("B", 100, 20.0, 12.0)])
        current = self._rows([("A", 150, 10.0, 6.0), ("B", 150, 20.0, 12.0)])
        effects = {e["effect"]: e["amount"] for e in
                   variance.margin_bridge(prior, current)["effects"]}
        assert effects["Volume"] == pytest.approx(600.0)
        assert effects["Mix"] == pytest.approx(0.0, abs=1e-9)

    def test_a_pure_mix_shift_lands_entirely_in_mix(self):
        """Same total pounds, richer basket. Volume must not move."""
        prior = self._rows([("A", 100, 10.0, 6.0), ("B", 100, 20.0, 12.0)])
        current = self._rows([("A", 50, 10.0, 6.0), ("B", 150, 20.0, 12.0)])
        effects = {e["effect"]: e["amount"] for e in
                   variance.margin_bridge(prior, current)["effects"]}
        assert effects["Volume"] == pytest.approx(0.0, abs=1e-9)
        assert effects["Mix"] == pytest.approx(200.0)

    def test_a_launch_is_not_counted_as_volume(self):
        """
        There is no prior price to compare a product launched this year
        against, and folding it into volume is how a launch flatters a bridge.
        """
        prior = self._rows([("A", 100, 10.0, 6.0)])
        current = self._rows([("A", 100, 10.0, 6.0), ("NEW", 40, 15.0, 9.0)])
        effects = {e["effect"]: e["amount"] for e in
                   variance.margin_bridge(prior, current)["effects"]}
        assert effects["New products"] == pytest.approx(240.0)
        assert effects["Volume"] == pytest.approx(0.0)
        assert effects["Mix"] == pytest.approx(0.0)

    def test_a_discontinued_product_is_a_loss_not_a_volume_decline(self):
        prior = self._rows([("A", 100, 10.0, 6.0), ("OLD", 40, 15.0, 9.0)])
        current = self._rows([("A", 100, 10.0, 6.0)])
        effects = {e["effect"]: e["amount"] for e in
                   variance.margin_bridge(prior, current)["effects"]}
        assert effects["Lost products"] == pytest.approx(-240.0)
        assert effects["Volume"] == pytest.approx(0.0)

    def test_the_revenue_bridge_also_sums_exactly(self):
        prior = self._rows([("A", 100, 10.0, 6.0), ("B", 50, 20.0, 14.0)])
        current = self._rows([("A", 130, 10.4, 6.1), ("C", 10, 9.0, 5.0)])
        bridge = variance.revenue_bridge(prior, current)
        assert bridge["residual"] == pytest.approx(0.0, abs=1e-9)

    def test_transaction_level_input_is_aggregated_first(self):
        """Rows arriving one per invoice line must give the same answer as the
        same volume arriving pre-aggregated."""
        split = self._rows([("A", 60, 10.0, 6.0), ("A", 40, 10.0, 6.0)])
        whole = self._rows([("A", 100, 10.0, 6.0)])
        current = self._rows([("A", 100, 11.0, 6.0)])
        assert (variance.margin_bridge(split, current)["margin_change"]
                == pytest.approx(variance.margin_bridge(whole, current)["margin_change"]))


# ==========================================================================
# Bundles
# ==========================================================================

class TestBundles:
    COMPONENTS = [{"price": 10.0, "cost": 7.0}, {"price": 6.0, "cost": 4.0}]

    def test_the_break_even_cannibalisation_is_margin_kept_over_margin_given_up(self):
        """
        Setting incremental margin to zero and solving gives c* = B/S. A bundle
        keeping 80% of standalone margin survives up to 80% cannibalisation.
        """
        bundle = bundles.build_bundle(self.COMPONENTS, discount=0.10)
        expected = bundle["bundle_margin"] / bundle["standalone_margin"]
        assert bundles.break_even_cannibalisation(bundle) == pytest.approx(expected)

    def test_incremental_margin_is_exactly_zero_at_the_break_even_rate(self):
        """The definition, checked against the implementation."""
        bundle = bundles.build_bundle(self.COMPONENTS, discount=0.12)
        rate = bundles.break_even_cannibalisation(bundle)
        result = bundles.incremental_margin(
            bundle, expected_units=5000, cannibalisation_rate=rate)
        assert result["incremental_margin"] == pytest.approx(0.0, abs=1e-9)

    def test_above_the_break_even_rate_the_bundle_destroys_value(self):
        bundle = bundles.build_bundle(self.COMPONENTS, discount=0.12)
        rate = bundles.break_even_cannibalisation(bundle)
        worse = bundles.incremental_margin(
            bundle, expected_units=5000, cannibalisation_rate=min(rate + 0.1, 1.0))
        assert worse["incremental_margin"] < 0
        assert worse["verdict"] == "Destroys value"
        assert worse["headroom"] < 0

    def test_a_price_override_back_solves_the_discount(self):
        bundle = bundles.build_bundle(self.COMPONENTS, price_override=14.0)
        assert bundle["standalone_price"] == 16.0
        assert bundle["discount"] == pytest.approx(0.125)

    def test_a_bundle_with_no_standalone_margin_has_nothing_to_protect(self):
        bundle = bundles.build_bundle(
            [{"price": 5.0, "cost": 5.0}], discount=0.10)
        assert bundles.break_even_cannibalisation(bundle) == 0.0

    def test_the_discount_sweep_marks_exactly_one_best_point(self):
        points = bundles.optimal_bundle_discount(
            self.COMPONENTS, expected_units=1000, cannibalisation_rate=0.3,
            demand_lift_per_point=20,
        )
        assert sum(p["is_best"] for p in points) == 1

    def test_with_no_demand_lift_the_answer_is_discount_nothing(self):
        """The honest answer when nobody can put a number on the lift, which is
        why the parameter has no default guess baked in."""
        points = bundles.optimal_bundle_discount(
            self.COMPONENTS, expected_units=1000, cannibalisation_rate=0.3)
        best = next(p for p in points if p["is_best"])
        assert best["discount"] == pytest.approx(0.0)

    def test_attach_value_is_reported_per_anchor_unit(self):
        result = bundles.attach_value(
            anchor_units=10_000, attach_rate=0.25, addon_price=9.0, addon_cost=6.0)
        assert result["addon_units"] == 2500
        assert result["addon_margin"] == pytest.approx(7500)
        assert result["margin_per_anchor_unit"] == pytest.approx(0.75)

    def test_a_ladder_reports_the_step_between_rungs(self):
        rungs = bundles.price_ladder(
            10.0, [{"name": "Good", "margin": 0.15},
                   {"name": "Better", "margin": 0.25, "cost_uplift": 0.5}])
        assert rungs[0]["step_from_previous"] == 0.0
        assert rungs[1]["step_from_previous"] == pytest.approx(
            rungs[1]["price"] - rungs[0]["price"])
        assert rungs[1]["price"] > rungs[0]["price"]


# ==========================================================================
# Guardrails
# ==========================================================================

class TestGuardrails:
    def test_the_floor_price_is_grossed_up_for_the_deductions_taken(self):
        """
        Quoting a floor that ignores the deductions this customer always takes
        is how a deal clears the guardrail on screen and misses in the ledger.
        """
        bare = guardrails.price_band(10.0, floor_margin=0.12)
        loaded = guardrails.price_band(10.0, floor_margin=0.12,
                                       deductions={"rebate": 0.5, "freight_out": 0.25})
        assert loaded["floor_price"] == pytest.approx(bare["floor_price"] + 0.75)

    def test_a_price_at_the_floor_realises_exactly_the_floor_margin(self):
        deductions = {"rebate": 0.5, "freight_out": 0.25}
        band = guardrails.price_band(10.0, floor_margin=0.12, deductions=deductions)
        score = guardrails.score_deal(
            list_price=band["floor_price"], final_cost=10.0,
            deductions=deductions, floor_margin=0.12)
        assert score["pocket_margin_pct"] == pytest.approx(0.12, abs=1e-9)
        assert score["within_guardrail"]

    @pytest.mark.parametrize(
        "margin,approver",
        [(0.22, "None"), (0.21, "Rep"), (0.18, "Sales manager"),
         (0.14, "Commercial director"), (0.02, "VP Finance")],
    )
    def test_the_escalation_ladder_is_on_the_gap_to_target(self, margin, approver):
        """
        Two deals at ten points off are not the same deal when one product
        carries thirty-eight points of margin and the other nineteen.
        """
        assert guardrails.approval_tier(margin, 0.22)["approver"] == approver

    def test_a_loss_making_line_is_named_as_such(self):
        score = guardrails.score_deal(
            list_price=10.0, final_cost=12.0, deductions={"volume_discount": 1.0})
        assert score["verdict"] == "Loss-making"
        assert not score["within_guardrail"]

    def test_the_scan_ranks_by_money_not_by_severity(self):
        """
        A scan sorted by how far below floor a line sits puts the analyst on a
        forty-dollar account for the first twenty minutes.
        """
        rows = [
            {"product_id": "small", "pocket_margin_pct": -0.5, "floor_margin": 0.12,
             "extended_margin": 40},
            {"product_id": "big", "pocket_margin_pct": 0.05, "floor_margin": 0.12,
             "extended_margin": 900_000},
        ]
        findings = guardrails.scan_exceptions(rows)
        assert findings[0]["product_id"] == "big"

    def test_one_line_can_breach_more_than_one_rule(self):
        """The fixes differ, so both conversations have to happen."""
        rows = [{"product_id": "X", "pocket_margin_pct": -0.1, "floor_margin": 0.12,
                 "leakage_pct": 0.4, "extended_margin": 1000}]
        codes = {f["code"] for f in guardrails.scan_exceptions(rows)}
        assert {"LOSS", "LEAKAGE"} <= codes

    def test_every_finding_carries_an_action_in_plain_words(self):
        rows = [{"product_id": "X", "pocket_margin_pct": 0.02, "floor_margin": 0.12,
                 "extended_margin": 10}]
        for finding in guardrails.scan_exceptions(rows):
            assert len(finding["action"]) > 30

    def test_every_finding_carries_a_traffic_light(self):
        """
        The exception report is read by people who are not going to memorise
        that severity 2 is worse than severity 5. A colour that every rule
        carries is what makes the table sortable by urgency rather than by rank.
        """
        rows = [{"product_id": "X", "pocket_margin_pct": -0.1, "floor_margin": 0.12,
                 "leakage_pct": 0.4, "days_since_price_change": 400,
                 "extended_margin": 1000}]
        findings = guardrails.scan_exceptions(rows)
        alerts = {f["code"]: f["alert"] for f in findings}
        assert alerts["LOSS"] == "Red"
        assert alerts["LEAKAGE"] == "Amber"
        assert alerts["STALE"] == "Green"
        assert set(alerts.values()) <= set(guardrails.ALERT_ORDER)

    def test_the_colour_follows_when_the_money_moves_not_how_much(self):
        """
        A large stale listing outranking a small loss-making one is the wrong
        morning. Money already leaving is red however small it is.
        """
        rows = [
            {"product_id": "tiny_loss", "pocket_margin_pct": -0.4, "extended_margin": 5},
            {"product_id": "huge_stale", "pocket_margin_pct": 0.4,
             "days_since_price_change": 900, "extended_margin": 900_000},
        ]
        by_product = {f["product_id"]: f["alert"] for f in guardrails.scan_exceptions(rows)}
        assert by_product["tiny_loss"] == "Red"
        assert by_product["huge_stale"] == "Green"

    def test_the_summary_carries_the_colour_through(self):
        rows = [{"product_id": "A", "pocket_margin_pct": -0.1, "extended_margin": 100}]
        summary = guardrails.exception_summary(guardrails.scan_exceptions(rows))
        assert next(s for s in summary if s["code"] == "LOSS")["alert"] == "Red"

    def test_the_summary_totals_the_money_per_rule(self):
        rows = [
            {"product_id": "A", "pocket_margin_pct": -0.1, "extended_margin": 100},
            {"product_id": "B", "pocket_margin_pct": -0.2, "extended_margin": 300},
        ]
        summary = guardrails.exception_summary(guardrails.scan_exceptions(rows))
        loss = next(s for s in summary if s["code"] == "LOSS")
        assert loss["count"] == 2
        assert loss["margin_at_risk"] == pytest.approx(400)


# ==========================================================================
# Segmentation
# ==========================================================================

class TestSegmentation:
    @staticmethod
    def _quotes(slope=-10.0, intercept=1.0, n=800):
        """Deterministic quotes generated from a known logistic."""
        quotes = []
        for i in range(n):
            ratio = 0.80 + 0.5 * (i % 100) / 99
            probability = 1 / (1 + math.exp(-(intercept + slope * (ratio - 1))))
            # Deterministic threshold rather than a draw, so the test does not
            # depend on a random seed: every ratio appears eight times and wins
            # the number of times the probability says it should.
            won = (i // 100) < round(probability * 8)
            quotes.append({"price_ratio": ratio, "won": won})
        return quotes

    def test_a_known_win_curve_is_recovered(self):
        fit = segmentation.fit_win_curve(self._quotes(slope=-10.0))
        assert fit["converged"]
        assert fit["usable"]
        assert fit["slope"] == pytest.approx(-10.0, rel=0.25)

    def test_the_indifference_price_is_where_the_curve_crosses_half(self):
        fit = segmentation.fit_win_curve(self._quotes(slope=-12.0, intercept=0.0))
        assert fit["indifference_price"] == pytest.approx(1.0, abs=0.03)

    def test_an_upward_slope_is_reported_as_unusable(self):
        """
        Winning the dearer quotes means something other than price is driving
        the sample -- usually urgency. Inverting it would be worse than
        refusing it.
        """
        fit = segmentation.fit_win_curve(self._quotes(slope=+10.0))
        assert not fit["usable"]
        assert "not downward" in fit["reason"]

    def test_a_sample_with_no_losses_cannot_be_fitted(self):
        quotes = [{"price_ratio": 0.9 + i / 200, "won": True} for i in range(60)]
        fit = segmentation.fit_win_curve(quotes)
        assert not fit["usable"]
        assert "no variation" in fit["reason"]

    def test_thin_price_buckets_are_dropped_from_the_observed_rates(self):
        """
        A bucket of two with one win reads as a 50% win rate and would be drawn
        the same size as a bucket of four hundred.
        """
        quotes = ([{"price_ratio": 1.0, "won": True}] * 40
                  + [{"price_ratio": 1.4, "won": True}] * 2)
        buckets = segmentation.observed_win_rates(quotes)
        assert all(b["quotes"] >= 5 for b in buckets)
        assert not any(b["price_ratio"] > 1.3 for b in buckets)

    def test_price_band_percentiles_are_volume_weighted(self):
        """
        A band computed over four hundred small accounts and one large one must
        not describe the four hundred.
        """
        rows = ([{"pocket_price": 10.0, "quantity": 1.0}] * 9
                + [{"pocket_price": 20.0, "quantity": 900.0}])
        stats = segmentation.price_band_stats(rows)
        assert stats["median"] == pytest.approx(20.0)
        assert stats["mean"] == pytest.approx((10 * 9 + 20 * 900) / 909)

    def test_the_realisation_gap_only_values_the_move_up_to_the_median(self):
        """
        Deliberately conservative: it assumes nobody pays more than someone
        comparable already does.
        """
        rows = [{"pocket_price": p, "quantity": 100.0} for p in (8, 9, 10, 11, 12)]
        gap = segmentation.realisation_gap(rows)
        assert gap["target_price"] == pytest.approx(10.0)
        assert gap["opportunity"] == pytest.approx((2 + 1) * 100)
        assert gap["lines_below"] == 2

    def test_the_p75_target_is_available_and_is_not_the_default(self):
        rows = [{"pocket_price": p, "quantity": 100.0} for p in (8, 9, 10, 11, 12)]
        stretch = segmentation.realisation_gap(rows, target="p75")
        assert stretch["target_price"] > segmentation.realisation_gap(rows)["target_price"]

    def test_a_segment_profile_separates_level_from_consistency(self):
        """
        A low average price paid consistently is positioning; the same average
        with a forty-point spread is execution, and they need different
        meetings.
        """
        rows = (
            [{"segment": "tight", "pocket_price": 10.0, "quantity": 10.0, "final_cost": 7.0}] * 5
            + [{"segment": "wide", "pocket_price": p, "quantity": 10.0, "final_cost": 7.0}
               for p in (6, 8, 10, 12, 14)]
        )
        profile = {p["segment"]: p for p in segmentation.segment_profile(rows)}
        assert profile["tight"]["band_width_pct"] == pytest.approx(0.0)
        assert profile["wide"]["band_width_pct"] > 0.5
        assert profile["tight"]["avg_price"] == pytest.approx(profile["wide"]["avg_price"])
