"""How much volume moves when price does, and what a price change is worth."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh
from pricing.elasticity import break_even_volume_change, price_change_impact

sh.page()

st.title("Elasticity and optimal price")
sh.lede(
    "Elasticity is the percentage change in volume for a one percent change in price. "
    "It is estimated here from the log of one against the log of the other, on list "
    "price rather than realised price, with promotional months excluded, seasonality "
    "divided out and a time trend held constant. Each of those four is a correction for "
    "a specific confound, and each one moves the answer - fitting on realised price "
    "instead of list, without excluding promotions, prints elasticities near minus four "
    "on products whose true response is under minus one."
)

estimates = sh.load("elasticity_estimates")
curves = sh.load("price_response_curve")
hurdles = sh.load("price_change_hurdles")

categories = estimates[estimates["scope"] == "Category"].sort_values("elasticity")

# --- The estimates -------------------------------------------------------
st.markdown("## What the data says, by category")

left, right = st.columns([3, 2])
with left:
    plot = categories.copy()
    plot["label"] = plot["elasticity"].map(lambda v: f"{v:.2f}")
    fig = sh.bar(plot, "member", "elasticity", horizontal=True, text="label",
                 colour=sh.SERIES[0])
    sh.reference_line(fig, x=-1.0, label="Unit elastic")
    sh.show(fig, 330, x_title="Own-price elasticity (negative is normal)")
with right:
    display = categories.copy()
    display["Category"] = display["member"]
    display["Elasticity"] = display["elasticity"].map(lambda v: f"{v:.2f}")
    display["R2"] = display["r_squared"].map(lambda v: f"{v:.3f}")
    display["Price variation"] = display["price_cv"].map(sh.pct)
    display["Months"] = display["observations"]
    sh.table(display[["Category", "Elasticity", "R2", "Price variation", "Months"]])
    sh.caption(
        "R-squared is low across the board and that is honest, not a bug: monthly "
        "volume is driven mostly by season, lifecycle and who happened to order. "
        "The price coefficient is still identified because prices are reviewed on a "
        "staggered quarterly cycle rather than all at once."
    )

st.markdown("### The profit-maximising price, and when not to believe it")
sh.caption(
    "Under constant elasticity the optimum is cost times e/(e+1). At an elasticity of "
    "minus two that is twice cost; at minus 1.1 it is eleven times cost, which is "
    "arithmetic rather than advice - the elasticity was fitted around today's price and "
    "says nothing about demand at eleven times it. Anything implying a markup over five "
    "is reported and marked not actionable."
)
optimum = categories.copy()
optimum["Category"] = optimum["member"]
optimum["Elasticity"] = optimum["elasticity"].map(lambda v: f"{v:.2f}")
optimum["Avg pocket price"] = optimum["avg_pocket_price"].map(sh.dollars)
optimum["Avg unit cost"] = optimum["avg_unit_cost"].map(sh.dollars)
optimum["Implied markup"] = optimum["implied_markup"].map(
    lambda v: "n/a" if pd.isna(v) else f"{v:.1f}x"
)
optimum["Optimal price"] = optimum.apply(
    lambda r: sh.dollars(r["optimal_price"]) if r["optimal_actionable"] else "-", axis=1
)
optimum["Verdict"] = optimum["optimal_note"].map(
    lambda note: "Actionable" if note == "ok" else note
)
sh.table(
    optimum[["Category", "Elasticity", "Avg pocket price", "Avg unit cost",
             "Implied markup", "Optimal price", "Verdict"]]
)

# --- Response curve ------------------------------------------------------
st.markdown("## What happens if we move the price")
category = st.selectbox("Category", categories["member"].tolist())
row = categories[categories["member"] == category].iloc[0]

curve = curves[curves["category"] == category]
if curve.empty:
    st.info("No usable elasticity for this category, so no response curve.")
else:
    curve_left, curve_right = st.columns([3, 2])
    with curve_left:
        plot = curve.copy()
        plot["revenue_indexed"] = plot["revenue"] / plot.loc[
            plot["is_current"] == 1, "revenue"].iloc[0] * 100
        plot["profit_indexed"] = plot["profit"] / plot.loc[
            plot["is_current"] == 1, "profit"].iloc[0] * 100
        plot["volume_indexed"] = plot["quantity"] / plot.loc[
            plot["is_current"] == 1, "quantity"].iloc[0] * 100
        fig = sh.lines(
            plot, "price_change_pct",
            {"volume_indexed": "Volume", "revenue_indexed": "Revenue",
             "profit_indexed": "Profit"},
            hover_fmt=".1f",
        )
        # Indexed to 100 at today's price so three measures of different scale
        # share one axis. Two y-scales would let any pair be made to cross
        # wherever the author wanted, and the reader could not tell.
        sh.reference_line(fig, y=100, label="Today")
        best = plot.loc[plot["is_profit_max"] == 1]
        if not best.empty:
            sh.reference_line(fig, x=float(best["price_change_pct"].iloc[0]),
                              label="Profit max", colour=sh.SERIES[2])
        fig.update_xaxes(tickformat=".0%")
        sh.show(fig, 380, showlegend=True, y_title="Indexed to 100 at today's price",
                x_title="Price change")
    with curve_right:
        st.markdown("### The volume a cut has to find")
        sh.caption(
            "Cutting price by d on a contribution margin of m needs volume up by "
            "d/(m-d) just to stand still. Five points off a thirty-point margin needs "
            "twenty percent more volume; off a fifteen-point margin it needs fifty."
        )
        hurdle = hurdles[hurdles["category"] == category].copy()
        hurdle["Price change"] = hurdle["price_change_pct"].map(lambda v: f"{v:+.1%}")
        hurdle["Break-even volume"] = hurdle["break_even_volume_pct"].map(
            lambda v: "no price covers cost" if pd.isna(v) else f"{v:+.1%}"
        )
        hurdle["Expected volume"] = hurdle["expected_volume_pct"].map(lambda v: f"{v:+.1%}")
        hurdle["Margin change"] = hurdle["expected_margin_change"].map(
            lambda v: sh.money(v, 2))
        sh.table(
            hurdle[["Price change", "Break-even volume", "Expected volume",
                    "Margin change", "verdict"]].rename(columns={"verdict": "Verdict"}),
            height=330,
        )

# --- Interactive what-if -------------------------------------------------
st.markdown("## Scenario: move this category's price")
sh.caption(
    "The elasticity below starts at the estimate and is yours to override. That is "
    "deliberate: an estimate with an r-squared of 0.14 is a starting point for a "
    "conversation with sales, not a number to price off unchallenged."
)

controls = st.columns(3)
with controls[0]:
    change = st.slider("Price change", -0.20, 0.20, 0.03, 0.005, format="%+.1f%%")
with controls[1]:
    assumed = st.slider("Elasticity", -4.0, -0.2, float(round(row["elasticity"], 2)), 0.05)
with controls[2]:
    st.metric("Estimated from data", f"{row['elasticity']:.2f}",
              f"R2 {row['r_squared']:.3f}")

impact = price_change_impact(
    base_quantity=float(row["volume_units"]),
    base_price=float(row["avg_pocket_price"]),
    unit_cost=float(row["avg_unit_cost"]),
    price_change_pct=change,
    elasticity=assumed,
)
contribution = (row["avg_pocket_price"] - row["avg_unit_cost"]) / row["avg_pocket_price"]
hurdle_value = break_even_volume_change(change, contribution)

sh.kpis(
    [
        ("New price", sh.dollars(impact["new_price"]), f"{change:+.1%}", "off"),
        ("Volume response", f"{impact['volume_change_pct']:+.1%}",
         f"break-even {hurdle_value:+.1%}" if pd.notna(hurdle_value) else None, "off"),
        ("Revenue", sh.money(impact["revenue_after"], 1),
         f"{impact['revenue_change_pct']:+.1%}"),
        ("Margin", sh.money(impact["margin_after"], 1),
         f"{impact['margin_change_pct']:+.1%}"),
    ]
)

if change < 0:
    if impact["volume_change_pct"] > hurdle_value:
        st.success(
            f"At an elasticity of {assumed:.2f} the cut brings "
            f"{impact['volume_change_pct']:+.1%} volume against a break-even of "
            f"{hurdle_value:+.1%}. It pays - provided the elasticity holds and "
            "competitors do not follow."
        )
    else:
        st.warning(
            f"The cut needs {hurdle_value:+.1%} volume and the elasticity implies "
            f"{impact['volume_change_pct']:+.1%}. It does not pay for itself."
        )
elif change > 0:
    st.info(
        f"A rise of {change:.1%} can afford to lose {abs(hurdle_value):.1%} of volume "
        f"before contribution falls. The elasticity implies losing "
        f"{abs(impact['volume_change_pct']):.1%}."
    )

# --- Product-level -------------------------------------------------------
st.markdown("## Product-level estimates, and why to distrust them")
sh.caption(
    "The same fit run on each product's own 36 months. Most come back usable by the "
    "letter of the test and are still not worth pricing off - 36 observations against "
    "a price that moves four times a year cannot pin an elasticity. The correlation "
    "between the product-level estimate and the category it sits in is weak, which is "
    "the finding: price the category, sanity-check the product."
)
products = estimates[estimates["scope"] == "Product"].copy()
# `member` carries a category name on some rows and a product code on others,
# so it arrives as text while dim_product[product_id] arrives as an integer.
# Both sides are cast rather than one, so the join cannot silently match
# nothing if either source changes dtype later.
products["member"] = products["member"].astype(str)
dimension = sh.load("dim_product")[["product_id", "description", "category"]].rename(
    columns={"product_id": "member", "category": "product_category"})
dimension["member"] = dimension["member"].astype(str)
products = products.merge(dimension, on="member", how="left", suffixes=("", "_dim"))
show_only = st.checkbox("Only those with an actionable optimum", value=True)
if show_only:
    products = products[products["optimal_actionable"]]
products = products.sort_values("pocket_revenue", ascending=False).head(60)
display = products.copy()
display["Product"] = display["description"]
display["Category"] = display["product_category"]
display["Elasticity"] = display["elasticity"].map(lambda v: f"{v:.2f}")
display["R2"] = display["r_squared"].map(lambda v: f"{v:.3f}")
display["Pocket revenue"] = display["pocket_revenue"].map(lambda v: sh.money(v, 1))
display["Price vs optimal"] = display["price_vs_optimal_pct"].map(
    lambda v: "n/a" if pd.isna(v) else f"{v:+.0%}"
)
sh.table(display[["Product", "Category", "Elasticity", "R2", "Pocket revenue",
                  "Price vs optimal"]], height=400)

sh.footer()
