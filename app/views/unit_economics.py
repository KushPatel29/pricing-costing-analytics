"""Contribution, markup against margin, cost to serve, and break-even."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh
from pricing.unit_economics import (
    break_even,
    break_even_curve,
    margin_to_markup,
    markup_to_margin,
    operating_leverage,
)

sh.page()

st.title("Unit economics and break-even")
sh.lede(
    "Everything on this page turns on one distinction that costing systems are built "
    "to obscure: absorbed cost is not variable cost. A standard cost carries a share "
    "of the fixed pool, which is right for valuing inventory and wrong for every "
    "decision about the next unit. Price off it and you refuse business that would "
    "have paid for itself; break even on it and you are wrong by the whole fixed pool, "
    "in the direction that flatters you."
)

economics = sh.load("unit_economics")
portfolio = sh.load("break_even_portfolio")
products = sh.load("break_even_products")
curve = sh.load("break_even_curve")
markup_table = sh.load("markup_vs_margin")

book = portfolio[portfolio["scope"] == "Whole book"].iloc[0]

sh.kpis(
    [
        ("Contribution margin", sh.pct(book["contribution_ratio"]),
         "after every variable cost", "off"),
        ("Fixed cost", sh.money(book["fixed_costs"], 2), "per year", "off"),
        ("Break-even revenue", sh.money(book["break_even_revenue"], 2),
         f"{book['break_even_revenue'] / book['revenue']:.0%} of actual", "off"),
        ("Margin of safety", sh.pct(book["margin_of_safety"]),
         "how far revenue can fall", "off"),
    ]
)

# --- The break-even chart -----------------------------------------------
st.markdown("## The break-even chart")
sh.caption(
    "Four series, all currency, so they legitimately share one axis. Fixed cost is "
    "flat by definition; total cost is fixed plus variable; the crossing with revenue "
    "is break-even. The distance from there to actual volume is the margin of safety, "
    "and it is the number to quote alongside break-even -- break-even alone says "
    "nothing about how much room there is."
)

left, right = st.columns([3, 2])
with left:
    fig = sh.lines(
        curve, "quantity",
        {"revenue": "Revenue", "total_cost": "Total cost",
         "variable_cost": "Variable cost", "fixed_cost": "Fixed cost"},
        hover_fmt="$,.0f",
    )
    crossing = curve[curve["is_break_even"] == 1]
    if not crossing.empty:
        sh.reference_line(fig, x=float(crossing["quantity"].iloc[0]),
                          label="Break-even", colour=sh.SERIES[1])
    sh.reference_line(fig, x=float(book["revenue"] / (book["revenue"] / curve["quantity"].max()
                                                      * 1.35) * 1.0)
                      if False else float(economics["volume_units"].sum()),
                      label="Actual volume", colour=sh.INK_MUTED)
    sh.show(fig, 400, showlegend=True, y_title="Annual dollars", x_title="Units sold")
with right:
    st.markdown("### By category")
    display = portfolio[portfolio["scope"] == "Category"].copy()
    display["Category"] = display["member"]
    display["Contribution"] = display["contribution_ratio"].map(sh.pct)
    display["Break-even"] = display["break_even_revenue"].map(lambda v: sh.money(v, 2))
    display["Safety"] = display["margin_of_safety"].map(sh.pct)
    display["Leverage"] = display["operating_leverage"].map(
        lambda v: "n/a" if pd.isna(v) else f"{v:.2f}x")
    sh.table(display[["Category", "Contribution", "Break-even", "Safety", "Leverage"]])
    sh.caption(
        "Operating leverage is contribution over operating profit: at 4x, a ten "
        "percent fall in volume takes forty percent of the profit. It is undefined "
        "at break-even, where profit is zero -- which is not a bug, it is the point."
    )

# --- Markup against margin ----------------------------------------------
st.markdown("## Markup is not margin")
sh.lede(
    "The single most expensive arithmetic confusion in pricing. A 25% markup is a 20% "
    "margin. A 25% margin is a 33.3% markup. Quoting one where the other is meant "
    "underprices every line by a few points, forever, and it never announces itself "
    "because both numbers are plausible and the tool keeps working."
)

markup_left, markup_right = st.columns([3, 2])
with markup_left:
    fig = sh.lines(
        markup_table, "rate",
        {"markup_needed_for_that_margin": "Markup needed for that margin",
         "margin_if_read_as_markup": "Margin you get if you apply it as markup"},
        hover_fmt=".1%",
    )
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(tickformat=".0%")
    sh.show(fig, 320, showlegend=True, y_title="", x_title="The rate somebody said")
with markup_right:
    display = markup_table.copy()
    display["Rate quoted"] = display["rate"].map(sh.pct)
    display["As a margin"] = display["margin_if_read_as_margin"].map(sh.pct)
    display["Markup that achieves it"] = display[
        "markup_needed_for_that_margin"].map(sh.pct)
    display["If applied as markup"] = display["margin_if_read_as_markup"].map(sh.pct)
    display["Points lost"] = display["gap"].map(lambda v: f"{v:.1%}")
    sh.table(display[["Rate quoted", "Markup that achieves it",
                      "If applied as markup", "Points lost"]])

converter = st.columns(3)
with converter[0]:
    rate = st.slider("A rate somebody quoted", 0.02, 0.60, 0.30, 0.01)
with converter[1]:
    st.metric("Read as a margin, the markup needed is",
              sh.pct(margin_to_markup(rate)))
with converter[2]:
    st.metric("Read as a markup, the margin you get is",
              sh.pct(markup_to_margin(rate)),
              f"{markup_to_margin(rate) - rate:.1%} vs what was said", "off")

# --- Per product ---------------------------------------------------------
st.markdown("## Where the contribution is")
sh.lede(
    "Cost to serve is separated from cost of goods on purpose. It is a logistics "
    "decision -- drop size, frequency, zone -- and folding it into product cost hides "
    "the account that is unprofitable because it orders a dozen units at a time rather "
    "than because the product is priced wrong."
)

filtered = sh.sidebar_filters(economics, {"category": "Category", "brand_tier": "Brand tier"})

chart_left, chart_right = st.columns([3, 2])
with chart_left:
    plot = filtered[filtered["volume_units"] > 0].copy()
    fig = sh.scatter(plot, "volume_units", "contribution_pct", size="pocket_revenue",
                     hover="description")
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(type="log")
    sh.reference_line(fig, y=float(book["contribution_ratio"]), label="Book average")
    sh.reference_line(fig, y=0, label="Covers variable cost", colour=sh.CRITICAL)
    sh.show(fig, 380, y_title="Contribution margin",
            x_title="Volume, log scale (units)")
    sh.caption(
        "Anything under the red line loses money on every additional unit, so no "
        "volume fixes it -- the price or the variable cost has to move first."
    )
with chart_right:
    st.markdown("### Cost to serve, ranked")
    worst = filtered.nlargest(15, "cost_to_serve_pct").copy()
    worst["Product"] = worst["description"]
    worst["Cost to serve"] = worst["cost_to_serve_pct"].map(sh.pct)
    worst["Contribution"] = worst["contribution_pct"].map(sh.pct)
    worst["Volume"] = worst["volume_units"].map(lambda v: f"{v:,.0f}")
    sh.table(worst[["Product", "Cost to serve", "Contribution", "Volume"]], height=380)

st.markdown("### Every product's break-even")
display = products.merge(
    economics[["product_id", "contribution_pct", "markup_pct"]], on="product_id", how="left")
display = display[display["product_id"].isin(filtered["product_id"])]
display = display.sort_values("margin_of_safety")
out = display.copy()
out["Product"] = out["description"]
out["Contribution / unit"] = out["contribution_per_unit"].map(sh.dollars)
out["Contribution %"] = out["contribution_ratio"].map(sh.pct)
out["Markup"] = out["markup_pct"].map(lambda v: "n/a" if pd.isna(v) else f"{v:.1%}")
out["Break-even units"] = out["break_even_units"].map(
    lambda v: "no volume covers it" if pd.isna(v) else f"{v:,.0f}")
out["Actual units"] = out["actual_volume_units"].map(lambda v: f"{v:,.0f}")
out["Margin of safety"] = out["margin_of_safety"].map(
    lambda v: "n/a" if pd.isna(v) else f"{v:.0%}")
out["Category"] = out["category"]
sh.table(out[["Product", "Category", "Contribution / unit", "Contribution %", "Markup",
              "Break-even units", "Actual units", "Margin of safety"]], height=420)

# --- Calculator ----------------------------------------------------------
st.markdown("## Work a break-even")
sh.caption(
    "Fixed cost, price and variable cost. Nothing else -- and in particular not an "
    "absorbed standard cost, which is what makes most break-even numbers wrong."
)

inputs = st.columns(4)
with inputs[0]:
    fixed = st.number_input("Fixed cost ($)", 1_000.0, 50_000_000.0, 250_000.0, 5_000.0)
with inputs[1]:
    price = st.number_input("Price per unit ($)", 0.5, 500.0, 28.00, 0.50)
with inputs[2]:
    variable = st.number_input("Variable cost per unit ($)", 0.1, 500.0, 19.50, 0.50)
with inputs[3]:
    actual = st.number_input("Actual units", 0.0, 5_000_000.0, 40_000.0, 1_000.0)

target = st.slider("Target profit ($)", 0.0, 2_000_000.0, 0.0, 10_000.0)
result = break_even(fixed_costs=fixed, price=price, variable_cost=variable,
                    actual_quantity=actual, target_profit=target)

if not result["exists"]:
    st.error(result["reason"])
else:
    leverage = operating_leverage(
        result["contribution_per_unit"] * actual, result["operating_profit"])
    sh.kpis(
        [
            ("Contribution / unit", sh.dollars(result["contribution_per_unit"]),
             sh.pct(result["contribution_ratio"]), "off"),
            ("Break-even units", f"{result['break_even_units']:,.0f}",
             sh.money(result["break_even_revenue"], 2), "off"),
            ("Margin of safety", sh.pct(result["margin_of_safety"]),
             f"{result['margin_of_safety_units']:+,.0f} units", "off"),
            ("Operating profit", sh.money(result["operating_profit"], 2),
             f"leverage {leverage['leverage']:.2f}x" if leverage["defined"]
             else leverage["reason"], "off"),
        ]
    )
    points = pd.DataFrame(
        break_even_curve(fixed_costs=fixed, price=price, variable_cost=variable,
                         max_quantity=max(actual, result["break_even_units"]) * 1.4)
    )
    fig = sh.lines(points, "quantity",
                   {"revenue": "Revenue", "total_cost": "Total cost",
                    "fixed_cost": "Fixed cost"}, hover_fmt="$,.0f")
    sh.reference_line(fig, x=result["break_even_units"], label="Break-even",
                      colour=sh.SERIES[1])
    sh.reference_line(fig, x=actual, label="Actual", colour=sh.INK_MUTED)
    sh.show(fig, 320, showlegend=True, y_title="Dollars", x_title="Units")

sh.footer()
