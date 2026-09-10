"""List price down to pocket margin, and which deduction is costing the most."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from app import shared as sh
from pricing.waterfall import ALL_DEDUCTIONS, DEDUCTION_LABELS, build_waterfall, waterfall_steps

sh.page()

st.title("Price waterfall")
sh.lede(
    "The headline discount is never the whole discount. A customer quoted eight points "
    "off list also takes a quarterly rebate, an early-payment term, freight we absorb "
    "and a returns allowance - none of which appear on the invoice the salesperson is "
    "looking at. The distance from list price to what we actually keep is revenue "
    "leakage, and on this book it runs to a sixth of list."
)

steps = sh.load("price_waterfall").sort_values("sort_order")
leakage = sh.load("leakage_by_dimension")
monthly = sh.load("waterfall_monthly").sort_values("month")

list_value = float(steps.loc[steps["step"] == "List value", "amount"].iloc[0])
pocket = float(steps.loc[steps["step"] == "Pocket revenue", "amount"].iloc[0])
margin = float(steps.loc[steps["step"] == "Pocket margin", "amount"].iloc[0])
invoice = float(steps.loc[steps["step"] == "Promotional discount", "running"].iloc[0])

sh.kpis(
    [
        ("List value", sh.money(list_value, 1), None),
        ("Invoice revenue", sh.money(invoice, 1),
         f"{invoice / list_value - 1:.1%} vs list", "off"),
        ("Pocket revenue", sh.money(pocket, 1),
         f"{pocket / list_value - 1:.1%} vs list", "off"),
        ("Pocket margin", sh.pct(margin / pocket), None),
    ]
)

st.markdown("## Three fiscal years, list to margin")
sh.show(sh.waterfall(steps, value_fmt="si"), 460)
sh.caption(
    "Blue bars are levels, orange bars are what comes off. On-invoice discounts are "
    "what the customer sees; off-invoice deductions settle later and are the ones a "
    "quoting screen never shows; cost to serve is what it costs to hand the product "
    "over, and it belongs here because it is a real reduction in what a customer is "
    "worth."
)

# --- Which deduction, cut which way -------------------------------------
st.markdown("## Which deduction, and where")

dimension_labels = {
    "category": "Product category", "brand_tier": "Brand tier", "segment": "Customer segment",
    "channel": "Channel", "region": "Region", "tier": "Customer volume tier",
}
choice = st.selectbox("Cut by", list(dimension_labels), format_func=dimension_labels.get)
cut = leakage[leakage["dimension"] == choice]

left, right = st.columns([3, 2])
with left:
    ranked = (
        cut.groupby(["deduction", "bucket"], as_index=False)["amount"].sum()
        .sort_values("amount", ascending=False)
    )
    ranked["label"] = ranked["amount"].map(lambda v: sh.money(v, 1))
    fig = sh.bar(ranked, "deduction", "amount", horizontal=True, text="label",
                 colour=sh.SERIES[1])
    sh.show(fig, 380, x_title="Extended dollars over three years")
with right:
    st.markdown("### Leakage rate by member")
    rate = (
        cut.groupby("member", as_index=False)
        .agg(amount=("amount", "sum"), list_value=("list_value", "first"))
    )
    rate["leakage_pct"] = rate["amount"] / rate["list_value"]
    rate = rate.sort_values("leakage_pct", ascending=False)
    display = rate.copy()
    display[dimension_labels[choice]] = display["member"]
    display["Leakage"] = display["leakage_pct"].map(sh.pct)
    display["Amount"] = display["amount"].map(lambda v: sh.money(v, 1))
    sh.table(display[[dimension_labels[choice], "Leakage", "Amount"]], height=330)

sh.caption(
    "Ranked by dollars, not by rate. A quarter-point rebate running across the whole "
    "book is worth more than a six-point discount on one account, and only one of the "
    "two ever gets argued about in a meeting."
)

# --- Trend ---------------------------------------------------------------
st.markdown("## Is it getting worse?")
trend_left, trend_right = st.columns(2)
with trend_left:
    fig = sh.lines(monthly, "month", {"leakage_pct": "Leakage % of list"}, hover_fmt=".2%")
    fig.update_yaxes(tickformat=".0%")
    sh.reference_line(fig, y=float(monthly["leakage_pct"].mean()), label="Average")
    sh.show(fig, 320, y_title="Share of list price lost")
with trend_right:
    fig = sh.lines(monthly, "month", {"pocket_margin_pct": "Pocket margin %"},
                   hover_fmt=".2%")
    fig.update_yaxes(tickformat=".0%")
    sh.reference_line(fig, y=float(monthly["pocket_margin_pct"].mean()), label="Average")
    sh.show(fig, 320, y_title="Margin on pocket revenue")

# --- One line, end to end -----------------------------------------------
st.markdown("## Walk a single line")
sh.caption(
    "Pick a customer and a product and see the same arithmetic on one invoice line. "
    "Everything here runs through the same functions the roll-up above uses."
)

sales = sh.sales()
recent = sales[sales["month"] == sales["month"].max()]

pick_left, pick_right = st.columns(2)
with pick_left:
    customer = st.selectbox(
        "Customer",
        sorted(recent["customer_name"].dropna().unique()),
        index=0,
    )
with pick_right:
    lines_for_customer = recent[recent["customer_name"] == customer]
    product = st.selectbox(
        "Product",
        sorted(lines_for_customer["description"].dropna().unique()),
        index=0,
    )

line = lines_for_customer[lines_for_customer["description"] == product]
if line.empty:
    st.info("No line for that combination in the latest month.")
else:
    row = line.iloc[0]
    deductions = {key: float(row[key]) for key in ALL_DEDUCTIONS}
    result = build_waterfall(
        list_price=float(row["list_price"]),
        final_cost=float(row["final_cost"]),
        deductions=deductions,
        quantity=float(row["quantity_units"]),
    )
    detail_left, detail_right = st.columns([3, 2])
    with detail_left:
        import pandas as pd

        step_frame = pd.DataFrame(
            waterfall_steps(list_price=float(row["list_price"]),
                            final_cost=float(row["final_cost"]),
                            deductions=deductions)
        )
        sh.show(sh.waterfall(step_frame, label="label", value_fmt=",.2f"), 400)
    with detail_right:
        sh.kpis([("Quantity", f"{row['quantity_units']:,.0f} units", None)])
        sh.kpis(
            [
                ("List", sh.dollars(result["list_price"]), None),
                ("Pocket", sh.dollars(result["pocket_price"]),
                 f"{-result['leakage_pct']:.1%}", "off"),
            ]
        )
        sh.kpis(
            [
                ("Cost", sh.dollars(result["final_cost"]), None),
                ("Pocket margin", sh.pct(result["pocket_margin_pct"]), None),
            ]
        )
        import pandas as pd

        breakdown = pd.DataFrame(
            [
                {"Deduction": DEDUCTION_LABELS[key], "Per unit": sh.dollars(value, 4),
                 "% of list": sh.pct(value / row["list_price"], 2),
                 "Extended": sh.money(value * row["quantity_units"], 2)}
                for key, value in deductions.items() if value
            ]
        )
        sh.table(breakdown, height=260)

sh.footer()
