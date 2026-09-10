"""
Turn the raw facts in ``data/`` into the analysis tables in ``output/``.

One build, two consumers. The Streamlit pages and the Power BI model read the
*same* CSVs, so a number quoted on the dashboard and the same number quoted in
the app cannot drift -- which they do, every time, when a DAX measure and a
pandas groupby are each asked to re-derive "pocket margin" from the raw fact.
The rule in this repo is that anything with a definition worth arguing about is
computed once, here, in Python that has tests.

Everything is a pure function of ``data/``. Run the generator first::

    python -m seed.generate_market
    python -m engine.build_pricing_analytics
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from engine import build_extended as extended
from pricing import (
    bundles,
    competitive,
    elasticity,
    guardrails,
    segmentation,
    variance,
    waterfall,
)
from pricing.waterfall import ALL_DEDUCTIONS, DEDUCTION_LABELS, bucket_of

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "output"

# The dimensions every roll-up is cut by. Kept in one place so a new dimension
# reaches the waterfall, the price bands and the exception scan together.
CUTS = ("category", "brand_tier", "segment", "channel", "region", "tier")


def read(name: str, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    return pd.read_csv(data_dir / f"{name}.csv")


def _material_master(data_dir: Path) -> pd.DataFrame | None:
    """
    The ERP material master, when the raw extract has been generated.

    Optional rather than required: the analysis stands on its own from clean
    data, and the ERP layer is what adds the master-data findings on top. A
    missing raw/ directory means fewer "fix the cost" recommendations, not a
    crash.
    """
    path = data_dir.parent / "raw" / "erp_material_master.csv"
    return pd.read_csv(path) if path.exists() else None


def load(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    """Read every generated table and join the sales fact to its dimensions."""
    tables = {
        name: read(name, data_dir)
        for name in (
            "dim_product", "dim_customer", "dim_competitor", "dim_month",
            "fact_sales", "fact_competitor_price", "fact_quote", "fact_cost_ledger",
            "fact_budget", "fact_overhead", "fact_price_cost_panel", "fact_commodity_index",
            "dim_salesperson", "dim_cost_element", "fact_cost_element", "fact_fixed_cost",
            "fact_promotion", "fact_price_change",
        )
    }
    sales = tables["fact_sales"].merge(
        tables["dim_product"][
            ["product_id", "description", "category", "sub_category", "brand_tier",
             "lifecycle", "elasticity", "target_margin", "commodity_index"]
        ],
        on="product_id", how="left",
    ).merge(
        tables["dim_customer"][
            ["customer_id", "customer_name", "segment", "channel", "region", "tier",
             "price_list", "payment_terms", "salesperson_id", "salesperson"]
        ],
        on="customer_id", how="left",
    )
    sales["month"] = pd.to_datetime(sales["month"])
    sales["month_of_year"] = sales["month"].dt.month
    sales["month_index"] = (
        sales["month"].dt.year * 12 + sales["month"].dt.month
    )
    sales["month_index"] -= sales["month_index"].min()
    sales["fiscal_year"] = np.where(
        sales["month"].dt.month >= 7, sales["month"].dt.year + 1, sales["month"].dt.year
    )
    tables["sales"] = sales
    return tables


# ---------------------------------------------------------------------------
# 1. Price waterfall
# ---------------------------------------------------------------------------


def build_waterfall_outputs(sales: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    The list-to-pocket waterfall at total and cut every way, plus the deduction
    ranking that says which line to attack first.
    """
    def extended(frame: pd.DataFrame) -> pd.Series:
        out = {"list_value": frame["list_value"].sum(),
               "quantity_units": frame["quantity_units"].sum(),
               "cogs": frame["cogs"].sum(),
               "lines": len(frame)}
        for key in ALL_DEDUCTIONS:
            out[key] = float((frame[key] * frame["quantity_units"]).sum())
        return pd.Series(out)

    steps = []
    total = extended(sales)
    running = float(total["list_value"])
    steps.append({"scope": "Total", "dimension": "Total", "member": "All",
                  "step": "List value", "kind": "total", "amount": running,
                  "running": running, "bucket": "List", "sort_order": 0})
    for i, key in enumerate(ALL_DEDUCTIONS, start=1):
        amount = float(total[key])
        running -= amount
        steps.append({"scope": "Total", "dimension": "Total", "member": "All",
                      "step": DEDUCTION_LABELS[key], "kind": "decrease",
                      "amount": -amount, "running": running,
                      "bucket": bucket_of(key), "sort_order": i})
    steps.append({"scope": "Total", "dimension": "Total", "member": "All",
                  "step": "Pocket revenue", "kind": "total", "amount": running,
                  "running": running, "bucket": "Pocket",
                  "sort_order": len(ALL_DEDUCTIONS) + 1})
    cogs = float(total["cogs"])
    steps.append({"scope": "Total", "dimension": "Total", "member": "All",
                  "step": "Cost of goods", "kind": "decrease", "amount": -cogs,
                  "running": running - cogs, "bucket": "Cost",
                  "sort_order": len(ALL_DEDUCTIONS) + 2})
    steps.append({"scope": "Total", "dimension": "Total", "member": "All",
                  "step": "Pocket margin", "kind": "total", "amount": running - cogs,
                  "running": running - cogs, "bucket": "Margin",
                  "sort_order": len(ALL_DEDUCTIONS) + 3})

    # Leakage by deduction and by cut. This is the table the dashboard ranks on.
    leakage_rows = []
    for dimension in CUTS:
        for member, group in sales.groupby(dimension):
            ext = extended(group)
            list_value = float(ext["list_value"])
            for key in ALL_DEDUCTIONS:
                amount = float(ext[key])
                if amount == 0:
                    continue
                leakage_rows.append(
                    {
                        "dimension": dimension,
                        "member": member,
                        "deduction": DEDUCTION_LABELS[key],
                        "bucket": bucket_of(key),
                        "amount": round(amount, 2),
                        "pct_of_list": round(amount / list_value, 6) if list_value else 0.0,
                        "list_value": round(list_value, 2),
                    }
                )

    # Monthly waterfall levels, so leakage can be trended.
    monthly = (
        sales.groupby(["month", "fiscal_year"], as_index=False)
        .agg(list_value=("list_value", "sum"),
             invoice_revenue=("revenue", "sum"),
             pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"),
             quantity_units=("quantity_units", "sum"))
    )
    monthly["net_revenue"] = (
        monthly["invoice_revenue"]
        - sales.groupby("month")
        .apply(lambda g: float((g["off_invoice_deductions"] * g["quantity_units"]).sum()),
               include_groups=False)
        .reindex(monthly["month"]).to_numpy()
    )
    monthly["pocket_margin"] = monthly["pocket_revenue"] - monthly["cogs"]
    monthly["leakage_pct"] = 1 - monthly["pocket_revenue"] / monthly["list_value"]
    monthly["pocket_margin_pct"] = monthly["pocket_margin"] / monthly["pocket_revenue"]
    monthly["realised_price_unit"] = monthly["pocket_revenue"] / monthly["quantity_units"]

    return {
        "price_waterfall": pd.DataFrame(waterfall.add_deltas(steps)).round(2),
        "leakage_by_dimension": pd.DataFrame(leakage_rows),
        "waterfall_monthly": monthly.round({c: 4 for c in monthly.columns if c != "month"}),
    }


