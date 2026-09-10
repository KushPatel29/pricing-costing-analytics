"""Our price against the market, and how much of a cost move ever reaches it."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh
from pricing.competitive import indexed_price, market_position, price_dispersion

sh.page()

st.title("Competitive position")
sh.lede(
    "A price index is our price over the market's, times a hundred. Parity is 100. "
    "The two ways it lies are both about what 'the market' means: an unweighted mean "
    "gives a discount brand with two points of share the same vote as the category "
    "leader, and a shelf price scraped in March is not evidence about June. The index "
    "here weights each observation by how fresh it is and takes the median when no "
    "share weights are supplied, and it reports the age of the data it used."
)

index_df = sh.load("competitive_index")
summary = sh.load("competitive_summary")
passthrough = sh.load("passthrough")
competitors = sh.load("dim_competitor")
observations = sh.load("fact_competitor_price")
commodity = sh.load("commodity_index_weekly")

latest_month = index_df["month"].max()
latest = index_df[index_df["month"] == latest_month]

sh.kpis(
    [
        ("Median price index", f"{summary['median_index'].iloc[0]:,.1f}",
         f"{summary['median_index'].iloc[0] - 100:+.1f} vs parity"),
        ("Products covered", sh.num(summary["products_covered"].iloc[0]),
         f"{summary['coverage'].iloc[0]:.0%} of the priced book", "off"),
        ("Under market", sh.num(summary["underpriced_products"].iloc[0]),
         sh.money(summary["opportunity"].iloc[0], 1) + " of revenue", "off"),
        ("Over market", sh.num(summary["overpriced_products"].iloc[0]),
         sh.money(summary["revenue_at_risk"].iloc[0], 1) + " at risk", "off"),
    ]
)

# --- Distribution --------------------------------------------------------
st.markdown("## Where the book sits")
left, right = st.columns([3, 2])

with left:

    bands = latest.copy()
    bands["band"] = bands["price_index"].map(market_position)
    counts = (
        bands.groupby("band", as_index=False)
        .agg(products=("product_id", "count"), revenue=("pocket_revenue", "sum"))
    )
    order = ["Deep discount", "Below market", "At market", "Premium", "Super-premium"]
    counts["band"] = pd.Categorical(counts["band"], categories=order, ordered=True)
    counts = counts.sort_values("band")
    counts["label"] = counts["revenue"].map(lambda v: sh.money(v, 1))
    fig = sh.bar(
        counts, "band", "products", text="label",
        colours=[sh.POSITION_COLOURS.get(b, sh.SERIES[0]) for b in counts["band"]],
    )
    sh.show(fig, 340, y_title="Products", x_title="Position, latest month")
    sh.caption(
        "Bar height is the product count; the label on each bar is the revenue "
        "sitting in that band, because a dozen products at a fourteen-point premium "
        "matters only in proportion to what they sell."
    )

with right:
    st.markdown("### Index against margin")
    sh.caption(
        "Bottom-left is the quadrant to act on: priced under the market and still "
        "carrying margin, so a rise costs little volume and is not needed to survive."
    )
    plot = latest[latest["pocket_revenue"] > 0].copy()
    plot = plot[plot["margin_pct"].notna()]
    fig = sh.scatter(plot, "price_index", "margin_pct", size="pocket_revenue",
                     hover="description")
    fig.update_yaxes(tickformat=".0%")
    sh.reference_line(fig, x=100, label="Parity")
    sh.reference_line(fig, y=float(plot["margin_pct"].median()), label="Median margin")
    sh.show(fig, 340, y_title="Pocket margin", x_title="Price index")

# --- The action lists ----------------------------------------------------
st.markdown("## The two lists")
sh.caption(
    "Ranked by revenue, not by the size of the gap. A fourteen-point premium on a "
    "product nobody buys is a curiosity; three points on the top line is the meeting."
)

under_tab, over_tab, coverage_tab = st.tabs(
    ["Priced under the market", "Priced over the market", "What we cannot see"]
)


def _action_table(frame: pd.DataFrame) -> pd.DataFrame:
    display = frame.copy()
    display["Product"] = display["description"]
    display["Category"] = display["category"]
    display["Our price"] = display["our_price"].map(sh.dollars)
    display["Market"] = display["market_price"].map(sh.dollars)
    display["Index"] = display["price_index"].map(lambda v: f"{v:,.0f}")
    display["Gap"] = display["gap_pct"].map(lambda v: f"{v:+.1%}")
    display["Margin"] = display["margin_pct"].map(
        lambda v: "n/a" if pd.isna(v) else f"{v:.1%}")
    display["Revenue"] = display["pocket_revenue"].map(lambda v: sh.money(v, 1))
    display["Seen at"] = display["competitors_seen"].map(lambda v: f"{v:.0f} competitors")
    return display[["Product", "Category", "Our price", "Market", "Index", "Gap",
                    "Margin", "Revenue", "Seen at"]]


with under_tab:
    under = latest[latest["price_index"] < 96].sort_values("pocket_revenue", ascending=False)
    sh.table(_action_table(under.head(40)), height=420)
with over_tab:
    over = latest[latest["price_index"] > 115].sort_values("pocket_revenue", ascending=False)
    sh.table(_action_table(over.head(40)), height=420)
with coverage_tab:
    st.markdown(
        "Competitive files are always partial. Treating a product with no observation "
        "as being at parity is the most common way an index lies, so it is left out of "
        "the median and counted here instead."
    )
    products = sh.load("dim_product")
    seen = set(latest["product_id"])
    missing = products[~products["product_id"].isin(seen)]
    sh.kpis(
        [
            ("Products with no observation this month", sh.num(len(missing)), None),
            ("Coverage", sh.pct(summary["coverage"].iloc[0]), None),
            ("Oldest observation used",
             f"{latest['max_age_days'].max():,.0f} days", None),
        ]
    )
    sh.table(
        missing[["product_id", "description", "category", "brand_tier"]]
        .rename(columns={"product_id": "Code", "description": "Product",
                         "category": "Category", "brand_tier": "Tier"}),
        height=320,
    )

# --- Who is doing what ---------------------------------------------------
st.markdown("## The competitors themselves")
left, right = st.columns([2, 3])

with left:
    display = competitors.copy()
    coverage = (
        observations[observations["month"] == latest_month]
        .groupby("competitor_id")["product_id"].nunique()
        .rename("observed")
    )
    display = display.merge(coverage, left_on="competitor_id", right_index=True, how="left")
    display["Competitor"] = display["competitor_name"]
    display["Positioning"] = display["positioning"]
    display["Products seen"] = display["observed"].fillna(0).map(lambda v: f"{v:,.0f}")
    sh.table(display[["Competitor", "Positioning", "Products seen"]])

with right:
    st.markdown("### How wide is the market's own spread?")
    sh.caption(
        "A tight spread means the index is a real position. A spread of forty points "
        "means the products being compared are not actually comparable, and the index "
        "is arithmetic on incommensurable things."
    )
    recent_obs = observations[observations["month"] == latest_month]
    spread_rows = []
    for category, group in recent_obs.merge(
        sh.load("dim_product")[["product_id", "category"]], on="product_id"
    ).groupby("category"):
        stats = price_dispersion(group["observed_price"])
        spread_rows.append(
            {"Category": category, "Observations": stats["n"],
             "P25": sh.dollars(stats["p25"]), "Median": sh.dollars(stats["median"]),
             "P75": sh.dollars(stats["p75"]),
             "Spread": sh.pct(stats["spread_pct"], 0)}
        )
    sh.table(pd.DataFrame(spread_rows))

# --- Pass-through --------------------------------------------------------
st.markdown("## Did the cost move reach the price?")
sh.lede(
    "Pass-through is measured twice on purpose. Against list price it is a decision - "
    "how much of the input move the last review passed on. Against realised pocket "
    "price it is an outcome, and the gap between the two is the share of an announced "
    "increase that discounting handed straight back."
)

display = passthrough.copy()
display["Input index"] = display["commodity_index"]
display["Usable"] = display["usable"].map(lambda v: "yes" if v else "no")
display["To list"] = display["passthrough_to_list"].map(lambda v: f"{v:.2f}")
display["To pocket"] = display["passthrough_to_pocket"].map(lambda v: f"{v:.2f}")
display["Given back in discount"] = display["discount_giveback"].map(lambda v: f"{v:+.2f}")
display["Best lag"] = display["best_lag_quarters"].map(lambda v: f"{v:.0f} quarters")
display["R2"] = display["r_squared"].map(lambda v: f"{v:.3f}")
display["Index moved"] = display["index_change_pct"].map(lambda v: f"{v:+.1%}")
display["List moved"] = display["list_price_change_pct"].map(lambda v: f"{v:+.1%}")
display["Pocket moved"] = display["realised_price_change_pct"].map(lambda v: f"{v:+.1%}")
sh.table(
    display[["Input index", "Usable", "To list", "To pocket", "Given back in discount",
             "Best lag", "R2", "Index moved", "List moved", "Pocket moved"]]
)
unusable = passthrough[~passthrough["usable"]]
for row in unusable.itertuples():
    st.warning(
        f"**{row.commodity_index}** carries no usable pass-through: {row.reason}. "
        "The coefficient is shown for completeness and should not be quoted."
    )
sh.caption(
    "Changes are the last twelve months against the first twelve, not endpoint to "
    "endpoint. Every one of these series has an annual season and the window opens in "
    "July before the peak import season and closes in June after it, so an "
    "endpoint comparison charges the whole seasonal swing to the trend."
)

st.markdown("### The input indices")
index_choice = st.multiselect(
    "Show", sorted(commodity["index_name"].unique()),
    default=sorted(commodity["index_name"].unique())[:3],
)
if index_choice:
    wide = (
        commodity[commodity["index_name"].isin(index_choice)]
        .pivot_table(index="week_start", columns="index_name", values="index_value")
        .reset_index()
    )
    fig = sh.lines(wide, "week_start", {c: c for c in index_choice}, hover_fmt=",.1f")
    sh.reference_line(fig, y=100, label="Start of window = 100")
    sh.show(fig, 340, showlegend=True, y_title="Index, 100 at start of FY2024")

# --- Index-linked pricing ------------------------------------------------
st.markdown("## What an index-linked clause would say today")
sh.caption(
    "A contract that moves with the input index at a stated pass-through. At 1.0 the "
    "buyer carries the whole move; at 0.6 the seller eats forty percent of it, which "
    "is close to what most negotiated clauses actually say."
)
clause = st.columns(3)
with clause[0]:
    which = st.selectbox("Input index", sorted(commodity["index_name"].unique()))
with clause[1]:
    base_price = st.number_input("Contract base price ($/unit)", 1.0, 80.0, 12.50, 0.25)
with clause[2]:
    share = st.slider("Pass-through in the clause", 0.0, 1.2, 0.65, 0.05)

series = commodity[commodity["index_name"] == which].sort_values("week_start")
if len(series) > 1:
    base_index = float(series["index_value"].iloc[0])
    now_index = float(series["index_value"].iloc[-1])
    implied = indexed_price(base_price, now_index, base_index, share)
    sh.kpis(
        [
            ("Index at contract start", f"{base_index:,.1f}", None),
            ("Index now", f"{now_index:,.1f}",
             f"{now_index / base_index - 1:+.1%}", "off"),
            ("Price the clause implies", sh.dollars(implied),
             f"{implied / base_price - 1:+.1%}", "off"),
            ("Full pass-through would be",
             sh.dollars(indexed_price(base_price, now_index, base_index, 1.0)), None),
        ]
    )

sh.footer()
