"""
The second half of the analysis: unit economics, forecasting, profitability,
promotions, price-list operations, scenarios and recommendations.

Split from :mod:`engine.build_pricing_analytics` for size, not for meaning --
``python -m engine.build_pricing_analytics`` runs both, and everything here
reads the same ``data/`` and writes the same ``output/``.

One rule holds across the whole file, and it is the reason this layer exists at
all: **anything with a definition worth arguing about is computed once, here.**
Contribution margin, fixed-cost allocation, what counts as promotional
incrementality, which forecast method won -- each of those is a decision, and a
decision made twice is made differently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pricing import forecast as fc
from pricing import recommend as rec
from pricing import scenario as sc
from pricing import unit_economics as ue
from pricing import waterfall

# The dimensions profitability is cut by. Salesperson is on the list because a
# rep's discounting habit moves realised price without moving anything a
# category- or channel-level cut would show.
#
# `description` and `customer_name` are the product and customer cuts. They
# were missing while the README, the app and the dashboard slicer all said
# "profit by product, customer, region, channel and salesperson" -- the two
# named first were the two not there. They go last because they are the long
# ones: 240 and 150 members against six to nine for everything else.
PROFIT_CUTS = ("category", "sub_category", "brand_tier", "segment", "channel",
               "region", "tier", "salesperson", "price_list",
               "description", "customer_name")

# Bands for the discount-to-margin matrix. Wide enough to hold a useful count
# per cell, narrow enough that the diagonal is visible.
DISCOUNT_BANDS = [(-0.001, 0.05), (0.05, 0.10), (0.10, 0.15),
                  (0.15, 0.20), (0.20, 0.30), (0.30, 1.01)]
MARGIN_BANDS = [(-9.0, 0.0), (0.0, 0.10), (0.10, 0.20),
                (0.20, 0.30), (0.30, 0.40), (0.40, 9.0)]

# The r-squared a product's own elasticity fit has to clear before it is
# preferred over its category's. Below this the slope is noise with a decimal
# point, and the category estimate -- fitted over twenty times the observations
# -- is the better answer even though it is less specific.
MIN_PRODUCT_FIT = 0.10


def _band(value: float, bands: list[tuple[float, float]], fmt: str = "{:.0%}") -> str:
    for low, high in bands:
        if low <= value < high:
            if low <= -1:
                return f"< {fmt.format(high)}"
            if high >= 1.0 and fmt.endswith("%}"):
                return f"{fmt.format(low)}+"
            return f"{fmt.format(max(low, 0))} to {fmt.format(high)}"
    return "Other"


# ---------------------------------------------------------------------------
# 1. Unit economics and break-even
# ---------------------------------------------------------------------------

def build_unit_economics(
    sales: pd.DataFrame, cost_elements: pd.DataFrame, fixed_costs: pd.DataFrame,
    products: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """
    Contribution per unit, and the break-even the fixed pool implies.

    The whole thing turns on splitting cost by **behaviour** rather than by
    absorption. A standard cost carries a share of the fixed pool, which is
    right for valuing inventory and wrong for every decision about the next
    unit: price off it and you refuse business that would have paid for
    itself; break even on it and you are wrong by the entire fixed pool, in the
    direction that flatters you.
    """
    latest_year = sales["fiscal_year"].max()
    recent = sales[sales["fiscal_year"] == latest_year]

    variable = (
        cost_elements[cost_elements["behaviour"] == "Variable"]
        .groupby(["product_id", "month"], as_index=False)
        .agg(variable_cost=("actual_cost", "sum"), volume_units=("volume_units", "first"))
    )
    variable["month"] = pd.to_datetime(variable["month"])
    variable["fiscal_year"] = np.where(
        variable["month"].dt.month >= 7,
        variable["month"].dt.year + 1, variable["month"].dt.year)
    variable = variable[variable["fiscal_year"] == latest_year]

    per_product = (
        recent.groupby(["product_id", "description", "category", "brand_tier"],
                       as_index=False)
        .agg(volume_units=("quantity_units", "sum"),
             list_value=("list_value", "sum"),
             pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"))
    )
    per_product["cost_to_serve"] = (
        recent.groupby("product_id")
        .apply(lambda g: float((g["cost_to_serve"] * g["quantity_units"]).sum()),
               include_groups=False)
        .reindex(per_product["product_id"]).to_numpy()
    )
    variable_by_product = variable.groupby("product_id")["variable_cost"].sum()
    per_product["production_variable_cost"] = (
        variable_by_product.reindex(per_product["product_id"]).fillna(0).to_numpy())
    # Where the element split has no row for a product-month, fall back to the
    # cost stack rather than reporting an infinite contribution.
    per_product["production_variable_cost"] = np.where(
        per_product["production_variable_cost"] > 0,
        per_product["production_variable_cost"], per_product["cogs"])

    per_product["variable_cost"] = (
        per_product["production_variable_cost"] + per_product["cost_to_serve"])
    per_product["price_unit"] = per_product["pocket_revenue"] / per_product["volume_units"]
    per_product["variable_cost_unit"] = per_product["variable_cost"] / per_product["volume_units"]
    per_product["contribution"] = per_product["pocket_revenue"] - per_product["variable_cost"]
    per_product["contribution_unit"] = per_product["contribution"] / per_product["volume_units"]
    per_product["contribution_pct"] = (
        per_product["contribution"] / per_product["pocket_revenue"])
    per_product["gross_margin"] = per_product["pocket_revenue"] - per_product["cogs"]
    per_product["gross_margin_pct"] = (
        per_product["gross_margin"] / per_product["pocket_revenue"])
    per_product["markup_pct"] = np.where(
        per_product["cogs"] > 0,
        per_product["gross_margin"] / per_product["cogs"], np.nan)
    per_product["cost_to_serve_pct"] = (
        per_product["cost_to_serve"] / per_product["pocket_revenue"])

    fixed = fixed_costs.copy()
    fixed["month"] = pd.to_datetime(fixed["month"])
    fixed["fiscal_year"] = np.where(
        fixed["month"].dt.month >= 7, fixed["month"].dt.year + 1, fixed["month"].dt.year)
    annual_fixed = float(fixed[fixed["fiscal_year"] == latest_year]["actual_fixed_cost"].sum())

    # Allocated by volume share, and the basis is stated in the column name
    # because every allocation is arbitrary and the argument is always about
    # which arbitrary one was used.
    total_volume = float(per_product["volume_units"].sum())
    per_product["fixed_allocated_by_volume"] = (
        annual_fixed * per_product["volume_units"] / total_volume)
    per_product["operating_profit"] = (
        per_product["contribution"] - per_product["fixed_allocated_by_volume"])
    per_product["operating_margin_pct"] = (
        per_product["operating_profit"] / per_product["pocket_revenue"])

    # Break-even per product, at its own contribution and its allocated fixed.
    break_even_rows = []
    for row in per_product.itertuples():
        result = ue.break_even(
            fixed_costs=row.fixed_allocated_by_volume,
            price=row.price_unit, variable_cost=row.variable_cost_unit,
            actual_quantity=row.volume_units,
        )
        break_even_rows.append(
            {
                "product_id": row.product_id, "description": row.description,
                "category": row.category,
                "exists": result["exists"],
                "break_even_units": result["break_even_units"],
                "break_even_revenue": result["break_even_revenue"],
                "contribution_per_unit": result["contribution_per_unit"],
                "contribution_ratio": result["contribution_ratio"],
                "actual_volume_units": row.volume_units,
                "margin_of_safety": result["margin_of_safety"],
                "operating_profit": result["operating_profit"],
                "note": result["reason"],
            }
        )

    # Book level, and per category, at the mix each currently sells.
    portfolio_rows = []
    for label, frame in [("Whole book", per_product)] + list(
        per_product.groupby("category")
    ):
        share = float(frame["volume_units"].sum()) / total_volume
        rows = [
            {"price": r.price_unit, "variable_cost": r.variable_cost_unit,
             "quantity": r.volume_units}
            for r in frame.itertuples()
        ]
        result = ue.portfolio_break_even(rows, annual_fixed * (1.0 if label == "Whole book"
                                                               else share))
        leverage = ue.operating_leverage(result.get("contribution", 0.0),
                                         result.get("operating_profit", 0.0))
        portfolio_rows.append(
            {
                "scope": label if label == "Whole book" else "Category",
                "member": label,
                "revenue": result.get("revenue", 0.0),
                "variable_cost": result.get("variable_cost", 0.0),
                "contribution": result.get("contribution", 0.0),
                "contribution_ratio": result.get("contribution_ratio", 0.0),
                "fixed_costs": result.get("fixed_costs", 0.0),
                "break_even_revenue": result.get("break_even_revenue", float("nan")),
                "break_even_volume": result.get("break_even_volume", float("nan")),
                "operating_profit": result.get("operating_profit", 0.0),
                "margin_of_safety": result.get("margin_of_safety", float("nan")),
                "operating_leverage": leverage["leverage"],
                "note": result.get("reason", ""),
            }
        )

    # The chart: fixed, variable, total cost and revenue against volume.
    book = portfolio_rows[0]
    price = book["revenue"] / total_volume if total_volume else 0.0
    variable_unit = book["variable_cost"] / total_volume if total_volume else 0.0
    curve = pd.DataFrame(
        ue.break_even_curve(
            fixed_costs=annual_fixed, price=price, variable_cost=variable_unit,
            max_quantity=total_volume * 1.35, steps=46,
        )
    )

    markup_table = pd.DataFrame(
        ue.markup_margin_table([0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50])
    )

    return {
        "unit_economics": per_product.round(4),
        "break_even_products": pd.DataFrame(break_even_rows).round(4),
        "break_even_portfolio": pd.DataFrame(portfolio_rows).round(4),
        "break_even_curve": curve.round(2),
        "markup_vs_margin": markup_table.round(4),
    }


# ---------------------------------------------------------------------------
# 2. Forecasting
# ---------------------------------------------------------------------------

def build_forecast(sales: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """
    Forecast volume, revenue, cost and margin, and prove the method choice.

    The last six months are held out. Everything the comparison sees stops
    before them, so the forecast-versus-actual chart is a genuine out-of-sample
    score rather than a fit redrawn -- which is the difference between a
    forecasting page and a decorative one.
    """
    HOLDOUT = 6
    monthly = (
        sales.groupby("month", as_index=False)
        .agg(volume_units=("quantity_units", "sum"),
             revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"))
        .sort_values("month")
    )
    monthly["margin"] = monthly["revenue"] - monthly["cogs"]

    measures = {"volume_units": "Volume (units)", "revenue": "Pocket revenue",
                "cogs": "Cost of goods", "margin": "Gross margin"}

    accuracy_rows, series_rows, future_rows = [], [], []
    for column, label in measures.items():
        values = monthly[column].tolist()
        train, held = values[:-HOLDOUT], values[-HOLDOUT:]

        for result in fc.compare_methods(train):
            accuracy_rows.append(
                {"measure": label, "method": result["method"],
                 "wape": result["wape"], "mape": result["mape"],
                 "bias": result["bias"], "folds": result["folds"],
                 "rank": result["rank"], "is_best": result["is_best"]}
            )

        chosen = fc.forecast(train, horizon=HOLDOUT)
        predicted = [p["forecast"] for p in chosen["points"]]
        score = fc.accuracy_against_actual(predicted, held)
        accuracy_rows.append(
            {"measure": label, "method": f"{chosen['method']} (held out)",
             "wape": score["wape"], "mape": score["mape"], "bias": score["bias"],
             "folds": HOLDOUT, "rank": 0, "is_best": False}
        )

        months = monthly["month"].tolist()
        for i, month in enumerate(months):
            row = {"measure": label, "month": month, "actual": values[i],
                   "forecast": np.nan, "low": np.nan, "high": np.nan,
                   "period": "History"}
            if i >= len(train):
                point = chosen["points"][i - len(train)]
                row.update({"forecast": point["forecast"], "low": point["low"],
                            "high": point["high"], "period": "Held out"})
            series_rows.append(row)

        ahead = fc.forecast(values, horizon=HOLDOUT)
        last = months[-1]
        for point in ahead["points"]:
            future = (pd.Timestamp(last) + pd.DateOffset(months=point["step"])).date()
            series_rows.append(
                {"measure": label, "month": future, "actual": np.nan,
                 "forecast": point["forecast"], "low": point["low"],
                 "high": point["high"], "period": "Forecast"}
            )
        future_rows.append(
            {"measure": label, "method": ahead["method"],
             "next_6_total": sum(p["forecast"] for p in ahead["points"]),
             "last_6_actual": sum(values[-HOLDOUT:]),
             "backtest_wape": ahead["accuracy"].get("wape", np.nan),
             "holdout_wape": score["wape"]}
        )

    series = pd.DataFrame(series_rows)
    series["month"] = pd.to_datetime(series["month"])
    forward = pd.DataFrame(future_rows)
    forward["change_pct"] = (
        forward["next_6_total"] / forward["last_6_actual"] - 1).round(4)
    return {
        "forecast_series": series.round(
            {c: 2 for c in series.columns if c != "month"}),
        "forecast_accuracy": pd.DataFrame(accuracy_rows).round(5),
        "forecast_summary": forward.round(4),
    }


# ---------------------------------------------------------------------------
# 3. Profitability
# ---------------------------------------------------------------------------

def build_profitability(
    sales: pd.DataFrame, fixed_costs: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """
    Profit by every dimension, down to operating profit after allocated fixed.

    Long form -- one row per (dimension, member) -- so one dashboard visual and
    one slicer serve all nine cuts. The fixed allocation is by revenue share and
    the column says so: every allocation is arbitrary, and the argument is
    always about which arbitrary one was used rather than about the arithmetic.
    """
    latest_year = sales["fiscal_year"].max()
    recent = sales[sales["fiscal_year"] == latest_year].copy()
    fixed = fixed_costs.copy()
    fixed["month"] = pd.to_datetime(fixed["month"])
    fixed["fiscal_year"] = np.where(
        fixed["month"].dt.month >= 7, fixed["month"].dt.year + 1, fixed["month"].dt.year)
    annual_fixed = float(fixed[fixed["fiscal_year"] == latest_year]["actual_fixed_cost"].sum())
    total_revenue = float(recent["pocket_revenue"].sum())

    frames = []
    for dimension in PROFIT_CUTS:
        if dimension not in recent.columns:
            continue
        grouped = (
            recent.groupby(dimension, as_index=False)
            .agg(volume_units=("quantity_units", "sum"),
                 list_value=("list_value", "sum"),
                 invoice_revenue=("revenue", "sum"),
                 pocket_revenue=("pocket_revenue", "sum"),
                 cogs=("cogs", "sum"),
                 lines=("product_id", "count"),
                 products=("product_id", "nunique"),
                 customers=("customer_id", "nunique"))
        )
        grouped["cost_to_serve"] = (
            recent.groupby(dimension)
            .apply(lambda g: float((g["cost_to_serve"] * g["quantity_units"]).sum()),
                   include_groups=False)
            .reindex(grouped[dimension]).to_numpy()
        )
        grouped = grouped.rename(columns={dimension: "member"})
        grouped.insert(0, "dimension", dimension)
        frames.append(grouped)

    profit = pd.concat(frames, ignore_index=True)
    profit["gross_margin"] = profit["pocket_revenue"] - profit["cogs"]
    profit["gross_margin_pct"] = profit["gross_margin"] / profit["pocket_revenue"]
    profit["contribution"] = profit["gross_margin"]
    profit["fixed_allocated_by_revenue"] = (
        annual_fixed * profit["pocket_revenue"] / total_revenue)
    profit["operating_profit"] = profit["contribution"] - profit["fixed_allocated_by_revenue"]
    profit["operating_margin_pct"] = profit["operating_profit"] / profit["pocket_revenue"]
    profit["leakage_pct"] = 1 - profit["pocket_revenue"] / profit["list_value"]
    profit["price_unit"] = profit["pocket_revenue"] / profit["volume_units"]
    profit["revenue_share"] = profit["pocket_revenue"] / total_revenue
    profit["avg_drop_units"] = profit["volume_units"] / profit["lines"]

    # A two-dimensional grid for the heatmap: segment against category, which
    # is the cut that most often hides a loss inside two healthy totals.
    heat = (
        recent.groupby(["segment", "category"], as_index=False)
        .agg(pocket_revenue=("pocket_revenue", "sum"), cogs=("cogs", "sum"),
             volume_units=("quantity_units", "sum"), list_value=("list_value", "sum"))
    )
    heat["gross_margin"] = heat["pocket_revenue"] - heat["cogs"]
    heat["gross_margin_pct"] = heat["gross_margin"] / heat["pocket_revenue"]
    heat["leakage_pct"] = 1 - heat["pocket_revenue"] / heat["list_value"]

    # Discount depth against realised margin, as a matrix. The cell that matters
    # is deep discount and thin margin, and it is invisible in either average.
    lines = recent.copy()
    lines["discount_depth"] = 1 - lines["pocket_price"] / lines["list_price"]
    lines["line_margin_pct"] = np.where(
        lines["pocket_revenue"] > 0,
        (lines["pocket_revenue"] - lines["cogs"]) / lines["pocket_revenue"], -9.0)
    lines["discount_band"] = lines["discount_depth"].map(
        lambda v: _band(v, DISCOUNT_BANDS))
    lines["margin_band"] = lines["line_margin_pct"].map(lambda v: _band(v, MARGIN_BANDS))
    matrix = (
        lines.groupby(["discount_band", "margin_band"], as_index=False)
        .agg(lines=("product_id", "count"), volume_units=("quantity_units", "sum"),
             pocket_revenue=("pocket_revenue", "sum"), cogs=("cogs", "sum"))
    )
    matrix["gross_margin"] = matrix["pocket_revenue"] - matrix["cogs"]
    matrix["share_of_revenue"] = matrix["pocket_revenue"] / total_revenue

    # Price against units, per product, for the demand scatter.
    scatter = (
        sales.groupby(["product_id", "description", "category", "month"], as_index=False)
        .agg(volume_units=("quantity_units", "sum"), list_price=("list_price", "first"),
             pocket_revenue=("pocket_revenue", "sum"),
             on_promotion=("on_promotion", "max"))
    )
    scatter["pocket_price"] = scatter["pocket_revenue"] / scatter["volume_units"]
    scatter["promoted"] = np.where(scatter["on_promotion"] == 1, "Promoted", "Base")

    return {
        "profitability": profit.round(4),
        "profit_heatmap": heat.round(4),
        "discount_margin_matrix": matrix.round(4),
        "price_vs_volume": scatter.round(
            {c: 4 for c in scatter.columns if c != "month"}),
    }


# ---------------------------------------------------------------------------
# 4. Promotions and price-list operations
# ---------------------------------------------------------------------------

def build_operations(
    promotions: pd.DataFrame, price_changes: pd.DataFrame, panel: pd.DataFrame
) -> dict[str, pd.DataFrame]:
    """Promotion payback, and how the price list is actually being maintained."""
    promo = promotions.copy()
    promo["month"] = pd.to_datetime(promo["month"])
    promo_summary = (
        promo.groupby("mechanic", as_index=False)
        .agg(promotions=("promo_id", "count"),
             volume_units=("volume_units", "sum"),
             incremental_volume_units=("incremental_volume_units", "sum"),
             incremental_margin=("incremental_margin", "sum"),
             discount_on_baseline=("discount_on_baseline", "sum"),
             net_promo_margin=("net_promo_margin", "sum"))
        .sort_values("net_promo_margin", ascending=False)
    )
    promo_summary["roi"] = (
        promo_summary["net_promo_margin"] / promo_summary["discount_on_baseline"])
    promo_summary["paid"] = np.where(
        promo_summary["net_promo_margin"] > 0, "Paid for itself", "Bought its own volume")

    changes = price_changes.copy()
    changes["effective_month"] = pd.to_datetime(changes["effective_month"])
    change_summary = (
        changes.groupby(["reason", "direction"], as_index=False)
        .agg(changes=("change_id", "count"),
             median_pct=("pct_change", "median"),
             median_days=("days_to_approve", "median"))
    )
    approval = (
        changes.groupby("approval_state", as_index=False)
        .agg(changes=("change_id", "count"),
             median_days=("days_to_approve", "median"),
             median_pct=("pct_change", "median"))
    )

    # How much of the book has not been touched, which is the finding that
    # pays for this whole table: an unmanaged item is the cheapest margin
    # available precisely because nobody has been near it.
    panel = panel.copy()
    panel["month"] = pd.to_datetime(panel["month"])
    latest = panel["month"].max()
    last_change = changes.groupby("product_id")["effective_month"].max()
    coverage = (
        panel[panel["month"] == latest][["product_id", "review_cadence",
                                         "list_price_unit", "actual_final_cost_unit"]]
        .copy()
    )
    coverage = coverage.merge(
        last_change.rename("last_change"), left_on="product_id", right_index=True, how="left")
    coverage["days_since_change"] = (latest - coverage["last_change"]).dt.days
    coverage["days_since_change"] = coverage["days_since_change"].fillna(
        (latest - panel["month"].min()).days)
    coverage["margin_pct"] = (
        1 - coverage["actual_final_cost_unit"] / coverage["list_price_unit"])
    coverage["stale"] = coverage["days_since_change"] > 300

    cadence = (
        coverage.groupby("review_cadence", as_index=False)
        .agg(products=("product_id", "count"),
             median_days_since_change=("days_since_change", "median"),
             median_margin_pct=("margin_pct", "median"),
             stale=("stale", "sum"))
    )

    return {
        "promotion_analysis": promo.round(
            {c: 4 for c in promo.columns if c != "month"}),
        "promotion_summary": promo_summary.round(4),
        "price_change_log": changes,
        "price_change_summary": change_summary.round(5),
        "price_change_approvals": approval.round(5),
        "price_list_coverage": cadence.round(4),
    }


# ---------------------------------------------------------------------------
# 5. Cost elements
# ---------------------------------------------------------------------------

def build_cost_element_analysis(
    cost_elements: pd.DataFrame, fixed_costs: pd.DataFrame, products: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """
    Material, labour and overhead against standard, as a waterfall.

    Elements rather than variance *types*: "purchase price variance" tells a
    plant manager nothing, and "material is nine points over standard, labour is
    on it, overhead is under-absorbed" tells three different people what to do.
    """
    elements = cost_elements.copy()
    elements["month"] = pd.to_datetime(elements["month"])
    elements = elements.merge(
        products[["product_id", "category", "brand_tier"]], on="product_id", how="left")
    elements["fiscal_year"] = np.where(
        elements["month"].dt.month >= 7,
        elements["month"].dt.year + 1, elements["month"].dt.year)

    by_element = (
        elements.groupby(["fiscal_year", "cost_element", "behaviour"], as_index=False)
        .agg(standard_cost=("standard_cost", "sum"), actual_cost=("actual_cost", "sum"),
             volume_units=("volume_units", "sum"))
    )

    fixed = fixed_costs.copy()
    fixed["month"] = pd.to_datetime(fixed["month"])
    fixed["fiscal_year"] = np.where(
        fixed["month"].dt.month >= 7, fixed["month"].dt.year + 1, fixed["month"].dt.year)
    fixed_by_element = (
        fixed.groupby(["fiscal_year", "cost_element", "behaviour"], as_index=False)
        .agg(standard_cost=("budgeted_fixed_cost", "sum"),
             actual_cost=("actual_fixed_cost", "sum"))
    )
    fixed_by_element["volume_units"] = 0.0

    combined = pd.concat([by_element, fixed_by_element], ignore_index=True)
    combined["variance"] = combined["actual_cost"] - combined["standard_cost"]
    combined["variance_pct"] = combined["variance"] / combined["standard_cost"]
    combined["verdict"] = np.where(
        combined["variance"] > 0, "Unfavourable", "Favourable")

    latest = combined["fiscal_year"].max()
    current = combined[combined["fiscal_year"] == latest].sort_values(
        "variance", ascending=False)
    opening = float(current["standard_cost"].sum())
    steps = [{"step": "Standard cost", "kind": "total",
              "amount": opening, "running": opening, "sort_order": 0}]
    running = opening
    for i, row in enumerate(current.itertuples(), start=1):
        running += float(row.variance)
        steps.append({"step": row.cost_element, "kind":
                      "increase" if row.variance >= 0 else "decrease",
                      "amount": float(row.variance), "running": running,
                      "sort_order": i})
    closing = float(current["actual_cost"].sum())
    steps.append({"step": "Actual cost", "kind": "total",
                  "amount": closing, "running": closing,
                  "sort_order": len(current) + 1})
    steps = waterfall.add_deltas(steps)

    by_category = (
        elements[elements["fiscal_year"] == latest]
        .groupby(["category", "cost_element"], as_index=False)
        .agg(standard_cost=("standard_cost", "sum"), actual_cost=("actual_cost", "sum"))
    )
    by_category["variance"] = by_category["actual_cost"] - by_category["standard_cost"]

    return {
        "cost_element_summary": combined.round(2),
        "cost_element_waterfall": pd.DataFrame(steps).round(2),
        "cost_element_by_category": by_category.round(2),
    }


# ---------------------------------------------------------------------------
# 6. Scenarios
# ---------------------------------------------------------------------------

def build_scenarios(
    unit_economics: pd.DataFrame, fixed_costs: pd.DataFrame, elasticity: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Best/base/worst, a tornado, and a two-way sensitivity grid on the book."""
    volume = float(unit_economics["volume_units"].sum())
    revenue = float(unit_economics["pocket_revenue"].sum())
    variable = float(unit_economics["variable_cost"].sum())
    list_value = float(unit_economics["list_value"].sum())
    annual_fixed = float(unit_economics["fixed_allocated_by_volume"].sum())

    usable = elasticity[(elasticity["scope"] == "Category") & elasticity["usable"]]
    book_elasticity = float(usable["elasticity"].mean()) if len(usable) else -1.5

    base = {
        "base_price": list_value / volume,
        "base_volume": volume,
        "base_unit_cost": variable / volume,
        "base_discount": 1 - revenue / list_value,
        "fixed_costs": annual_fixed,
        "elasticity": book_elasticity,
    }
    ranges = {
        "price_change": (-0.04, 0.04),
        "unit_cost_change": (-0.05, 0.12),
        "volume_change": (-0.10, 0.07),
        "discount_change": (-0.02, 0.03),
        "fixed_cost_change": (-0.03, 0.08),
    }

    three = pd.DataFrame(sc.three_point(base, ranges))
    torn = pd.DataFrame(sc.tornado(base, ranges))
    grid = pd.DataFrame(
        sc.sensitivity_grid(
            base,
            x_input="price_change",
            x_values=[round(-0.06 + 0.01 * i, 3) for i in range(13)],
            y_input="unit_cost_change",
            y_values=[round(-0.06 + 0.02 * i, 3) for i in range(10)],
        )
    )
    thresholds = pd.DataFrame([
        sc.break_even_input(base, input_name=name)
        for name in ("unit_cost_change", "volume_change", "discount_change",
                     "price_change", "fixed_cost_change")
    ])
    assumptions = pd.DataFrame([
        {"input": "Base list price per unit", "value": base["base_price"]},
        {"input": "Base volume (units)", "value": base["base_volume"]},
        {"input": "Base variable cost per unit", "value": base["base_unit_cost"]},
        {"input": "Average discount off list", "value": base["base_discount"]},
        {"input": "Annual fixed cost", "value": base["fixed_costs"]},
        {"input": "Book elasticity", "value": base["elasticity"]},
    ])
    return {
        "scenario_three_point": three.round(4),
        "scenario_tornado": torn.round(4),
        "scenario_grid": grid.round(4),
        "scenario_thresholds": thresholds.round(5),
        "scenario_assumptions": assumptions.round(4),
    }


