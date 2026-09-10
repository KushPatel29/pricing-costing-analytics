"""What different customers pay for the same thing, and what they would have paid."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from app import shared as sh
from pricing.segmentation import price_band_stats, realisation_gap

sh.page()

st.title("Price bands and willingness to pay")
sh.lede(
    "Two questions. What do different customers actually pay for the same product - "
    "the price band - and how wide is it? And what would they have paid: the only "
    "place a seller observes demand at prices it did not charge is the quotes it lost, "
    "because transaction data contains no losses at all."
)

bands = sh.load("price_bands")
profile = sh.load("segment_profile")
wtp = sh.load("wtp_fits")
curve = sh.load("wtp_curve")
loss_reasons = sh.load("quote_loss_reasons")
customers = sh.load("customer_profitability")
quotes = sh.load("fact_quote")

opportunity = float(bands["realisation_opportunity"].sum())
sh.kpis(
    [
        ("Realisation opportunity", sh.money(opportunity, 2),
         "moving below-median lines to their own median", "off"),
        ("Median band width", sh.pct(float(bands["band_width_pct"].median())),
         "p10 to p90, as a share of median", "off"),
        ("Products analysed", sh.num(len(bands)), None),
        ("Quote win rate", sh.pct(float(wtp.loc[wtp["segment"] == "All", "win_rate"].iloc[0])),
         f"{int(wtp.loc[wtp['segment'] == 'All', 'quotes'].iloc[0]):,} quotes", "off"),
    ]
)

# --- Price bands ---------------------------------------------------------
st.markdown("## The price band, product by product")
sh.lede(
    "The spread of pocket prices one product achieves across its customer base. Width "
    "is the most reliable margin opportunity in any book of business - not because the "
    "low end is wrong, but because a band thirty points wide usually cannot be "
    "explained by anything the seller would defend out loud."
)

top = bands.head(25).sort_values("realisation_opportunity")
fig = go.Figure()
fig.add_trace(
    go.Scatter(
        x=top["p10_price"], y=top["description"], mode="markers", name="10th percentile",
        marker=dict(color=sh.SERIES[1], size=11, line=dict(width=2, color=sh.SURFACE)),
        hovertemplate="10th percentile: %{x:$,.2f}<extra></extra>",
    )
)
fig.add_trace(
    go.Scatter(
        x=top["p90_price"], y=top["description"], mode="markers", name="90th percentile",
        marker=dict(color=sh.SERIES[0], size=11, line=dict(width=2, color=sh.SURFACE)),
        hovertemplate="90th percentile: %{x:$,.2f}<extra></extra>",
    )
)
fig.add_trace(
    go.Scatter(
        x=top["median_price"], y=top["description"], mode="markers", name="Median",
        marker=dict(color=sh.SERIES[2], size=13, symbol="diamond",
                    line=dict(width=2, color=sh.SURFACE)),
        hovertemplate="Median: %{x:$,.2f}<extra></extra>",
    )
)
for row in top.itertuples():
    fig.add_shape(type="line", x0=row.p10_price, x1=row.p90_price,
                  y0=row.description, y1=row.description,
                  line=dict(color=sh.GRID, width=3), layer="below")
sh.show(fig, 620, showlegend=True, x_title="Pocket price per unit, volume-weighted")
sh.caption(
    "Percentiles are volume-weighted: each line counts in proportion to what it "
    "shipped, so a band computed over four hundred small accounts and one large one "
    "does not describe the four hundred."
)

st.markdown("### Ranked by what closing the band is worth")
display = bands.head(40).copy()
display["Product"] = display["description"]
display["Customers"] = display["customers"]
display["P10"] = display["p10_price"].map(sh.dollars)
display["Median"] = display["median_price"].map(sh.dollars)
display["P90"] = display["p90_price"].map(sh.dollars)
display["Band width"] = display["band_width_pct"].map(sh.pct)
display["Opportunity"] = display["realisation_opportunity"].map(lambda v: sh.money(v, 2))
display["Lines below median"] = display["lines_below_median"]
display["Category"] = display["category"]
sh.table(display[["Product", "Category", "Customers", "P10", "Median", "P90",
                  "Band width", "Lines below median", "Opportunity"]], height=420)
sh.caption(
    "Deliberately conservative: it values only the gap up to each product's own "
    "volume-weighted median - a price half that product's volume already pays - so "
    "the number survives a room containing the salespeople who own those accounts."
)

# --- One product in detail -----------------------------------------------
st.markdown("## Walk one product's band")
sales = sh.sales()
latest_fy = int(sales["fiscal_year"].max())
recent = sales[sales["fiscal_year"] == latest_fy]

product = st.selectbox("Product", bands["description"].tolist())
lines = recent[recent["description"] == product]

if lines.empty:
    st.info("No lines for that product in the latest fiscal year.")
else:
    stats = price_band_stats(lines.rename(columns={"quantity_units": "quantity"})
                             .to_dict("records"))
    gap = realisation_gap(lines.rename(columns={"quantity_units": "quantity"})
                          .to_dict("records"))
    sh.kpis(
        [
            ("P10", sh.dollars(stats["p10"]), None),
            ("Median", sh.dollars(stats["median"]), None),
            ("P90", sh.dollars(stats["p90"]), None),
            ("Opportunity to median", sh.money(gap["opportunity"], 2),
             f"{gap['lines_below']} lines below", "off"),
        ]
    )
    by_customer = (
        lines.groupby(["customer_name", "segment", "tier"], as_index=False)
        .agg(quantity_units=("quantity_units", "sum"), pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"))
    )
    by_customer["pocket_price"] = by_customer["pocket_revenue"] / by_customer["quantity_units"]
    by_customer["margin_pct"] = (
        (by_customer["pocket_revenue"] - by_customer["cogs"]) / by_customer["pocket_revenue"]
    )
    band_left, band_right = st.columns([3, 2])
    with band_left:
        fig = sh.scatter(by_customer.sort_values("quantity_units"), "quantity_units",
                         "pocket_price", size="quantity_units", hover="customer_name")
        sh.reference_line(fig, y=stats["median"], label="Volume-weighted median")
        fig.update_xaxes(type="log")
        sh.show(fig, 360, y_title="Pocket price per unit",
                x_title="Volume, log scale (units)")
        sh.caption(
            "Log scale on volume, because customer size spans three orders of "
            "magnitude and a linear axis puts every small account in one column. "
            "Points below the line are paying less than half this product's volume "
            "already pays."
        )
    with band_right:
        display = by_customer.sort_values("pocket_price").copy()
        display["Customer"] = display["customer_name"]
        display["Segment"] = display["segment"]
        display["Price"] = display["pocket_price"].map(sh.dollars)
        display["Volume"] = display["quantity_units"].map(lambda v: f"{v:,.0f} units")
        display["Margin"] = display["margin_pct"].map(sh.pct)
        sh.table(display[["Customer", "Segment", "Price", "Volume", "Margin"]], height=360)

# --- Segment profile -----------------------------------------------------
st.markdown("## Segments: level, and consistency")
sh.lede(
    "Band width is what makes this a pricing view rather than a sales report. A "
    "segment paying a low average price consistently is a positioning decision; a "
    "segment paying a low average price with a forty-point spread is an execution "
    "failure, and the two need different meetings."
)
dimension = st.selectbox(
    "Cut by",
    ["segment", "channel", "region", "tier", "brand_tier", "category"],
    format_func=lambda c: {"segment": "Customer segment", "channel": "Channel",
                           "region": "Region", "tier": "Customer volume tier",
                           "brand_tier": "Brand tier",
                           "category": "Product category"}[c],
)
cut = profile[profile["dimension"] == dimension].sort_values("revenue", ascending=False)
display = cut.copy()
display["Member"] = display["member"]
display["Revenue"] = display["revenue"].map(lambda v: sh.money(v, 2))
display["Volume"] = display["volume"].map(lambda v: f"{v:,.0f} units")
display["Margin"] = display["margin_pct"].map(sh.pct)
display["Avg price"] = display["avg_price"].map(sh.dollars)
display["Median price"] = display["median_price"].map(sh.dollars)
display["Band width"] = display["band_width_pct"].map(sh.pct)
display["Opportunity"] = display["realisation_opportunity"].map(lambda v: sh.money(v, 2))
sh.table(display[["Member", "Revenue", "Volume", "Margin", "Avg price",
                  "Median price", "Band width", "Opportunity"]])

# --- Willingness to pay --------------------------------------------------
st.markdown("## What they would have paid")
sh.lede(
    "A logistic fitted to won and lost quotes, on the ratio of our quote to the "
    "competing one. The ratio rather than the absolute price, because a curve fitted "
    "over a $34 monitor and a $6 pack of gel pens at once would be fitted to the "
    "difference between a monitor and a pen. The headline output is the ratio at "
    "which we win half "
    "the time."
)

wtp_left, wtp_right = st.columns([3, 2])
with wtp_left:
    segments = [s for s in wtp["segment"].tolist() if s != "All"]
    chosen = st.multiselect("Segments", segments, default=segments[:3], max_selections=3)
    to_plot = ["All"] + chosen
    fig = go.Figure()
    for slot, segment in enumerate(to_plot):
        fitted = curve[(curve["segment"] == segment) & (curve["kind"] == "Fitted")]
        observed = curve[(curve["segment"] == segment) & (curve["kind"] == "Observed")]
        colour = sh.INK_MUTED if segment == "All" else sh.SERIES_ALL_PAIRS[
            (slot - 1) % len(sh.SERIES_ALL_PAIRS)]
        fig.add_trace(
            go.Scatter(x=fitted["price_ratio"], y=fitted["win_rate"], mode="lines",
                       name=segment, line=dict(color=colour, width=2),
                       hovertemplate=f"{segment}<br>ratio %{{x:.2f}} -> "
                                     "%{y:.0%} win<extra></extra>")
        )
        if not observed.empty:
            fig.add_trace(
                go.Scatter(x=observed["price_ratio"], y=observed["win_rate"],
                           mode="markers", name=f"{segment} (observed)",
                           marker=dict(color=colour, size=9, symbol="circle-open",
                                       line=dict(width=2)),
                           showlegend=False,
                           hovertemplate=f"{segment} observed<br>ratio %{{x:.2f}} -> "
                                         "%{y:.0%}<extra></extra>")
            )
    fig.update_yaxes(tickformat=".0%")
    sh.reference_line(fig, y=0.5, label="Half the quotes")
    sh.reference_line(fig, x=1.0, label="Parity with competitor")
    sh.show(fig, 400, showlegend=True, y_title="Probability of winning",
            x_title="Our quote / competing quote")
    sh.caption(
        "Hollow markers are observed win rates in five-point price buckets; the line "
        "is the fit. Buckets holding fewer than five quotes are dropped - a bucket of "
        "two with one win reads as a 50% win rate and would be drawn the same size as "
        "a bucket of four hundred."
    )
with wtp_right:
    st.markdown("### Where each segment gives up")
    display = wtp[wtp["segment"] != "All"].copy()
    display["Segment"] = display["segment"]
    display["Quotes"] = display["quotes"]
    display["Win rate"] = display["win_rate"].map(sh.pct)
    display["Price sensitivity"] = display["slope"].map(lambda v: f"{abs(v):.1f}")
    display["50/50 price ratio"] = display["indifference_price_ratio"].map(
        lambda v: "n/a" if pd.isna(v) else f"{v:.3f}")
    sh.table(display.sort_values("slope")[
        ["Segment", "Quotes", "Win rate", "Price sensitivity", "50/50 price ratio"]])
    sh.caption(
        "Sensitivity is the steepness of the curve. A distributor resells and shops "
        "hard; a hotel buys on consistency and specification and barely notices a "
        "point either way. Those are different price lists, and this is the evidence "
        "for saying so."
    )
    st.markdown("### Why we lost")
    display = loss_reasons.copy()
    display["Reason"] = display["loss_reason"].replace("", "Not recorded")
    display["Quotes"] = display["quotes"]
    display["Value"] = display["value_lost"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Reason", "Quotes", "Value"]])

# --- Customer profitability ---------------------------------------------
st.markdown("## Customers: margin against volume")
sh.lede(
    "Quadrants against the medians. The bottom-right quadrant is the one that pays for "
    "the analysis - large accounts at below-median margin, where a point of price is "
    "worth more than anything else on this page. Cost to serve is in the margin here, "
    "so a big account with small drops shows up where it belongs."
)
quadrant_left, quadrant_right = st.columns([3, 2])
with quadrant_left:
    plot = customers.copy()
    plot["Quadrant"] = plot["quadrant"]
    fig = sh.scatter(
        plot, "volume_units", "pocket_margin_pct", size="pocket_revenue",
        colour_by="Quadrant", hover="customer_name",
        colour_map={"Protect": sh.SERIES[0], "Grow": sh.SERIES[2],
                    "Fix price": sh.SERIES[1], "Review or exit": sh.INK_MUTED},
    )
    fig.update_yaxes(tickformat=".0%")
    fig.update_xaxes(type="log")
    sh.reference_line(fig, y=float(plot["pocket_margin_pct"].median()), label="Median margin")
    sh.show(fig, 420, showlegend=True, y_title="Pocket margin",
            x_title="Volume, log scale (units)")
with quadrant_right:
    counts = (
        customers.groupby("quadrant", as_index=False)
        .agg(customers=("customer_id", "count"), revenue=("pocket_revenue", "sum"),
             margin=("pocket_margin", "sum"))
    )
    display = counts.copy()
    display["Quadrant"] = display["quadrant"]
    display["Customers"] = display["customers"]
    display["Revenue"] = display["revenue"].map(lambda v: sh.money(v, 2))
    display["Margin"] = display["margin"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Quadrant", "Customers", "Revenue", "Margin"]])

    worst = customers.sort_values("pocket_margin_pct").head(12)
    st.markdown("### Thinnest margins")
    display = worst.copy()
    display["Customer"] = display["customer_name"]
    display["Margin"] = display["pocket_margin_pct"].map(sh.pct)
    display["Leakage"] = display["leakage_pct"].map(sh.pct)
    display["Avg drop"] = display["avg_drop_units"].map(lambda v: f"{v:,.0f} units")
    sh.table(display[["Customer", "Margin", "Leakage", "Avg drop"]], height=320)

sh.footer()
