"""
Write ``docs/DATA_DICTIONARY.md`` from the CSVs themselves.

Generated rather than maintained, because a hand-written data dictionary is
wrong within two commits and then actively misleading -- a reader trusts it
more than the CSV, which is the opposite of what its accuracy deserves.

CI runs this with ``--check`` and fails if the committed file has drifted.

Usage::

    python -m docs.build_dictionary
    python -m docs.build_dictionary --check
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "DATA_DICTIONARY.md"

# One sentence per table. A column list without this is a schema dump; the
# sentence is the part a reader cannot get from the header row.
DESCRIPTIONS: dict[str, str] = {
    # data/
    "dim_product": "The catalogue. Category, cut, brand tier, pack format and "
                   "lifecycle, plus the cost drivers and the elasticity each item obeys.",
    "dim_customer": "The customer base, with everything that drives its deductions: "
                    "segment, channel, region, volume tier, price list and payment terms.",
    "dim_competitor": "Five competitors, each with a positioning multiplier and a "
                      "share of the catalogue it is observed on.",
    "dim_date": "Daily date table with a July-to-June fiscal calendar.",
    "dim_month": "The month grain every fact is keyed on. Not marked as a date "
                 "table -- Power BI needs a contiguous daily column for that -- so "
                 "period comparisons subtract `month_index`.",
    "fact_sales": "Invoice lines at month x customer x product, carrying all ten "
                  "waterfall deductions per unit and the four price levels.",
    "fact_competitor_price": "What the market was seen charging. Deliberately "
                             "partial and uneven: a competitive file always is.",
    "fact_quote": "Won and lost quotes with the competing price. The only place "
                  "demand at prices we did not charge is observable.",
    "fact_cost_ledger": "Purchases and production yields against standard, which "
                        "is where purchase price and yield variance come from.",
    "fact_price_cost_panel": "One row per product per month: input cost, standard "
                             "cost, the full cost stack, and the list price that "
                             "quarter's review set.",
    "fact_commodity_index": "Six weekly input indices with their own drift, "
                            "volatility and seasonality.",
    "fact_budget": "The plan, set once a year at category x month, and the actuals "
                   "beside it.",
    "fact_overhead": "Monthly overhead pools with a budgeted absorption rate per unit.",

    # data/ -- the operations layer
    "dim_salesperson": "Twelve reps, each with a region, a segment focus and a "
                       "discount appetite that shows up in their book's leakage.",
    "dim_cost_element": "The eight elements the cost stack is built from, and "
                        "whether each behaves as a variable or a fixed cost.",
    "fact_cost_element": "Standard against actual for every cost element, by "
                         "product and month. The variance analysis reads this.",
    "fact_fixed_cost": "The four fixed pools -- DC facility, inventory carrying, "
                       "technology and marketplace fees, selling and admin -- by "
                       "month.",
    "fact_promotion": "Every promotion run: its mechanic, its depth, the products "
                      "it covered and the months it ran.",
    "fact_price_change": "The price-list change log. One row per change, with the "
                         "reason, who asked, and how long approval took.",

    # raw/ -- the ERP extract, before staging
    "erp_billing_items": "The billing document items as SAP SD would hand them "
                         "over, defects included: missing standard costs, "
                         "duplicated lines, UOM mismatches, a handful of postings "
                         "dated after the extract.",
    "erp_material_master": "The material master. Base UOM, pack size and standard "
                           "cost -- with the gaps that make a line unpriceable.",
    "erp_customer_master": "The sold-to party master, including the orphans that "
                           "billing lines point at and this table does not have.",
    "erp_condition_records": "Pricing condition records: the list price and the "
                             "discount conditions each customer is entitled to.",

    # output/ -- staging and quality
    "staged_billing_items": "The extract after staging: typed, de-duplicated, "
                            "joined to the masters, and flagged for whether each "
                            "line can be priced at all.",
    "staging_log": "One row per staging step, with the rows and the value it moved. "
                   "This is the audit trail the reconciliation is built from.",
    "staging_summary": "The staging run as a dozen headline numbers.",
    "data_quality_checks": "Twelve rules across the six quality dimensions, each "
                           "with the rows it checked, the rows it failed, and the "
                           "action a failure calls for.",
    "data_quality_score": "The rules rolled up per dimension, plus an overall row. "
                          "Row-weighted, not an average of the per-dimension "
                          "scores -- twelve rules over wildly different row counts "
                          "average to a number that flatters the smallest table.",
    "reconciliation": "Extract total down to staged total, with every exclusion "
                      "named and the unexplained line at the bottom. It balances "
                      "to zero or the staging is wrong.",

    # output/ -- unit economics and break-even
    "unit_economics": "Per product: contribution, contribution per unit, markup, "
                      "cost to serve, and operating profit after fixed costs are "
                      "allocated.",
    "break_even_products": "Break-even units and revenue per product, with the "
                           "margin of safety against what it actually sold.",
    "break_even_portfolio": "The same for the whole book and for each category, "
                            "with operating leverage.",
    "break_even_curve": "A volume grid from zero upward: revenue, fixed cost, "
                        "variable cost, total cost and profit at each point. This "
                        "is the break-even chart.",
    "markup_vs_margin": "The conversion table. For each rate, what it means read "
                        "as a margin, what markup that margin needs, and the gap "
                        "between the two readings.",

    # output/ -- scenarios
    "scenario_assumptions": "The inputs the base case was run on.",
    "scenario_three_point": "Worst, base and best case. The ends are every "
                            "assumption at its own extreme at once -- a stress "
                            "test, not an interval.",
    "scenario_tornado": "One row per input, ranked by how far moving it alone "
                        "moves operating profit.",
    "scenario_grid": "A two-input sensitivity grid: operating profit at every "
                     "combination of two assumptions.",
    "scenario_thresholds": "The value at which each input drives operating profit "
                           "through zero, and a stated reason where it never does.",

    # output/ -- forecasting
    "forecast_series": "Actual and forecast by month for four measures, with an "
                       "empirical interval taken from backtest residuals rather "
                       "than assumed.",
    "forecast_accuracy": "Every method against every measure in a rolling-origin "
                         "backtest, ranked by WAPE.",
    "forecast_summary": "The chosen method per measure, its backtest and holdout "
                        "error, and the next six months against the last six.",

    # output/ -- profitability
    "profitability": "Long form: one row per (dimension, member) across product, "
                     "customer, region, channel, segment and salesperson at once, "
                     "down to operating margin.",
    "profit_heatmap": "The segment-by-category grid: revenue, margin and leakage "
                      "for every pair.",
    "product_co_purchase": "For each product, the single partner most often on the "
                           "same order, and how often. This is what the bundle "
                           "candidates are built from.",

    # output/ -- discount, promotion and price response
    "discount_margin_matrix": "Invoice lines banded by discount depth against "
                              "margin achieved. The cells off the diagonal are the "
                              "conversation.",
    "promotion_summary": "One row per mechanic: incremental volume, incremental "
                         "margin, the discount given away on baseline volume, and "
                         "whether it paid.",
    "promotion_analysis": "The same at product and month grain, with the baseline "
                          "each promotion is measured against.",
    "price_vs_volume": "Price and units by product and month, split by whether the "
                       "month was promoted. The price-response scatter reads this.",

    # output/ -- cost elements
    "cost_element_summary": "Standard against actual per cost element for the "
                            "current year, with the variance and its verdict.",
    "cost_element_waterfall": "The same as waterfall bars, standard cost through "
                              "each element to actual.",
    "cost_element_by_category": "Which categories carry each element's variance.",

    # output/ -- pricing operations
    "price_change_log": "Every price change: from, to, why, who asked, and how "
                        "long the approval took.",
    "price_change_summary": "The log rolled up by reason and direction.",
    "price_change_approvals": "The log rolled up by approval state.",
    "price_list_coverage": "How much of the book each review cadence covers, how "
                           "stale it has gone, and what margin it holds.",
    "pricing_decision_register": "One governed request per product, with the "
                                 "evidence state, delegated reviewer, effective "
                                 "period, monitoring window and rollback trigger.",
    "price_realization_monitor": "Approved historical list-price changes matched "
                                 "to fixed pre/post invoice and pocket-price "
                                 "windows for observational follow-up.",
    "pricing_release_gates": "The eight controls that decide whether the evidence "
                             "packet can enter human review, with a state and "
                             "supporting evidence for each control.",

    # output/ -- recommendations
    "recommendations": "One row per product: the action, the price it implies, "
                       "what it is worth, the confidence behind it, and the "
                       "rationale in the words an analyst would use.",
    "recommendation_summary": "The recommendations rolled up by action.",
    "recommendation_note": "The written summary, in one row, so a card and the "
                           "paragraph under it cannot disagree.",

    # output/ -- the SQL marts
    "sql_waterfall": "The waterfall computed in DuckDB rather than pandas. A test "
                     "holds the two to 1e-8 of each other; two implementations "
                     "that agree are worth more than one that is merely asserted.",
    "sql_profitability": "The profitability cut, in SQL, using GROUPING SETS.",
    "sql_price_bands": "Price bands in SQL, using PERCENTILE_CONT.",
    "sql_monthly_trend": "The monthly trend in SQL: LAG at one and twelve months, "
                         "and a rolling three-month average.",
    "sql_margin_concentration": "Margin concentration in SQL: rank overall and "
                                "within category, with the running share.",
    "sql_exceptions": "The guardrail exceptions in SQL, for the same reason.",

    # output/
    "price_waterfall": "The list-to-pocket-margin waterfall at the book total, one "
                       "row per step.",
    "leakage_by_dimension": "Long form: one row per dimension, member and "
                            "deduction, so the dashboard can rank by dollars.",
    "waterfall_monthly": "The four price levels by month, so leakage can be trended.",
    "elasticity_estimates": "Own-price elasticity per category and per product, "
                            "with the fit diagnostics and the implied optimum.",
    "price_response_curve": "Volume, revenue and profit across a band of prices "
                            "either side of today's.",
    "price_change_hurdles": "For a grid of price changes: the volume each needs to "
                            "break even, and the volume the elasticity implies.",
    "competitive_index": "Price index per product per month, with the market price "
                         "it was measured against and how old that evidence is.",
    "competitive_summary": "The latest month's coverage, median index and the "
                           "revenue sitting above and below the band.",
    "passthrough": "How much of each input-cost move reached list price and how "
                   "much reached pocket price, estimated on quarterly changes.",
    "commodity_index_weekly": "The input indices, weekly.",
    "cost_variance_detail": "Purchase price, yield and labour variance per product "
                            "per month, restated through the tested helpers.",
    "cost_variance_long": "The same, one row per variance type, for stacked charts.",
    "budget_variance": "Budget against actual by month and category, with the sign "
                       "interpreted per line.",
    "overhead_variance": "Spending and volume variance per cost pool.",
    "margin_bridge_steps": "The margin bridge as waterfall bars, per year-pair.",
    "margin_bridge_effects": "Price, cost, volume, mix, new and lost -- plus the "
                             "residual, which is float noise and is shown so you "
                             "can check.",
    "revenue_bridge_effects": "The same decomposition on revenue.",
    "price_bands": "Per product: the volume-weighted p10, median and p90 pocket "
                   "price, and what closing the band to the median is worth.",
    "segment_profile": "Level and consistency by segment, channel, region, tier, "
                       "brand tier and category.",
    "wtp_fits": "The fitted win curve per segment: slope, and the price ratio at "
                "which we win half the time.",
    "wtp_curve": "The fitted curve and the observed win rates, for plotting one "
                 "over the other.",
    "quote_loss_reasons": "Why quotes were lost, and what they were worth.",
    "customer_profitability": "One row per customer with margin, leakage, cost to "
                              "serve and a quadrant against the medians.",
    "deal_scores": "The latest month's lines scored against floor, target and "
                   "stretch, with the approver each would need.",
    "guardrail_exceptions": "Every guardrail breach, ranked by margin at risk.",
    "guardrail_summary": "One row per rule: how many breaches and how much money.",
    "bundle_candidates": "Product pairs the same customers already buy together, "
                         "priced and judged on incremental margin.",
    "bundle_discount_sweep": "Incremental margin across a sweep of bundle discounts.",
    "price_ladders": "A good/better/best ladder on the median item of each category.",
    "executive_summary": "The dozen numbers a pricing analyst opens a meeting "
                         "with, pre-aggregated so a card and a heading cannot "
                         "disagree.",
}


def kind_of(series: pd.Series) -> str:
    """
    A stable name for a column's type.

    Not `str(series.dtype)`. That is a pandas *repr* and it moves between
    versions -- text is `object` on pandas 2 and `str` on pandas 3, so a
    dictionary generated on one and checked on the other differs on every text
    column in the file and the CI failure points at the document rather than at
    the version skew underneath it. It is also the wrong register: a reader
    wants to know a column holds text, not that it is backed by `object`.
    """
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        return "decimal"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "date"
    return "text"


def describe(path: Path) -> tuple[int, list[tuple[str, str, str]]]:
    """Row count, and (column, type, example) for each column."""
    frame = pd.read_csv(path)
    rows = []
    for name in frame.columns:
        series = frame[name].dropna()
        example = "" if series.empty else str(series.iloc[0])
        if len(example) > 28:
            example = example[:25] + "..."
        rows.append((name, kind_of(frame[name]), example))
    return len(frame), rows


def build() -> str:
    lines = [
        "# Data dictionary",
        "",
        "Generated by `python -m docs.build_dictionary`. CI fails if it drifts from",
        "the CSVs, because a hand-maintained dictionary is wrong within two commits",
        "and then actively misleading -- a reader trusts it more than the file.",
        "",
        "`data/` is what the generator writes at the grain the analysis wants.",
        "`raw/` is the same business delivered the way an ERP delivers it -- SAP SD",
        "billing items and the three masters, defects included -- and is what the",
        "quality rules and the reconciliation run against. `output/` is what the",
        "analysis engine computes, and is what both the Streamlit app and the Power",
        "BI model read: nothing with a definition worth arguing about is computed",
        "twice.",
        "",
    ]
    for directory, heading in (("data", "Generated data"),
                               ("raw", "The ERP extract"),
                               ("output", "Analysis outputs")):
        lines += [f"## {heading} — `{directory}/`", ""]
        for path in sorted((ROOT / directory).glob("*.csv")):
            name = path.stem
            rows, columns = describe(path)
            lines += [
                f"### `{name}`",
                "",
                DESCRIPTIONS.get(name, "_No description._"),
                "",
                f"{rows:,} rows × {len(columns)} columns.",
                "",
                "| Column | Type | Example |",
                "|---|---|---|",
            ]
            lines += [f"| `{c}` | {t} | {e} |" for c, t, e in columns]
            lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    content = build()
    if args.check:
        if not TARGET.exists():
            print(f"{TARGET} does not exist. Run: python -m docs.build_dictionary",
                  file=sys.stderr)
            return 1
        committed = TARGET.read_text(encoding="utf-8")
        if committed != content:
            # Print the diff, not just the verdict. "Out of date" is fine when
            # you can run the command yourself; it is useless when the failure
            # is in CI on another operating system and the log needs
            # credentials to read. The lines themselves say whether this is a
            # real change or a float that formatted differently.
            print("docs/DATA_DICTIONARY.md is out of date.\n"
                  "run: python -m docs.build_dictionary\n", file=sys.stderr)
            diff = difflib.unified_diff(
                committed.splitlines(), content.splitlines(),
                fromfile="committed", tofile="regenerated", lineterm="", n=1,
            )
            for line in list(diff)[:60]:
                print(line, file=sys.stderr)
            return 1
        print("data dictionary matches the CSVs")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    tables = content.count("\n### ")
    print(f"wrote {TARGET} ({tables} tables)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
