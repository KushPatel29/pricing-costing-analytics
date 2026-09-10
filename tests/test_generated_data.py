"""
The generated data has to carry the relationships the analysis claims to find.

A synthetic dataset that is merely *shaped* like a business is not a test of
anything: every page renders, every chart draws, and nothing would fail if the
regression underneath it were broken. So the generator puts known relationships
in -- a category elasticity, a pass-through share, a segment's price
sensitivity -- and these tests take them back out through the real analysis
code. When one of them stops being recoverable, either the generator drifted or
the estimator did, and both are worth knowing.

They also pin the arithmetic invariants: the waterfall descends, the bridge
sums, the cost sheet and the semantic model describe the same catalogue.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pricing.elasticity import deseasonalise, estimate_elasticity
from pricing.waterfall import ALL_DEDUCTIONS
from seed.catalogue import CATEGORY_ELASTICITY, DATA_END, DATA_START, SEGMENT_PRICE_SENSITIVITY
from seed.generate_market import CATEGORY_PASSTHROUGH

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "output"


def spearman(left: pd.Series, right: pd.Series) -> float:
    """
    Rank correlation, without pulling in scipy for it.

    `Series.corr(method="spearman")` delegates to scipy, which is a forty-megabyte
    dependency this project has no other use for -- and which was installed here
    as somebody else's transitive dependency, so three tests passed locally and
    failed in CI with ModuleNotFoundError. Spearman *is* Pearson on the ranks;
    the default method needs nothing extra.
    """
    return float(left.rank().corr(right.rank()))


def read(directory: Path, name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(directory / f"{name}.csv", **kwargs)


@pytest.fixture(scope="module")
def sales() -> pd.DataFrame:
    frame = read(DATA, "fact_sales", dtype={"product_id": str, "customer_id": str})
    frame["month"] = pd.to_datetime(frame["month"])
    return frame


@pytest.fixture(scope="module")
def products() -> pd.DataFrame:
    return read(DATA, "dim_product", dtype={"product_id": str})


@pytest.fixture(scope="module")
def sample_sheets():
    """
    The two workbooks, built in memory rather than read off disk.

    `sample_data/` is gitignored -- an .xlsx is a zip archive, so regenerating
    it changes the bytes without changing the data and every rebuild would show
    as a binary diff. Building here also makes this a test of the generator
    rather than of whatever happens to be sitting in the directory.
    """
    from seed.generate_sheets import generate

    return generate()


# ==========================================================================
# Shape and coverage
# ==========================================================================

class TestShape:
    def test_the_window_is_three_complete_fiscal_years(self):
        """
        A range that stops mid-year makes every year-on-year comparison a
        277-day period against a 365-day one, and the resulting "sales are down
        24%" is an artefact of the calendar rather than a fact.
        """
        months = read(DATA, "dim_month")
        assert len(months) == 36
        assert pd.to_datetime(months["month"]).min().date() == DATA_START
        assert pd.to_datetime(months["month"]).max().date().replace(day=1) == \
            DATA_END.replace(day=1)
        counts = months.groupby("fiscal_year").size()
        assert set(counts) == {12}, f"fiscal years are not all complete: {counts.to_dict()}"

    def test_the_month_index_is_contiguous(self):
        """Period comparison subtracts it; a gap silently reaches the wrong month."""
        index = read(DATA, "dim_month")["month_index"].tolist()
        assert index == list(range(len(index)))

    def test_the_book_is_broad_enough_to_analyse(self, sales, products):
        assert len(products) >= 200
        assert len(sales) >= 40_000
        assert sales["customer_id"].nunique() >= 100
        assert products["category"].nunique() >= 5

    def test_every_sales_line_resolves_to_a_product_and_a_customer(self, sales, products):
        customers = read(DATA, "dim_customer", dtype={"customer_id": str})
        assert set(sales["product_id"]) <= set(products["product_id"])
        assert set(sales["customer_id"]) <= set(customers["customer_id"])

    def test_the_cost_sheet_and_the_model_describe_the_same_catalogue(
        self, products, sample_sheets
    ):
        """
        The whole point of one shared catalogue: reprice item 20017 in the
        calculator and it is the item the waterfall, the price band and the
        guardrail scan are all talking about. They used to be two independent
        draws that happened to share a numbering scheme -- codes matched,
        descriptions did not, and the costs were quietly unrelated.
        """
        cost_sheet = sample_sheets[0].copy()
        cost_sheet["Item Code"] = cost_sheet["Item Code"].astype(str)
        merged = cost_sheet.merge(products, left_on="Item Code", right_on="product_id")
        assert len(merged) == len(products)
        assert (merged["Description"] == merged["description"]).all()
        assert (merged["Category"] == merged["category"]).all()

    def test_the_cost_sheet_is_the_panels_final_month(self, sample_sheets):
        """
        A cost sheet showing FY2024 prices next to a dashboard showing FY2026
        ones is the kind of inconsistency nobody notices until a reader adds two
        numbers that should have matched.
        """
        cost_sheet = sample_sheets[0].copy()
        cost_sheet["Item Code"] = cost_sheet["Item Code"].astype(str)
        panel = read(DATA, "fact_price_cost_panel", dtype={"product_id": str})
        last = panel[panel["month"] == panel["month"].max()]
        merged = cost_sheet.merge(last, left_on="Item Code", right_on="product_id")
        assert len(merged) == len(cost_sheet)
        assert np.allclose(merged["Final Cost"], merged["actual_final_cost_unit"], atol=0.001)


# ==========================================================================
# Arithmetic invariants
# ==========================================================================

class TestInvariants:
    def test_the_waterfall_descends_on_every_line(self, sales):
        assert (sales["list_price"] >= sales["invoice_price"] - 1e-6).all()
        assert (sales["invoice_price"] >= sales["net_price"] - 1e-6).all()
        assert (sales["net_price"] >= sales["pocket_price"] - 1e-6).all()

    def test_the_deduction_columns_reconcile_to_the_levels(self, sales):
        sample = sales.head(5000)
        deductions = sample[list(ALL_DEDUCTIONS)].sum(axis=1)
        assert np.allclose(sample["list_price"] - deductions, sample["pocket_price"],
                           atol=0.001)

    def test_leakage_is_material_but_not_absurd(self, sales):
        """
        Between a tenth and a third of list. Below that the waterfall page has
        nothing to say; above it the data is not a business.
        """
        leakage = 1 - sales["pocket_revenue"].sum() / sales["list_value"].sum()
        assert 0.10 < leakage < 0.33, f"leakage is {leakage:.1%}"

    def test_most_lines_make_money_and_some_do_not(self, sales):
        """
        A book where everything clears is a book with no exception report; one
        where a fifth is under water is a book nobody would have shipped.
        """
        margin_pct = (sales["pocket_revenue"] - sales["cogs"]) / sales["pocket_revenue"]
        losing = (margin_pct <= 0).mean()
        assert 0.01 < losing < 0.15, f"{losing:.1%} of lines are loss-making"

    def test_the_margin_bridge_effects_sum_to_the_change(self):
        effects = read(OUT, "margin_bridge_effects")
        for comparison, group in effects.groupby("comparison"):
            residual = group.loc[group["effect"] == "Residual", "amount"].sum()
            assert abs(residual) < 0.01, f"{comparison} leaves {residual} unexplained"

    def test_the_waterfall_output_reconciles_to_the_fact(self, sales):
        steps = read(OUT, "price_waterfall")
        list_value = steps.loc[steps["step"] == "List value", "amount"].iloc[0]
        pocket = steps.loc[steps["step"] == "Pocket revenue", "amount"].iloc[0]
        assert list_value == pytest.approx(sales["list_value"].sum(), rel=1e-4)
        assert pocket == pytest.approx(sales["pocket_revenue"].sum(), rel=1e-4)

    def test_the_executive_summary_agrees_with_the_tables_it_summarises(self, sales):
        """
        A card and a page heading disagreeing is the most embarrassing failure
        a dashboard has, so the KPI row is computed once and read twice.
        """
        summary = read(OUT, "executive_summary").set_index("kpi")["value"]
        latest = sales[sales["month"].dt.year * 12 + sales["month"].dt.month
                       >= (sales["month"].max().year * 12 + sales["month"].max().month) - 11]
        assert summary["Pocket revenue"] == pytest.approx(
            latest["pocket_revenue"].sum(), rel=1e-3)
        bands = read(OUT, "price_bands")
        assert summary["Realisation opportunity"] == pytest.approx(
            bands["realisation_opportunity"].sum(), rel=1e-3)


# ==========================================================================
# The relationships the generator put in
# ==========================================================================

class TestRecoverability:
    def test_category_elasticity_is_recovered_in_the_right_order(self, sales, products):
        """
        The ordering is the claim that matters: consumer electronics is the
        most price-sensitive thing in the book -- every SKU is one click from a
        marketplace comparison -- and pet supplies the least, because it is
        habitual and brand-loyal. An estimator that cannot tell those apart is
        not measuring price response.
        """
        estimates = read(OUT, "elasticity_estimates")
        categories = estimates[estimates["scope"] == "Category"].set_index("member")
        seeded = products.groupby("category")["elasticity"].mean()

        ordering = categories["elasticity"].rank().reindex(seeded.index)
        truth = seeded.rank()
        correlation = spearman(ordering, truth)
        assert correlation > 0.7, (
            "the estimated elasticity ordering does not follow the seeded one "
            f"(Spearman {correlation:.2f}):\n"
            + pd.DataFrame({"seeded": seeded, "estimated": categories["elasticity"]}).to_string()
        )
        assert (categories.loc["Consumer Electronics", "elasticity"]
                < categories.loc["Pet Supplies", "elasticity"])

    def test_the_most_elastic_categories_are_estimated_close_to_the_truth(self, products):
        """
        Where there is enough price movement to fit, the estimate should land
        near the seeded value. Attenuation toward zero is expected and bounded.
        """
        estimates = read(OUT, "elasticity_estimates")
        categories = estimates[estimates["scope"] == "Category"].set_index("member")
        seeded = products.groupby("category")["elasticity"].mean()
        for category in ("Consumer Electronics", "Office & Stationery"):
            assert categories.loc[category, "elasticity"] == pytest.approx(
                seeded[category], abs=0.6), f"{category} is off"

    def test_the_optimal_price_guard_actually_fires(self):
        """
        The case the guard exists for: demand inelastic enough that the markup
        rule either has no interior solution or produces a price nobody should
        act on. Asserted as a property rather than against a named category --
        which category lands under the threshold depends on the draw, and a
        test pinned to one of them fails for a reason that is not a defect.

        What has to hold is the behaviour: something is refused, everything
        refused has an implausible markup, and nothing actionable does.
        """
        estimates = read(OUT, "elasticity_estimates")
        categories = estimates[estimates["scope"] == "Category"]
        products = estimates[estimates["scope"] == "Product"]

        refused = pd.concat([categories, products])
        refused = refused[refused["optimal_exists"] & ~refused["optimal_actionable"]]
        assert len(refused) > 0, (
            "nothing was refused, so the guard is not being exercised at all"
        )
        assert (refused["implied_markup"] > 5.0).all(), (
            "something was refused despite a credible markup"
        )
        actionable = pd.concat([categories, products])
        actionable = actionable[actionable["optimal_actionable"]]
        assert (actionable["implied_markup"] <= 5.0).all()

        # And the least elastic category has to be among the least actionable,
        # in one of the two ways that can happen: an implausible markup, or --
        # the stronger case -- an estimate above -1, where the markup rule has
        # no interior solution at all and the implied markup is not a number.
        least_elastic = categories.nlargest(1, "elasticity").iloc[0]
        assert not least_elastic["optimal_actionable"]
        assert (not least_elastic["optimal_exists"]
                or least_elastic["implied_markup"] > 4.0)

    def test_the_estimator_is_what_recovers_it_not_the_output_file(self, sales, products):
        """
        Run the fit here rather than trusting the committed CSV, so a broken
        estimator cannot pass by virtue of a stale output.
        """
        panel = (
            sales[sales["on_promotion"] == 0]
            .merge(products[["product_id", "category"]], on="product_id")
            .groupby(["product_id", "category", "month"], as_index=False)
            .agg(quantity_units=("quantity_units", "sum"), list_price=("list_price", "first"))
        )
        panel["moy"] = panel["month"].dt.month
        panel["t"] = panel["month"].dt.year * 12 + panel["month"].dt.month
        panel["t"] -= panel["t"].min()
        for column in ("list_price", "quantity_units"):
            logs = np.log(panel[column])
            panel[f"rel_{column}"] = np.exp(
                logs - logs.groupby(panel["product_id"]).transform("mean"))

        electronics = panel[panel["category"] == "Consumer Electronics"]
        fit = estimate_elasticity(
            electronics["rel_list_price"],
            deseasonalise(electronics["rel_quantity_units"], electronics["moy"]),
            controls={"trend": electronics["t"].tolist()},
        )
        assert fit["usable"]
        assert fit["elasticity"] == pytest.approx(
            CATEGORY_ELASTICITY["Consumer Electronics"], abs=0.8)

    def test_passthrough_is_recovered_where_the_index_moved(self):
        """
        Estimated on quarterly changes, because that is how often prices are
        reviewed. The estimate attenuates -- the realised price is a
        volume-weighted mix across a category, and mix moves it -- so this
        checks the direction and the magnitude band rather than the point.
        """
        passthrough = read(OUT, "passthrough")
        usable = passthrough[passthrough["usable"]]
        assert len(usable) >= 4, "too few usable pass-through estimates to claim anything"
        # A band rather than a point. Every estimate attenuates -- the list
        # price series is a monthly mean over the items on an index, and a
        # regression on it recovers the direction and the rough magnitude of
        # the decision rather than the decision itself.
        assert (usable["passthrough_to_list"] > 0.20).all()
        assert (usable["passthrough_to_list"] < 1.3).all()
        # Every category passes on less than all of it, which is the margin story.
        assert usable["passthrough_to_list"].mean() < 1.0

    def test_the_pass_through_ordering_follows_the_seeded_one(self):
        """
        The stronger claim, and the one that matters: the category that passes
        on least of a cost move should *estimate* as passing on least. Pet
        supplies is seeded lowest -- a differentiated, habitual category absorbs
        input moves rather than passing them on -- and the estimate has to agree
        without being told.
        """
        from seed.catalogue import CATEGORY_INDEX

        assert CATEGORY_PASSTHROUGH["Pet Supplies"] == min(CATEGORY_PASSTHROUGH.values())
        passthrough = read(OUT, "passthrough").set_index("commodity_index")
        usable = passthrough[passthrough["usable"]]
        assert len(usable) >= 5, "too few usable estimates to claim an ordering"

        # Two categories can share an input index -- home & kitchen and
        # sporting goods both track resin -- so the seeded pass-through for
        # that index is the average of the categories on it. Taking the last
        # one to be written, which a plain dict comprehension does, compares
        # the estimate against a number that was never the target.
        by_index: dict[str, list[float]] = {}
        for category, rate in CATEGORY_PASSTHROUGH.items():
            index_name = CATEGORY_INDEX[category]
            if index_name in usable.index:
                by_index.setdefault(index_name, []).append(rate)
        seeded = pd.Series({k: sum(v) / len(v) for k, v in by_index.items()})
        estimated = usable["passthrough_to_list"].reindex(seeded.index)
        detail = pd.DataFrame({"seeded": seeded,
                               "estimated": estimated}).sort_values("seeded")
        correlation = spearman(seeded, estimated)
        assert correlation > 0.5, (
            f"pass-through ordering does not follow the seeded one "
            f"(Spearman {correlation:.2f}):\n{detail.to_string()}"
        )

        # The claim that actually holds, and the one worth making. With eleven
        # quarterly observations per index you can separate a category that
        # absorbs cost moves from one that passes them on; you cannot rank 0.74
        # against 0.79, and the estimates for the high group sit inside a band
        # narrower than the noise. So this asserts the separation rather than
        # the full ordering -- and every estimate attenuates toward zero, which
        # is what a regression on a noisy realisation of a decision does.
        lowest_two = detail.head(2)["estimated"].max()
        rest = detail.tail(len(detail) - 2)["estimated"].min()
        assert lowest_two < rest, (
            "the categories seeded to absorb cost moves should estimate below "
            f"every category seeded to pass them on:\n{detail.to_string()}"
        )
        assert (estimated < seeded + 0.05).all(), (
            f"estimates should attenuate toward zero, not exceed the seeded "
            f"rate:\n{detail.to_string()}"
        )

    def test_segment_price_sensitivity_is_recovered_in_the_right_order(self):
        """
        A distributor resells and shops hard; a hotel buys on specification and
        barely notices a point. Those are different price lists, and the win/loss
        file is the evidence for saying so.
        """
        fits = read(OUT, "wtp_fits")
        fits = fits[(fits["segment"] != "All") & fits["usable"]].set_index("segment")
        seeded = pd.Series(SEGMENT_PRICE_SENSITIVITY)
        estimated = -fits["slope"]
        common = seeded.index.intersection(estimated.index)
        correlation = spearman(seeded[common], estimated[common])
        assert correlation > 0.8, (
            f"segment sensitivity ordering does not follow the seeded one "
            f"(Spearman {correlation:.2f}):\n"
            + pd.DataFrame({"seeded": seeded[common],
                            "estimated": estimated[common]}).to_string()
        )

    def test_the_win_curve_slopes_downward_for_every_segment(self):
        fits = read(OUT, "wtp_fits")
        assert (fits["slope"] < 0).all()
        assert fits["usable"].all()

    def test_price_bands_are_wide_enough_to_be_worth_closing(self):
        """
        The realisation opportunity is the most reliable finding in any book of
        business. If the generated bands were narrow there would be nothing to
        find, and the page would be decoration.
        """
        bands = read(OUT, "price_bands")
        assert bands["band_width_pct"].median() > 0.08
        assert bands["realisation_opportunity"].sum() > 0

    def test_purchase_price_variance_builds_through_a_fiscal_year(self):
        """
        Standard cost is frozen each July while actual cost keeps moving, so
        variance accumulates within a year and resets across the boundary. A PPV
        series that trends smoothly across July is measuring something else.
        """
        detail = read(OUT, "cost_variance_detail")
        detail["month"] = pd.to_datetime(detail["month"])
        by_period = (
            detail.assign(period=((detail["month"].dt.month - 7) % 12) + 1)
            .groupby("period")["purchase_price_variance"].sum()
        )
        first_quarter = by_period.loc[1:3].abs().sum()
        last_quarter = by_period.loc[10:12].abs().sum()
        assert last_quarter > first_quarter, (
            "variance should be larger late in the fiscal year than just after "
            f"the standard was re-struck: {first_quarter:,.0f} vs {last_quarter:,.0f}"
        )

    def test_the_competitive_index_is_centred_near_parity(self):
        """
        A book priced at a median index of 130 is not a market position, it is a
        generator that forgot competitors scale with our own prices.
        """
        index = read(OUT, "competitive_index")
        assert 90 < index["price_index"].median() < 112

    def test_the_export_sheet_holds_codes_the_cost_sheet_does_not(self, sample_sheets):
        """
        Reporting what it could not price is part of the tool's job, and without
        orphans in the data that path is never exercised.
        """
        cost, export = sample_sheets
        orphans = set(export["Product Code"].astype(str)) - set(cost["Item Code"].astype(str))
        assert orphans


# ==========================================================================
# Determinism
# ==========================================================================

class TestDeterminism:
    def test_the_catalogue_is_a_function_of_the_seed_alone(self):
        """
        The cost sheet rebuilds the catalogue by itself, without generating
        customers or sales. If the catalogue rode the shared random stream it
        would land on a different draw -- which it did, and the two described
        different products under the same codes.
        """
        from seed.generate_market import product_catalogue

        assert product_catalogue(seed=11, items=25).equals(
            product_catalogue(seed=11, items=25))
        assert not product_catalogue(seed=11, items=25).equals(
            product_catalogue(seed=12, items=25))

    def test_the_price_panel_is_a_function_of_the_seed_and_the_products(self):
        from seed.generate_market import (
            build_commodity_index,
            monthly_index,
            price_cost_panel,
            product_catalogue,
        )

        products = product_catalogue(seed=5, items=12)
        index = monthly_index(build_commodity_index(np.random.default_rng(5)))
        first = price_cost_panel(products, index, seed=5)
        second = price_cost_panel(products, index, seed=5)
        assert first.equals(second)

    def test_regenerating_the_market_reproduces_it(self):
        from seed.generate_market import generate

        first = generate(seed=3, items=8, customers=6)
        second = generate(seed=3, items=8, customers=6)
        for name in first:
            pd.testing.assert_frame_equal(first[name], second[name])
