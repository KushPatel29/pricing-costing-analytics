"""
Entry point. Sets the page config once, then routes to a view.

``st.navigation`` rather than the ``pages/`` directory convention, because the
directory convention takes its sidebar labels from filenames -- which is how an
app ends up with a nav entry called "streamlit app" and another called
"2 Elasticity and Optimal Price". Here the labels, icons, order and section
headings are written down, and the views live in ``app/views/`` with ordinary
Python names.

The sections follow the order the work is actually done in: establish that the
data is usable, understand cost, look outward at the market, find where the
profit is, model the change, and then say what to do about it.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from app import shared as sh

sh.configure()

VIEWS = ROOT / "app" / "views"


def _page(filename: str, title: str, icon: str, *, default: bool = False):
    return st.Page(VIEWS / filename, title=title, icon=icon, default=default)


navigation = st.navigation(
    {
        "Overview": [
            _page("overview.py", "Executive summary", ":material/dashboard:",
                  default=True),
            _page("decision_room.py", "Pricing decision room", ":material/fact_check:"),
            _page("recommendations.py", "Recommendations", ":material/task_alt:"),
        ],
        "Data": [
            _page("data_quality.py", "Data quality and reconciliation",
                  ":material/rule:"),
        ],
        "Cost": [
            _page("cost_variance.py", "Cost and variance analysis",
                  ":material/receipt_long:"),
            _page("unit_economics.py", "Unit economics and break-even",
                  ":material/balance:"),
            _page("cost_to_price_calculator.py", "Cost-to-price calculator",
                  ":material/calculate:"),
        ],
        "Market": [
            _page("competitive.py", "Market and competitor benchmarking",
                  ":material/compare_arrows:"),
            _page("segments.py", "Price bands and willingness to pay",
                  ":material/groups:"),
        ],
        "Profitability": [
            _page("profitability.py", "Profitability and segmentation",
                  ":material/pie_chart:"),
            _page("price_waterfall.py", "Price waterfall", ":material/waterfall_chart:"),
            _page("margin_bridge.py", "Margin bridge", ":material/insights:"),
        ],
        "Modelling": [
            _page("simulator.py", "Pricing simulator", ":material/tune:"),
            _page("elasticity.py", "Elasticity and optimal price",
                  ":material/trending_down:"),
            _page("forecast.py", "Forecast against actual", ":material/timeline:"),
            _page("bundles.py", "Bundles and ladders", ":material/inventory_2:"),
        ],
        "Execution": [
            _page("guardrails.py", "Deal guardrails", ":material/gavel:"),
            _page("promotions.py", "Promotions and price-list operations",
                  ":material/campaign:"),
        ],
    }
)

with st.sidebar:
    st.markdown("---")
    sh.caption(
        "Meridian Supply Co - a multi-category B2B wholesale distributor. Generated "
        "data, reproducible from a fixed seed. The cost stack is real; the products, "
        "customers and competitors are not."
    )

navigation.run()