# ---------------------------------------------------------------------------
# 7. Recommendations
# ---------------------------------------------------------------------------

def build_recommendations(
    sales: pd.DataFrame, unit_economics: pd.DataFrame, competitive_index: pd.DataFrame,
    elasticity: pd.DataFrame, deal_scores: pd.DataFrame, panel: pd.DataFrame,
    products: pd.DataFrame, bundles: pd.DataFrame,
    co_purchase: pd.DataFrame | None = None,
    material_master: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """
    One recommended action per product, with the reason and what it is worth.

    Everything here is assembled from tables already computed; the decision
    layer itself lives in :mod:`pricing.recommend` and is rule-based on purpose.
    A scoring model would rank better and be unusable -- the person defending
    the increase to the customer needs to know it was recommended because the
    item is nine points under the market and has not been repriced in fourteen
    months, not because it scored 0.83.
    """
    latest_month = competitive_index["month"].max()
    index_latest = competitive_index[competitive_index["month"] == latest_month][
        ["product_id", "price_index", "market_price", "competitors_seen"]]

    # A product's own fit is preferred only when it is *good*, not merely
    # usable. Thirty-six monthly observations against a price that moves four
    # times a year produce plenty of fits that clear the usability bar and
    # still carry an r-squared of 0.02 -- and pricing off one of those is
    # pricing off noise. The elasticity page says to price the category and
    # sanity-check the product; this is that rule, applied.
    product_elasticity = (
        elasticity[elasticity["scope"] == "Product"][
            ["member", "elasticity", "usable", "r_squared"]]
        .rename(columns={"member": "product_id", "elasticity": "product_elasticity",
                         "usable": "product_usable", "r_squared": "product_r_squared"})
    )
    product_elasticity["product_usable"] = (
        product_elasticity["product_usable"]
        & (product_elasticity["product_r_squared"] >= MIN_PRODUCT_FIT)
    )
    category_elasticity = (
        elasticity[elasticity["scope"] == "Category"][["member", "elasticity", "usable"]]
        .rename(columns={"member": "category", "elasticity": "category_elasticity",
                         "usable": "category_usable"})
    )

    panel = panel.copy()
    panel["month"] = pd.to_datetime(panel["month"])
    panel = panel.sort_values(["product_id", "month"])
    panel["changed"] = panel.groupby("product_id")["list_price_unit"].diff().abs() > 0.0005
    last_change = panel[panel["changed"]].groupby("product_id")["month"].max()
    cost_at_change = (
        panel[panel["changed"]].sort_values("month")
        .groupby("product_id")["actual_final_cost_unit"].last())

    months_of_history = sales.groupby("product_id")["month"].nunique()
    attachment = (
        co_purchase.set_index("product_id")["co_purchase_rate"]
        if co_purchase is not None and len(co_purchase)
        else pd.Series(dtype=float)
    )

    frame = unit_economics.merge(index_latest, on="product_id", how="left")
    frame = frame.merge(product_elasticity, on="product_id", how="left")
    frame = frame.merge(category_elasticity, on="category", how="left")
    frame = frame.merge(
        products[["product_id", "target_margin"]], on="product_id", how="left")
    frame["last_change"] = frame["product_id"].map(last_change)
    frame["cost_at_change"] = frame["product_id"].map(cost_at_change)
    frame["months_of_history"] = frame["product_id"].map(months_of_history).fillna(0)
    frame["co_purchase_rate"] = frame["product_id"].map(attachment).fillna(0.0)

    reference = pd.to_datetime(panel["month"].max())
    frame["days_since_price_change"] = (
        (reference - pd.to_datetime(frame["last_change"])).dt.days).fillna(999)
    frame["cost_change_pct"] = np.where(
        frame["cost_at_change"].notna() & (frame["cost_at_change"] > 0),
        frame["cogs"] / frame["volume_units"] / frame["cost_at_change"] - 1, 0.0)
    volume_floor = frame["volume_units"].quantile(0.15)

    # The data-quality finding has to reach the recommendation, or the two are
    # separate exercises that happen to sit in the same repo. An item whose
    # material master carries no standard cost cannot be priced at all, and the
    # recommendation for it is "fix the master data", not "raise it four points"
    # -- which is what every margin-driven rule below would otherwise say,
    # because a margin measured against a zero cost looks wonderful.
    broken_cost: set[str] = set()
    if material_master is not None and len(material_master):
        broken = material_master[
            material_master["standard_cost"].isna()
            | (material_master["standard_cost"] <= 0)
            | (material_master["recovery"] <= 0.30)
            | (material_master["recovery"] > 1.0)
        ]
        broken_cost = set(broken["material"].astype(str))

    rows = []
    for row in frame.itertuples():
        usable = bool(row.product_usable) if pd.notna(row.product_usable) else False
        elasticity_value = (
            row.product_elasticity if usable
            else (row.category_elasticity if pd.notna(row.category_elasticity) else 0.0)
        )
        rows.append(
            {
                "product_id": row.product_id,
                "description": row.description,
                "category": row.category,
                "current_price": row.price_unit,
                "unit_cost": row.cogs / row.volume_units if row.volume_units else 0.0,
                "volume_units": row.volume_units,
                "margin_pct": row.gross_margin_pct,
                "target_margin": row.target_margin,
                "floor_margin": max(0.05, (row.target_margin or 0.22) - 0.09),
                "price_index": row.price_index,
                "market_price": row.market_price,
                "competitors_seen": row.competitors_seen,
                "days_since_price_change": row.days_since_price_change,
                "cost_change_pct": row.cost_change_pct,
                "elasticity": elasticity_value,
                "elasticity_usable": usable or bool(row.category_usable),
                "months_of_history": row.months_of_history,
                "is_small_volume": row.volume_units <= volume_floor,
                "co_purchase_rate": row.co_purchase_rate,
                "cost_is_missing": row.cogs <= 0 or str(row.product_id) in broken_cost,
            }
        )

    recommendations = pd.DataFrame(rec.recommend_book(rows))
    summary = pd.DataFrame(rec.summarise(recommendations.to_dict("records")))
    note = pd.DataFrame([{
        "note": rec.executive_note(recommendations.to_dict("records")),
        "products": len(recommendations),
        "margin_delta": float(recommendations["margin_delta"].sum()),
        "revenue_delta": float(recommendations["revenue_delta"].sum()),
    }])
    return {
        "recommendations": recommendations.round(4),
        "recommendation_summary": summary.round(2),
        "recommendation_note": note.round(2),
    }
