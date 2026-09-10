"""What-if pricing: best/base/worst, a tornado, and a two-way sensitivity grid."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import shared as sh
from pricing import scenario as sc

sh.page()

st.title("Pricing simulator")
sh.lede(
    "A scenario model is easy to build and easy to build uselessly. The two failures "
    "are both about honesty. The first is moving every input to its optimistic end at "
    "once and calling it the best case -- costs down, volume up, discounts down and "
    "price up together is not a case, it is a wish. The second is presenting three "
    "numbers as though the middle one were a forecast rather than an assumption."
)

assumptions = sh.load("scenario_assumptions").set_index("input")["value"]
tornado = sh.load("scenario_tornado")
thresholds = sh.load("scenario_thresholds")

base = {
    "base_price": float(assumptions["Base list price per unit"]),
    "base_volume": float(assumptions["Base volume (units)"]),
    "base_unit_cost": float(assumptions["Base variable cost per unit"]),
    "base_discount": float(assumptions["Average discount off list"]),
    "fixed_costs": float(assumptions["Annual fixed cost"]),
    "elasticity": float(assumptions["Book elasticity"]),
}

# --- The controls --------------------------------------------------------
st.markdown("## Move the assumptions")
sh.caption(
    "Volume change is an *underlying* move -- a new listing, a lost account, a market "
    "that grew -- applied on top of whatever the elasticity implies for the price move. "
    "Keeping the two apart is what lets a scenario say 'we raise four points and lose "
    "the one big account' without the model counting the same volume twice."
)

row1 = st.columns(3)
with row1[0]:
    price_change = st.slider("List price", -0.15, 0.15, 0.0, 0.005, format="%+.1f%%")
with row1[1]:
    cost_change = st.slider("Input cost", -0.15, 0.25, 0.0, 0.005, format="%+.1f%%")
with row1[2]:
    volume_change = st.slider("Underlying volume", -0.25, 0.25, 0.0, 0.005,
                              format="%+.1f%%")
row2 = st.columns(3)
with row2[0]:
    discount_change = st.slider("Discount depth", -0.08, 0.10, 0.0, 0.005,
                                format="%+.1f%%")
with row2[1]:
    fixed_change = st.slider("Fixed cost", -0.15, 0.25, 0.0, 0.005, format="%+.1f%%")
with row2[2]:
    elasticity = st.slider("Elasticity", -3.5, -0.2, base["elasticity"], 0.05)

overrides = {
    "price_change": price_change, "unit_cost_change": cost_change,
    "volume_change": volume_change, "discount_change": discount_change,
    "fixed_cost_change": fixed_change, "elasticity": elasticity,
}
baseline = sc.evaluate(**base)
result = sc.evaluate(**{**base, **overrides})

sh.kpis(
    [
        ("Revenue", sh.money(result["revenue"], 2),
         f"{result['revenue'] / baseline['revenue'] - 1:+.1%}", "off"),
        ("Contribution", sh.money(result["contribution"], 2),
         sh.pct(result["contribution_pct"]) + " of revenue", "off"),
        ("Operating profit", sh.money(result["operating_profit"], 2),
         f"{result['operating_profit'] - baseline['operating_profit']:+,.0f}", "off"),
        ("Volume", f"{result['volume']:,.0f}",
         f"{result['volume'] / baseline['volume'] - 1:+.1%}", "off"),
    ]
)

detail_left, detail_right = st.columns([3, 2])
with detail_left:
    steps = pd.DataFrame([
        {"label": "Base operating profit", "kind": "total",
         "amount": baseline["operating_profit"]},
    ])
    walk = []
    running = baseline["operating_profit"]
    for key, label in (("price_change", "List price"),
                       ("unit_cost_change", "Input cost"),
                       ("volume_change", "Underlying volume"),
                       ("discount_change", "Discount depth"),
                       ("fixed_cost_change", "Fixed cost")):
        # One at a time, cumulative, so the bars add to the scenario. An
        # all-at-once decomposition leaves interaction terms nobody can place.
        applied = {k: v for k, v in overrides.items()
                   if k == "elasticity" or list(overrides).index(k) <= list(overrides).index(key)}
        value = sc.evaluate(**{**base, **applied})["operating_profit"]
        walk.append({"label": label, "kind": "increase" if value >= running else "decrease",
                     "amount": value - running})
        running = value
    steps = pd.concat([steps, pd.DataFrame(walk),
                       pd.DataFrame([{"label": "Scenario", "kind": "total",
                                      "amount": result["operating_profit"]}])],
                      ignore_index=True)
    sh.show(sh.waterfall(steps, label="label", value_fmt="si"), 360)
    sh.caption(
        "Applied cumulatively, left to right, so the bars add up to the scenario. "
        "Moving all five at once and attributing the total to each in turn leaves "
        "interaction terms that have to go somewhere, and they always end up in the "
        "last bar."
    )
with detail_right:
    st.markdown("### The line")
    display = pd.DataFrame([
        {"Line": "List price", "Base": sh.dollars(baseline["list_price"]),
         "Scenario": sh.dollars(result["list_price"])},
        {"Line": "Discount", "Base": sh.pct(baseline["discount"]),
         "Scenario": sh.pct(result["discount"])},
        {"Line": "Net price", "Base": sh.dollars(baseline["net_price"]),
         "Scenario": sh.dollars(result["net_price"])},
        {"Line": "Variable cost", "Base": sh.dollars(baseline["unit_cost"]),
         "Scenario": sh.dollars(result["unit_cost"])},
        {"Line": "Contribution / unit",
         "Base": sh.dollars(baseline["unit_contribution"]),
         "Scenario": sh.dollars(result["unit_contribution"])},
        {"Line": "Volume", "Base": f"{baseline['volume']:,.0f}",
         "Scenario": f"{result['volume']:,.0f}"},
        {"Line": "Revenue", "Base": sh.money(baseline["revenue"], 2),
         "Scenario": sh.money(result["revenue"], 2)},
        {"Line": "Contribution", "Base": sh.money(baseline["contribution"], 2),
         "Scenario": sh.money(result["contribution"], 2)},
        {"Line": "Fixed cost", "Base": sh.money(baseline["fixed_costs"], 2),
         "Scenario": sh.money(result["fixed_costs"], 2)},
        {"Line": "Operating profit", "Base": sh.money(baseline["operating_profit"], 2),
         "Scenario": sh.money(result["operating_profit"], 2)},
    ])
    sh.table(display)

# --- Best / base / worst -------------------------------------------------
st.markdown("## Best, base and worst")
sh.lede(
    "Built by moving every input to its own good or bad end. The caveat belongs with "
    "the output: these are not a confidence interval. Every assumption landing at its "
    "optimistic end at once is far less likely than any one of them doing so, so the "
    "spread here is wider than the real one and is a stress test rather than a forecast."
)

range_columns = st.columns(3)
with range_columns[0]:
    price_range = st.slider("Price range", -0.12, 0.12, (-0.04, 0.04), 0.005)
with range_columns[1]:
    cost_range = st.slider("Cost range", -0.12, 0.25, (-0.05, 0.12), 0.005)
with range_columns[2]:
    volume_range = st.slider("Volume range", -0.25, 0.20, (-0.10, 0.07), 0.005)

ranges = {
    "price_change": price_range,
    "unit_cost_change": cost_range,
    "volume_change": volume_range,
    "discount_change": (-0.02, 0.03),
    "fixed_cost_change": (-0.03, 0.08),
}
three = pd.DataFrame(sc.three_point(base, ranges))
torn = pd.DataFrame(sc.tornado(base, ranges))

case_left, case_right = st.columns([2, 3])
with case_left:
    display = three.copy()
    display["Case"] = display["scenario"]
    display["Revenue"] = display["revenue"].map(lambda v: sh.money(v, 2))
    display["Contribution"] = display["contribution"].map(lambda v: sh.money(v, 2))
    display["Operating profit"] = display["operating_profit"].map(
        lambda v: sh.money(v, 2))
    display["vs base"] = display["delta"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Case", "Revenue", "Contribution", "Operating profit", "vs base"]])
    st.caption(three["note"].iloc[0])
with case_right:
    st.markdown("### What actually drives the spread")
    plot = torn.sort_values("swing")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=plot["input"], x=plot["downside_delta"], orientation="h", name="Downside",
        marker=dict(color=sh.SERIES[1], line=dict(width=2, color=sh.SURFACE)),
        hovertemplate="%{y} downside: %{x:$,.0f}<extra></extra>"))
    fig.add_trace(go.Bar(
        y=plot["input"], x=plot["upside_delta"], orientation="h", name="Upside",
        marker=dict(color=sh.SERIES[0], line=dict(width=2, color=sh.SURFACE)),
        hovertemplate="%{y} upside: %{x:$,.0f}<extra></extra>"))
    fig.update_layout(barmode="relative")
    fig.update_yaxes(showgrid=False)
    sh.reference_line(fig, x=0, label="Base")
    sh.show(fig, 320, showlegend=True, x_title="Operating profit against base")

sh.caption(
    "This is the output that earns the modelling. Three numbers say the answer is "
    "uncertain; the tornado says which guess to go and reduce -- and the biggest bar "
    "is often an input somebody could measure for the cost of an afternoon."
)

display = torn.copy()
display["Input"] = display["input"]
display["Range"] = display.apply(
    lambda r: f"{r['low_value']:+.1%} to {r['high_value']:+.1%}", axis=1)
display["Swing"] = display["swing"].map(lambda v: sh.money(v, 2))
display["Share of spread"] = display["share_of_swing"].map(sh.pct)
display["Cumulative"] = display["cumulative_share"].map(sh.pct)
sh.table(display[["Input", "Range", "Swing", "Share of spread", "Cumulative"]])

# --- How far can it move -------------------------------------------------
st.markdown("## How far can each input move before profit hits zero")
sh.caption(
    "A better question than any three-point case, and it has one answer. Where an "
    "input never crosses zero in a plausible range, that is the good-news case and is "
    "reported as such rather than as a bound."
)
display = thresholds.copy()
display["Input"] = display["input"]
display["Threshold"] = display.apply(
    lambda r: "no crossing in range" if not r["exists"] else f"{r['threshold']:+.1%}",
    axis=1)
display["Note"] = display["reason"].replace("ok", "crosses zero at the threshold shown")
sh.table(display[["Input", "Threshold", "Note"]])

# --- Two-way grid --------------------------------------------------------
st.markdown("## Price against cost, two ways at once")
sh.caption(
    "Two inputs is the practical limit of a readable grid. Beyond that the answer is "
    "the tornado, not a cube nobody can hold in their head. Diverging colour, because "
    "the question is which side of the base each cell falls on."
)

grid_price = [round(-0.06 + 0.01 * i, 3) for i in range(13)]
grid_cost = [round(-0.06 + 0.02 * i, 3) for i in range(10)]
grid = pd.DataFrame(sc.sensitivity_grid(
    base, x_input="price_change", x_values=grid_price,
    y_input="unit_cost_change", y_values=grid_cost))
matrix = grid.pivot(index="y", columns="x", values="operating_profit"
                    if "operating_profit" in grid.columns else "value")

fig = go.Figure(go.Heatmap(
    z=matrix.values,
    x=[f"{v:+.0%}" for v in matrix.columns],
    y=[f"{v:+.0%}" for v in matrix.index],
    colorscale=[[0.0, sh.DIVERGING[6]], [0.25, sh.DIVERGING[5]],
                [0.5, sh.DIVERGING[3]], [0.75, sh.DIVERGING[1]],
                [1.0, sh.DIVERGING[0]]],
    zmid=float(baseline["operating_profit"]),
    hovertemplate="Price %{x}, cost %{y}<br>Operating profit %{z:$,.0f}<extra></extra>",
    colorbar=dict(title="", thickness=12, outlinewidth=0),
))
sh.show(fig, 380, y_title="Input cost change", x_title="List price change")

sh.footer()
