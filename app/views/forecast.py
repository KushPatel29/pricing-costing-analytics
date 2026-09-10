"""Forecast revenue, cost, volume and margin -- and prove the method choice."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import shared as sh
from pricing import forecast as fc

sh.page()

st.title("Forecast against actual")
sh.lede(
    "Six methods, all simple, and a rolling-origin backtest that picks between them on "
    "out-of-sample error. That order matters: choosing a method first and reporting its "
    "in-sample fit is how forecasting goes wrong in a portfolio, because any method can "
    "be made to fit history and the number a business acts on is the one for a month "
    "that has not happened."
)

series = sh.load("forecast_series")
accuracy = sh.load("forecast_accuracy")
summary = sh.load("forecast_summary")

measures = summary["measure"].tolist()
measure = st.radio("Measure", measures, horizontal=True)

chosen = summary[summary["measure"] == measure].iloc[0]
line = series[series["measure"] == measure].sort_values("month")
held = line[line["period"] == "Held out"]

sh.kpis(
    [
        ("Method chosen", chosen["method"], "by backtest, not by preference", "off"),
        ("Held-out error", sh.pct(chosen["holdout_wape"]),
         "WAPE on six months it never saw", "off"),
        ("Next six months", sh.money(chosen["next_6_total"], 2),
         f"{chosen['change_pct']:+.1%} on the last six", "off"),
        ("Backtest error", sh.pct(chosen["backtest_wape"]),
         "across every rolling fold", "off"),
    ]
)

# --- The chart -----------------------------------------------------------
st.markdown("## History, held out, and forward")
sh.caption(
    "The last six months of history were held back. Everything the method comparison "
    "saw stopped before them, so the fit against those six is a genuine out-of-sample "
    "score rather than a redrawn line. The band is the spread of the errors this "
    "method actually made at that horizon -- not a normal interval around a model, "
    "which would be a claim about the model rather than about its accuracy."
)

fig = go.Figure()
history = line[line["period"] != "Forecast"]
fig.add_trace(go.Scatter(
    x=history["month"], y=history["actual"], name="Actual", mode="lines",
    line=dict(color=sh.SERIES[0], width=2),
    hovertemplate="Actual<br>%{x|%b %Y}: %{y:,.0f}<extra></extra>"))

predicted = line[line["forecast"].notna()].sort_values("month")
if not predicted.empty:
    fig.add_trace(go.Scatter(
        x=list(predicted["month"]) + list(predicted["month"])[::-1],
        y=list(predicted["high"]) + list(predicted["low"])[::-1],
        fill="toself", fillcolor="rgba(235,104,52,0.13)",
        line=dict(width=0), hoverinfo="skip", showlegend=False, name="Interval"))
    fig.add_trace(go.Scatter(
        x=predicted["month"], y=predicted["forecast"], name="Forecast", mode="lines",
        line=dict(color=sh.SERIES[1], width=2, dash="dot"),
        hovertemplate="Forecast<br>%{x|%b %Y}: %{y:,.0f}<extra></extra>"))

if not held.empty:
    sh.reference_line(fig, x=held["month"].min(), label="Held out from here")
fig.update_layout(hovermode="x unified")
sh.show(fig, 400, showlegend=True, y_title=measure)

# --- Method comparison ---------------------------------------------------
st.markdown("## Which method, and how it was chosen")
sh.lede(
    "Every prediction scored below was made from data that stopped before the month it "
    "predicts. Refitting on everything and reporting the fit is how a method that "
    "memorises history wins a comparison it should lose."
)

compare_left, compare_right = st.columns([3, 2])
with compare_left:
    ranked = accuracy[(accuracy["measure"] == measure)
                      & (~accuracy["method"].str.contains("held out"))]
    ranked = ranked.sort_values("wape")
    plot = ranked.copy()
    plot["label"] = plot["wape"].map(lambda v: f"{v:.1%}")
    fig = sh.bar(
        plot, "method", "wape", horizontal=True, text="label",
        colours=[sh.SERIES[2] if best else sh.SERIES[0] for best in plot["is_best"]],
    )
    fig.update_xaxes(tickformat=".0%")
    sh.show(fig, 320, x_title="Out-of-sample WAPE (lower is better)")
with compare_right:
    display = ranked.copy()
    display["Method"] = display["method"]
    display["WAPE"] = display["wape"].map(sh.pct)
    display["Bias"] = display["bias"].map(lambda v: f"{v:+.1%}")
    display["Folds"] = display["folds"]
    sh.table(display[["Method", "WAPE", "Bias", "Folds"]])
    sh.caption(
        "Bias is reported beside error on purpose. A method can have low WAPE and "
        "still lean high every month, and that compounds into inventory nobody "
        "ordered or a plan nobody hits."
    )

st.info(
    "**The unglamorous answer usually wins.** On seasonal demand, last year's same "
    "month -- optionally nudged by the year's trend -- is hard to beat, and Holt-"
    "Winters with grid-searched smoothing constants frequently does not beat it. That "
    "is a result, not a failure to try: a method chosen because it scored best out of "
    "sample is defensible, and one chosen because it sounds sophisticated is not."
)

# --- All four measures ---------------------------------------------------
st.markdown("## All four measures")
display = summary.copy()
display["Measure"] = display["measure"]
display["Method"] = display["method"]
display["Backtest WAPE"] = display["backtest_wape"].map(sh.pct)
display["Held-out WAPE"] = display["holdout_wape"].map(sh.pct)
display["Last six months"] = display["last_6_actual"].map(lambda v: sh.money(v, 2))
display["Next six months"] = display["next_6_total"].map(lambda v: sh.money(v, 2))
display["Change"] = display["change_pct"].map(lambda v: f"{v:+.1%}")
sh.table(display[["Measure", "Method", "Backtest WAPE", "Held-out WAPE",
                  "Last six months", "Next six months", "Change"]])

# --- Try it --------------------------------------------------------------
st.markdown("## Forecast a series yourself")
sh.caption(
    "Pick a category and a horizon. The method is re-chosen by backtest on that "
    "series alone, because the best method for total revenue is not necessarily the "
    "best method for one category's volume."
)

sales = sh.sales()
picker = st.columns(3)
with picker[0]:
    category = st.selectbox("Category", ["All"] + sorted(sales["category"].dropna().unique()))
with picker[1]:
    column = st.selectbox("Measure", ["quantity_units", "pocket_revenue", "cogs"],
                          format_func={"quantity_units": "Volume (units)",
                                       "pocket_revenue": "Pocket revenue",
                                       "cogs": "Cost of goods"}.get)
with picker[2]:
    horizon = st.slider("Horizon (months)", 1, 12, 6)

subset = sales if category == "All" else sales[sales["category"] == category]
monthly = subset.groupby("month", as_index=False)[column].sum().sort_values("month")

if len(monthly) < 24:
    st.info("Not enough history in that slice to backtest a seasonal method.")
else:
    result = fc.forecast(monthly[column].tolist(), horizon=horizon)
    points = pd.DataFrame(result["points"])
    points["month"] = [
        (pd.Timestamp(monthly["month"].iloc[-1]) + pd.DateOffset(months=int(s))).date()
        for s in points["step"]
    ]
    sh.kpis(
        [
            ("Method", result["method"], "chosen by backtest", "off"),
            ("Backtest WAPE", sh.pct(result["accuracy"].get("wape", float("nan"))), None),
            ("Forecast total", sh.money(float(points["forecast"].sum()), 2), None),
            ("History", f"{result['history_periods']} months", None, "off"),
        ]
    )
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=monthly["month"], y=monthly[column], name="Actual",
                             mode="lines", line=dict(color=sh.SERIES[0], width=2)))
    fig.add_trace(go.Scatter(
        x=list(points["month"]) + list(points["month"])[::-1],
        y=list(points["high"]) + list(points["low"])[::-1],
        fill="toself", fillcolor="rgba(235,104,52,0.13)", line=dict(width=0),
        hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=points["month"], y=points["forecast"], name="Forecast",
                             mode="lines", line=dict(color=sh.SERIES[1], width=2,
                                                     dash="dot")))
    sh.show(fig, 340, showlegend=True, y_title=column)

sh.footer()
