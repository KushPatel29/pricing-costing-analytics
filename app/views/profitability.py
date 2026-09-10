"""Profit by product, customer, region, channel and salesperson."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import shared as sh

sh.page()

st.title("Profitability and segmentation")
sh.lede(
    "Eleven cuts of the same book, down to operating profit after allocated fixed cost. "
    "The allocation is by revenue share and the column says so: every allocation is "
    "arbitrary, and the argument is always about which arbitrary one was used rather "
    "than about the arithmetic."
)

profit = sh.load("profitability")
heat = sh.load("profit_heatmap")
matrix = sh.load("discount_margin_matrix")
scatter = sh.load("price_vs_volume")

DIMENSION_LABELS = {
    "category": "Product category", "sub_category": "Sub-category",
    "brand_tier": "Brand tier", "segment": "Customer segment", "channel": "Channel",
    "region": "Region", "tier": "Customer volume tier", "salesperson": "Salesperson",
    "price_list": "Price list",
}

total_revenue = float(profit[profit["dimension"] == "category"]["pocket_revenue"].sum())
total_margin = float(profit[profit["dimension"] == "category"]["gross_margin"].sum())
total_fixed = float(
    profit[profit["dimension"] == "category"]["fixed_allocated_by_revenue"].sum())

sh.kpis(
    [
        ("Pocket revenue", sh.money(total_revenue, 2), "latest fiscal year", "off"),
        ("Gross margin", sh.money(total_margin, 2),
         sh.pct(total_margin / total_revenue), "off"),
        ("Fixed cost allocated", sh.money(total_fixed, 2), "by revenue share", "off"),
        ("Operating profit", sh.money(total_margin - total_fixed, 2),
         sh.pct((total_margin - total_fixed) / total_revenue), "off"),
    ]
)

# --- One cut at a time ---------------------------------------------------
st.markdown("## Pick a cut")
dimension = st.selectbox("Cut by", list(DIMENSION_LABELS),
                         format_func=DIMENSION_LABELS.get)
cut = profit[profit["dimension"] == dimension].sort_values("pocket_revenue",
                                                           ascending=False)

cut_left, cut_right = st.columns([3, 2])
with cut_left:
    plot = cut.head(18).sort_values("gross_margin_pct")
    plot["label"] = plot["gross_margin_pct"].map(sh.pct)
    fig = sh.bar(plot, "member", "gross_margin_pct", horizontal=True, text="label",
                 colour=sh.SERIES[0])
    fig.update_xaxes(tickformat=".0%")
    sh.reference_line(fig, x=total_margin / total_revenue, label="Book average")
    sh.show(fig, 420, x_title="Gross margin")
with cut_right:
    st.markdown("### Revenue against margin")
    sh.caption(
        "Size is revenue. Bottom-right is the quadrant that pays for the analysis: "
        "large and thin, where a point of price is worth more than anything else on "
        "the page."
    )
    fig = sh.scatter(cut, "pocket_revenue", "gross_margin_pct", size="pocket_revenue",
                     hover="member")
    fig.update_yaxes(tickformat=".0%")
    sh.reference_line(fig, y=total_margin / total_revenue, label="Book average")
    sh.show(fig, 420, y_title="Gross margin", x_title="Pocket revenue")

display = cut.copy()
display[DIMENSION_LABELS[dimension]] = display["member"]
display["Revenue"] = display["pocket_revenue"].map(lambda v: sh.money(v, 2))
display["Share"] = display["revenue_share"].map(sh.pct)
display["Gross margin"] = display["gross_margin"].map(lambda v: sh.money(v, 2))
display["Margin %"] = display["gross_margin_pct"].map(sh.pct)
display["Leakage"] = display["leakage_pct"].map(sh.pct)
display["Cost to serve"] = display["cost_to_serve"].map(lambda v: sh.money(v, 2))
display["Operating profit"] = display["operating_profit"].map(lambda v: sh.money(v, 2))
display["Operating %"] = display["operating_margin_pct"].map(sh.pct)
sh.table(display[[DIMENSION_LABELS[dimension], "Revenue", "Share", "Gross margin",
                  "Margin %", "Leakage", "Cost to serve", "Operating profit",
                  "Operating %"]], height=380)

if dimension == "salesperson":
    st.info(
        "**Why a salesperson dimension belongs in a pricing model.** A rep's appetite "
        "for discounting moves realised price without moving anything a product- or "
        "customer-level cut would show. Two reps working the same segment at the same "
        "volume can sit four margin points apart, and the only way to see it is to put "
        "them on the invoice line."
    )

# --- The heatmap ---------------------------------------------------------
st.markdown("## Segment against category")
sh.lede(
    "The cut that most often hides a loss inside two healthy totals: a segment that "
    "looks fine and a category that looks fine, with one cell between them that does "
    "not. Sequential colour, one hue, because margin here is a magnitude."
)

measure = st.radio("Colour by", ["Gross margin %", "Leakage %", "Gross margin $"],
                   horizontal=True)
column = {"Gross margin %": "gross_margin_pct", "Leakage %": "leakage_pct",
          "Gross margin $": "gross_margin"}[measure]

pivot = heat.pivot(index="segment", columns="category", values=column)
fmt = "$,.0f" if column == "gross_margin" else ".1%"
fig = go.Figure(go.Heatmap(
    z=pivot.values, x=list(pivot.columns), y=list(pivot.index),
    colorscale=[[i / (len(sh.SEQUENTIAL) - 1), c] for i, c in enumerate(sh.SEQUENTIAL)],
    reversescale=(column == "leakage_pct"),
    text=[[f"{v:{fmt}}" if pd.notna(v) else "" for v in row] for row in pivot.values],
    texttemplate="%{text}", textfont=dict(size=10),
    hovertemplate="%{y} / %{x}<br>" + measure + ": %{text}<extra></extra>",
    colorbar=dict(title="", thickness=12, outlinewidth=0),
))
sh.show(fig, 420, y_title="", x_title="")
sh.caption(
    "Every cell carries its number as well as its colour. Three of this palette's "
    "hues sit under 3:1 contrast on white, so a value that is only encoded as colour "
    "would be unreadable for some viewers -- the label is the relief, not decoration."
)

# --- Discount against margin --------------------------------------------
st.markdown("## Discount depth against realised margin")
sh.lede(
    "The cell that matters is deep discount and thin margin, and it is invisible in "
    "either average on its own. Every line in the latest year, banded both ways."
)

matrix_left, matrix_right = st.columns([3, 2])
with matrix_left:
    pivot = matrix.pivot(index="margin_band", columns="discount_band",
                        values="pocket_revenue").fillna(0)
    order_margin = ["< 0%", "0% to 10%", "10% to 20%", "20% to 30%",
                    "30% to 40%", "40%+"]
    order_discount = ["0% to 5%", "5% to 10%", "10% to 15%", "15% to 20%",
                      "20% to 30%", "30%+"]
    pivot = pivot.reindex(index=[m for m in order_margin if m in pivot.index],
                          columns=[d for d in order_discount if d in pivot.columns])
    fig = go.Figure(go.Heatmap(
        z=pivot.values, x=list(pivot.columns), y=list(pivot.index),
        colorscale=[[i / (len(sh.SEQUENTIAL) - 1), c]
                    for i, c in enumerate(sh.SEQUENTIAL)],
        text=[[sh.money(v, 1) if v else "" for v in row] for row in pivot.values],
        texttemplate="%{text}", textfont=dict(size=10),
        hovertemplate="Discount %{x}, margin %{y}<br>%{text}<extra></extra>",
        colorbar=dict(title="", thickness=12, outlinewidth=0),
    ))
    sh.show(fig, 380, y_title="Realised margin", x_title="Discount off list")
with matrix_right:
    worst = matrix[matrix["margin_band"].isin(["< 0%", "0% to 10%"])]
    st.markdown("### Deep discount, thin margin")
    display = worst.sort_values("pocket_revenue", ascending=False).copy()
    display["Discount"] = display["discount_band"]
    display["Margin"] = display["margin_band"]
    display["Revenue"] = display["pocket_revenue"].map(lambda v: sh.money(v, 2))
    display["Share"] = display["share_of_revenue"].map(sh.pct)
    display["Lines"] = display["lines"]
    sh.table(display[["Discount", "Margin", "Revenue", "Share", "Lines"]], height=340)

# --- Price against volume ------------------------------------------------
st.markdown("## Price against units sold")
sh.lede(
    "The raw demand relationship, one point per product-month. Promoted months are "
    "separated because pairing a price cut with a display and an end-cap is not the "
    "same experiment as changing the list price -- pooling the two prints elasticities "
    "near minus four on products whose real response is under minus one."
)

pick = st.columns(2)
with pick[0]:
    category = st.selectbox("Category", sorted(scatter["category"].dropna().unique()),
                            key="scatter_category")
with pick[1]:
    products = scatter[scatter["category"] == category]["description"].dropna().unique()
    product = st.selectbox("Product", ["All in category"] + sorted(products))

subset = scatter[scatter["category"] == category]
if product != "All in category":
    subset = subset[subset["description"] == product]

fig = sh.scatter(subset, "pocket_price", "volume_units", colour_by="promoted",
                 hover="description",
                 colour_map={"Base": sh.SERIES[0], "Promoted": sh.SERIES[1]})
fig.update_yaxes(type="log")
sh.show(fig, 400, showlegend=True, y_title="Units sold, log scale",
        x_title="Realised price per unit")
sh.caption(
    "Log scale on volume, because a demand curve is multiplicative -- on a linear "
    "axis the relationship looks like a hook rather than a line, and the slope that "
    "matters is the one in logs."
)

sh.footer()