# ---------------------------------------------------------------------------
# 2. Elasticity and price response
# ---------------------------------------------------------------------------


def build_elasticity_outputs(
    sales: pd.DataFrame, products: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """
    Estimate elasticity per category and per product, then price the optimum.

    Fitted on **list price** with promotional months excluded, seasonality
    divided out and a linear trend controlled for. Each of those three is a
    correction for a specific confound, and each one moves the answer:

    * pocket price instead of list carries the customer mix, so months when the
      big discounted accounts happened to order look like price cuts;
    * promotional months pair a price cut with a display and an end-cap, and
      attributing all of that lift to the price prints elasticities near -4 on
      products whose true value is under -1;
    * seasonality and trend are the two things that move volume without price
      moving at all.

    Product-level estimates are computed and kept, but they are noisy on 36
    monthly observations and the ``usable`` flag says so on most of them. The
    category estimate is the one to price off.
    """
    base = sales[sales["on_promotion"] == 0]
    panel = (
        base.groupby(["product_id", "category", "month", "month_of_year", "month_index"],
                     as_index=False)
        .agg(quantity_units=("quantity_units", "sum"),
             list_price=("list_price", "first"),
             pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"))
    )
    panel["unit_cost"] = panel["cogs"] / panel["quantity_units"]

    # Within-product demeaning in logs, so a pooled category fit is not
    # dominated by the level difference between a $6 pack of pens and a $340
    # monitor.
    panel["log_price"] = np.log(panel["list_price"])
    panel["log_qty"] = np.log(panel["quantity_units"])
    panel["rel_price"] = np.exp(
        panel["log_price"] - panel.groupby("product_id")["log_price"].transform("mean")
    )
    panel["rel_qty"] = np.exp(
        panel["log_qty"] - panel.groupby("product_id")["log_qty"].transform("mean")
    )

    rows = []
    for category, group in panel.groupby("category"):
        deseasonalised = elasticity.deseasonalise(
            group["rel_qty"], group["month_of_year"]
        )
        fit = elasticity.estimate_elasticity(
            group["rel_price"], deseasonalised,
            controls={"trend": group["month_index"].tolist()},
        )
        volume = float(group["quantity_units"].sum())
        revenue = float(group["pocket_revenue"].sum())
        unit_cost = float(group["cogs"].sum() / volume) if volume else 0.0
        price = revenue / volume if volume else 0.0
        opt = elasticity.optimal_price(unit_cost, fit["elasticity"])
        rows.append(
            {
                "scope": "Category",
                "member": category,
                "products": group["product_id"].nunique(),
                "observations": fit["observations"],
                "elasticity": round(fit["elasticity"], 4),
                "r_squared": round(fit["r_squared"], 4),
                "price_cv": round(fit["price_cv"], 4),
                "usable": bool(fit["usable"]),
                "reason": fit["reason"],
                "volume_units": round(volume, 1),
                "pocket_revenue": round(revenue, 2),
                "avg_pocket_price": round(price, 4),
                "avg_unit_cost": round(unit_cost, 4),
                "optimal_price": round(opt["price"], 4) if opt["exists"] else np.nan,
                "optimal_exists": bool(opt["exists"]),
                "optimal_actionable": bool(opt["actionable"]),
                "implied_markup": round(opt["markup"], 3) if opt["exists"] else np.nan,
                "optimal_note": opt["reason"],
                "price_vs_optimal_pct": round(price / opt["price"] - 1, 4)
                if opt["actionable"] and opt["price"] else np.nan,
            }
        )

    for product_id, group in panel.groupby("product_id"):
        if len(group) < 12:
            continue
        deseasonalised = elasticity.deseasonalise(group["rel_qty"], group["month_of_year"])
        fit = elasticity.estimate_elasticity(
            group["rel_price"], deseasonalised,
            controls={"trend": group["month_index"].tolist()},
        )
        volume = float(group["quantity_units"].sum())
        revenue = float(group["pocket_revenue"].sum())
        unit_cost = float(group["cogs"].sum() / volume) if volume else 0.0
        price = revenue / volume if volume else 0.0
        opt = elasticity.optimal_price(unit_cost, fit["elasticity"])
        rows.append(
            {
                "scope": "Product",
                "member": product_id,
                "products": 1,
                "observations": fit["observations"],
                "elasticity": round(fit["elasticity"], 4),
                "r_squared": round(fit["r_squared"], 4),
                "price_cv": round(fit["price_cv"], 4),
                "usable": bool(fit["usable"]),
                "reason": fit["reason"],
                "volume_units": round(volume, 1),
                "pocket_revenue": round(revenue, 2),
                "avg_pocket_price": round(price, 4),
                "avg_unit_cost": round(unit_cost, 4),
                "optimal_price": round(opt["price"], 4) if opt["exists"] else np.nan,
                "optimal_exists": bool(opt["exists"]),
                "optimal_actionable": bool(opt["actionable"]),
                "implied_markup": round(opt["markup"], 3) if opt["exists"] else np.nan,
                "optimal_note": opt["reason"],
                "price_vs_optimal_pct": round(price / opt["price"] - 1, 4)
                if opt["actionable"] and opt["price"] else np.nan,
            }
        )

    estimates = pd.DataFrame(rows)
    estimates = estimates.merge(
        products[["product_id", "description", "category", "brand_tier", "elasticity"]]
        .rename(columns={"product_id": "member", "elasticity": "seeded_elasticity"}),
        on="member", how="left",
    )

    # Price response curves for each category, at the category's own economics.
    curves = []
    for row in estimates[estimates["scope"] == "Category"].itertuples():
        if not row.usable:
            continue
        for point in elasticity.price_response_curve(
            base_quantity=row.volume_units,
            base_price=row.avg_pocket_price,
            unit_cost=row.avg_unit_cost,
            elasticity=row.elasticity,
        ):
            curves.append({"category": row.member, **{k: round(v, 4) for k, v in point.items()}})

    # A break-even table: for a grid of price cuts, the volume each needs.
    hurdles = []
    for row in estimates[estimates["scope"] == "Category"].itertuples():
        contribution = (
            (row.avg_pocket_price - row.avg_unit_cost) / row.avg_pocket_price
            if row.avg_pocket_price else 0.0
        )
        for change in (-0.10, -0.075, -0.05, -0.025, 0.025, 0.05, 0.075, 0.10):
            hurdle = elasticity.break_even_volume_change(change, contribution)
            impact = elasticity.price_change_impact(
                base_quantity=row.volume_units,
                base_price=row.avg_pocket_price,
                unit_cost=row.avg_unit_cost,
                price_change_pct=change,
                elasticity=row.elasticity,
            )
            hurdles.append(
                {
                    "category": row.member,
                    "price_change_pct": change,
                    "contribution_margin_pct": round(contribution, 4),
                    "break_even_volume_pct": round(hurdle, 4) if np.isfinite(hurdle) else np.nan,
                    "expected_volume_pct": round(impact["volume_change_pct"], 4),
                    "expected_margin_change": round(impact["margin_change"], 2),
                    "expected_revenue_change": round(impact["revenue_change"], 2),
                    "verdict": "Worth it" if impact["margin_change"] > 0 else "Destroys margin",
                }
            )

    return {
        "elasticity_estimates": estimates,
        "price_response_curve": pd.DataFrame(curves),
        "price_change_hurdles": pd.DataFrame(hurdles),
    }


# ---------------------------------------------------------------------------
# 3. Competitive position and pass-through
# ---------------------------------------------------------------------------


def build_competitive_outputs(
    sales: pd.DataFrame,
    observations: pd.DataFrame,
    products: pd.DataFrame,
    panel: pd.DataFrame,
    index_weekly: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Price index per product-month, the book-level gaps, and pass-through."""
    obs = observations.copy()
    obs["month"] = pd.to_datetime(obs["month"])

    revenue = (
        sales.groupby(["product_id", "month"], as_index=False)
        .agg(pocket_revenue=("pocket_revenue", "sum"), quantity_units=("quantity_units", "sum"),
             cogs=("cogs", "sum"))
    )

    rows = []
    for (product_id, month), group in obs.groupby(["product_id", "month"]):
        result = competitive.price_index(
            float(group["our_list_price"].iloc[0]),
            group.rename(columns={"observed_price": "price",
                                  "competitor_id": "competitor"}).to_dict("records"),
            age_key="observation_age_days",
        )
        rows.append(
            {
                "product_id": product_id,
                "month": month,
                "our_price": round(result["our_price"], 4),
                "market_price": round(result["market_price"], 4),
                "price_index": round(result["index"], 2),
                "gap_pct": round(result["gap_pct"], 4),
                "position": result["position"],
                "observations": result["observations"],
                "competitors_seen": result["competitors"],
                "max_age_days": result["max_age_days"],
            }
        )
    index_df = pd.DataFrame(rows).merge(revenue, on=["product_id", "month"], how="left")
    index_df = index_df.merge(
        products[["product_id", "description", "category", "brand_tier", "lifecycle"]],
        on="product_id", how="left",
    )
    index_df[["pocket_revenue", "quantity_units", "cogs"]] = index_df[
        ["pocket_revenue", "quantity_units", "cogs"]
    ].fillna(0.0)
    index_df["margin_pct"] = np.where(
        index_df["pocket_revenue"] > 0,
        (index_df["pocket_revenue"] - index_df["cogs"]) / index_df["pocket_revenue"],
        np.nan,
    )

    # Latest month per product, which is the actionable view.
    latest_month = index_df["month"].max()
    latest = index_df[index_df["month"] == latest_month].copy()
    gaps = competitive.competitive_gaps(
        latest.rename(columns={"price_index": "index", "pocket_revenue": "revenue"})
        .to_dict("records")
    )
    summary = pd.DataFrame(
        [
            {
                "month": latest_month,
                "median_index": round(gaps["median_index"], 2),
                "products_covered": len(latest),
                "coverage": round(gaps["coverage"], 4),
                "underpriced_products": len(gaps["underpriced"]),
                "overpriced_products": len(gaps["overpriced"]),
                "revenue_at_risk": round(gaps["revenue_at_risk"], 2),
                "opportunity": round(gaps["opportunity"], 2),
            }
        ]
    )

    # Pass-through: how much of each input-cost move reached the price.
    #
    # Measured twice on purpose. Against *list* price it is a decision -- how
    # much of the move the last review passed on. Against *realised* pocket
    # price it is an outcome, and the gap between the two is the share of an
    # announced increase that discounting gave straight back. Reporting only
    # the first flatters the pricing team; only the second blames them for
    # discounts they may not have granted.
    panel = panel.copy()
    panel["month"] = pd.to_datetime(panel["month"])
    realised = (
        sales.groupby(["commodity_index", "month"], as_index=False)
        .agg(pocket_revenue=("pocket_revenue", "sum"), quantity_units=("quantity_units", "sum"),
             list_value=("list_value", "sum"))
    )
    realised["realised_price"] = realised["pocket_revenue"] / realised["quantity_units"]
    index_monthly = (
        panel.groupby(["commodity_index", "month"], as_index=False)["index_value"].mean()
    )
    # Pass-through *to list* is measured on the price list itself -- the mean
    # list price of the items on that index -- not on a volume-weighted
    # realisation of it. Weighting by volume lets sales mix into a number that
    # is supposed to be about a pricing decision: a heavy promotional month
    # shifts the weighted list price without a single list price having moved,
    # and on this data that contamination was enough to scramble the ordering
    # of the seven indices against each other.
    list_monthly = (
        panel.groupby(["commodity_index", "month"], as_index=False)["list_price_unit"]
        .mean().rename(columns={"list_price_unit": "list_price"})
    )
    merged = realised.merge(index_monthly, on=["commodity_index", "month"], how="inner")
    merged = merged.merge(list_monthly, on=["commodity_index", "month"], how="inner")

    def like_for_like_change(series: pd.Series, window: int = 12) -> float:
        """
        Last twelve months against the first twelve, not endpoint to endpoint.

        Every one of these series has an annual season. The window starts in
        July before the peak import season and ends in June after it, so an
        endpoint comparison charges the whole seasonal swing to the trend: an
        index read as *down* 14% over a window in which it actually rose by a
        quarter. Averaging complete years removes the season without needing to
        model it.
        """
        if len(series) < window * 2:
            return float("nan")
        first = float(series.iloc[:window].mean())
        last = float(series.iloc[-window:].mean())
        return (last / first - 1.0) if first else float("nan")

    pass_rows = []
    for name, group in merged.sort_values("month").groupby("commodity_index"):
        # Estimated on **quarterly** changes, not monthly. List prices are only
        # reviewed once a quarter, so a month-over-month difference between two
        # reviews measures customer mix and nothing else -- and it shows: on
        # this data the monthly fit explains 7% of the variation and the
        # quarterly fit 59%, and the monthly coefficient for the one category
        # whose price barely tracks its input came out at -2.15.
        quarterly = (
            group.set_index("month")[["index_value", "list_price", "realised_price"]]
            .resample("QE").mean()
        )
        to_list = competitive.best_passthrough_lag(
            quarterly["index_value"].tolist(),
            competitive.index_series(quarterly["list_price"].tolist()),
            max_lag=2,
        )
        to_pocket = competitive.best_passthrough_lag(
            quarterly["index_value"].tolist(),
            competitive.index_series(quarterly["realised_price"].tolist()),
            max_lag=2,
        )
        index_change = like_for_like_change(group["index_value"])
        list_change = like_for_like_change(group["list_price"])
        pocket_change = like_for_like_change(group["realised_price"])
        # A cumulative ratio is meaningless when the denominator is near zero:
        # a 0.3% index move and a 3% price move is not a pass-through of ten.
        material = abs(index_change) > 0.03
        pass_rows.append(
            {
                "commodity_index": name,
                "passthrough_to_list": round(to_list["passthrough"], 4),
                "passthrough_to_pocket": round(to_pocket["passthrough"], 4),
                "discount_giveback": round(
                    to_list["passthrough"] - to_pocket["passthrough"], 4),
                "best_lag_quarters": to_list["lag"],
                "r_squared": round(to_list["r_squared"], 4),
                "observations": to_list["observations"],
                "usable": bool(to_list.get("usable")),
                "reason": to_list.get("reason", ""),
                "index_change_pct": round(index_change, 4),
                "list_price_change_pct": round(list_change, 4),
                "realised_price_change_pct": round(pocket_change, 4),
                "cumulative_passthrough": round(list_change / index_change, 4)
                if material else np.nan,
                "material_move": bool(material),
            }
        )

    weekly = index_weekly.copy()
    weekly["week_start"] = pd.to_datetime(weekly["week_start"])

    return {
        "competitive_index": index_df.round(
            {c: 4 for c in index_df.columns if c != "month"}),
        "competitive_summary": summary,
        "passthrough": pd.DataFrame(pass_rows),
        "commodity_index_weekly": weekly,
    }


# ---------------------------------------------------------------------------
# 4. Cost variance, budget and overhead
# ---------------------------------------------------------------------------


def build_cost_variance_outputs(
    ledger: pd.DataFrame,
    products: pd.DataFrame,
    budget: pd.DataFrame,
    overhead: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Standard-costing variances, restated through the tested helpers."""
    joined = ledger.merge(
        products[["product_id", "description", "category", "brand_tier", "supplier"]]
        .rename(columns={"supplier": "product_supplier"}),
        on="product_id", how="left",
    )
    joined["month"] = pd.to_datetime(joined["month"])
    joined["fiscal_year"] = np.where(
        joined["month"].dt.month >= 7, joined["month"].dt.year + 1, joined["month"].dt.year
    )

    rows = []
    for row in joined.itertuples():
        ppv = variance.purchase_price_variance(
            actual_price=row.actual_input_cost_unit,
            standard_price=row.standard_input_cost_unit,
            actual_quantity=row.purchased_units,
        )
        yv = variance.yield_variance(
            output_units=row.output_units,
            actual_input_units=row.purchased_units,
            standard_sellable_rate=row.standard_sellable_rate,
            standard_input_price=row.standard_input_cost_unit,
        )
        lrv = variance.labour_rate_variance(
            actual_rate=row.actual_handling_unit,
            standard_rate=row.standard_handling_unit,
            actual_hours=row.output_units,
        )
        lev = variance.labour_efficiency_variance(
            actual_hours=row.labour_hours,
            standard_hours=row.standard_hours,
            standard_rate=row.standard_handling_unit * 68.0,
        )
        total = variance.total_cost_variance([ppv, yv, lrv, lev])
        rows.append(
            {
                "month": row.month,
                "fiscal_year": row.fiscal_year,
                "product_id": row.product_id,
                "description": row.description,
                "category": row.category,
                "brand_tier": row.brand_tier,
                "supplier": row.supplier,
                "purchased_units": row.purchased_units,
                "output_units": row.output_units,
                "standard_sellable_rate": row.standard_sellable_rate,
                "actual_recovery": row.actual_recovery,
                "recovery_gap": round(row.actual_recovery - row.standard_sellable_rate, 4),
                "purchase_price_variance": round(ppv["variance"], 2),
                "yield_variance": round(yv["variance"], 2),
                "labour_rate_variance": round(lrv["variance"], 2),
                "labour_efficiency_variance": round(lev["variance"], 2),
                "total_variance": round(total["variance"], 2),
                "verdict": total["verdict"],
                "standard_cost_value": round(row.standard_input_cost_unit * row.purchased_units, 2),
                "actual_cost_value": round(row.actual_input_cost_unit * row.purchased_units, 2),
            }
        )
    detail = pd.DataFrame(rows)

    # Long form, one row per variance type, for a stacked chart that does not
    # need four separate measures.
    value_cols = ["purchase_price_variance", "yield_variance",
                  "labour_rate_variance", "labour_efficiency_variance"]
    long = detail.melt(
        id_vars=["month", "fiscal_year", "product_id", "category", "brand_tier", "supplier"],
        value_vars=value_cols, var_name="variance_type", value_name="variance",
    )
    long["variance_type"] = long["variance_type"].map(
        {
            "purchase_price_variance": "Purchase price",
            "yield_variance": "Yield",
            "labour_rate_variance": "Labour rate",
            "labour_efficiency_variance": "Labour efficiency",
        }
    )
    long["verdict"] = np.where(long["variance"] > 0, "Unfavourable", "Favourable")

    # Budget vs actual, restated so the sign convention is explicit per line.
    bud = budget.copy()
    bud["month"] = pd.to_datetime(bud["month"])
    bud_rows = []
    for row in bud.itertuples():
        rev = variance.budget_variance(actual=row.actual_revenue, budget=row.budget_revenue)
        cogs = variance.budget_variance(
            actual=row.actual_cogs, budget=row.budget_cogs, higher_is_better=False
        )
        margin = variance.budget_variance(actual=row.actual_margin, budget=row.budget_margin)
        bud_rows.append(
            {
                "month": row.month,
                "fiscal_year": row.fiscal_year,
                "category": row.category,
                "budget_revenue": row.budget_revenue,
                "actual_revenue": row.actual_revenue,
                "revenue_variance": round(rev["variance"], 2),
                "revenue_variance_pct": round(rev["variance_pct"], 4),
                "revenue_verdict": rev["verdict"],
                "budget_cogs": row.budget_cogs,
                "actual_cogs": row.actual_cogs,
                "cogs_variance": round(cogs["variance"], 2),
                "cogs_verdict": cogs["verdict"],
                "budget_margin": row.budget_margin,
                "actual_margin": row.actual_margin,
                "margin_variance": round(margin["variance"], 2),
                "margin_verdict": margin["verdict"],
                "budget_volume_units": row.budget_volume_units,
                "actual_volume_units": row.actual_volume_units,
            }
        )

    oh = overhead.copy()
    oh["month"] = pd.to_datetime(oh["month"])
    oh_rows = []
    for row in oh.itertuples():
        spend = variance.overhead_spending_variance(
            actual_overhead=row.actual_overhead,
            budgeted_rate=row.budgeted_rate_unit,
            actual_driver=row.actual_volume_units,
        )
        vol = variance.overhead_volume_variance(
            budgeted_driver=row.budgeted_volume_units,
            actual_driver=row.actual_volume_units,
            budgeted_rate=row.budgeted_rate_unit,
        )
        oh_rows.append(
            {
                "month": row.month,
                "cost_pool": row.cost_pool,
                "budgeted_rate_unit": row.budgeted_rate_unit,
                "actual_overhead": row.actual_overhead,
                "absorbed_overhead": round(spend["absorbed"], 2),
                "spending_variance": round(spend["variance"], 2),
                "spending_verdict": spend["verdict"],
                "volume_variance": round(vol["variance"], 2),
                "volume_verdict": vol["verdict"],
                "total_variance": round(spend["variance"] + vol["variance"], 2),
            }
        )

    return {
        "cost_variance_detail": detail,
        "cost_variance_long": long,
        "budget_variance": pd.DataFrame(bud_rows),
        "overhead_variance": pd.DataFrame(oh_rows),
    }


# ---------------------------------------------------------------------------
# 5. Margin bridge
# ---------------------------------------------------------------------------


def build_bridge_outputs(sales: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Year-on-year and quarter-on-quarter bridges, on complete periods only.

    The comparison periods are complete fiscal years by construction, which is
    the guard that matters: bridging a part-year against a full one produces a
    volume effect the size of the missing months and a story about a business
    that did not happen.
    """
    def rows_for(frame: pd.DataFrame) -> list[dict]:
        grouped = (
            frame.groupby("product_id", as_index=False)
            .agg(quantity=("quantity_units", "sum"),
                 pocket_revenue=("pocket_revenue", "sum"),
                 cogs=("cogs", "sum"))
        )
        grouped["price"] = grouped["pocket_revenue"] / grouped["quantity"]
        grouped["cost"] = grouped["cogs"] / grouped["quantity"]
        return grouped[["product_id", "quantity", "price", "cost"]].to_dict("records")

    years = sorted(sales["fiscal_year"].unique())
    steps, effects = [], []
    for prior_year, current_year in zip(years, years[1:], strict=False):
        prior = rows_for(sales[sales["fiscal_year"] == prior_year])
        current = rows_for(sales[sales["fiscal_year"] == current_year])
        bridge = variance.margin_bridge(prior, current)
        label = f"FY{prior_year} to FY{current_year}"
        for order, step in enumerate(variance.bridge_steps(bridge, value_key="margin")):
            steps.append({"comparison": label, "sort_order": order,
                          **{k: (round(v, 2) if isinstance(v, float) else v)
                             for k, v in step.items()}})
        for effect in bridge["effects"]:
            effects.append(
                {
                    "comparison": label,
                    "effect": effect["effect"],
                    "amount": round(effect["amount"], 2),
                    "pct_of_prior": round(effect["amount"] / bridge["margin_prior"], 6)
                    if bridge["margin_prior"] else 0.0,
                }
            )
        effects.append({"comparison": label, "effect": "Residual",
                        "amount": round(bridge["residual"], 6), "pct_of_prior": 0.0})

    # The same decomposition on revenue, which is the number leadership asks for.
    revenue_effects = []
    for prior_year, current_year in zip(years, years[1:], strict=False):
        bridge = variance.revenue_bridge(
            rows_for(sales[sales["fiscal_year"] == prior_year]),
            rows_for(sales[sales["fiscal_year"] == current_year]),
        )
        label = f"FY{prior_year} to FY{current_year}"
        for effect in bridge["effects"]:
            revenue_effects.append(
                {"comparison": label, "effect": effect["effect"],
                 "amount": round(effect["amount"], 2)}
            )

    return {
        "margin_bridge_steps": pd.DataFrame(steps),
        "margin_bridge_effects": pd.DataFrame(effects),
        "revenue_bridge_effects": pd.DataFrame(revenue_effects),
    }


# ---------------------------------------------------------------------------
# 6. Price bands, segments and willingness to pay
# ---------------------------------------------------------------------------


def build_segment_outputs(
    sales: pd.DataFrame, quotes: pd.DataFrame, customers: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Price dispersion, the realisation opportunity, and the win curve."""
    latest_year = sales["fiscal_year"].max()
    recent = sales[sales["fiscal_year"] == latest_year]

    band_rows = []
    for (product_id, description, category), group in recent.groupby(
        ["product_id", "description", "category"]
    ):
        if len(group) < 8:
            continue
        gap = segmentation.realisation_gap(
            group.rename(columns={"quantity_units": "quantity"}).to_dict("records")
        )
        band_rows.append(
            {
                "product_id": product_id,
                "description": description,
                "category": category,
                "customers": group["customer_id"].nunique(),
                "lines": gap["n"],
                "volume_units": round(gap["volume"], 1),
                "p10_price": round(gap["p10"], 4),
                "median_price": round(gap["median"], 4),
                "p90_price": round(gap["p90"], 4),
                "band_width_pct": round(gap["band_width_pct"], 4),
                "realisation_opportunity": round(gap["opportunity"], 2),
                "lines_below_median": gap["lines_below"],
            }
        )
    bands = pd.DataFrame(band_rows).sort_values(
        "realisation_opportunity", ascending=False
    ).reset_index(drop=True)

    profiles = []
    for dimension in ("segment", "channel", "region", "tier", "brand_tier", "category"):
        frame = pd.DataFrame(
            segmentation.segment_profile(
                recent.rename(columns={"quantity_units": "quantity"}).to_dict("records"),
                key=dimension,
            )
        )
        frame = frame.rename(columns={dimension: "member"})
        frame.insert(0, "dimension", dimension)
        profiles.append(frame)
    profile = pd.concat(profiles, ignore_index=True)

    # Willingness to pay, overall and by segment.
    quote_rows = quotes.to_dict("records")
    fits = []
    for label, subset in [("All", quote_rows)] + [
        (segment, group.to_dict("records")) for segment, group in quotes.groupby("segment")
    ]:
        fit = segmentation.fit_win_curve(subset)
        fits.append(
            {
                "segment": label,
                "quotes": fit["observations"],
                "wins": fit["wins"],
                "win_rate": round(fit["win_rate"], 4),
                "intercept": round(fit["intercept"], 4),
                "slope": round(fit["slope"], 4),
                "indifference_price_ratio": round(fit["indifference_price"], 4),
                "converged": bool(fit["converged"]),
                "usable": bool(fit["usable"]),
                "reason": fit["reason"],
            }
        )
    wtp = pd.DataFrame(fits)

    curve_rows = []
    for label, subset in [("All", quote_rows)] + [
        (segment, group.to_dict("records")) for segment, group in quotes.groupby("segment")
    ]:
        fit = segmentation.fit_win_curve(subset)
        for point in segmentation.win_curve_points(fit):
            curve_rows.append({"segment": label, "kind": "Fitted",
                               "price_ratio": round(point["price_ratio"], 4),
                               "win_rate": round(point["win_probability"], 4),
                               "quotes": np.nan})
        for point in segmentation.observed_win_rates(subset):
            curve_rows.append({"segment": label, "kind": "Observed",
                               "price_ratio": round(point["price_ratio"], 4),
                               "win_rate": round(point["win_rate"], 4),
                               "quotes": point["quotes"]})

    lost = quotes[quotes["won"] == 0]
    loss_reasons = (
        lost.groupby("loss_reason", as_index=False)
        .agg(quotes=("quote_id", "count"), volume_units=("quantity_units", "sum"))
        .sort_values("quotes", ascending=False)
    )
    loss_reasons["value_lost"] = (
        lost.groupby("loss_reason")
        .apply(lambda g: float((g["quoted_price"] * g["quantity_units"]).sum()),
               include_groups=False)
        .reindex(loss_reasons["loss_reason"]).to_numpy().round(2)
    )

    # Customer profitability quadrant: margin rate against volume, with the
    # cost to serve that makes a large account unprofitable visible on it.
    customer_rows = (
        recent.groupby(["customer_id", "customer_name", "segment", "channel",
                        "region", "tier"], as_index=False)
        .agg(volume_units=("quantity_units", "sum"),
             list_value=("list_value", "sum"),
             invoice_revenue=("revenue", "sum"),
             pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"),
             lines=("product_id", "count"),
             products=("product_id", "nunique"))
    )
    customer_rows["cost_to_serve"] = (
        recent.groupby("customer_id")
        .apply(lambda g: float((g["cost_to_serve"] * g["quantity_units"]).sum()),
               include_groups=False)
        .reindex(customer_rows["customer_id"]).to_numpy()
    )
    customer_rows["pocket_margin"] = customer_rows["pocket_revenue"] - customer_rows["cogs"]
    customer_rows["pocket_margin_pct"] = (
        customer_rows["pocket_margin"] / customer_rows["pocket_revenue"]
    )
    customer_rows["leakage_pct"] = 1 - customer_rows["pocket_revenue"] / customer_rows["list_value"]
    customer_rows["avg_drop_units"] = customer_rows["volume_units"] / customer_rows["lines"]
    customer_rows = customer_rows.merge(
        customers[["customer_id", "price_list", "payment_terms"]], on="customer_id", how="left"
    )
    median_margin = customer_rows["pocket_margin_pct"].median()
    median_volume = customer_rows["volume_units"].median()
    customer_rows["quadrant"] = np.select(
        [
            (customer_rows["pocket_margin_pct"] >= median_margin)
            & (customer_rows["volume_units"] >= median_volume),
            (customer_rows["pocket_margin_pct"] >= median_margin)
            & (customer_rows["volume_units"] < median_volume),
            (customer_rows["pocket_margin_pct"] < median_margin)
            & (customer_rows["volume_units"] >= median_volume),
        ],
        ["Protect", "Grow", "Fix price"],
        default="Review or exit",
    )

    return {
        "price_bands": bands,
        "segment_profile": profile.round(4),
        "wtp_fits": wtp,
        "wtp_curve": pd.DataFrame(curve_rows),
        "quote_loss_reasons": loss_reasons,
        "customer_profitability": customer_rows.round(4),
    }


# ---------------------------------------------------------------------------
# 7. Guardrails and bundles
# ---------------------------------------------------------------------------


def build_guardrail_outputs(
    sales: pd.DataFrame, competitive_index: pd.DataFrame, panel: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Score the most recent month's lines against the band and scan for breaches."""
    latest_month = sales["month"].max()
    recent = sales[sales["month"] == latest_month].copy()

    index_latest = competitive_index[
        competitive_index["month"] == competitive_index["month"].max()
    ][["product_id", "price_index"]]
    recent = recent.merge(index_latest, on="product_id", how="left")

    # How long since this product's list price last moved, and how far its cost
    # has travelled since -- the two inputs to the "stale price" rules.
    panel = panel.copy()
    panel["month"] = pd.to_datetime(panel["month"])
    panel = panel.sort_values(["product_id", "month"])
    panel["price_changed"] = (
        panel.groupby("product_id")["list_price_unit"].diff().abs() > 0.0005
    )
    last_change = (
        panel[panel["price_changed"]].groupby("product_id")["month"].max()
        .rename("last_price_change")
    )
    cost_at_change = (
        panel[panel["price_changed"]].sort_values("month")
        .groupby("product_id")["actual_final_cost_unit"].last().rename("cost_at_last_change")
    )
    recent = recent.merge(last_change, on="product_id", how="left")
    recent = recent.merge(cost_at_change, on="product_id", how="left")
    recent["days_since_price_change"] = (
        (latest_month - recent["last_price_change"]).dt.days.fillna(999)
    )
    recent["cost_change_pct"] = np.where(
        recent["cost_at_last_change"].notna() & (recent["cost_at_last_change"] > 0),
        recent["final_cost"] / recent["cost_at_last_change"] - 1,
        0.0,
    )

    scored_rows = []
    for row in recent.itertuples():
        deductions = {key: getattr(row, key) for key in ALL_DEDUCTIONS}
        score = guardrails.score_deal(
            list_price=row.list_price,
            final_cost=row.final_cost,
            deductions=deductions,
            floor_margin=max(0.05, row.target_margin - 0.09),
            target_margin=row.target_margin,
            stretch_margin=row.target_margin + 0.08,
            quantity=row.quantity_units,
        )
        scored_rows.append(
            {
                "month": row.month,
                "product_id": row.product_id,
                "description": row.description,
                "category": row.category,
                "customer_id": row.customer_id,
                "customer_name": row.customer_name,
                "segment": row.segment,
                "tier": row.tier,
                "quantity_units": row.quantity_units,
                "list_price": score["list_price"],
                "pocket_price": round(score["pocket_price"], 4),
                "floor_price": round(score["floor_price"], 4),
                "target_price": round(score["target_price"], 4),
                "stretch_price": round(score["stretch_price"], 4),
                "pocket_margin_pct": round(score["pocket_margin_pct"], 4),
                "floor_margin": round(score["floor_margin"], 4),
                "target_margin": round(score["target_margin"], 4),
                "verdict": score["verdict"],
                "within_guardrail": bool(score["within_guardrail"]),
                "approver": score["approver"],
                "margin_gap_dollars": round(score["margin_gap_dollars"], 2),
                "extended_margin": round(score["extended_margin"], 2),
                "leakage_pct": round(1 - score["pocket_price"] / row.list_price, 4)
                if row.list_price else 0.0,
                "price_index": row.price_index,
                "days_since_price_change": row.days_since_price_change,
                "cost_change_pct": round(row.cost_change_pct, 4),
            }
        )
    scored = pd.DataFrame(scored_rows)

    findings = pd.DataFrame(guardrails.scan_exceptions(scored.to_dict("records")))
    if not findings.empty:
        findings = findings.merge(
            scored[["product_id", "customer_id", "category", "segment", "customer_name",
                    "quantity_units", "list_price", "pocket_price"]].drop_duplicates(
                subset=["product_id", "customer_id"]),
            on=["product_id", "customer_id"], how="left",
        )
    summary = pd.DataFrame(guardrails.exception_summary(findings.to_dict("records")))

    return {
        "deal_scores": scored,
        "guardrail_exceptions": findings,
        "guardrail_summary": summary,
    }


def build_bundle_outputs(sales: pd.DataFrame, products: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Candidate bundles, built from products the same customers already buy together.

    Affinity comes from co-purchase in the same month, which is the closest
    thing an invoice file has to a basket. Each candidate is then priced across
    a sweep of discounts and judged on incremental margin, not on its own.
    """
    latest_year = sales["fiscal_year"].max()
    recent = sales[sales["fiscal_year"] == latest_year]

    baskets = recent.groupby(["customer_id", "month"])["product_id"].apply(set)
    pair_counts: dict[tuple[str, str], int] = {}
    line_counts: dict[str, int] = {}
    for basket in baskets:
        items = sorted(basket)
        for product_id in items:
            line_counts[product_id] = line_counts.get(product_id, 0) + 1
        if len(items) > 24:      # cap the pair explosion on very broad baskets
            items = items[:24]
        for i, left in enumerate(items):
            for right in items[i + 1:]:
                pair_counts[(left, right)] = pair_counts.get((left, right), 0) + 1

    economics = (
        recent.groupby("product_id", as_index=False)
        .agg(volume_units=("quantity_units", "sum"),
             pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"))
    )
    economics["price"] = economics["pocket_revenue"] / economics["volume_units"]
    economics["cost"] = economics["cogs"] / economics["volume_units"]
    econ = economics.set_index("product_id")[["price", "cost", "volume_units"]].to_dict("index")
    names = products.set_index("product_id")["description"].to_dict()
    categories = products.set_index("product_id")["category"].to_dict()

    top_pairs = sorted(pair_counts.items(), key=lambda kv: -kv[1])[:24]
    candidates, sweeps = [], []
    for rank, ((left, right), count) in enumerate(top_pairs, start=1):
        if left not in econ or right not in econ:
            continue
        components = [
            {"price": econ[left]["price"], "cost": econ[left]["cost"], "quantity": 1.0},
            {"price": econ[right]["price"], "cost": econ[right]["cost"], "quantity": 1.0},
        ]
        # Cannibalisation is the co-purchase rate: the share of the bundle's
        # likely buyers who already buy both without being asked.
        buyers = min(
            recent[recent["product_id"] == left]["customer_id"].nunique(),
            recent[recent["product_id"] == right]["customer_id"].nunique(),
        )
        cannibalisation = float(np.clip(count / max(buyers * 12, 1), 0.05, 0.9))
        expected_units = float(min(econ[left]["volume_units"], econ[right]["volume_units"]) * 0.12)

        bundle = bundles.build_bundle(components, discount=0.10)
        result = bundles.incremental_margin(
            bundle, expected_units=expected_units, cannibalisation_rate=cannibalisation
        )
        candidates.append(
            {
                "rank": rank,
                "bundle_id": f"B{rank:02d}",
                "product_a": left,
                "product_b": right,
                "name_a": names.get(left, left),
                "name_b": names.get(right, right),
                "category_a": categories.get(left, ""),
                "category_b": categories.get(right, ""),
                "co_purchase_months": count,
                "standalone_price": round(bundle["standalone_price"], 4),
                "bundle_price": round(bundle["bundle_price"], 4),
                "standalone_margin_pct": round(bundle["standalone_margin_pct"], 4),
                "bundle_margin_pct": round(bundle["bundle_margin_pct"], 4),
                "expected_units": round(expected_units, 1),
                "cannibalisation_rate": round(cannibalisation, 4),
                "break_even_cannibalisation": round(result["break_even_cannibalisation"], 4),
                "headroom": round(result["headroom"], 4),
                "incremental_margin": round(result["incremental_margin"], 2),
                "verdict": result["verdict"],
            }
        )
        for point in bundles.optimal_bundle_discount(
            components,
            expected_units=expected_units,
            cannibalisation_rate=cannibalisation,
            demand_lift_per_point=expected_units * 0.012,
            steps=18,
        ):
            sweeps.append({"bundle_id": f"B{rank:02d}",
                           **{k: round(v, 4) if isinstance(v, float) else v
                              for k, v in point.items()}})

    # A good/better/best ladder on the median item of each category.
    ladders = []
    for category, group in products.groupby("category"):
        median_id = group["product_id"].iloc[len(group) // 2]
        if median_id not in econ:
            continue
        cost = econ[median_id]["cost"]
        for rung in bundles.price_ladder(
            cost,
            [
                {"name": "Good", "margin": 0.16, "cost_uplift": 0.0},
                {"name": "Better", "margin": 0.24, "cost_uplift": cost * 0.06},
                {"name": "Best", "margin": 0.33, "cost_uplift": cost * 0.15},
            ],
        ):
            ladders.append({"category": category, "product_id": median_id,
                            "description": names.get(median_id, median_id),
                            **{k: round(v, 4) if isinstance(v, float) else v
                               for k, v in rung.items()}})

    # Attachment, for every product rather than only the two dozen that made
    # the candidate list.
    #
    # The measure is the share of a product's orders that also contain its
    # *single most frequent partner* -- not the share that contain anything
    # else at all. A distributor's customers buy twenty lines at a time, so
    # "was it bought alongside something" is true of 99% of the book and
    # discriminates nothing. "Is there one specific item it travels with" is
    # the question a bundle actually asks, and it separates a keyboard that
    # goes out with a monitor two orders in five from a cat litter that goes
    # out with whatever else was on the pallet.
    partner_best: dict[str, tuple[str, int]] = {}
    for (left, right), count in pair_counts.items():
        if count > partner_best.get(left, ("", 0))[1]:
            partner_best[left] = (right, count)
        if count > partner_best.get(right, ("", 0))[1]:
            partner_best[right] = (left, count)

    co_purchase = pd.DataFrame(
        [
            {
                "product_id": product_id,
                "description": names.get(product_id, product_id),
                "order_lines": count,
                "top_partner": partner_best.get(product_id, ("", 0))[0],
                "top_partner_orders": partner_best.get(product_id, ("", 0))[1],
                "co_purchase_rate": (
                    partner_best.get(product_id, ("", 0))[1] / count if count else 0.0),
            }
            for product_id, count in line_counts.items()
        ]
    ).sort_values("co_purchase_rate", ascending=False)
    co_purchase["top_partner_name"] = co_purchase["top_partner"].map(names).fillna("")

    return {
        "bundle_candidates": pd.DataFrame(candidates),
        "bundle_discount_sweep": pd.DataFrame(sweeps),
        "price_ladders": pd.DataFrame(ladders),
        "product_co_purchase": co_purchase.round(4),
    }


# ---------------------------------------------------------------------------
# 8. Executive summary
# ---------------------------------------------------------------------------


def build_executive_summary(outputs: dict[str, pd.DataFrame], sales: pd.DataFrame) -> pd.DataFrame:
    """
    The dozen numbers a pricing analyst opens a leadership meeting with.

    One row per KPI with its value, its unit and a sentence of interpretation,
    so a card on a dashboard and a line in the app can render the same thing
    without either restating the definition.
    """
    latest_year = int(sales["fiscal_year"].max())
    prior_year = latest_year - 1
    recent = sales[sales["fiscal_year"] == latest_year]
    prior = sales[sales["fiscal_year"] == prior_year]

    list_value = float(recent["list_value"].sum())
    pocket_revenue = float(recent["pocket_revenue"].sum())
    cogs = float(recent["cogs"].sum())
    leakage = 1 - pocket_revenue / list_value if list_value else 0.0
    pocket_margin_pct = (pocket_revenue - cogs) / pocket_revenue if pocket_revenue else 0.0
    prior_margin_pct = (
        (float(prior["pocket_revenue"].sum()) - float(prior["cogs"].sum()))
        / float(prior["pocket_revenue"].sum())
        if len(prior) and prior["pocket_revenue"].sum() else 0.0
    )

    exceptions = outputs.get("guardrail_exceptions", pd.DataFrame())
    bands = outputs.get("price_bands", pd.DataFrame())
    comp = outputs.get("competitive_summary", pd.DataFrame())
    passthrough = outputs.get("passthrough", pd.DataFrame())
    variances = outputs.get("cost_variance_detail", pd.DataFrame())
    # Scoped to the same year as every other card on this row. Unscoped, the
    # two variance cards summed all three fiscal years and were stamped with
    # the latest one: -$5.6M sitting beside a revenue figure of $260M, both
    # labelled FY2026, one of them describing a period three times as long.
    if not variances.empty and "fiscal_year" in variances.columns:
        variances = variances[variances["fiscal_year"] == latest_year]

    rows = [
        ("Pocket revenue", pocket_revenue, "currency",
         f"FY{latest_year} revenue after every discount, rebate and cost to serve."),
        ("Pocket margin %", pocket_margin_pct, "percent",
         f"Down {abs(pocket_margin_pct - prior_margin_pct):.1%} on FY{prior_year}."
         if pocket_margin_pct < prior_margin_pct else
         f"Up {pocket_margin_pct - prior_margin_pct:.1%} on FY{prior_year}."),
        ("Revenue leakage %", leakage, "percent",
         "Share of list price that never reaches us. Every point is "
         f"${list_value * 0.01:,.0f}."),
        ("Realisation opportunity", float(bands["realisation_opportunity"].sum())
         if not bands.empty else 0.0, "currency",
         "Value of moving every below-median line up to its own product's median price."),
        ("Median price index", float(comp["median_index"].iloc[0]) if not comp.empty else 0.0,
         "index", "Our price against the market, latest month. 100 is parity."),
        ("Products under market", float(comp["underpriced_products"].iloc[0])
         if not comp.empty else 0.0, "count",
         "Priced below 96 on the index, with margin available."),
        ("Margin at risk", float(exceptions["margin_at_risk"].sum())
         if not exceptions.empty else 0.0, "currency",
         "Extended margin on lines breaching a guardrail this month."),
        ("Guardrail breaches", float(len(exceptions)), "count",
         "Lines below floor, loss-making, or outside the index band."),
        ("Purchase price variance", float(variances["purchase_price_variance"].sum())
         if not variances.empty else 0.0, "currency",
         f"FY{latest_year} input cost against the standard frozen that July. "
         "Positive is unfavourable."),
        ("Yield variance", float(variances["yield_variance"].sum())
         if not variances.empty else 0.0, "currency",
         f"FY{latest_year} units bought beyond what the standard sellable rate "
         "allows for what shipped."),
        ("Average pass-through", float(passthrough["passthrough_to_pocket"].mean())
         if not passthrough.empty else 0.0, "ratio",
         "Share of an input-cost move that reached the realised price, estimated "
         "across all three years. Below 1 compresses margin."),
        ("Volume", float(recent["quantity_units"].sum()), "count",
         f"Units shipped in FY{latest_year}."),
    ]
    return pd.DataFrame(
        [
            {"kpi": name, "value": round(value, 6), "unit": unit,
             "note": note, "fiscal_year": latest_year, "sort_order": i}
            for i, (name, value, unit, note) in enumerate(rows)
        ]
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    tables = load(data_dir)
    sales = tables["sales"]

    outputs: dict[str, pd.DataFrame] = {}
    outputs.update(build_waterfall_outputs(sales))
    outputs.update(build_elasticity_outputs(sales, tables["dim_product"]))
    outputs.update(
        build_competitive_outputs(
            sales, tables["fact_competitor_price"], tables["dim_product"],
            tables["fact_price_cost_panel"], tables["fact_commodity_index"],
        )
    )
    outputs.update(
        build_cost_variance_outputs(
            tables["fact_cost_ledger"], tables["dim_product"],
            tables["fact_budget"], tables["fact_overhead"],
        )
    )
    outputs.update(build_bridge_outputs(sales))
    outputs.update(build_segment_outputs(sales, tables["fact_quote"], tables["dim_customer"]))
    outputs.update(
        build_guardrail_outputs(
            sales, outputs["competitive_index"], tables["fact_price_cost_panel"]
        )
    )
    outputs.update(build_bundle_outputs(sales, tables["dim_product"]))

    # --- the second half of the analysis ---------------------------------
    outputs.update(
        extended.build_unit_economics(
            sales, tables["fact_cost_element"], tables["fact_fixed_cost"],
            tables["dim_product"],
        )
    )
    outputs.update(extended.build_forecast(sales))
    outputs.update(extended.build_profitability(sales, tables["fact_fixed_cost"]))
    outputs.update(
        extended.build_operations(
            tables["fact_promotion"], tables["fact_price_change"],
            tables["fact_price_cost_panel"],
        )
    )
    outputs.update(
        extended.build_cost_element_analysis(
            tables["fact_cost_element"], tables["fact_fixed_cost"], tables["dim_product"],
        )
    )
    outputs.update(
        extended.build_scenarios(
            outputs["unit_economics"], tables["fact_fixed_cost"],
            outputs["elasticity_estimates"],
        )
    )
    outputs.update(
        extended.build_recommendations(
            sales, outputs["unit_economics"], outputs["competitive_index"],
            outputs["elasticity_estimates"], outputs["deal_scores"],
            tables["fact_price_cost_panel"], tables["dim_product"],
            outputs["bundle_candidates"],
            co_purchase=outputs["product_co_purchase"],
            material_master=_material_master(data_dir),
        )
    )
    outputs["executive_summary"] = build_executive_summary(outputs, sales)
    return outputs


def write(outputs: dict[str, pd.DataFrame], out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in outputs.items():
        frame.to_csv(out_dir / f"{name}.csv", index=False, lineterminator="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    outputs = build(args.data_dir)
    write(outputs, args.out_dir)
    print(f"wrote {len(outputs)} analysis tables to {args.out_dir}/")
    for name, frame in sorted(outputs.items()):
        print(f"  {name:28s} {len(frame):>7,} rows x {len(frame.columns):>2} cols")
    print()
    for row in outputs["executive_summary"].itertuples():
        if row.unit == "percent":
            value = f"{row.value:>12.1%}"
        elif row.unit == "currency":
            value = f"{row.value:>12,.0f}"
        else:
            value = f"{row.value:>12,.2f}"
        print(f"  {row.kpi:26s}{value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
