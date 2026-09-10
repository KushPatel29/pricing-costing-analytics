"""Did the promotion pay, and how is the price list actually being maintained."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import plotly.graph_objects as go
import streamlit as st

from app import shared as sh

sh.page()

st.title("Promotions and price-list operations")
sh.lede(
    "Two halves of the same job. Whether the promotions paid, which turns entirely on "
    "the baseline they displaced rather than the volume they sold. And how the price "
    "list is actually being maintained -- who asks for changes, who signs them, how "
    "long it takes, and how much of the book nobody has been near."
)

promotions = sh.load("promotion_analysis")
promo_summary = sh.load("promotion_summary")
changes = sh.load("price_change_log")
change_summary = sh.load("price_change_summary")
approvals = sh.load("price_change_approvals")
coverage = sh.load("price_list_coverage")

paid = promotions[promotions["net_promo_margin"] > 0]

promo_tab, ops_tab = st.tabs(["Promotions", "Price-list operations"])

# ==========================================================================
with promo_tab:
    sh.kpis(
        [
            ("Promotions run", sh.num(len(promotions)), None),
            ("Paid for themselves", sh.num(len(paid)),
             f"{len(paid) / max(len(promotions), 1):.0%}", "off"),
            ("Incremental margin",
             sh.money(float(promotions["incremental_margin"].sum()), 2),
             "from genuinely new volume", "off"),
            ("Discount on baseline",
             sh.money(float(promotions["discount_on_baseline"].sum()), 2),
             "given to volume that was never at risk", "off"),
        ]
    )

    st.info(
        "**The baseline is the whole analysis.** Volume during a promotion is not "
        "incremental volume: most of it would have sold anyway, some is a forward buy "
        "that empties next month, and the difference between those and genuine new "
        "demand is the difference between a promotion that paid and one that bought "
        "its own sales at a discount. The baseline here is the median non-promoted "
        "month for the same product -- the estimate a pricing analyst can actually "
        "defend to the person who ran the promotion."
    )

    st.markdown("## By mechanic")
    mech_left, mech_right = st.columns([3, 2])
    with mech_left:
        plot = promo_summary.sort_values("net_promo_margin")
        plot["label"] = plot["net_promo_margin"].map(lambda v: sh.money(v, 1))
        fig = sh.bar(
            plot, "mechanic", "net_promo_margin", horizontal=True, text="label",
            colours=[sh.GOOD if v > 0 else sh.CRITICAL
                     for v in plot["net_promo_margin"]],
        )
        sh.reference_line(fig, x=0, label="Break-even")
        sh.show(fig, 320, x_title="Net promotional margin")
    with mech_right:
        display = promo_summary.copy()
        display["Mechanic"] = display["mechanic"]
        display["Promos"] = display["promotions"]
        display["Incremental margin"] = display["incremental_margin"].map(
            lambda v: sh.money(v, 2))
        display["Discount given away"] = display["discount_on_baseline"].map(
            lambda v: sh.money(v, 2))
        display["Net"] = display["net_promo_margin"].map(lambda v: sh.money(v, 2))
        display["Verdict"] = display["paid"]
        sh.table(display[["Mechanic", "Promos", "Incremental margin",
                          "Discount given away", "Net", "Verdict"]])

    st.markdown("## Depth against payback")
    sh.caption(
        "Each point is one promoted product-month. Depth on the horizontal, net "
        "margin on the vertical, size is the volume it moved. The pattern to look for "
        "is where the cloud crosses zero: past that depth, the discount handed to "
        "volume that was never at risk outruns the margin the extra volume brought."
    )
    plot = promotions[promotions["discount_depth"] > 0].copy()
    plot["outcome"] = plot["verdict"]
    fig = sh.scatter(plot, "discount_depth", "net_promo_margin", size="volume_units",
                     colour_by="outcome", hover="description",
                     colour_map={"Paid for itself": sh.SERIES[2],
                                 "Bought its own volume": sh.SERIES[1]})
    fig.update_xaxes(tickformat=".0%")
    sh.reference_line(fig, y=0, label="Break-even")
    sh.show(fig, 400, showlegend=True, y_title="Net promotional margin",
            x_title="Discount depth")

    st.markdown("## Every promotion")
    filtered = sh.sidebar_filters(promotions,
                                  {"category": "Category", "mechanic": "Mechanic"})
    display = filtered.sort_values("net_promo_margin").copy()
    display["Product"] = display["description"]
    display["Month"] = display["month"].dt.strftime("%b %Y")
    display["Mechanic"] = display["mechanic"]
    display["Depth"] = display["discount_depth"].map(sh.pct)
    display["Baseline"] = display["baseline_volume_units"].map(lambda v: f"{v:,.0f}")
    display["Sold"] = display["volume_units"].map(lambda v: f"{v:,.0f}")
    display["Incremental"] = display["incremental_volume_units"].map(
        lambda v: f"{v:+,.0f}")
    display["Net margin"] = display["net_promo_margin"].map(lambda v: sh.money(v, 2))
    display["Verdict"] = display["verdict"]
    sh.table(display[["Product", "Month", "Mechanic", "Depth", "Baseline", "Sold",
                      "Incremental", "Net margin", "Verdict"]], height=420)

# ==========================================================================
with ops_tab:
    pending = changes[changes["approval_state"] == "Pending"]
    rejected = changes[changes["approval_state"] == "Rejected"]
    stale = coverage["stale"].sum()

    sh.kpis(
        [
            ("Price changes", sh.num(len(changes)), "over three years", "off"),
            ("Median turnaround",
             f"{changes['days_to_approve'].median():.0f} days", None, "off"),
            ("Pending", sh.num(len(pending)),
             f"{len(rejected)} rejected", "off"),
            ("Unmanaged items", sh.num(stale),
             "no price change in 300 days", "off"),
        ]
    )

    st.markdown("## Who asks, and who signs")
    ops_left, ops_right = st.columns(2)
    with ops_left:
        by_reason = (
            changes.groupby(["reason", "direction"], as_index=False)
            .agg(changes=("change_id", "count"))
        )
        fig = go.Figure()
        for slot, direction in enumerate(("Increase", "Decrease")):
            subset = by_reason[by_reason["direction"] == direction]
            fig.add_trace(go.Bar(
                y=subset["reason"], x=subset["changes"], orientation="h",
                name=direction,
                marker=dict(color=sh.SERIES[slot], line=dict(width=2, color=sh.SURFACE),
                            cornerradius=4),
                hovertemplate=f"{direction}<br>%{{y}}: %{{x:,}}<extra></extra>"))
        fig.update_layout(barmode="relative")
        fig.update_yaxes(showgrid=False)
        sh.show(fig, 320, showlegend=True, x_title="Price changes")
        sh.caption(
            "A rise is a pass-through or a review; a cut is a market move or a "
            "customer asking. A change log where nobody ever asks for a discount is "
            "a log nobody would believe."
        )
    with ops_right:
        display = approvals.copy()
        display["State"] = display["approval_state"]
        display["Changes"] = display["changes"]
        display["Median days"] = display["median_days"].map(lambda v: f"{v:.0f}")
        display["Median move"] = display["median_pct"].map(lambda v: f"{v:+.1%}")
        sh.table(display[["State", "Changes", "Median days", "Median move"]])
        st.markdown("### By reason")
        display = change_summary.copy()
        display["Reason"] = display["reason"]
        display["Direction"] = display["direction"]
        display["Changes"] = display["changes"]
        display["Median move"] = display["median_pct"].map(lambda v: f"{v:+.1%}")
        display["Median days"] = display["median_days"].map(lambda v: f"{v:.0f}")
        sh.table(display[["Reason", "Direction", "Changes", "Median move",
                          "Median days"]], height=260)

    st.markdown("## How much of the book is actually managed")
    sh.lede(
        "A category manager works through the price list a slice at a time and the "
        "long tail falls off the end of it. Those items are the cheapest margin in the "
        "book precisely because nobody has been near them -- their price has not moved "
        "while their input cost has."
    )
    cover_left, cover_right = st.columns([2, 3])
    with cover_left:
        display = coverage.copy()
        display["Cadence"] = display["review_cadence"]
        display["Products"] = display["products"]
        display["Median days since change"] = display[
            "median_days_since_change"].map(lambda v: f"{v:,.0f}")
        display["Median margin"] = display["median_margin_pct"].map(sh.pct)
        display["Stale"] = display["stale"]
        sh.table(display[["Cadence", "Products", "Median days since change",
                          "Median margin", "Stale"]])
    with cover_right:
        plot = coverage.copy()
        plot["label"] = plot["median_margin_pct"].map(sh.pct)
        fig = sh.bar(plot, "review_cadence", "median_margin_pct", text="label",
                     colours=[sh.SERIES[0], sh.SERIES[2], sh.SERIOUS])
        fig.update_yaxes(tickformat=".0%")
        sh.show(fig, 300, y_title="Median list margin")
        sh.caption(
            "The gap between the managed and the unmanaged items is the finding. It "
            "is not that anyone priced them wrong -- it is that nobody priced them at "
            "all while their cost moved underneath."
        )

    st.markdown("## The change log")
    display = changes.sort_values("effective_month", ascending=False).head(300).copy()
    display["Effective"] = display["effective_month"].dt.strftime("%b %Y")
    display["Product"] = display["description"]
    display["From"] = display["previous_price"].map(sh.dollars)
    display["To"] = display["new_price"].map(sh.dollars)
    display["Move"] = display["pct_change"].map(lambda v: f"{v:+.1%}")
    display["Reason"] = display["reason"]
    display["Requested by"] = display["requested_by"]
    display["State"] = display["approval_state"]
    display["Days"] = display["days_to_approve"]
    sh.table(display[["Effective", "Product", "From", "To", "Move", "Reason",
                      "Requested by", "State", "Days"]], height=420)

sh.footer()
