"""The floor, the target, who signs for the gap - and this month's breaches."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh
from pricing.guardrails import price_band, score_deal
from pricing.waterfall import ALL_DEDUCTIONS, DEDUCTION_LABELS

sh.page()

st.title("Deal guardrails")
sh.lede(
    "Everything else in this app is analysis. This page has to survive contact with a "
    "salesperson at ten to five on a Friday, which means answering three questions on "
    "one screen: what is the least I can charge, what should I be charging, and who do "
    "I need if I want to go lower."
)

scores = sh.load("deal_scores")
exceptions = sh.load("guardrail_exceptions")
summary = sh.load("guardrail_summary")

latest_month = scores["month"].max()
breaches = int(len(exceptions))
at_risk = float(exceptions["margin_at_risk"].sum()) if not exceptions.empty else 0.0

sh.kpis(
    [
        ("Lines scored", sh.num(len(scores)), latest_month.strftime("%B %Y"), "off"),
        ("Within guardrail", sh.pct(float(scores["within_guardrail"].mean())), None),
        ("Breaches", sh.num(breaches), None),
        ("Margin at risk", sh.money(at_risk, 2), None),
    ]
)

st.info(
    "**Two design decisions worth stating.** The floor is on *pocket* price, not list: "
    "a rule saying 'no more than fifteen points off list' says nothing about rebates, "
    "freight or terms, which is where the margin actually goes. And approval tiers are "
    "on the gap to target margin, not on the discount: two deals at ten points off are "
    "not the same deal when one product carries thirty-eight points of margin and the "
    "other nineteen."
)

# --- Distribution --------------------------------------------------------
st.markdown("## Where this month's lines landed")
left, right = st.columns([3, 2])

with left:
    order = ["Loss-making", "Below floor", "Below target", "At target", "Above stretch"]
    counts = (
        scores.groupby("verdict", as_index=False)
        .agg(lines=("product_id", "count"), margin=("extended_margin", "sum"))
    )
    counts["verdict"] = pd.Categorical(counts["verdict"], categories=order, ordered=True)
    counts = counts.sort_values("verdict")
    counts["label"] = counts["margin"].map(lambda v: sh.money(v, 1))
    fig = sh.bar(
        counts, "verdict", "lines", text="label",
        colours=[sh.VERDICT_COLOURS.get(v, sh.SERIES[0]) for v in counts["verdict"]],
    )
    sh.show(fig, 340, y_title="Invoice lines")
    sh.caption(
        "Bar height is the line count; the label is the extended margin in that band. "
        "Status colours are paired with the verdict as text on every mark, because a "
        "status colour must never carry the meaning on its own."
    )

with right:
    st.markdown("### Who has to sign")
    approvals = (
        scores[scores["approver"] != "None"]
        .groupby("approver", as_index=False)
        .agg(lines=("product_id", "count"), gap=("margin_gap_dollars", "sum"))
        .sort_values("gap", ascending=False)
    )
    display = approvals.copy()
    display["Approver"] = display["approver"]
    display["Lines"] = display["lines"]
    display["Margin given up"] = display["gap"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Approver", "Lines", "Margin given up"]])
    sh.caption(
        "The escalation ladder is two points to a rep, five to a sales manager, ten to "
        "a commercial director, and everything past that to finance."
    )

# --- Exceptions ----------------------------------------------------------
st.markdown("## The exception report")
sh.lede(
    "Ranked by margin at risk, not by how far below floor a line sits. A scan that "
    "sorts by severity puts the analyst on a forty-dollar account for the first twenty "
    "minutes. A line can appear under more than one rule, because the fixes differ."
)

if not summary.empty:
    display = summary.copy()
    display["Rule"] = display["code"]
    display["Lines"] = display["count"]
    display["Margin at risk"] = display["margin_at_risk"].map(lambda v: sh.money(v, 2))
    display["What to do"] = display["action"]
    sh.table(display[["Rule", "Lines", "Margin at risk", "What to do"]])

if not exceptions.empty:
    rules = ["All"] + sorted(exceptions["code"].unique())
    chosen_rule = st.selectbox("Rule", rules)
    filtered = exceptions if chosen_rule == "All" else exceptions[
        exceptions["code"] == chosen_rule]
    display = filtered.head(80).copy()
    display["Rule"] = display["code"]
    display["Product"] = display["description"]
    display["Customer"] = display["customer_name"]
    display["Margin"] = display["pocket_margin_pct"].map(sh.pct)
    display["Index"] = display["price_index"].map(
        lambda v: "n/a" if pd.isna(v) else f"{v:,.0f}")
    display["At risk"] = display["margin_at_risk"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Rule", "Product", "Customer", "Margin", "Index", "At risk"]],
             height=420)

# --- Quote scoring -------------------------------------------------------
st.markdown("## Score a quote")
sh.lede(
    "Pick a real line, adjust the price, and see where it lands against the band. The "
    "floor, target and stretch prices are grossed up for the deductions this customer "
    "actually takes - quoting a floor that ignores them is how a deal clears the "
    "guardrail on the screen and misses it in the ledger."
)

sales = sh.sales()
recent = sales[sales["month"] == sales["month"].max()]

pick = st.columns(2)
with pick[0]:
    customer = st.selectbox("Customer", sorted(recent["customer_name"].dropna().unique()))
lines_for = recent[recent["customer_name"] == customer]
with pick[1]:
    product = st.selectbox("Product", sorted(lines_for["description"].dropna().unique()))

line = lines_for[lines_for["description"] == product]
if line.empty:
    st.info("No line for that combination in the latest month.")
else:
    row = line.iloc[0]
    deductions = {key: float(row[key]) for key in ALL_DEDUCTIONS}
    target = float(row["target_margin"])
    floor = max(0.05, target - 0.09)
    stretch = target + 0.08

    controls = st.columns(3)
    with controls[0]:
        quoted = st.number_input(
            "Quoted list price $/unit", 0.5, 200.0, float(round(row["list_price"], 2)), 0.05
        )
    with controls[1]:
        quantity = st.number_input("Quantity (units)", 10.0, 200_000.0,
                                   float(round(row["quantity_units"], 0)), 10.0)
    with controls[2]:
        extra = st.slider("Extra discount off list", 0.0, 0.30, 0.0, 0.005)

    adjusted = dict(deductions)
    adjusted["promo_discount"] = adjusted.get("promo_discount", 0.0) + quoted * extra

    score = score_deal(
        list_price=quoted, final_cost=float(row["final_cost"]), deductions=adjusted,
        floor_margin=floor, target_margin=target, stretch_margin=stretch,
        quantity=quantity,
    )

    sh.kpis(
        [
            ("Pocket price", sh.dollars(score["pocket_price"]),
             f"{score['pocket_price'] / quoted - 1:.1%} of list", "off"),
            ("Pocket margin", sh.pct(score["pocket_margin_pct"]),
             f"target {target:.1%}", "off"),
            ("Verdict", score["verdict"], None),
            ("Approver", score["approver"],
             sh.money(score["margin_gap_dollars"], 2) + " given up"
             if score["margin_gap_dollars"] else None, "off"),
        ]
    )

    band_left, band_right = st.columns([3, 2])
    with band_left:
        band = pd.DataFrame(
            [
                {"level": "Floor", "price": score["floor_price"]},
                {"level": "Target", "price": score["target_price"]},
                {"level": "Stretch", "price": score["stretch_price"]},
                {"level": "Quoted", "price": quoted},
            ]
        )
        band["label"] = band["price"].map(sh.dollars)
        fig = sh.bar(
            band, "level", "price", horizontal=True, text="label",
            colours=[sh.SERIOUS, sh.SERIES[0], sh.GOOD,
                     sh.GOOD if score["within_guardrail"] else sh.CRITICAL],
        )
        sh.show(fig, 280, x_title="List price per unit")
        if not score["within_guardrail"]:
            st.error(
                f"Below the floor. This quote needs {sh.dollars(score['floor_price'])} "
                f"to clear a {floor:.0%} pocket margin once this customer's deductions "
                "are counted."
            )
    with band_right:
        breakdown = pd.DataFrame(
            [
                {"Deduction": DEDUCTION_LABELS[key], "Per unit": sh.dollars(value, 4),
                 "% of list": sh.pct(value / quoted, 2)}
                for key, value in adjusted.items() if value
            ]
        )
        sh.table(breakdown, height=280)

# --- Band designer -------------------------------------------------------
st.markdown("## Design a guardrail")
sh.caption(
    "What the floor, target and stretch prices come out at for a given cost and a "
    "given deduction stack. Deductions are absolute per unit, so grossing up is an "
    "addition rather than a division: the rebate is fourteen cents a unit whatever "
    "the list price ends up being."
)

designer = st.columns(4)
with designer[0]:
    cost = st.number_input("Final cost $/unit", 0.5, 90.0, 9.60, 0.10, key="band_cost")
with designer[1]:
    floor_margin = st.slider("Floor margin", 0.02, 0.40, 0.12, 0.01)
with designer[2]:
    target_margin = st.slider("Target margin", 0.05, 0.50, 0.22, 0.01)
with designer[3]:
    stretch_margin = st.slider("Stretch margin", 0.08, 0.60, 0.30, 0.01)

deduction_total = st.slider("Deductions this customer takes, $/unit", 0.0, 6.0, 1.85, 0.05)

band = price_band(
    cost, floor_margin=floor_margin, target_margin=target_margin,
    stretch_margin=stretch_margin,
    deductions={"volume_discount": deduction_total},
)
sh.kpis(
    [
        ("Floor list price", sh.dollars(band["floor_price"]),
         sh.pct(floor_margin), "off"),
        ("Target list price", sh.dollars(band["target_price"]),
         sh.pct(target_margin), "off"),
        ("Stretch list price", sh.dollars(band["stretch_price"]),
         sh.pct(stretch_margin), "off"),
        ("Gross-up for deductions", sh.dollars(band["deduction_uplift"]), None),
    ]
)
if target_margin <= floor_margin or stretch_margin <= target_margin:
    st.warning("Floor, target and stretch have to be in that order for the band to mean anything.")

sh.footer()
