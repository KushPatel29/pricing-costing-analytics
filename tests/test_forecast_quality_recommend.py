"""
Forecasting, data quality, and the recommendation layer.

Three modules that produce something other than a number -- a chosen method, a
work list, a sentence -- and each has a way of being confidently wrong that a
value test would not catch. So these check the *refusals* as much as the
answers: a method chosen on in-sample fit, a quality score that hides which
rows failed, and a price recommendation on a product whose cost is missing are
all outputs that look fine and are not.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from pricing import forecast as fc
from pricing import quality as dq
from pricing import recommend as rec

# ==========================================================================
# Forecasting
# ==========================================================================

def seasonal_series(periods: int = 48, trend: float = 1.004,
                    amplitude: float = 0.25, base: float = 1000.0) -> list[float]:
    """A deterministic seasonal series -- no RNG, so the test cannot flake."""
    return [
        base * (trend ** t) * (1 + amplitude * math.cos(2 * math.pi * (t % 12) / 12))
        for t in range(periods)
    ]


class TestMethods:
    def test_seasonal_naive_reaches_the_same_month_last_year(self):
        series = list(range(24))
        assert fc.seasonal_naive(series, 3) == [12, 13, 14]

    def test_seasonal_naive_falls_back_when_there_is_no_season_yet(self):
        assert fc.seasonal_naive([5.0, 6.0], 2) == [6.0, 6.0]

    def test_drift_extends_the_average_change(self):
        assert fc.drift([10.0, 12.0, 14.0], 2) == pytest.approx([16.0, 18.0])

    def test_seasonal_naive_with_drift_scales_last_year_by_the_years_growth(self):
        series = [100.0] * 12 + [110.0] * 12
        forecasts = fc.seasonal_naive_drift(series, 3)
        assert forecasts == pytest.approx([121.0, 121.0, 121.0])

    def test_holt_winters_tracks_a_clean_seasonal_series(self):
        series = seasonal_series()
        forecasts = fc.holt_winters(series, 12)
        expected = seasonal_series(60)[48:]
        error = fc.wape(expected, forecasts)
        assert error < 0.06, f"Holt-Winters is {error:.1%} out on a clean series"

    def test_every_method_returns_the_horizon_asked_for(self):
        series = seasonal_series()
        for name, method in fc.METHODS.items():
            assert len(method(series, 7)) == 7, name


class TestAccuracy:
    def test_wape_weights_by_size_where_mape_does_not(self):
        """
        The reason WAPE is the headline. One quiet month with near-zero volume
        dominates a MAPE and stops the metric describing the series.
        """
        actual = [1000.0, 1000.0, 1.0]
        predicted = [1100.0, 1100.0, 2.0]
        assert fc.wape(actual, predicted) == pytest.approx(201 / 2001)
        assert fc.mape(actual, predicted) > 0.36        # dragged up by the tiny month

    def test_bias_is_signed_where_error_is_not(self):
        actual = [100.0, 100.0]
        assert fc.bias(actual, [110.0, 110.0]) == pytest.approx(0.10)
        assert fc.bias(actual, [90.0, 90.0]) == pytest.approx(-0.10)
        assert fc.wape(actual, [110.0, 110.0]) == fc.wape(actual, [90.0, 90.0])

    def test_a_perfect_forecast_scores_zero(self):
        series = seasonal_series(24)
        assert fc.wape(series, series) == pytest.approx(0.0)
        assert fc.bias(series, series) == pytest.approx(0.0)


class TestBacktest:
    def test_every_scored_prediction_was_made_before_the_month_it_predicts(self):
        """
        The property the whole comparison rests on. A method that memorises
        history wins an in-sample comparison it should lose, so the backtest
        has to refit on a growing window -- and a method that peeks would score
        a perfect zero here.
        """
        series = seasonal_series()
        cheating = fc.backtest(series, "Seasonal naive", horizon=3, min_train=24)
        assert cheating["wape"] > 0, "a rolling-origin backtest cannot be perfect"
        assert cheating["folds"] == len(series) - 24

    def test_a_short_series_is_refused_rather_than_fitted(self):
        result = fc.backtest([1.0] * 10, "Seasonal naive", min_train=18)
        assert not result["usable"]
        assert "need" in result["reason"]

    def test_comparison_ranks_by_out_of_sample_error(self):
        results = fc.compare_methods(seasonal_series())
        usable = [r for r in results if r["rank"] > 0]
        errors = [r["wape"] for r in usable]
        assert errors == sorted(errors)
        assert sum(r["is_best"] for r in results) == 1

    def test_the_seasonal_methods_beat_the_naive_one_on_seasonal_data(self):
        """
        The sanity check on the whole apparatus: if a naive last-value forecast
        wins on a series with a 25% annual swing, the backtest is broken.
        """
        results = {r["method"]: r for r in fc.compare_methods(seasonal_series())}
        assert results["Seasonal naive"]["wape"] < results["Naive"]["wape"]
        assert results["Seasonal naive"]["wape"] < results["Moving average (3)"]["wape"]

    def test_forecast_picks_its_method_by_backtest_unless_told(self):
        series = seasonal_series()
        automatic = fc.forecast(series, horizon=6)
        assert automatic["chosen_by"] == "backtest"
        forced = fc.forecast(series, horizon=6, method="Naive")
        assert forced["chosen_by"] == "caller"
        assert forced["method"] == "Naive"

    def test_the_interval_widens_with_horizon(self):
        """
        Because the errors did. It is an empirical interval from the backtest
        residuals at each step, not a formula that assumes it should widen.
        """
        result = fc.forecast(seasonal_series(), horizon=6)
        widths = [p["high"] - p["low"] for p in result["points"]
                  if not math.isnan(p["high"])]
        assert len(widths) >= 4
        assert widths[-1] > widths[0]

    def test_the_forecast_brackets_its_own_point(self):
        for point in fc.forecast(seasonal_series(), horizon=6)["points"]:
            if math.isnan(point["low"]):
                continue
            assert point["low"] <= point["forecast"] <= point["high"]

    def test_scoring_against_actual_handles_a_ragged_pair(self):
        result = fc.accuracy_against_actual([1.0, 2.0, 3.0], [1.0, 2.0])
        assert result["periods"] == 2


# ==========================================================================
# Data quality
# ==========================================================================

def frames_with(**overrides) -> dict[str, pd.DataFrame]:
    """A tiny, clean ERP extract, with the named tables replaced."""
    base = {
        "erp_billing_items": pd.DataFrame({
            "billing_document": ["1", "2"], "item_number": ["010", "020"],
            "document_type": ["Invoice", "Invoice"],
            "billing_date": ["2026-01-05", "2026-01-06"],
            "extract_date": ["2026-07-02", "2026-07-02"],
            "sold_to_party": ["C1", "C1"], "material": ["M1", "M1"],
            "billed_quantity": [10.0, 20.0], "sales_uom": ["EA", "EA"],
            "net_price": [5.0, 5.0], "net_value": [50.0, 100.0],
            "pack_size_units": [1.0, 1.0],
        }),
        "erp_material_master": pd.DataFrame({
            "material": ["M1"], "base_uom": ["EA"], "standard_cost": [3.0],
            "recovery": [0.95],
        }),
        "erp_customer_master": pd.DataFrame({
            "customer": ["C1"], "sales_district": ["West"],
        }),
        "erp_condition_records": pd.DataFrame({
            "condition_record": ["CR1"], "material": ["M1"],
            "condition_value": [8.0], "days_since_change": [30],
        }),
    }
    base.update(overrides)
    return base


class TestQualityRules:
    def test_clean_data_passes_every_check(self):
        results = dq.run_checks(frames_with())
        failing = results[~results["passed"]]
        assert failing.empty, failing[["code", "check", "rows_failing"]].to_string()

    def test_a_rule_names_the_rows_not_just_a_count(self):
        """
        "Data quality: 94%" is not actionable. Every finding carries the keys,
        so the output is a work list.
        """
        items = frames_with()["erp_billing_items"]
        duplicated = pd.concat([items, items.head(1)], ignore_index=True)
        frames = frames_with(erp_billing_items=duplicated)
        rows = dq.failing_rows(frames, "DQ03")
        assert len(rows) == 2
        assert set(rows.columns) == {"billing_document", "item_number"}

    def test_a_missing_standard_cost_is_critical(self):
        master = frames_with()["erp_material_master"].assign(standard_cost=[0.0])
        results = dq.run_checks(frames_with(erp_material_master=master))
        row = results.set_index("code").loc["DQ01"]
        assert row["rows_failing"] == 2
        assert row["severity"] == "Critical"

    def test_a_credit_memo_is_not_a_zero_quantity_defect(self):
        """A negative line on a credit note is correct. Flagging it would fill
        the report with the one thing that is meant to look like that."""
        items = frames_with()["erp_billing_items"].copy()
        items.loc[0, ["billed_quantity", "document_type"]] = [-5.0, "Credit memo"]
        results = dq.run_checks(frames_with(erp_billing_items=items))
        assert results.set_index("code").loc["DQ04", "rows_failing"] == 0

    def test_a_rule_that_raises_is_reported_not_skipped(self):
        """
        A check silently not running is worse than a check failing, because the
        report still says the data is clean.
        """
        broken = dq.Rule(
            "XX01", "Validity", "High", "erp_billing_items", "always explodes",
            lambda f: 1 / 0, "n/a",
        )
        results = dq.run_checks(frames_with(), [broken])
        assert results.iloc[0]["rows_failing"] == -1
        assert "ZeroDivisionError" in results.iloc[0]["error"]
        assert not results.iloc[0]["passed"]

    def test_an_unknown_dimension_is_refused_at_construction(self):
        with pytest.raises(ValueError, match="unknown dimension"):
            dq.Rule("XX", "Vibes", "High", "t", "d", lambda f: pd.DataFrame(), "a")

    def test_the_score_is_row_weighted_not_rule_weighted(self):
        """
        Twelve rules of which one fails on a single row is not 92% quality, and
        averaging the rules would say it was.
        """
        results = pd.DataFrame([
            {"code": "A", "dimension": "Validity", "severity": "High",
             "rows_checked": 10_000, "rows_failing": 1, "passed": False},
            {"code": "B", "dimension": "Validity", "severity": "High",
             "rows_checked": 10_000, "rows_failing": 0, "passed": True},
        ])
        score = dq.quality_score(results)
        overall = score[score["dimension"] == "Overall"].iloc[0]
        assert overall["score"] == pytest.approx(1 - 1 / 20_000)
        assert overall["score"] > 0.99

    @pytest.mark.parametrize(
        "score,light", [(1.0, "Green"), (0.996, "Green"), (0.99, "Amber"),
                        (0.97, "Red")])
    def test_the_traffic_light(self, score, light):
        assert dq.traffic_light(score) == light


class TestReconciliation:
    def test_it_balances_when_the_exclusions_explain_the_gap(self):
        source = pd.DataFrame({"net_value": [100.0, 50.0, 25.0]})
        staged = pd.DataFrame({"net_value": [100.0]})
        result = dq.reconcile(source, staged, measure="net_value",
                              exclusions={"Dropped duplicates": 50.0,
                                          "Dropped orphans": 25.0})
        assert result["balanced"]
        assert result["unexplained"] == pytest.approx(0.0)

    def test_an_unnamed_exclusion_fails_rather_than_rounding_away(self):
        """
        The whole point. A pipeline that drops rows and reports a clean total
        has hidden the problem in the direction that flatters it.
        """
        source = pd.DataFrame({"net_value": [100.0, 50.0]})
        staged = pd.DataFrame({"net_value": [100.0]})
        result = dq.reconcile(source, staged, measure="net_value")
        assert not result["balanced"]
        assert result["unexplained"] == pytest.approx(50.0)

    def test_the_statement_reads_as_a_reconciliation(self):
        result = dq.reconcile(
            pd.DataFrame({"net_value": [100.0, 50.0]}),
            pd.DataFrame({"net_value": [100.0]}),
            measure="net_value", exclusions={"Dropped": 50.0})
        rows = dq.reconciliation_rows(result)
        assert rows["line"].iloc[0] == "Extract total"
        assert rows["line"].iloc[-1] == "Staged total"
        # Opening less every movement equals the closing balance.
        assert rows["amount"].iloc[0] + rows["amount"].iloc[1:-1].sum() == \
            pytest.approx(rows["amount"].iloc[-1])


# ==========================================================================
# Recommendations
# ==========================================================================

def product(**overrides) -> dict:
    base = {
        "product_id": "20001", "description": "Wireless Earbuds - Value",
        "category": "Consumer Electronics",
        "current_price": 20.0, "unit_cost": 15.0, "volume_units": 50_000,
        # margin_pct has to follow from price and cost. Feeding the rules a
        # margin that contradicts the two numbers it is derived from tests a
        # situation the engine never produces.
        "margin_pct": 0.25, "target_margin": 0.22, "floor_margin": 0.13,
        "price_index": 104.0, "market_price": 19.2, "competitors_seen": 4,
        "days_since_price_change": 60, "cost_change_pct": 0.01,
        "elasticity": -1.8, "elasticity_usable": True, "months_of_history": 36,
        "is_small_volume": False, "co_purchase_rate": 0.1,
        "cost_is_missing": False,
    }
    base.update(overrides)
    return base


class TestRecommend:
    def test_master_data_is_tested_before_anything_that_divides_by_cost(self):
        """
        A margin measured against a zero cost looks wonderful, so every
        margin-driven rule below would say "raise it". The order matters.
        """
        result = rec.recommend(product(unit_cost=0.0, margin_pct=1.0,
                                       cost_is_missing=True))
        assert result["action"] == "Fix cost"
        assert result["recommended_price"] == 20.0
        assert "standard cost" in result["rationale"]

    def test_a_healthy_item_is_left_alone(self):
        result = rec.recommend(product())
        assert result["action"] == "Maintain"
        assert result["price_change_pct"] == 0.0
        assert result["margin_delta"] == 0.0

    def test_under_the_market_earns_an_increase(self):
        result = rec.recommend(product(price_index=88.0, unit_cost=17.0,
                                       margin_pct=0.15))
        assert result["action"] == "Increase"
        assert result["recommended_price"] > 20.0
        assert "index 88" in result["rationale"]

    def test_under_the_market_but_already_above_target_still_earns_one(self):
        """
        The cheapest margin in the book: nine points under the market while
        already clearing its own target. Taking the minimum against a
        target-margin price that sits *below* current used to cancel the
        increase entirely and return "raise to $20.00 (+0.0%)".
        """
        result = rec.recommend(product(price_index=88.0, margin_pct=0.25))
        assert result["action"] == "Increase"
        assert result["recommended_price"] > 20.0

    def test_an_increase_is_capped_at_something_a_customer_would_accept(self):
        """A single move larger than the cap is a conversation, not a price-list
        change, and recommending it as one gets the whole file ignored."""
        result = rec.recommend(product(price_index=40.0, margin_pct=0.02))
        assert result["price_change_pct"] <= rec.MAX_SINGLE_INCREASE + 1e-9

    def test_an_unmanaged_item_whose_cost_moved_earns_an_increase(self):
        result = rec.recommend(product(days_since_price_change=430,
                                       cost_change_pct=0.14))
        assert result["action"] == "Increase"
        assert "430 days" in result["rationale"]

    def test_a_stale_price_alone_is_not_enough(self):
        """Stale is only a problem if the cost moved underneath it."""
        assert rec.recommend(product(days_since_price_change=430,
                                     cost_change_pct=0.0))["action"] == "Maintain"

    def test_over_the_market_on_elastic_demand_can_earn_a_discount(self):
        result = rec.recommend(product(price_index=128.0, unit_cost=8.0,
                                       margin_pct=0.60, elasticity=-2.6))
        assert result["action"] == "Discount"
        assert result["recommended_price"] < 20.0
        assert result["margin_delta"] > 0

    def test_a_discount_that_does_not_pay_is_not_recommended(self):
        """
        Being over the market is the trigger for asking the question, not the
        answer to it. A file that tells you to discount into a margin loss is a
        file that gets ignored wholesale.
        """
        result = rec.recommend(product(price_index=128.0, unit_cost=13.0,
                                       margin_pct=0.35, elasticity=-1.45))
        assert result["action"] == "Maintain"
        assert "defend the premium" in result["rationale"]

    def test_a_small_loss_making_item_the_market_will_not_pay_for_is_exited(self):
        result = rec.recommend(product(margin_pct=-0.08, is_small_volume=True,
                                       volume_units=400, market_price=12.0,
                                       unit_cost=15.0, price_index=None))
        assert result["action"] == "Discontinue"

    def test_a_thin_item_bought_alongside_others_is_bundled(self):
        result = rec.recommend(product(is_small_volume=True, volume_units=900,
                                       co_purchase_rate=0.55))
        assert result["action"] == "Bundle"

    def test_confidence_is_about_the_evidence_not_the_prize(self):
        strong = rec.recommend(product(volume_units=10))
        weak = rec.recommend(product(volume_units=10_000_000,
                                     elasticity_usable=False,
                                     competitors_seen=0, months_of_history=4))
        assert strong["confidence"] == "High"
        assert weak["confidence"] == "Low"
        assert "no competitive coverage" in weak["evidence"]

    def test_no_usable_elasticity_assumes_no_volume_response_and_says_so(self):
        result = rec.recommend(product(price_index=88.0, unit_cost=17.0,
                                       margin_pct=0.15, elasticity_usable=False))
        assert result["volume_change_pct"] == 0.0
        assert "assumes volume holds" in result["rationale"]

    def test_the_book_is_ranked_by_what_the_recommendation_is_worth(self):
        rows = [product(product_id="a", price_index=88.0, unit_cost=17.0,
                        margin_pct=0.15, volume_units=10),
                product(product_id="b", price_index=88.0, unit_cost=17.0,
                        margin_pct=0.15, volume_units=500_000)]
        ranked = rec.recommend_book(rows)
        assert ranked[0]["product_id"] == "b"

    def test_the_summary_covers_every_action_present(self):
        rows = [product(), product(price_index=88.0, unit_cost=17.0,
                                   margin_pct=0.15)]
        summary = rec.summarise(rec.recommend_book(rows))
        assert {r["action"] for r in summary} == {"Maintain", "Increase"}
        assert sum(r["products"] for r in summary) == 2

    def test_the_executive_note_is_generated_from_the_recommendations(self):
        """
        Generated rather than written, so it cannot drift from the table under
        it -- which is the usual failure of an executive summary and the reason
        nobody trusts the ones that are typed.
        """
        rows = [product(price_index=88.0, unit_cost=17.0, margin_pct=0.15)
                for _ in range(3)]
        note = rec.executive_note(rec.recommend_book(rows))
        assert "3 products reviewed" in note
        assert "price increase" in note

    def test_an_empty_book_produces_a_sentence_rather_than_an_error(self):
        assert "No products" in rec.executive_note([])
