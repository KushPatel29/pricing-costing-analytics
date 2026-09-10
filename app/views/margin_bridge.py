"""Price, cost, volume, mix, launches and losses - and they sum exactly."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh
from pricing.variance import bridge_steps, margin_bridge, revenue_bridge

sh.page()

st.title("Margin bridge")
sh.lede(
    "Margin moved. How much of it was price, how much was input cost, how much was "
    "selling more, and how much was selling a different blend of the same things? "
    "This is where decompositions usually go wrong: the cross-terms have to be "
    "assigned to something, and one assigned by intuition leaves a residual that gets "
    "quietly labelled 'other'. The six effects here sum to the actual change to the "
    "cent, and the residual is shown so you can check."
)

steps = sh.load("margin_bridge_steps")
effects = sh.load("margin_bridge_effects")
revenue_effects = sh.load("revenue_bridge_effects")

comparisons = steps["comparison"].unique().tolist()
comparison = st.radio("Comparison", comparisons, index=len(comparisons) - 1,
                      horizontal=True)

measure = st.radio("Measure", ["Gross margin", "Revenue"], horizontal=True,
                   label_visibility="collapsed")

if measure == "Gross margin":
    chart_steps = steps[steps["comparison"] == comparison].sort_values("sort_order")
    chosen_effects = effects[effects["comparison"] == comparison]
    opening = float(chart_steps["amount"].iloc[0])
    closing = float(chart_steps["amount"].iloc[-1])
else:
    chosen_effects = revenue_effects[revenue_effects["comparison"] == comparison].copy()
    sales = sh.sales()
    prior_year, current_year = (int(comparison[2:6]), int(comparison[-4:]))
    opening = float(sales[sales["fiscal_year"] == prior_year]["pocket_revenue"].sum())
    closing = float(sales[sales["fiscal_year"] == current_year]["pocket_revenue"].sum())
    rows = [{"label": "Prior", "kind": "total", "amount": opening}]
    for row in chosen_effects.itertuples():
        rows.append({"label": row.effect, "kind": "increase" if row.amount >= 0
                     else "decrease", "amount": row.amount})
    rows.append({"label": "Current", "kind": "total", "amount": closing})
    chart_steps = pd.DataFrame(rows)

sh.kpis(
    [
        (f"{measure}, prior year", sh.money(opening, 2), None),
        (f"{measure}, current year", sh.money(closing, 2),
         f"{closing / opening - 1:+.1%}" if opening else None),
        ("Change", sh.money(closing - opening, 2), None),
        ("Residual", sh.money(
            float(chosen_effects[chosen_effects["effect"] == "Residual"]["amount"].sum())
            if "Residual" in set(chosen_effects["effect"]) else 0.0, 2),
         "float noise only", "off"),
    ]
)

plot_steps = chart_steps[chart_steps["label"] != "Residual"]
sh.show(sh.waterfall(plot_steps, label="label", value_fmt="si"), 440)

left, right = st.columns([2, 3])
with left:
    st.markdown("### How each effect is defined")
    definitions = pd.DataFrame(
        [
            ("Price", "Current volume times the change in price. Valued at what we "
                      "actually sell now, not at last year's volume."),
            ("Cost", "Current volume times the change in unit cost, negated - a cost "
                     "rise reduces margin."),
            ("Volume", "The change in total units, at last year's average unit margin. "
                       "Pure scale, blend held constant."),
            ("Mix", "The blend shift, valued at last year's unit margins. Selling the "
                    "same total units of a richer basket lands here."),
            ("New products", "Whole contribution of anything with no prior-year sales. "
                             "There is no price to compare a launch against."),
            ("Lost products", "Whole contribution of anything we sold last year and "
                              "not this one, negated."),
        ],
        columns=["Effect", "Definition"],
    )
    sh.table(definitions)
with right:
    st.markdown("### The numbers")
    display = chosen_effects[chosen_effects["effect"] != "Residual"].copy()
    display["Effect"] = display["effect"]
    display["Amount"] = display["amount"].map(lambda v: sh.money(v, 2))
    if "pct_of_prior" in display.columns:
        display["% of prior"] = display["pct_of_prior"].map(sh.pct)
        columns = ["Effect", "Amount", "% of prior"]
    else:
        columns = ["Effect", "Amount"]
    sh.table(display[columns])
    sh.caption(
        "Price and cost pull in opposite directions in a rising input market, and the "
        "net of the two is the pass-through story: when cost outruns price, the "
        "quarterly review cycle is the reason."
    )

# --- Cut the bridge ------------------------------------------------------
st.markdown("## The same bridge, one slice at a time")
sh.lede(
    "Run the decomposition inside a single category, segment or channel. The effects "
    "still sum exactly, because the split is computed on that slice's own like-for-like "
    "product set rather than apportioned down from the total."
)

sales = sh.sales()
dimension_labels = {
    "category": "Product category", "brand_tier": "Brand tier",
    "segment": "Customer segment", "channel": "Channel", "region": "Region",
}
slice_columns = st.columns([1, 2])
with slice_columns[0]:
    dimension = st.selectbox("Dimension", list(dimension_labels),
                             format_func=dimension_labels.get)
with slice_columns[1]:
    members = st.multiselect(
        dimension_labels[dimension],
        sorted(sales[dimension].dropna().unique()),
        default=sorted(sales[dimension].dropna().unique())[:1],
    )

prior_year, current_year = int(comparison[2:6]), int(comparison[-4:])


def _rows(frame: pd.DataFrame) -> list[dict]:
    grouped = (
        frame.groupby("product_id", as_index=False)
        .agg(quantity=("quantity_units", "sum"), pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"))
    )
    grouped = grouped[grouped["quantity"] > 0]
    grouped["price"] = grouped["pocket_revenue"] / grouped["quantity"]
    grouped["cost"] = grouped["cogs"] / grouped["quantity"]
    return grouped[["product_id", "quantity", "price", "cost"]].to_dict("records")


if members:
    subset = sales[sales[dimension].isin(members)]
    prior = _rows(subset[subset["fiscal_year"] == prior_year])
    current = _rows(subset[subset["fiscal_year"] == current_year])
    if prior and current:
        bridge = (margin_bridge if measure == "Gross margin" else revenue_bridge)(
            prior, current
        )
        key = "margin" if measure == "Gross margin" else "revenue"
        frame = pd.DataFrame(bridge_steps(bridge, value_key=key))
        chart, table_column = st.columns([3, 2])
        with chart:
            sh.show(sh.waterfall(frame, label="label", value_fmt="si"), 380)
        with table_column:
            sh.kpis(
                [
                    ("Products both years", sh.num(bridge["products_common"]), None),
                    ("New", sh.num(bridge["products_new"]), None),
                    ("Lost", sh.num(bridge["products_lost"]), None),
                ]
            )
            display = pd.DataFrame(bridge["effects"])
            display["Effect"] = display["effect"]
            display["Amount"] = display["amount"].map(lambda v: sh.money(v, 2))
            sh.table(display[["Effect", "Amount"]])
            st.caption(
                f"Residual: {bridge['residual']:.6f} - float noise, not an "
                "unexplained remainder."
            )
    else:
        st.info("Not enough history in that slice for both years of the comparison.")
else:
    st.info(f"Pick at least one {dimension_labels[dimension].lower()}.")

sh.footer()
