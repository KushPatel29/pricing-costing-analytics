"""Standard-costing variances, budget against actual, and overhead absorption."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from app import shared as sh
from pricing.variance import purchase_price_variance, yield_variance

sh.page()

st.title("Cost variance")
sh.lede(
    "Standard cost is frozen at the start of each fiscal year; actual cost keeps "
    "moving with the input market. The gap is the variance, and every number on this "
    "page is signed the same way: positive is unfavourable. That one convention is "
    "what makes a variance report readable by someone who did not build it - a pack "
    "where half the columns are good when big gets read backwards by everyone."
)

detail = sh.load("cost_variance_detail")
long = sh.load("cost_variance_long")
budget = sh.load("budget_variance")
overhead = sh.load("overhead_variance")

fiscal_years = sorted(detail["fiscal_year"].unique())
chosen_fy = st.radio("Fiscal year", ["All"] + [f"FY{y}" for y in fiscal_years],
                     index=len(fiscal_years), horizontal=True)
if chosen_fy != "All":
    year = int(chosen_fy[2:])
    detail = detail[detail["fiscal_year"] == year]
    long = long[long["fiscal_year"] == year]
    budget = budget[budget["fiscal_year"] == year]

ppv = float(detail["purchase_price_variance"].sum())
yv = float(detail["yield_variance"].sum())
lrv = float(detail["labour_rate_variance"].sum())
lev = float(detail["labour_efficiency_variance"].sum())
total = ppv + yv + lrv + lev
standard_value = float(detail["standard_cost_value"].sum())


def _delta(value: float) -> str:
    return "Unfavourable" if value > 0 else "Favourable"


sh.kpis(
    [
        ("Purchase price", sh.money(ppv, 2), _delta(ppv), "off"),
        ("Yield", sh.money(yv, 2), _delta(yv), "off"),
        ("Labour", sh.money(lrv + lev, 2), _delta(lrv + lev), "off"),
        ("Total against standard", sh.money(total, 2),
         f"{total / standard_value:+.2%} of standard cost" if standard_value else None,
         "off"),
    ]
)

# --- Trend ---------------------------------------------------------------
st.markdown("## Variance through the year")
sh.caption(
    "Purchase price variance resets every July, because that is when the standard is "
    "re-struck. A PPV chart that trends smoothly across a year boundary is measuring "
    "something other than a standard-costing variance."
)
monthly = (
    long.groupby(["month", "variance_type"], as_index=False)["variance"].sum()
    .pivot_table(index="month", columns="variance_type", values="variance")
    .reset_index()
    .fillna(0.0)
)
present = [c for c in ["Purchase price", "Yield", "Labour rate", "Labour efficiency"]
           if c in monthly.columns]
fig = sh.lines(monthly, "month", {c: c for c in present}, hover_fmt=",.0f")
sh.reference_line(fig, y=0, label="On standard")
sh.show(fig, 360, showlegend=True, y_title="Variance ($) - positive is unfavourable")

# --- Where it sits -------------------------------------------------------
st.markdown("## Where the variance sits")
left, right = st.columns(2)

with left:
    by_category = (
        long.groupby(["category", "variance_type"], as_index=False)["variance"].sum()
    )
    import plotly.graph_objects as go

    fig = go.Figure()
    for slot, kind in enumerate(present):
        subset = by_category[by_category["variance_type"] == kind]
        fig.add_trace(
            go.Bar(
                x=subset["category"], y=subset["variance"], name=kind,
                marker=dict(color=sh.SERIES[slot], line=dict(width=2, color=sh.SURFACE),
                            cornerradius=4),
                hovertemplate=f"{kind}<br>%{{x}}: %{{y:$,.0f}}<extra></extra>",
            )
        )
    fig.update_layout(barmode="relative")
    sh.show(fig, 340, showlegend=True, y_title="Variance ($)")
    sh.caption("Stacked, so the bar height is the category's total against standard.")

with right:
    st.markdown("### Yield: what the floor delivered against standard")
    sh.caption(
        "Standard says a unit shipped needs one over the sellable rate received. "
        "Anything used past that is yield loss, valued at standard price so a bad "
        "buying month does not show up here as a bad receiving month."
    )
    yields = (
        detail.groupby("category", as_index=False)
        .agg(standard_sellable_rate=("standard_sellable_rate", "mean"),
             actual_recovery=("actual_recovery", "mean"),
             yield_variance=("yield_variance", "sum"))
    )
    yields["gap"] = yields["actual_recovery"] - yields["standard_sellable_rate"]
    display = yields.copy()
    display["Category"] = display["category"]
    display["Standard"] = display["standard_sellable_rate"].map(sh.pct)
    display["Actual"] = display["actual_recovery"].map(sh.pct)
    display["Gap"] = display["gap"].map(lambda v: f"{v:+.2%}")
    display["Variance"] = display["yield_variance"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Category", "Standard", "Actual", "Gap", "Variance"]])

# --- Worst offenders -----------------------------------------------------
st.markdown("## The lines to investigate")
sh.caption(
    "Ranked by total variance against standard. The split matters more than the total: "
    "purchase price is a conversation with buying, yield is a conversation with the "
    "floor, and they are rarely the same conversation."
)
worst = (
    detail.groupby(["product_id", "description", "category", "supplier"], as_index=False)
    .agg(purchase_price_variance=("purchase_price_variance", "sum"),
         yield_variance=("yield_variance", "sum"),
         labour=("labour_rate_variance", "sum"),
         total_variance=("total_variance", "sum"),
         purchased_units=("purchased_units", "sum"))
    .sort_values("total_variance", ascending=False)
)
display = worst.head(30).copy()
display["Product"] = display["description"]
display["Supplier"] = display["supplier"]
display["Purchase price"] = display["purchase_price_variance"].map(lambda v: sh.money(v, 2))
display["Yield"] = display["yield_variance"].map(lambda v: sh.money(v, 2))
display["Labour"] = display["labour"].map(lambda v: sh.money(v, 2))
display["Total"] = display["total_variance"].map(lambda v: sh.money(v, 2))
display["Volume"] = display["purchased_units"].map(lambda v: f"{v:,.0f} units")
display["Category"] = display["category"]
sh.table(display[["Product", "Category", "Supplier", "Purchase price", "Yield",
                  "Labour", "Total", "Volume"]], height=420)

# --- Budget --------------------------------------------------------------
st.markdown("## Budget against actual")
sh.lede(
    "Revenue over budget is favourable; cost over budget is not. The sign is "
    "interpreted per line rather than left to the reader, because colouring the "
    "expense rows the wrong way is the single most common defect in a hand-built "
    "budget pack."
)

budget_left, budget_right = st.columns([3, 2])
with budget_left:
    by_month = (
        budget.groupby("month", as_index=False)
        .agg(budget_margin=("budget_margin", "sum"), actual_margin=("actual_margin", "sum"))
    )
    fig = sh.lines(by_month, "month",
                   {"budget_margin": "Budget margin", "actual_margin": "Actual margin"},
                   hover_fmt="$,.0f")
    sh.show(fig, 330, showlegend=True, y_title="Gross margin ($)")
with budget_right:
    by_category = (
        budget.groupby("category", as_index=False)
        .agg(budget_revenue=("budget_revenue", "sum"),
             actual_revenue=("actual_revenue", "sum"),
             budget_margin=("budget_margin", "sum"),
             actual_margin=("actual_margin", "sum"))
    )
    by_category["revenue_var"] = by_category["actual_revenue"] - by_category["budget_revenue"]
    by_category["margin_var"] = by_category["actual_margin"] - by_category["budget_margin"]
    display = by_category.copy()
    display["Category"] = display["category"]
    display["Revenue vs budget"] = display["revenue_var"].map(lambda v: sh.money(v, 2))
    display["Margin vs budget"] = display["margin_var"].map(lambda v: sh.money(v, 2))
    display["Verdict"] = display["margin_var"].map(
        lambda v: "Favourable" if v >= 0 else "Unfavourable")
    sh.table(display[["Category", "Revenue vs budget", "Margin vs budget", "Verdict"]])

# --- Overhead ------------------------------------------------------------
st.markdown("## Overhead: spending against absorption")
sh.lede(
    "Two different failures wear the same name. Spending variance is overspending the "
    "pool at the activity actually run. Volume variance is under-absorption from "
    "running below the volume the rate was set on - nobody overspent, the plant just "
    "ran light, and the unabsorbed pool lands in cost of sales anyway. It is the "
    "variance most often mistaken for the other one."
)
oh_left, oh_right = st.columns([3, 2])
with oh_left:
    by_pool = (
        overhead.groupby("cost_pool", as_index=False)
        .agg(spending_variance=("spending_variance", "sum"),
             volume_variance=("volume_variance", "sum"))
    )
    import plotly.graph_objects as go

    fig = go.Figure()
    for slot, (column, label) in enumerate(
        [("spending_variance", "Spending"), ("volume_variance", "Volume")]
    ):
        fig.add_trace(
            go.Bar(x=by_pool["cost_pool"], y=by_pool[column], name=label,
                   marker=dict(color=sh.SERIES[slot], line=dict(width=2, color=sh.SURFACE),
                               cornerradius=4),
                   hovertemplate=f"{label}<br>%{{x}}: %{{y:$,.0f}}<extra></extra>")
        )
    fig.update_layout(barmode="group")
    sh.reference_line(fig, y=0, label="")
    sh.show(fig, 330, showlegend=True, y_title="Variance ($)")
with oh_right:
    display = by_pool.copy()
    display["Cost pool"] = display["cost_pool"]
    display["Spending"] = display["spending_variance"].map(lambda v: sh.money(v, 2))
    display["Volume"] = display["volume_variance"].map(lambda v: sh.money(v, 2))
    display["Total"] = (display["spending_variance"] + display["volume_variance"]).map(
        lambda v: sh.money(v, 2))
    sh.table(display[["Cost pool", "Spending", "Volume", "Total"]])

# --- Worked example ------------------------------------------------------
with st.expander("Work one variance by hand"):
    sh.caption(
        "The same functions the table above uses, on inputs you choose. Useful for "
        "checking that the sign convention is what you think it is."
    )
    columns = st.columns(4)
    with columns[0]:
        std_price = st.number_input("Standard price $/unit", 0.5, 60.0, 8.40, 0.10)
    with columns[1]:
        act_price = st.number_input("Actual price $/unit", 0.5, 60.0, 8.95, 0.10)
    with columns[2]:
        purchased = st.number_input("Units received", 10.0, 500_000.0, 12_000.0, 100.0)
    with columns[3]:
        std_recovery = st.slider("Standard sellable rate", 0.40, 0.99, 0.92, 0.01)

    output_units = st.slider("Units sellable", 100.0, float(purchased), float(purchased) * 0.64,
                          100.0)
    ppv_result = purchase_price_variance(
        actual_price=act_price, standard_price=std_price, actual_quantity=purchased
    )
    yv_result = yield_variance(
        output_units=output_units, actual_input_units=purchased,
        standard_sellable_rate=std_recovery, standard_input_price=std_price,
    )
    sh.kpis(
        [
            ("Purchase price variance", sh.money(ppv_result["variance"], 2),
             ppv_result["verdict"], "off"),
            ("Standard input allowed", f"{yv_result['standard_input_units']:,.0f} units",
             f"{yv_result['excess_units']:+,.0f} units used", "off"),
            ("Actual sellable rate", sh.pct(yv_result["actual_recovery"]),
             f"standard {yv_result['standard_sellable_rate']:.1%}", "off"),
            ("Yield variance", sh.money(yv_result["variance"], 2),
             yv_result["verdict"], "off"),
        ]
    )

sh.footer()
