"""The executive summary a pricing analyst opens a meeting with."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh

sh.page()

summary = sh.load("executive_summary")
monthly = sh.load("waterfall_monthly").sort_values("month")
bridge = sh.load("margin_bridge_effects")
exceptions = sh.load("guardrail_summary")
latest_fy = int(summary["fiscal_year"].iloc[0])

st.title("Pricing and costing analytics")
sh.lede(
    f"Three complete fiscal years for a multi-category B2B wholesale distributor, "
    f"through FY{latest_fy}. "
    "The cost stack is the one the calculator has always run - vendor invoice, unit "
    "conversion, freight lane, recovery, labour, margin. Everything else on these pages "
    "is what a pricing analyst does with it once the cost is known: what the market "
    "charges, what the customer actually pays after every deduction, how much volume "
    "moves when the price does, and which of last year's margin miss was price rather "
    "than mix."
)

# --- Headline KPIs -------------------------------------------------------
st.markdown("## Where the business stands")

first_row = summary.iloc[:4]
sh.kpis([(row.kpi, sh.format_kpi(row.value, row.unit), None) for row in first_row.itertuples()])
second_row = summary.iloc[4:8]
sh.kpis([(row.kpi, sh.format_kpi(row.value, row.unit), None) for row in second_row.itertuples()])

with st.expander("What each number means"):
    sh.table(
        summary[["kpi", "unit", "note"]].rename(
            columns={"kpi": "Measure", "unit": "Unit", "note": "Reading"}
        )
    )

# --- The two charts that frame everything else ---------------------------
left, right = st.columns([3, 2])

with left:
    st.markdown("### Realised price against cost")
    sh.caption(
        "Pocket price is what reaches us after every discount, rebate, term and "
        "delivery cost. Where the two lines converge, margin is being squeezed by "
        "cost the price reviews have not caught up with."
    )
    monthly = monthly.copy()
    monthly["unit_cost"] = monthly["cogs"] / monthly["quantity_units"]
    monthly["list_price_unit"] = monthly["list_value"] / monthly["quantity_units"]
    fig = sh.lines(
        monthly, "month",
        {"list_price_unit": "List price / unit", "realised_price_unit": "Pocket price / unit",
         "unit_cost": "Cost / unit"},
    )
    sh.show(fig, 330, showlegend=True, y_title="$ per unit")

with right:
    st.markdown("### Margin leakage")
    sh.caption(
        "The share of list price that never arrives. Every point of it is "
        f"{sh.money(float(monthly['list_value'].sum()) * 0.01 / 3, 1)} a year."
    )
    # The monthly series swings a point and a half either way on customer mix
    # alone -- which month the big discounted accounts happened to order. The
    # rolling mean is what the trend actually is; the monthly line is kept
    # beside it rather than replaced by it, because smoothing away the noise
    # without showing it is how a chart implies more precision than it has.
    monthly["leakage_trend"] = monthly["leakage_pct"].rolling(3, center=True).mean()
    fig = sh.lines(
        monthly, "month",
        {"leakage_pct": "Monthly", "leakage_trend": "3-month average"},
        hover_fmt=".2%",
    )
    fig.data[0].line.width = 1
    fig.data[0].opacity = 0.45
    fig.update_yaxes(tickformat=".0%")
    sh.reference_line(fig, y=float(monthly["leakage_pct"].mean()), label="3-year average")
    sh.show(fig, 330, showlegend=True, y_title="")

# --- What moved the margin ----------------------------------------------
st.markdown("## What moved the margin")
comparisons = bridge["comparison"].unique().tolist()
chosen = st.radio("Comparison", comparisons, index=len(comparisons) - 1,
                  horizontal=True, label_visibility="collapsed")
effects = bridge[(bridge["comparison"] == chosen) & (bridge["effect"] != "Residual")]

bridge_left, bridge_right = st.columns([3, 2])
with bridge_left:
    steps = sh.load("margin_bridge_steps")
    steps = steps[steps["comparison"] == chosen].sort_values("sort_order")
    sh.show(sh.waterfall(steps, label="label"), 360)
with bridge_right:
    sh.caption(
        "Six effects that sum to the change exactly. Price and cost are valued at "
        "current volume; volume is pure scale at last year's average unit margin; "
        "mix is the blend shift at last year's unit margins. Products that only "
        "exist in one of the two years are pulled out first - there is no prior "
        "price to compare a launch against, and folding it into volume flatters "
        "the bridge."
    )
    display = effects.copy()
    display["Effect"] = display["effect"]
    display["Amount"] = display["amount"].map(lambda v: sh.money(v, 2))
    display["% of prior margin"] = display["pct_of_prior"].map(sh.pct)
    sh.table(display[["Effect", "Amount", "% of prior margin"]])

# --- Where the work is --------------------------------------------------
st.markdown("## Where the work is this month")
sh.caption(
    "Guardrail breaches on the most recent month's invoice lines, ranked by margin "
    "at risk rather than by how far below the floor they sit. A quarter-point of "
    "leakage across the whole book beats a ten-point discount on one account."
)
if not exceptions.empty:
    display = exceptions.copy()
    display["Margin at risk"] = display["margin_at_risk"].map(lambda v: sh.money(v, 2))
    display["Lines"] = display["count"]
    display["Rule"] = display["code"]
    display["What to do"] = display["action"]
    sh.table(display[["Rule", "Lines", "Margin at risk", "What to do"]])

st.markdown("## The pages")
pages = pd.DataFrame(
    [
        ("Recommendations", "One action per product, with the reason and what it is worth."),
        ("Data quality and reconciliation",
         "What the ERP extract actually contained, and whether the pipeline reconciles."),
        ("Cost and variance analysis",
         "Material, handling and overhead against a frozen standard; budget against actual."),
        ("Unit economics and break-even",
         "Contribution, markup against margin, cost to serve, and the fixed pool."),
        ("Cost-to-price calculator",
         "The original tool: reprice a book of items and push the result back to the ERP."),
        ("Market and competitor benchmarking",
         "Our price against the market, and how much of a cost move ever reaches it."),
        ("Price bands and willingness to pay",
         "What different customers pay for the same thing, and what they would have paid."),
        ("Profitability and segmentation",
         "Nine cuts of the book, down to operating profit after allocated fixed cost."),
        ("Price waterfall", "List to pocket, and which deduction costs the most."),
        ("Margin bridge", "Price, cost, volume, mix, launches and losses - summing exactly."),
        ("Pricing simulator", "Best, base and worst, and which assumption actually matters."),
        ("Elasticity and optimal price",
         "How much volume moves when price does, and the volume a cut has to find."),
        ("Forecast against actual",
         "Six methods, backtested, and the one that won on data it never saw."),
        ("Bundles and ladders", "Bundle economics judged on incremental margin."),
        ("Deal guardrails", "Floor, target and stretch, and who signs for the gap."),
        ("Promotions and price-list operations",
         "Did the promotion pay, and how the price list is actually being maintained."),
    ],
    columns=["Page", "What it answers"],
)
sh.table(pages)

sh.footer()
