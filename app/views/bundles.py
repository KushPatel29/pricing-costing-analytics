"""Bundle economics judged on incremental margin, and good/better/best ladders."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh
from pricing.bundles import (
    attach_value,
    break_even_cannibalisation,
    build_bundle,
    incremental_margin,
    price_ladder,
)

sh.page()

st.title("Bundles and ladders")
sh.lede(
    "A bundle at twelve points off that sells four hundred units looks like a win "
    "until you ask how many of those four hundred customers were going to buy every "
    "component anyway. Those customers did not bring new business; they took twelve "
    "points off business already booked. A bundle creates value only if the margin "
    "from genuinely new demand covers the discount handed to demand that was never at "
    "risk."
)

candidates = sh.load("bundle_candidates")
sweep = sh.load("bundle_discount_sweep")
ladders = sh.load("price_ladders")

creates = candidates[candidates["verdict"] == "Creates value"]
sh.kpis(
    [
        ("Candidates evaluated", sh.num(len(candidates)), None),
        ("Create value at 10% off", sh.num(len(creates)),
         f"{len(creates) / max(len(candidates), 1):.0%} of candidates", "off"),
        ("Incremental margin available",
         sh.money(float(creates["incremental_margin"].sum()), 2), None),
        ("Median break-even cannibalisation",
         sh.pct(float(candidates["break_even_cannibalisation"].median())), None),
    ]
)

st.info(
    "**The one number that matters.** Set incremental margin to zero and solve for the "
    "cannibalisation rate and it comes out as bundle margin over standalone margin. A "
    "bundle keeping 80% of standalone margin survives up to 80% cannibalisation; one "
    "discounted to 55% dies above 55%. That turns 'is this bundle a good idea' into a "
    "question about the customer base, which somebody in sales can actually answer."
)

# --- The candidates ------------------------------------------------------
st.markdown("## Candidate bundles")
sh.caption(
    "Pairs are drawn from products the same customers already buy in the same month - "
    "the closest thing an invoice file has to a basket. Cannibalisation is estimated "
    "from that co-purchase rate, which is exactly the share of likely bundle buyers "
    "who already buy both without being asked."
)

display = candidates.copy()
display["Bundle"] = display["name_a"] + "  +  " + display["name_b"]
display["Standalone"] = display["standalone_price"].map(sh.dollars)
display["Bundle price"] = display["bundle_price"].map(sh.dollars)
display["Standalone margin"] = display["standalone_margin_pct"].map(sh.pct)
display["Bundle margin"] = display["bundle_margin_pct"].map(sh.pct)
display["Break-even cannibalisation"] = display["break_even_cannibalisation"].map(sh.pct)
display["Estimated cannibalisation"] = display["cannibalisation_rate"].map(sh.pct)
display["Headroom"] = display["headroom"].map(lambda v: f"{v:+.1%}")
display["Incremental margin"] = display["incremental_margin"].map(lambda v: sh.money(v, 2))
sh.table(
    display.sort_values("incremental_margin", ascending=False)[
        ["Bundle", "Standalone", "Bundle price", "Standalone margin", "Bundle margin",
         "Break-even cannibalisation", "Estimated cannibalisation", "Headroom",
         "Incremental margin", "verdict"]
    ].rename(columns={"verdict": "Verdict"}),
    height=420,
)

# --- Discount sweep ------------------------------------------------------
st.markdown("## How deep should the discount go?")
bundle_id = st.selectbox(
    "Bundle", candidates["bundle_id"].tolist(),
    format_func=lambda b: candidates.loc[candidates["bundle_id"] == b, "name_a"].iloc[0]
    + " + " + candidates.loc[candidates["bundle_id"] == b, "name_b"].iloc[0],
)
row = candidates[candidates["bundle_id"] == bundle_id].iloc[0]
points = sweep[sweep["bundle_id"] == bundle_id]

sweep_left, sweep_right = st.columns([3, 2])
with sweep_left:
    fig = sh.lines(points, "discount", {"incremental_margin": "Incremental margin"},
                   hover_fmt="$,.0f")
    best = points[points["is_best"] == 1]
    if not best.empty:
        sh.reference_line(fig, x=float(best["discount"].iloc[0]), label="Best",
                          colour=sh.SERIES[2])
    sh.reference_line(fig, y=0, label="Break-even")
    fig.update_xaxes(tickformat=".0%")
    sh.show(fig, 340, y_title="Incremental margin ($)", x_title="Bundle discount")
    sh.caption(
        "The sweep assumes each point of discount brings a little extra demand. With "
        "that lift set to zero the curve only falls and the answer is trivially "
        "'discount nothing' - which is the honest answer when nobody can put a number "
        "on the lift, and is why there is no default guess baked into the function."
    )
with sweep_right:
    sh.kpis(
        [
            ("Co-purchase months", sh.num(row["co_purchase_months"]), None),
            ("Break-even cannibalisation", sh.pct(row["break_even_cannibalisation"]), None),
        ]
    )
    display = points.copy()
    display["Discount"] = display["discount"].map(sh.pct)
    display["Price"] = display["bundle_price"].map(sh.dollars)
    display["Margin"] = display["bundle_margin_pct"].map(sh.pct)
    display["Incremental"] = display["incremental_margin"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Discount", "Price", "Margin", "Incremental"]], height=320)

# --- Build your own ------------------------------------------------------
st.markdown("## Build a bundle")
sh.caption(
    "Two components, a discount, and an honest guess at how many buyers would have "
    "taken both anyway."
)

sales = sh.sales()
latest_fy = int(sales["fiscal_year"].max())
recent = sales[sales["fiscal_year"] == latest_fy]
economics = (
    recent.groupby(["product_id", "description"], as_index=False)
    .agg(quantity_units=("quantity_units", "sum"), pocket_revenue=("pocket_revenue", "sum"),
         cogs=("cogs", "sum"))
)
economics["price"] = economics["pocket_revenue"] / economics["quantity_units"]
economics["cost"] = economics["cogs"] / economics["quantity_units"]
# Indexed on product_id, not description: two products can share a
# description, and .loc on a duplicated index returns a Series rather than a
# value. That reaches float() as a TypeError, but the real damage is upstream --
# two identical entries in the dropdown and no way to tell which is which.
lookup = economics.set_index("product_id")
label_of = dict(zip(economics["product_id"], economics["description"], strict=True))

builder = st.columns(4)
with builder[0]:
    first = st.selectbox("Component A", economics["product_id"].tolist(), index=0,
                         format_func=lambda p: label_of[p])
with builder[1]:
    second = st.selectbox("Component B", economics["product_id"].tolist(), index=1,
                          format_func=lambda p: label_of[p])
with builder[2]:
    discount = st.slider("Bundle discount", 0.0, 0.35, 0.10, 0.01)
with builder[3]:
    cannibalisation = st.slider("Cannibalisation", 0.0, 0.95, 0.35, 0.05)

units = st.slider("Expected bundle units (unit-equivalent)", 100, 60_000, 8_000, 100)

components = [
    {"price": float(lookup.loc[first, "price"]), "cost": float(lookup.loc[first, "cost"])},
    {"price": float(lookup.loc[second, "price"]), "cost": float(lookup.loc[second, "cost"])},
]
bundle = build_bundle(components, discount=discount)
result = incremental_margin(bundle, expected_units=units,
                           cannibalisation_rate=cannibalisation)
break_even = break_even_cannibalisation(bundle)

sh.kpis(
    [
        ("Standalone", sh.dollars(bundle["standalone_price"]),
         sh.pct(bundle["standalone_margin_pct"]) + " margin", "off"),
        ("Bundle price", sh.dollars(bundle["bundle_price"]),
         sh.pct(bundle["bundle_margin_pct"]) + " margin", "off"),
        ("Break-even cannibalisation", sh.pct(break_even),
         f"{result['headroom']:+.0%} headroom", "off"),
        ("Incremental margin", sh.money(result["incremental_margin"], 2),
         result["verdict"], "off"),
    ]
)

if result["incremental_margin"] > 0:
    st.success(
        f"At {cannibalisation:.0%} cannibalisation this bundle clears. It survives up "
        f"to {break_even:.0%}, so the question to put to sales is whether more than "
        f"{break_even:.0%} of the customers who would take it already buy both."
    )
else:
    st.warning(
        f"At {cannibalisation:.0%} cannibalisation this destroys "
        f"{sh.money(abs(result['incremental_margin']), 2)}. It needs cannibalisation "
        f"under {break_even:.0%}, or a shallower discount."
    )

detail = pd.DataFrame(
    [
        {"Line": "Margin from genuinely new demand",
         "Amount": sh.money(result["margin_from_new_demand"], 2)},
        {"Line": "Discount handed to buyers who would have bought both",
         "Amount": sh.money(-result["discount_given_to_existing"], 2)},
        {"Line": "Incremental margin",
         "Amount": sh.money(result["incremental_margin"], 2)},
    ]
)
sh.table(detail)

# --- Attach rates --------------------------------------------------------
st.markdown("## Add-ons")
sh.caption(
    "The same arithmetic with one component and an attach rate. Reported per anchor "
    "unit as well as in total, because 'adds thirty-four cents to every case of the "
    "anchor' is the form that can be compared directly with a price change on the "
    "anchor itself."
)
attach_columns = st.columns(4)
with attach_columns[0]:
    anchor_units = st.number_input("Anchor units (units)", 100, 500_000, 40_000, 1_000)
with attach_columns[1]:
    rate = st.slider("Attach rate", 0.0, 1.0, 0.22, 0.01)
with attach_columns[2]:
    addon_price = st.number_input("Add-on price $/unit", 0.5, 60.0, 9.20, 0.10)
with attach_columns[3]:
    addon_cost = st.number_input("Add-on cost $/unit", 0.1, 60.0, 6.40, 0.10)

attach = attach_value(anchor_units=anchor_units, attach_rate=rate,
                      addon_price=addon_price, addon_cost=addon_cost)
sh.kpis(
    [
        ("Add-on units", f"{attach['addon_units']:,.0f} units", None),
        ("Add-on revenue", sh.money(attach["addon_revenue"], 2), None),
        ("Add-on margin", sh.money(attach["addon_margin"], 2), None),
        ("Margin per anchor unit", sh.dollars(attach["margin_per_anchor_unit"], 3), None),
    ]
)

# --- Ladders -------------------------------------------------------------
st.markdown("## Good, better, best")
sh.lede(
    "A ladder built from one cost and a margin per rung, with a cost uplift for the "
    "packaging or portioning the higher rung actually incurs. The step between rungs "
    "comes back with each one: a ladder whose rungs are two cents apart is not a "
    "ladder, and that is visible here rather than after it ships."
)

ladder_left, ladder_right = st.columns([2, 3])
with ladder_left:
    display = ladders.copy()
    display["Category"] = display["category"]
    display["Tier"] = display["tier"]
    display["Price"] = display["price"].map(sh.dollars)
    display["Margin"] = display["target_margin"].map(sh.pct)
    display["Step up"] = display["step_pct"].map(
        lambda v: "-" if v == 0 else f"{v:+.1%}")
    sh.table(display[["Category", "Tier", "Price", "Margin", "Step up"]], height=420)
with ladder_right:
    st.markdown("### Design a ladder")
    base_cost = st.number_input("Base cost $/unit", 0.5, 60.0, 8.20, 0.10)
    rungs = st.columns(3)
    with rungs[0]:
        good = st.slider("Good margin", 0.02, 0.50, 0.16, 0.01)
    with rungs[1]:
        better = st.slider("Better margin", 0.02, 0.55, 0.24, 0.01)
    with rungs[2]:
        best = st.slider("Best margin", 0.02, 0.65, 0.33, 0.01)
    uplift = st.slider("Cost uplift at each step", 0.0, 0.30, 0.06, 0.01)

    built = pd.DataFrame(
        price_ladder(
            base_cost,
            [
                {"name": "Good", "margin": good, "cost_uplift": 0.0},
                {"name": "Better", "margin": better, "cost_uplift": base_cost * uplift},
                {"name": "Best", "margin": best, "cost_uplift": base_cost * uplift * 2.5},
            ],
        )
    )
    built["label"] = built["price"].map(sh.dollars)
    fig = sh.bar(built, "tier", "price", text="label")
    sh.show(fig, 280, y_title="Price per unit")
    steps = built["step_pct"].iloc[1:]
    if (steps < 0.08).any():
        st.warning(
            "At least one rung is under eight points above the one below it. Buyers do "
            "not trade up across a gap they cannot see."
        )

sh.footer()
