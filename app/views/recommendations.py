"""One recommended action per product, with the reason and what it is worth."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh

sh.page()

st.title("Recommendations")
sh.lede(
    "Every other page produces a number. This one produces a sentence, because the "
    "deliverable of a pricing analyst is not an elasticity -- it is 'raise these "
    "hundred-odd items by four points, here is what it is worth, and here is why "
    "volume will not walk'."
)

recommendations = sh.load("recommendations")
summary = sh.load("recommendation_summary")
note = sh.load("recommendation_note").iloc[0]

sh.note(note["note"], kind="success")

sh.kpis(
    [
        ("Products reviewed", sh.num(note["products"]), None),
        ("Annual margin at stake", sh.money(note["margin_delta"], 2),
         "if every action is taken", "off"),
        ("Revenue effect", sh.money(note["revenue_delta"], 2),
         "elastic demand trades revenue for margin", "off"),
        ("Actions recommended",
         sh.num(int((recommendations["action"] != "Maintain").sum())),
         f"of {len(recommendations)}", "off"),
    ]
)

st.info(
    "**Why these rules are transparent and ordered rather than a score.** A scoring "
    "model would rank better and be unusable. The person who has to defend the "
    "increase to the customer needs to know it was recommended because the item is "
    "nine points under the market, has not been repriced in fourteen months and sits "
    "below its own margin floor -- not because it scored 0.83. Master data is tested "
    "first, because every rule below it divides by a cost."
)

# --- The mix -------------------------------------------------------------
st.markdown("## What is being recommended")
mix_left, mix_right = st.columns([3, 2])

ACTION_COLOURS = {
    "Fix cost": sh.CRITICAL, "Discontinue": sh.SERIOUS, "Increase": sh.SERIES[0],
    "Discount": sh.SERIES[1], "Bundle": sh.SERIES[2], "Maintain": sh.INK_MUTED,
}

with mix_left:
    plot = summary.copy()
    plot["label"] = plot["margin_delta"].map(lambda v: sh.money(v, 1))
    fig = sh.bar(
        plot, "action", "products", text="label",
        colours=[ACTION_COLOURS.get(a, sh.SERIES[0]) for a in plot["action"]],
    )
    sh.show(fig, 320, y_title="Products")
    sh.caption(
        "Bar height is the product count; the label is the annual margin the action "
        "is worth. Maintain is the most common answer and it belongs in the output -- "
        "a recommendation file where every line says 'act' is a file nobody reads "
        "twice."
    )
with mix_right:
    display = summary.copy()
    display["Action"] = display["action"]
    display["Products"] = display["products"]
    display["Volume"] = display["volume_units"].map(lambda v: f"{v:,.0f}")
    display["Revenue effect"] = display["revenue_delta"].map(lambda v: sh.money(v, 2))
    display["Margin effect"] = display["margin_delta"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Action", "Products", "Volume", "Revenue effect", "Margin effect"]])
    sh.caption(
        "Revenue falls where margin rises: on elastic demand a price increase trades "
        "volume for margin, and reporting only the margin gain would hide the trade."
    )

# --- Confidence ----------------------------------------------------------
st.markdown("## How much the evidence is worth")
sh.lede(
    "Confidence is reported separately from the action, and it is about the evidence "
    "rather than the size of the prize: an increase justified by an elasticity fitted "
    "at an r-squared of 0.03 is a guess with a decimal point. Three inputs -- whether "
    "the elasticity fit is usable, how much competitive coverage there is, and how "
    "many months of the product's own history the conclusion rests on."
)

confidence_left, confidence_right = st.columns([2, 3])
with confidence_left:
    counts = (
        recommendations.groupby(["confidence"], as_index=False)
        .agg(products=("product_id", "count"), margin=("margin_delta", "sum"))
    )
    order = pd.Categorical(counts["confidence"], categories=["High", "Medium", "Low"],
                           ordered=True)
    counts = counts.assign(confidence=order).sort_values("confidence")
    counts["label"] = counts["margin"].map(lambda v: sh.money(v, 1))
    fig = sh.bar(counts, "confidence", "products", text="label",
                 colours=[sh.GOOD, sh.WARNING, sh.SERIOUS])
    sh.show(fig, 280, y_title="Products")
with confidence_right:
    at_risk = recommendations[
        (recommendations["confidence"] == "Low")
        & (recommendations["action"] != "Maintain")
    ].nlargest(10, "priority")
    st.markdown("### High value, thin evidence")
    sh.caption(
        "The list to be careful with: material recommendations resting on evidence "
        "that would not survive a challenge. Worth doing the work to firm up before "
        "taking them to a customer."
    )
    if at_risk.empty:
        st.caption("Nothing material rests on low-confidence evidence.")
    else:
        display = at_risk.copy()
        display["Product"] = display["description"]
        display["Action"] = display["action"]
        display["Margin effect"] = display["margin_delta"].map(lambda v: sh.money(v, 2))
        display["Why thin"] = display["evidence"]
        sh.table(display[["Product", "Action", "Margin effect", "Why thin"]], height=260)

# --- The list ------------------------------------------------------------
st.markdown("## The recommendation list")

filters = st.columns(3)
with filters[0]:
    actions = st.multiselect("Action", sorted(recommendations["action"].unique()),
                             default=[a for a in ("Increase", "Discount", "Fix cost",
                                                  "Discontinue", "Bundle")
                                      if a in set(recommendations["action"])])
with filters[1]:
    categories = st.multiselect("Category",
                                sorted(recommendations["category"].dropna().unique()))
with filters[2]:
    confidences = st.multiselect("Confidence", ["High", "Medium", "Low"])

filtered = recommendations
if actions:
    filtered = filtered[filtered["action"].isin(actions)]
if categories:
    filtered = filtered[filtered["category"].isin(categories)]
if confidences:
    filtered = filtered[filtered["confidence"].isin(confidences)]

display = filtered.copy()
display["Product"] = display["description"]
display["Action"] = display["action"]
display["Now"] = display["current_price"].map(sh.dollars)
display["Recommended"] = display["recommended_price"].map(sh.dollars)
display["Change"] = display["price_change_pct"].map(
    lambda v: "-" if v == 0 else f"{v:+.1%}")
display["Volume effect"] = display["volume_change_pct"].map(
    lambda v: "-" if v == 0 else f"{v:+.1%}")
display["Margin effect"] = display["margin_delta"].map(
    lambda v: "-" if v == 0 else sh.money(v, 2))
display["Confidence"] = display["confidence"]
display["Category"] = display["category"]
sh.table(
    display[["Product", "Category", "Action", "Now", "Recommended", "Change",
             "Volume effect", "Margin effect", "Confidence"]],
    height=440,
)

st.download_button(
    "Download the recommendation list (CSV)",
    filtered.to_csv(index=False).encode("utf-8"),
    file_name="pricing_recommendations.csv",
    mime="text/csv",
)

# --- One product ---------------------------------------------------------
st.markdown("## Read one recommendation in full")
sh.caption(
    "The rationale is generated from the same evidence the decision used, so it "
    "cannot drift from the table above it -- which is the usual failure of a written "
    "recommendation, and the reason nobody trusts the ones that are typed."
)

if filtered.empty:
    st.info("No products match those filters.")
else:
    chosen = st.selectbox("Product", filtered["description"].tolist())
    row = filtered[filtered["description"] == chosen].iloc[0]
    sh.kpis(
        [
            ("Action", row["action"], row["confidence"] + " confidence", "off"),
            ("Current price", sh.dollars(row["current_price"]),
             f"cost {row['unit_cost']:,.2f}", "off"),
            ("Recommended", sh.dollars(row["recommended_price"]),
             "-" if row["price_change_pct"] == 0 else f"{row['price_change_pct']:+.1%}",
             "off"),
            ("Margin effect", sh.money(row["margin_delta"], 2),
             "per year at this volume", "off"),
        ]
    )
    st.markdown(f"> {row['rationale']}")
    evidence = pd.DataFrame([
        {"Evidence": "Margin today", "Value": sh.pct(row["margin_pct"])},
        {"Evidence": "Price index against the market",
         "Value": "no coverage" if pd.isna(row["price_index"])
                  else f"{row['price_index']:.0f}"},
        {"Evidence": "Volume", "Value": f"{row['volume_units']:,.0f} units"},
        {"Evidence": "Confidence", "Value": f"{row['confidence']} - {row['evidence']}"},
    ])
    sh.table(evidence)

sh.footer()
