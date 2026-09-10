"""
What the semantic model contains: tables, relationships and measures.

Kept apart from the writer in :mod:`powerbi.build_pbip` so the *shape* of the
model can be read, reviewed and asserted on without wading through TMDL
serialisation. The tests import this module directly.

Nothing here duplicates a calculation. Every measure either aggregates a column
the Python engine already computed, or divides two such aggregates -- a DAX
expression that re-derives "pocket margin" from list price and seven deduction
columns is a second implementation of a definition that already has tests, and
the two drift the moment either is edited.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Tables. `source` is the directory the CSV lives in, relative to DataPath.
# --------------------------------------------------------------------------

TABLES: dict[str, dict] = {
    # Dimensions
    "dim_product": {"source": "data", "kind": "dimension"},
    "dim_customer": {"source": "data", "kind": "dimension"},
    "dim_competitor": {"source": "data", "kind": "dimension"},
    # The month dimension is deliberately NOT marked as a date table. Power BI
    # requires a contiguous *daily* date column for that, and every fact here is
    # monthly, so the marking would be rejected -- or worse, accepted against a
    # column with 30-day gaps and quietly wrong. Period-over-period comparisons
    # subtract `month_index`, which is contiguous by construction.
    "dim_month": {"source": "data", "kind": "dimension"},

    # The transaction fact
    "fact_sales": {"source": "data", "kind": "fact"},

    # Analysis tables the Python engine writes
    "price_waterfall": {"source": "output", "kind": "analysis"},
    "leakage_by_dimension": {"source": "output", "kind": "analysis"},
    "elasticity_estimates": {"source": "output", "kind": "analysis"},
    "price_change_hurdles": {"source": "output", "kind": "analysis"},
    "price_response_curve": {"source": "output", "kind": "analysis"},
    "competitive_index": {"source": "output", "kind": "analysis"},
    "passthrough": {"source": "output", "kind": "analysis"},
    "cost_variance_long": {"source": "output", "kind": "analysis"},
    "budget_variance": {"source": "output", "kind": "analysis"},
    "overhead_variance": {"source": "output", "kind": "analysis"},
    "margin_bridge_effects": {"source": "output", "kind": "analysis"},
    "price_bands": {"source": "output", "kind": "analysis"},
    "segment_profile": {"source": "output", "kind": "analysis"},
    "wtp_curve": {"source": "output", "kind": "analysis"},
    "wtp_fits": {"source": "output", "kind": "analysis"},
    "customer_profitability": {"source": "output", "kind": "analysis"},
    "deal_scores": {"source": "output", "kind": "analysis"},
    "guardrail_summary": {"source": "output", "kind": "analysis"},
    "bundle_candidates": {"source": "output", "kind": "analysis"},

    # Unit economics and break-even
    "unit_economics": {"source": "output", "kind": "analysis"},
    "break_even_products": {"source": "output", "kind": "analysis"},
    "break_even_portfolio": {"source": "output", "kind": "analysis"},
    "break_even_curve": {"source": "output", "kind": "analysis"},
    "markup_vs_margin": {"source": "output", "kind": "analysis"},

    # Scenario modelling
    "scenario_three_point": {"source": "output", "kind": "analysis"},
    "scenario_tornado": {"source": "output", "kind": "analysis"},
    "scenario_thresholds": {"source": "output", "kind": "analysis"},

    # Forecasting
    "forecast_series": {"source": "output", "kind": "analysis"},
    "forecast_accuracy": {"source": "output", "kind": "analysis"},
    "forecast_summary": {"source": "output", "kind": "analysis"},

    # Profitability and segmentation
    "profitability": {"source": "output", "kind": "analysis"},
    "profit_heatmap": {"source": "output", "kind": "analysis"},

    # Discount and promotion
    "discount_margin_matrix": {"source": "output", "kind": "analysis"},
    "promotion_summary": {"source": "output", "kind": "analysis"},
    "price_vs_volume": {"source": "output", "kind": "analysis"},

    # Cost elements
    "cost_element_summary": {"source": "output", "kind": "analysis"},
    "cost_element_waterfall": {"source": "output", "kind": "analysis"},
    "cost_element_by_category": {"source": "output", "kind": "analysis"},

    # Pricing operations
    "price_change_log": {"source": "output", "kind": "analysis"},
    "price_list_coverage": {"source": "output", "kind": "analysis"},

    # Data quality and reconciliation
    "data_quality_checks": {"source": "output", "kind": "analysis"},
    "data_quality_score": {"source": "output", "kind": "analysis"},
    "reconciliation": {"source": "output", "kind": "analysis"},

    # Recommendations and exceptions
    "recommendations": {"source": "output", "kind": "analysis"},
    "recommendation_summary": {"source": "output", "kind": "analysis"},
    "guardrail_exceptions": {"source": "output", "kind": "analysis"},
}

# --------------------------------------------------------------------------
# Relationships. All single-direction many-to-one, which is the only kind that
# cannot create an ambiguous filter path in a star.
# --------------------------------------------------------------------------

RELATIONSHIPS: list[tuple[str, str, str, str]] = [
    ("fact_sales", "product_id", "dim_product", "product_id"),
    ("fact_sales", "customer_id", "dim_customer", "customer_id"),
    ("fact_sales", "month", "dim_month", "month"),
    ("competitive_index", "product_id", "dim_product", "product_id"),
    ("competitive_index", "month", "dim_month", "month"),
    ("cost_variance_long", "product_id", "dim_product", "product_id"),
    ("cost_variance_long", "month", "dim_month", "month"),
    ("deal_scores", "product_id", "dim_product", "product_id"),
    ("deal_scores", "customer_id", "dim_customer", "customer_id"),
    ("price_bands", "product_id", "dim_product", "product_id"),
    ("customer_profitability", "customer_id", "dim_customer", "customer_id"),
    ("budget_variance", "month", "dim_month", "month"),
    ("overhead_variance", "month", "dim_month", "month"),
    ("unit_economics", "product_id", "dim_product", "product_id"),
    ("break_even_products", "product_id", "dim_product", "product_id"),
    ("price_vs_volume", "product_id", "dim_product", "product_id"),
    ("price_vs_volume", "month", "dim_month", "month"),
    ("price_change_log", "product_id", "dim_product", "product_id"),
    ("price_change_log", "effective_month", "dim_month", "month"),
    ("recommendations", "product_id", "dim_product", "product_id"),
    ("guardrail_exceptions", "product_id", "dim_product", "product_id"),
    ("guardrail_exceptions", "customer_id", "dim_customer", "customer_id"),
]

# Tables intentionally left unrelated, with the reason. A disconnected table is
# usually a modelling mistake; these are not, and saying so here stops the next
# person "fixing" them.
#
# Two tables the app uses are deliberately *not* in this model at all:
# `executive_summary` and `waterfall_monthly`. Both are pre-aggregated, and a
# pre-aggregated row does not respond to a slicer -- put one beside a card that
# does and the page carries two numbers for one thing, differing by whatever
# the reader last clicked. Every card here reads a measure over the fact
# instead, which is the same definition and filters correctly.
UNRELATED: dict[str, str] = {
    "price_waterfall": "One row per waterfall step at the book total. Slicing it "
                       "by product would be meaningless -- the steps are already "
                       "aggregates.",
    "leakage_by_dimension": "Long form: one row per (dimension, member, deduction). "
                            "Its own `dimension` column is the slicer.",
    "elasticity_estimates": "Keyed on `member`, which is a category on some rows and "
                            "a product code on others. Relating it would silently "
                            "join the category rows to nothing.",
    "price_change_hurdles": "A what-if grid, not an observation of the business.",
    "price_response_curve": "Keyed on category, which is not unique in dim_product -- "
                            "a relationship on it would be many-to-many and would need "
                            "a category dimension that nothing else asks for.",
    "passthrough": "One row per commodity index; the index is not a dimension "
                   "anything else is sliced by.",
    "margin_bridge_effects": "One row per effect per year-pair comparison.",
    "segment_profile": "Long form across six different dimensions at once.",
    "wtp_curve": "Fitted and observed win rates by price ratio, not by product.",
    "wtp_fits": "One row per segment plus an 'All' row that no dimension contains.",
    "guardrail_summary": "One row per guardrail rule.",
    "bundle_candidates": "A pair of products per row; a single-column relationship "
                         "cannot express that.",
    "dim_competitor": "Competitor observations are summarised into "
                      "`competitive_index` before the model sees them, so there is "
                      "no fact at competitor grain to relate to.",

    "break_even_portfolio": "One row per scope: the whole book, then each category. "
                            "Its own `member` column is the slicer.",
    "break_even_curve": "A volume grid, not an observation of the business. Every row "
                        "is a quantity nobody sold.",
    "markup_vs_margin": "A conversion table. It exists to settle the argument about "
                        "which of the two a rate is, and has no business keys.",
    "scenario_three_point": "Worst, base and best case for the whole book. Slicing it "
                            "by product would report a scenario nobody modelled.",
    "scenario_tornado": "One row per scenario input, ranked by how far it moves "
                        "operating profit.",
    "scenario_thresholds": "One row per input: the value at which operating profit "
                           "crosses zero, where it crosses at all.",
    "forecast_series": "Runs six months past the last actual, so `month` deliberately "
                       "reaches beyond dim_month. Relating it would drop exactly the "
                       "rows the page is about.",
    "forecast_accuracy": "One row per (measure, method) from the rolling-origin "
                         "backtest. A forecasting method is not a business dimension.",
    "forecast_summary": "One row per forecast measure.",
    "profitability": "Long form: one row per (dimension, member) across product, "
                     "customer, region, channel, segment and salesperson at once. Its "
                     "own `dimension` column is the slicer.",
    "profit_heatmap": "A segment-by-category grid. Neither key is unique in a "
                      "dimension table, so a relationship on either would be "
                      "many-to-many.",
    "discount_margin_matrix": "A banded cross-tab. `discount_band` and `margin_band` "
                              "are cuts of the fact, not keys into it.",
    "promotion_summary": "One row per promotional mechanic.",
    "cost_element_summary": "One row per cost element per fiscal year.",
    "cost_element_waterfall": "One row per waterfall step, already aggregated.",
    "cost_element_by_category": "Keyed on category, which is not unique in dim_product.",
    "price_list_coverage": "One row per review cadence: how much of the book each one "
                           "covers, and how stale it has gone.",
    "data_quality_checks": "One row per rule. The rules run against the staged ERP "
                           "extract, which is upstream of every dimension here.",
    "data_quality_score": "One row per quality dimension plus an Overall row that no "
                          "business dimension contains.",
    "reconciliation": "One row per reconciling line, from extract total down to "
                      "unexplained. Slicing a reconciliation stops it balancing.",
    "recommendation_summary": "One row per recommended action.",
}


# --------------------------------------------------------------------------
# What-if parameters. (table, column, min, max, step, format, measure)
#
# These are calculated tables rather than CSVs -- GENERATESERIES over a range,
# read back by one SELECTEDVALUE measure each. They are deliberately unrelated
# to everything: a parameter table joined to a fact would filter that fact to
# the rows matching the parameter value, which is the exact opposite of what a
# what-if is for, and it fails quietly by showing a smaller number rather than
# an error.
#
# Table names are bare words so DAX referring to them needs no quoting. Column
# names carry the units, because the column name is what the slicer shows.
# --------------------------------------------------------------------------

WHATIF_PARAMETERS: tuple[tuple[str, str, float, float, float, str, str], ...] = (
    ("PriceChange", "Price change %", -0.15, 0.15, 0.01, "0.0%", "Price change value"),
    ("CostChange", "Cost change %", -0.15, 0.25, 0.01, "0.0%", "Cost change value"),
    ("VolumeChange", "Volume change %", -0.25, 0.25, 0.01, "0.0%", "Volume change value"),
    ("DiscountChange", "Discount change pts", -0.05, 0.10, 0.005, "0.0%",
     "Discount change value"),
)


# --------------------------------------------------------------------------
# Field parameters. (table, column, ((label, measure), ...))
#
# A field parameter is a calculated table of NAMEOF() references. Dropped into
# a visual's value well it does not show text -- Power BI substitutes whichever
# measure the slicer has landed on, so one chart answers four questions and the
# report does not carry four charts that differ by one field.
#
# It is a calculated table like the what-ifs, and disconnected for the same
# reason. What makes it a *parameter* rather than a table of strings is one
# extended property on the hidden Fields column; without it the visual renders
# the measure names as a category axis, which looks like a chart until you read
# it. `build_pbip.field_parameter_tmdl` writes it, and a test asserts it.
# --------------------------------------------------------------------------

FIELD_PARAMETERS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("ProfitMetric", "Profit metric", (
        ("Pocket revenue", "Profitability revenue"),
        ("Margin $", "Profitability margin $"),
        ("Margin %", "Profitability margin %"),
        ("Operating margin %", "Profitability operating margin %"),
        ("Leakage %", "Profitability leakage %"),
    )),
    ("CostMetric", "Cost metric", (
        ("Standard cost", "Standard cost"),
        ("Actual cost", "Actual cost"),
        ("Variance", "Cost element variance"),
        ("Variance %", "Cost element variance %"),
    )),
)


def field_parameter_columns() -> dict[str, set[str]]:
    """
    ``{table: {column}}`` for the field-parameter tables.

    Three columns each: the label the slicer shows, the hidden Fields column a
    visual binds, and the hidden Order column the label sorts by. Without that
    third one the slicer lists the metrics alphabetically, which puts "Margin %"
    above "Pocket revenue" and reads as an accident.
    """
    return {
        table: {column, f"{column} Fields", f"{column} Order"}
        for table, column, _ in FIELD_PARAMETERS
    }


def whatif_columns() -> dict[str, set[str]]:
    """
    ``{table: {column}}`` for the parameter tables.

    Same shape the CSV-backed tables are described in, so a caller checking
    that a field exists can treat both kinds the same way.
    """
    return {table: {column} for table, column, *_ in WHATIF_PARAMETERS}

# --------------------------------------------------------------------------
# Measures. (name, DAX, format string, folder)
#
# Every VAR is prefixed `v`. Power BI reserves far more words for VAR names than
# the four that are documented -- Goal, Status, Trend, Variance are the known
# ones, and Move and Scope fail identically with no published list. A measure
# that trips one gets `state: SemanticError`, every dependent gets
# `DependencyError`, and the bound visual renders "Something's wrong with one or
# more fields" at runtime on a report that built and validated cleanly. The
# prefix sidesteps the entire question and a test enforces it.
# --------------------------------------------------------------------------

MEASURES: list[tuple[str, str, str, str]] = [
    # --- Revenue and margin ------------------------------------------------
    ("List value", "SUM(fact_sales[list_value])", "\\$#,0", "01 Revenue"),
    ("Invoice revenue", "SUM(fact_sales[revenue])", "\\$#,0", "01 Revenue"),
    ("Pocket revenue", "SUM(fact_sales[pocket_revenue])", "\\$#,0", "01 Revenue"),
    ("COGS", "SUM(fact_sales[cogs])", "\\$#,0", "01 Revenue"),
    ("Volume (units)", "SUM(fact_sales[quantity_units])", "#,0", "01 Revenue"),
    ("Invoice lines", "COUNTROWS(fact_sales)", "#,0", "01 Revenue"),
    ("Pocket margin $", "[Pocket revenue] - [COGS]", "\\$#,0", "01 Revenue"),
    ("Pocket margin %", "DIVIDE([Pocket margin $], [Pocket revenue])", "0.0%", "01 Revenue"),
    ("Invoice margin %",
     "DIVIDE([Invoice revenue] - [COGS], [Invoice revenue])", "0.0%", "01 Revenue"),

    # --- The waterfall -----------------------------------------------------
    ("Leakage $", "[List value] - [Pocket revenue]", "\\$#,0", "02 Waterfall"),
    ("Leakage %", "DIVIDE([Leakage $], [List value])", "0.0%", "02 Waterfall"),
    ("On-invoice discounts",
     "SUMX(fact_sales, fact_sales[on_invoice_discounts] * fact_sales[quantity_units])",
     "\\$#,0", "02 Waterfall"),
    ("Off-invoice deductions",
     "SUMX(fact_sales, fact_sales[off_invoice_deductions] * fact_sales[quantity_units])",
     "\\$#,0", "02 Waterfall"),
    ("Cost to serve",
     "SUMX(fact_sales, fact_sales[cost_to_serve] * fact_sales[quantity_units])",
     "\\$#,0", "02 Waterfall"),
    # `amount` is what a subtotal is worth; `delta` is what it moves the running
    # total by, which is zero. A waterfall chart plots each bar as a step, so a
    # subtotal bound to `amount` is added on top of the total it summarises and
    # the chart closes at about twice the real figure -- drawn, rescaled, and
    # wrong without an error anywhere.
    ("Waterfall amount", "SUM(price_waterfall[amount])", "\\$#,0", "02 Waterfall"),
    ("Waterfall step", "SUM(price_waterfall[delta])", "\\$#,0", "02 Waterfall"),
    ("Deduction amount", "SUM(leakage_by_dimension[amount])", "\\$#,0", "02 Waterfall"),
    ("Deduction % of list", "SUM(leakage_by_dimension[pct_of_list])", "0.00%", "02 Waterfall"),

    # --- Realised price ----------------------------------------------------
    ("List price per unit", "DIVIDE([List value], [Volume (units)])", "\\$#,0.00", "03 Price"),
    ("Pocket price per unit", "DIVIDE([Pocket revenue], [Volume (units)])",
     "\\$#,0.00", "03 Price"),
    ("Unit cost", "DIVIDE([COGS], [Volume (units)])", "\\$#,0.00", "03 Price"),

    # --- Period comparison, on month_index rather than a date table --------
    ("Pocket revenue LY",
     "VAR vShift = 12\n"
     "RETURN CALCULATE(\n"
     "    [Pocket revenue],\n"
     "    ALL(dim_month),\n"
     "    TREATAS(\n"
     "        SELECTCOLUMNS(VALUES(dim_month[month_index]),\n"
     "                      \"month_index\", dim_month[month_index] - vShift),\n"
     "        dim_month[month_index]\n"
     "    )\n"
     ")",
     "\\$#,0", "04 Comparison"),
    ("Pocket revenue YoY %",
     "DIVIDE([Pocket revenue] - [Pocket revenue LY], [Pocket revenue LY])",
     "0.0%", "04 Comparison"),
    ("Pocket margin $ LY",
     "VAR vShift = 12\n"
     "RETURN CALCULATE(\n"
     "    [Pocket margin $],\n"
     "    ALL(dim_month),\n"
     "    TREATAS(\n"
     "        SELECTCOLUMNS(VALUES(dim_month[month_index]),\n"
     "                      \"month_index\", dim_month[month_index] - vShift),\n"
     "        dim_month[month_index]\n"
     "    )\n"
     ")",
     "\\$#,0", "04 Comparison"),
    ("Pocket margin YoY pts",
     "VAR vNow = [Pocket margin %]\n"
     "VAR vThen = DIVIDE([Pocket margin $ LY], [Pocket revenue LY])\n"
     "RETURN vNow - vThen",
     "0.0%", "04 Comparison"),

    # --- Competitive -------------------------------------------------------
    ("Price index", "MEDIANX(competitive_index, competitive_index[price_index])",
     "#,0.0", "05 Competitive"),
    ("Price index target", "100", "#,0", "05 Competitive"),
    ("Products under market",
     "CALCULATE(DISTINCTCOUNT(competitive_index[product_id]),\n"
     "          competitive_index[price_index] < 96)", "#,0", "05 Competitive"),
    ("Products over market",
     "CALCULATE(DISTINCTCOUNT(competitive_index[product_id]),\n"
     "          competitive_index[price_index] > 115)", "#,0", "05 Competitive"),
    ("Market price", "AVERAGE(competitive_index[market_price])", "\\$#,0.00", "05 Competitive"),
    ("Gap to market %", "AVERAGE(competitive_index[gap_pct])", "0.0%", "05 Competitive"),
    ("Pass-through to list", "AVERAGE(passthrough[passthrough_to_list])",
     "0.00", "05 Competitive"),
    ("Pass-through to pocket", "AVERAGE(passthrough[passthrough_to_pocket])",
     "0.00", "05 Competitive"),

    # --- Cost --------------------------------------------------------------
    ("Cost variance", "SUM(cost_variance_long[variance])", "\\$#,0", "06 Cost"),
    ("Purchase price variance",
     "CALCULATE([Cost variance], cost_variance_long[variance_type] = \"Purchase price\")",
     "\\$#,0", "06 Cost"),
    ("Yield variance",
     "CALCULATE([Cost variance], cost_variance_long[variance_type] = \"Yield\")",
     "\\$#,0", "06 Cost"),
    ("Labour variance",
     "CALCULATE([Cost variance],\n"
     "          cost_variance_long[variance_type] IN {\"Labour rate\", \"Labour efficiency\"})",
     "\\$#,0", "06 Cost"),
    ("Budget revenue", "SUM(budget_variance[budget_revenue])", "\\$#,0", "06 Cost"),
    ("Actual revenue", "SUM(budget_variance[actual_revenue])", "\\$#,0", "06 Cost"),
    ("Revenue vs budget", "[Actual revenue] - [Budget revenue]", "\\$#,0", "06 Cost"),
    ("Margin vs budget",
     "SUM(budget_variance[actual_margin]) - SUM(budget_variance[budget_margin])",
     "\\$#,0", "06 Cost"),
    ("Overhead spending variance", "SUM(overhead_variance[spending_variance])",
     "\\$#,0", "06 Cost"),
    ("Overhead volume variance", "SUM(overhead_variance[volume_variance])",
     "\\$#,0", "06 Cost"),

    # --- Bridge ------------------------------------------------------------
    ("Bridge amount", "SUM(margin_bridge_effects[amount])", "\\$#,0", "07 Bridge"),

    # --- Elasticity --------------------------------------------------------
    ("Elasticity", "AVERAGE(elasticity_estimates[elasticity])", "0.00", "08 Elasticity"),
    ("Elasticity fit", "AVERAGE(elasticity_estimates[r_squared])", "0.000", "08 Elasticity"),
    ("Break-even volume %", "AVERAGE(price_change_hurdles[break_even_volume_pct])",
     "0.0%", "08 Elasticity"),
    ("Expected volume %", "AVERAGE(price_change_hurdles[expected_volume_pct])",
     "0.0%", "08 Elasticity"),
    ("Expected margin change", "SUM(price_change_hurdles[expected_margin_change])",
     "\\$#,0", "08 Elasticity"),
    ("Response revenue", "SUM(price_response_curve[revenue])", "\\$#,0", "08 Elasticity"),
    ("Response profit", "SUM(price_response_curve[profit])", "\\$#,0", "08 Elasticity"),
    ("Response volume", "SUM(price_response_curve[quantity])", "#,0", "08 Elasticity"),

    # --- Bands, segments and willingness to pay ----------------------------
    ("Realisation opportunity", "SUM(price_bands[realisation_opportunity])",
     "\\$#,0", "09 Bands"),
    ("Band width %", "AVERAGE(price_bands[band_width_pct])", "0.0%", "09 Bands"),
    ("Band p10", "AVERAGE(price_bands[p10_price])", "\\$#,0.00", "09 Bands"),
    ("Band median", "AVERAGE(price_bands[median_price])", "\\$#,0.00", "09 Bands"),
    ("Band p90", "AVERAGE(price_bands[p90_price])", "\\$#,0.00", "09 Bands"),
    ("Win rate", "AVERAGE(wtp_curve[win_rate])", "0.0%", "09 Bands"),
    ("Quote win rate", "AVERAGE(wtp_fits[win_rate])", "0.0%", "09 Bands"),
    ("Indifference price ratio", "AVERAGE(wtp_fits[indifference_price_ratio])",
     "0.000", "09 Bands"),
    ("Customer pocket margin %", "AVERAGE(customer_profitability[pocket_margin_pct])",
     "0.0%", "09 Bands"),
    ("Customer volume (units)", "SUM(customer_profitability[volume_units])", "#,0", "09 Bands"),

    # --- Guardrails --------------------------------------------------------
    ("Lines scored", "COUNTROWS(deal_scores)", "#,0", "10 Guardrails"),
    ("Within guardrail %",
     "VAR vClear = CALCULATE(COUNTROWS(deal_scores), deal_scores[within_guardrail] = TRUE())\n"
     "RETURN DIVIDE(vClear, [Lines scored])", "0.0%", "10 Guardrails"),
    ("Guardrail breaches", "SUM(guardrail_summary[count])", "#,0", "10 Guardrails"),
    ("Margin at risk", "SUM(guardrail_summary[margin_at_risk])", "\\$#,0", "10 Guardrails"),
    ("Deal margin %", "AVERAGE(deal_scores[pocket_margin_pct])", "0.0%", "10 Guardrails"),
    ("Extended margin", "SUM(deal_scores[extended_margin])", "\\$#,0", "10 Guardrails"),
    ("Margin gap $", "SUM(deal_scores[margin_gap_dollars])", "\\$#,0", "10 Guardrails"),

    # --- Bundles -----------------------------------------------------------
    ("Incremental margin", "SUM(bundle_candidates[incremental_margin])",
     "\\$#,0", "11 Bundles"),
    ("Break-even cannibalisation",
     "AVERAGE(bundle_candidates[break_even_cannibalisation])", "0.0%", "11 Bundles"),
    ("Cannibalisation headroom", "AVERAGE(bundle_candidates[headroom])",
     "0.0%", "11 Bundles"),
    ("Bundle margin %", "AVERAGE(bundle_candidates[bundle_margin_pct])",
     "0.0%", "11 Bundles"),
    ("Standalone margin %", "AVERAGE(bundle_candidates[standalone_margin_pct])",
     "0.0%", "11 Bundles"),
    ("Bundle price", "AVERAGE(bundle_candidates[bundle_price])",
     "\\$#,0.00", "11 Bundles"),
    ("Bundles considered", "COUNTROWS(bundle_candidates)", "#,0", "11 Bundles"),

    # --- What-if ------------------------------------------------------------
    #
    # The four parameters are read once each, then combined. Everything below
    # is a projection of measures that already exist rather than a second
    # definition of them: a DAX expression re-deriving pocket margin from list
    # price and seven deduction columns would drift from the Python the first
    # time either was edited.
    ("Price change value", "SELECTEDVALUE(PriceChange[Price change %], 0)",
     "0.0%", "13 What-if"),
    ("Cost change value", "SELECTEDVALUE(CostChange[Cost change %], 0)",
     "0.0%", "13 What-if"),
    ("Volume change value", "SELECTEDVALUE(VolumeChange[Volume change %], 0)",
     "0.0%", "13 What-if"),
    ("Discount change value", "SELECTEDVALUE(DiscountChange[Discount change pts], 0)",
     "0.0%", "13 What-if"),
    # A discount is a deduction from price, so the two move the realised price
    # in opposite directions and belong in one factor. Keeping them apart is
    # how a simulator ends up reporting a price rise and a deeper discount as
    # if they were independently good news.
    ("Scenario price factor", "1 + [Price change value] - [Discount change value]",
     "0.000", "13 What-if"),
    ("Scenario volume factor", "1 + [Volume change value]", "0.000", "13 What-if"),
    ("Scenario revenue",
     "[Pocket revenue] * [Scenario price factor] * [Scenario volume factor]",
     "\\$#,0", "13 What-if"),
    ("Scenario cost",
     "[COGS] * (1 + [Cost change value]) * [Scenario volume factor]",
     "\\$#,0", "13 What-if"),
    ("Scenario margin $", "[Scenario revenue] - [Scenario cost]", "\\$#,0", "13 What-if"),
    ("Scenario margin %", "DIVIDE([Scenario margin $], [Scenario revenue])",
     "0.0%", "13 What-if"),
    ("Scenario margin delta", "[Scenario margin $] - [Pocket margin $]",
     "\\$#,0", "13 What-if"),
    ("Scenario revenue delta", "[Scenario revenue] - [Pocket revenue]",
     "\\$#,0", "13 What-if"),
    ("Scenario margin pts", "[Scenario margin %] - [Pocket margin %]",
     "0.0%", "13 What-if"),
    ("Three-point operating profit", "SUM(scenario_three_point[operating_profit])",
     "\\$#,0", "13 What-if"),
    ("Three-point contribution %", "AVERAGE(scenario_three_point[contribution_pct])",
     "0.0%", "13 What-if"),
    ("Three-point delta", "SUM(scenario_three_point[delta])", "\\$#,0", "13 What-if"),
    ("Tornado swing", "SUM(scenario_tornado[swing])", "\\$#,0", "13 What-if"),
    ("Tornado upside", "SUM(scenario_tornado[upside_delta])", "\\$#,0", "13 What-if"),
    ("Tornado downside", "SUM(scenario_tornado[downside_delta])", "\\$#,0", "13 What-if"),
    ("Tornado share of swing", "AVERAGE(scenario_tornado[share_of_swing])",
     "0.0%", "13 What-if"),
    ("Break-even input", "AVERAGE(scenario_thresholds[threshold])", "0.0%", "13 What-if"),

    # --- Unit economics and break-even ---------------------------------------
    ("Contribution $", "SUM(unit_economics[contribution])", "\\$#,0", "14 Unit economics"),
    ("Contribution %",
     "DIVIDE([Contribution $], SUM(unit_economics[pocket_revenue]))",
     "0.0%", "14 Unit economics"),
    ("Contribution per unit",
     "DIVIDE([Contribution $], SUM(unit_economics[volume_units]))",
     "\\$#,0.00", "14 Unit economics"),
    ("Variable cost per unit",
     "DIVIDE(SUM(unit_economics[variable_cost]), SUM(unit_economics[volume_units]))",
     "\\$#,0.00", "14 Unit economics"),
    ("Markup %", "AVERAGE(unit_economics[markup_pct])", "0.0%", "14 Unit economics"),
    ("Cost to serve %", "AVERAGE(unit_economics[cost_to_serve_pct])",
     "0.0%", "14 Unit economics"),
    ("Operating profit", "SUM(unit_economics[operating_profit])",
     "\\$#,0", "14 Unit economics"),
    ("Operating margin %",
     "DIVIDE([Operating profit], SUM(unit_economics[pocket_revenue]))",
     "0.0%", "14 Unit economics"),
    ("Break-even revenue", "SUM(break_even_portfolio[break_even_revenue])",
     "\\$#,0", "14 Unit economics"),
    ("Break-even units", "SUM(break_even_portfolio[break_even_volume])",
     "#,0", "14 Unit economics"),
    ("Margin of safety", "AVERAGE(break_even_portfolio[margin_of_safety])",
     "0.0%", "14 Unit economics"),
    ("Operating leverage", "AVERAGE(break_even_portfolio[operating_leverage])",
     "0.00", "14 Unit economics"),
    ("Curve revenue", "SUM(break_even_curve[revenue])", "\\$#,0", "14 Unit economics"),
    ("Curve fixed cost", "SUM(break_even_curve[fixed_cost])", "\\$#,0", "14 Unit economics"),
    ("Curve variable cost", "SUM(break_even_curve[variable_cost])",
     "\\$#,0", "14 Unit economics"),
    ("Curve total cost", "SUM(break_even_curve[total_cost])", "\\$#,0", "14 Unit economics"),
    ("Curve profit", "SUM(break_even_curve[profit])", "\\$#,0", "14 Unit economics"),
    ("Product break-even units", "SUM(break_even_products[break_even_units])",
     "#,0", "14 Unit economics"),
    ("Margin read as markup", "AVERAGE(markup_vs_margin[margin_if_read_as_markup])",
     "0.0%", "14 Unit economics"),
    ("Markup needed", "AVERAGE(markup_vs_margin[markup_needed_for_that_margin])",
     "0.0%", "14 Unit economics"),
    ("Markup margin gap", "AVERAGE(markup_vs_margin[gap])", "0.0%", "14 Unit economics"),

    # --- Forecast -------------------------------------------------------------
    ("Forecast", "SUM(forecast_series[forecast])", "#,0", "15 Forecast"),
    ("Actual", "SUM(forecast_series[actual])", "#,0", "15 Forecast"),
    ("Forecast low", "SUM(forecast_series[low])", "#,0", "15 Forecast"),
    ("Forecast high", "SUM(forecast_series[high])", "#,0", "15 Forecast"),
    ("Method WAPE", "AVERAGE(forecast_accuracy[wape])", "0.0%", "15 Forecast"),
    ("Method bias", "AVERAGE(forecast_accuracy[bias])", "0.0%", "15 Forecast"),
    ("Backtest WAPE", "AVERAGE(forecast_summary[backtest_wape])", "0.0%", "15 Forecast"),
    ("Holdout WAPE", "AVERAGE(forecast_summary[holdout_wape])", "0.0%", "15 Forecast"),
    ("Next six months", "SUM(forecast_summary[next_6_total])", "#,0", "15 Forecast"),
    ("Last six months", "SUM(forecast_summary[last_6_actual])", "#,0", "15 Forecast"),
    ("Forecast change %", "AVERAGE(forecast_summary[change_pct])", "0.0%", "15 Forecast"),
    # forecast_series and forecast_summary each hold four measures in three
    # different units. Anything that adds across them -- a card, a total row,
    # an unfiltered axis -- reports dollars plus units plus dollars again, and
    # the number looks like a number. The headline visuals bind a scoped
    # measure rather than trusting a slicer to have been touched.
    ("Revenue actual",
     "CALCULATE([Actual], forecast_series[measure] = \"Pocket revenue\")",
     "\\$#,0", "15 Forecast"),
    ("Revenue forecast",
     "CALCULATE([Forecast], forecast_series[measure] = \"Pocket revenue\")",
     "\\$#,0", "15 Forecast"),
    ("Revenue forecast low",
     "CALCULATE([Forecast low], forecast_series[measure] = \"Pocket revenue\")",
     "\\$#,0", "15 Forecast"),
    ("Revenue forecast high",
     "CALCULATE([Forecast high], forecast_series[measure] = \"Pocket revenue\")",
     "\\$#,0", "15 Forecast"),
    ("Volume actual",
     "CALCULATE([Actual], forecast_series[measure] = \"Volume (units)\")",
     "#,0", "15 Forecast"),
    ("Volume forecast",
     "CALCULATE([Forecast], forecast_series[measure] = \"Volume (units)\")",
     "#,0", "15 Forecast"),
    ("Revenue next six months",
     "CALCULATE([Next six months], forecast_summary[measure] = \"Pocket revenue\")",
     "\\$#,0", "15 Forecast"),
    ("Revenue forecast change %",
     "CALCULATE([Forecast change %], forecast_summary[measure] = \"Pocket revenue\")",
     "0.0%", "15 Forecast"),
    ("Revenue backtest WAPE",
     "CALCULATE([Backtest WAPE], forecast_summary[measure] = \"Pocket revenue\")",
     "0.0%", "15 Forecast"),
    ("Revenue holdout WAPE",
     "CALCULATE([Holdout WAPE], forecast_summary[measure] = \"Pocket revenue\")",
     "0.0%", "15 Forecast"),

    # --- Profitability and segmentation ---------------------------------------
    ("Profitability revenue", "SUM(profitability[pocket_revenue])",
     "\\$#,0", "16 Profitability"),
    ("Profitability margin $", "SUM(profitability[gross_margin])",
     "\\$#,0", "16 Profitability"),
    ("Profitability margin %",
     "DIVIDE([Profitability margin $], [Profitability revenue])",
     "0.0%", "16 Profitability"),
    ("Profitability operating margin %",
     "DIVIDE(SUM(profitability[operating_profit]), [Profitability revenue])",
     "0.0%", "16 Profitability"),
    ("Profitability contribution", "SUM(profitability[contribution])",
     "\\$#,0", "16 Profitability"),
    ("Profitability leakage %", "AVERAGE(profitability[leakage_pct])",
     "0.0%", "16 Profitability"),
    ("Revenue share", "SUM(profitability[revenue_share])", "0.0%", "16 Profitability"),
    ("Segment revenue", "SUM(profit_heatmap[pocket_revenue])",
     "\\$#,0", "16 Profitability"),
    ("Segment margin $", "SUM(profit_heatmap[gross_margin])",
     "\\$#,0", "16 Profitability"),
    ("Segment margin %", "DIVIDE([Segment margin $], [Segment revenue])",
     "0.0%", "16 Profitability"),
    ("Segment leakage %", "AVERAGE(profit_heatmap[leakage_pct])",
     "0.0%", "16 Profitability"),

    # --- Discount and promotion -----------------------------------------------
    ("Banded revenue", "SUM(discount_margin_matrix[pocket_revenue])",
     "\\$#,0", "17 Discount"),
    ("Banded margin $", "SUM(discount_margin_matrix[gross_margin])",
     "\\$#,0", "17 Discount"),
    ("Banded margin %", "DIVIDE([Banded margin $], [Banded revenue])",
     "0.0%", "17 Discount"),
    ("Banded lines", "SUM(discount_margin_matrix[lines])", "#,0", "17 Discount"),
    ("Banded share of revenue", "SUM(discount_margin_matrix[share_of_revenue])",
     "0.0%", "17 Discount"),
    ("Promotions run", "SUM(promotion_summary[promotions])", "#,0", "17 Discount"),
    ("Incremental promo units", "SUM(promotion_summary[incremental_volume_units])",
     "#,0", "17 Discount"),
    ("Incremental promo margin", "SUM(promotion_summary[incremental_margin])",
     "\\$#,0", "17 Discount"),
    ("Discount on baseline", "SUM(promotion_summary[discount_on_baseline])",
     "\\$#,0", "17 Discount"),
    ("Net promo margin", "SUM(promotion_summary[net_promo_margin])",
     "\\$#,0", "17 Discount"),
    ("Promo ROI", "DIVIDE([Net promo margin], [Discount on baseline])",
     "0.00", "17 Discount"),
    ("Units sold", "SUM(price_vs_volume[volume_units])", "#,0", "17 Discount"),
    ("Price point", "AVERAGE(price_vs_volume[list_price])", "\\$#,0.00", "17 Discount"),
    ("Price point revenue", "SUM(price_vs_volume[pocket_revenue])",
     "\\$#,0", "17 Discount"),

    # --- Cost elements ---------------------------------------------------------
    ("Standard cost", "SUM(cost_element_summary[standard_cost])",
     "\\$#,0", "18 Cost elements"),
    ("Actual cost", "SUM(cost_element_summary[actual_cost])",
     "\\$#,0", "18 Cost elements"),
    ("Cost element variance", "SUM(cost_element_summary[variance])",
     "\\$#,0", "18 Cost elements"),
    ("Cost element variance %",
     "DIVIDE([Cost element variance], [Standard cost])", "0.0%", "18 Cost elements"),
    ("Element waterfall amount", "SUM(cost_element_waterfall[amount])",
     "\\$#,0", "18 Cost elements"),
    ("Element waterfall step", "SUM(cost_element_waterfall[delta])",
     "\\$#,0", "18 Cost elements"),
    ("Category cost variance", "SUM(cost_element_by_category[variance])",
     "\\$#,0", "18 Cost elements"),
    ("Category standard cost", "SUM(cost_element_by_category[standard_cost])",
     "\\$#,0", "18 Cost elements"),

    # --- Pricing operations ----------------------------------------------------
    ("Price changes", "COUNTROWS(price_change_log)", "#,0", "19 Operations"),
    ("Price increases",
     "CALCULATE([Price changes], price_change_log[direction] = \"Increase\")",
     "#,0", "19 Operations"),
    ("Price decreases",
     "CALCULATE([Price changes], price_change_log[direction] = \"Decrease\")",
     "#,0", "19 Operations"),
    ("Median price change %", "MEDIANX(price_change_log, price_change_log[pct_change])",
     "0.0%", "19 Operations"),
    ("Median days to approve",
     "MEDIANX(price_change_log, price_change_log[days_to_approve])",
     "#,0", "19 Operations"),
    ("Products on the price list", "SUM(price_list_coverage[products])",
     "#,0", "19 Operations"),
    ("Stale products", "SUM(price_list_coverage[stale])", "#,0", "19 Operations"),
    ("Days since price change",
     "MEDIANX(price_list_coverage, price_list_coverage[median_days_since_change])",
     "#,0", "19 Operations"),
    ("Coverage margin %", "AVERAGE(price_list_coverage[median_margin_pct])",
     "0.0%", "19 Operations"),

    # --- Data quality and reconciliation ---------------------------------------
    #
    # The score is row-weighted, not an average of the per-dimension scores:
    # twelve rules over wildly different row counts average to a number that
    # flatters whichever rule ran over the smallest table.
    ("Rows checked", "SUM(data_quality_checks[rows_checked])", "#,0", "20 Data quality"),
    ("Rows failing", "SUM(data_quality_checks[rows_failing])", "#,0", "20 Data quality"),
    ("Quality score", "DIVIDE([Rows checked] - [Rows failing], [Rows checked])",
     "0.00%", "20 Data quality"),
    ("Checks run", "COUNTROWS(data_quality_checks)", "#,0", "20 Data quality"),
    ("Critical checks failing",
     "CALCULATE([Checks run], data_quality_checks[severity] = \"Critical\")",
     "#,0", "20 Data quality"),
    ("Fail rate", "AVERAGE(data_quality_checks[fail_rate])", "0.00%", "20 Data quality"),
    ("Dimension score", "AVERAGE(data_quality_score[score])", "0.00%", "20 Data quality"),
    ("Reconciliation amount", "SUM(reconciliation[amount])", "\\$#,0", "20 Data quality"),
    ("Reconciliation step", "SUM(reconciliation[delta])", "\\$#,0", "20 Data quality"),
    ("Unexplained", "SUM(reconciliation[unexplained])", "\\$#,0.00", "20 Data quality"),

    # --- Recommendations and exceptions ----------------------------------------
    ("Products reviewed", "COUNTROWS(recommendations)", "#,0", "21 Recommendations"),
    ("Margin delta", "SUM(recommendations[margin_delta])", "\\$#,0", "21 Recommendations"),
    ("Revenue delta", "SUM(recommendations[revenue_delta])",
     "\\$#,0", "21 Recommendations"),
    ("Recommended price", "AVERAGE(recommendations[recommended_price])",
     "\\$#,0.00", "21 Recommendations"),
    ("Recommended move %", "AVERAGE(recommendations[price_change_pct])",
     "0.0%", "21 Recommendations"),
    ("Action products", "SUM(recommendation_summary[products])",
     "#,0", "21 Recommendations"),
    ("Action margin delta", "SUM(recommendation_summary[margin_delta])",
     "\\$#,0", "21 Recommendations"),
    ("Action revenue delta", "SUM(recommendation_summary[revenue_delta])",
     "\\$#,0", "21 Recommendations"),
    ("Action volume", "SUM(recommendation_summary[volume_units])",
     "#,0", "21 Recommendations"),
    ("Exceptions", "COUNTROWS(guardrail_exceptions)", "#,0", "21 Recommendations"),
    ("Exception margin at risk", "SUM(guardrail_exceptions[margin_at_risk])",
     "\\$#,0", "21 Recommendations"),
    ("Red exceptions",
     "CALCULATE([Exceptions], guardrail_exceptions[alert] = \"Red\")",
     "#,0", "21 Recommendations"),
    ("Exception margin %", "AVERAGE(guardrail_exceptions[pocket_margin_pct])",
     "0.0%", "21 Recommendations"),

    # --- Reference labels shown as card subtitles --------------------------
    ("Margin reference",
     "VAR vGap = [Pocket margin YoY pts]\n"
     "RETURN IF(ISBLANK(vGap), \"no prior year\",\n"
     "          FORMAT(vGap, \"+0.0%;-0.0%;0.0%\") & \" on last year\")",
     "", "12 Labels"),
    ("Leakage reference",
     "\"every point is \" & FORMAT([List value] * 0.01, \"\\$#,0\")",
     "", "12 Labels"),
    ("Index reference",
     "VAR vIndex = [Price index]\n"
     "RETURN FORMAT(vIndex - 100, \"+0.0;-0.0;0.0\") & \" against parity\"",
     "", "12 Labels"),
    ("Scenario reference",
     "\"price \" & FORMAT([Price change value], \"+0.0%;-0.0%;0.0%\") &\n"
     "\", cost \" & FORMAT([Cost change value], \"+0.0%;-0.0%;0.0%\") &\n"
     "\", volume \" & FORMAT([Volume change value], \"+0.0%;-0.0%;0.0%\")",
     "", "12 Labels"),
    ("Quality reference",
     "VAR vFailing = [Rows failing]\n"
     "RETURN FORMAT(vFailing, \"#,0\") & \" rows of \" & "
     "FORMAT([Rows checked], \"#,0\") & \" need a fix\"",
     "", "12 Labels"),
]
