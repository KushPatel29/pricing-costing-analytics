"""
Generate the market, transaction and cost data the pricing analysis runs on.

The cost sheet answers "what does this item cost". Everything a pricing analyst
does beyond that needs data the cost sheet has never held: what competitors are
charging, what customers actually paid after every deduction, which quotes were
lost and at what price, what the input indices did, and what the budget said
would happen. This module writes all of it, over three complete fiscal years.

It is a *generative* model, not a shuffle of random columns, and that is the
point. The relationships the analysis claims to find are put in deliberately so
that finding them is a real test of the code:

* input costs follow a commodity index with its own drift, volatility and
  seasonality; list prices follow the index at a **partial pass-through and a
  lag**, so margin compresses in a rising market exactly as it does in life;
* volume responds to price at a **known elasticity per category**, so a
  regression that fails to recover it is a bug in the regression;
* standard cost is frozen at each fiscal year start while actual cost keeps
  moving, so **purchase price variance accumulates through the year** instead
  of being noise;
* discounts are a function of customer tier, channel and terms, so the **price
  band for one product across its customers is wide and explicable**;
* quotes are won and lost on a logistic in price ratio with a
  **segment-specific sensitivity**, so the willingness-to-pay curve is real.

Nothing here reads the clock or the network. Usage::

    python -m seed.generate_market
    python -m seed.generate_market --items 120 --customers 60 --out-dir data
"""

from __future__ import annotations

import argparse
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from costing.formulas import build_cost_stack, calculate_price_from_margin
from seed.catalogue import (
    BRAND_TIER_MARGIN,
    BRAND_TIER_PREMIUM,
    BRAND_TIER_WEIGHTS,
    BRAND_TIERS,
    CATEGORIES,
    CATEGORY_ELASTICITY,
    CATEGORY_INDEX,
    CATEGORY_WEIGHTS,
    COMMODITY_INDICES,
    COMPETITORS,
    DATA_END,
    DATA_START,
    DEFAULT_CUSTOMERS,
    DEFAULT_ITEMS,
    DEFAULT_SEED,
    LIFECYCLE,
    LIFECYCLE_WEIGHTS,
    OBSERVATION_SOURCES,
    PACK_FORMATS,
    PAYMENT_TERM_DISCOUNT,
    PAYMENT_TERMS,
    PRICE_LISTS,
    PROMO_MECHANIC_NAMES,
    PROMO_MECHANIC_WEIGHTS,
    PROMO_MECHANICS,
    REGION_WEIGHTS,
    REGIONS,
    SEASONALITY,
    SEGMENT_CHANNEL,
    SEGMENT_PRICE_SENSITIVITY,
    SEGMENT_WEIGHTS,
    SEGMENTS,
    SUBCATEGORIES,
    SUPPLIERS,
    TIER_DISCOUNT,
    TIER_RANGE,
    TIER_REBATE,
    TIER_VOLUME_WEIGHT,
    TIER_WEIGHTS,
    TIERS,
    VENDOR_LANES,
    add_months,
    day_range,
    fiscal_period,
    fiscal_year,
    month_range,
    pick,
    week_starts,
)
from seed.operations import (
    assign_salespeople,
    build_cost_element_facts,
    build_cost_elements,
    build_fixed_costs,
    build_price_changes,
    build_promotions,
    build_salespeople,
)

OUT_DIR = Path("data")

MECHANIC_LIFT = {name: lift for name, lift, _ in PROMO_MECHANICS}
MECHANIC_DEPTH = {name: depth for name, _, depth in PROMO_MECHANICS}

# The catalogue is drawn from its own stream so it does not shift when anything
# else in the generator changes how much randomness it consumes. That is what
# lets seed/generate_sheets.py rebuild the identical catalogue on its own -- the
# cost sheet and the semantic model describe the same 240 items, not two
# independent draws that happen to share a numbering scheme.
PRODUCT_STREAM = 0x9E37
PANEL_STREAM = 0x85EB


def product_catalogue(*, seed: int = DEFAULT_SEED,
                      items: int = DEFAULT_ITEMS) -> pd.DataFrame:
    """The product dimension, reproducible from the seed alone."""
    return build_products(np.random.default_rng(seed ^ PRODUCT_STREAM), items)


def price_cost_panel(products: pd.DataFrame, index_m: pd.DataFrame, *,
                     seed: int = DEFAULT_SEED) -> pd.DataFrame:
    """
    The monthly cost and price panel, on its own stream.

    Same reason as the catalogue, and it was caught the hard way: the panel used
    to run on the shared generator, downstream of ``build_customers``, so the
    cost sheet -- which rebuilds the panel by itself and has no customers to
    build -- landed on a *different* draw and priced every item somewhere else.
    Item codes matched, descriptions matched, and the costs were quietly
    unrelated. An independent stream makes the panel a function of the seed and
    the products, and nothing else.
    """
    return build_price_cost_panel(products, index_m, np.random.default_rng(seed ^ PANEL_STREAM))

# List prices are reviewed quarterly, but not all on the same quarter. Reviews
# are staggered across the catalogue, which is both what actually happens (the
# category manager gets through the book a slice at a time) and what makes the
# data usable: if every price moved in the same month with the same index, price
# and time would be collinear and no elasticity could be identified from either.
REVIEW_CYCLES = ((7, 10, 1, 4), (8, 11, 2, 5), (9, 12, 3, 6))

# Not every item gets looked at. A category manager works through the book a
# slice at a time and the long tail falls off the end of the list -- which is
# how a processor ends up with items whose price has not moved in eighteen
# months while their input index rose a quarter. Those items are the timeliness
# finding, and they are worth real money: they are the cheapest margin in the
# book precisely because nobody has been near them.
REVIEW_CADENCES = (("Quarterly", 0.70), ("Semi-annual", 0.21), ("Unmanaged", 0.09))

# Neglect has a floor. An item can sit unreviewed for two years while its cost
# creeps, but once the margin on it goes visibly thin somebody notices -- a
# controller in a margin pack, a rep who cannot make the numbers work. Without
# this the unmanaged items run to a *negative* contribution and drag a whole
# category under water, which is directionally the right story told at an
# implausible magnitude: no processor sells a category below variable cost for
# two years without anyone raising it.
EMERGENCY_MARGIN_FLOOR = 0.09

# How much of an input-cost move each category's price review passes on.
# A commodity shopped against a visible marketplace price passes through most
# of a cost move because everyone else is doing the same; a differentiated
# beauty or pet line is priced on position and absorbs it.
CATEGORY_PASSTHROUGH = {
    "Consumer Electronics": 0.81,
    "Office & Stationery": 0.79,
    "Apparel & Accessories": 0.66,
    "Home & Kitchen": 0.72,
    "Sporting Goods": 0.61,
    "Tools & Hardware": 0.74,
    "Health & Beauty": 0.42,
    "Pet Supplies": 0.35,
}

# The sellable rate an item achieves: units received against units that can
# actually be shipped, after inbound damage, warehouse shrink and the returns
# that come back unsellable. It is the same divisor a processing yield is --
# you have to buy 1/rate units for every one you sell -- and in e-commerce it
# is nowhere near 1.0. Apparel and electronics carry the heaviest return rates
# and the most unsellable comebacks; paper and pet food barely move.
CATEGORY_SELLABLE_RATE = {
    "Apparel & Accessories": 0.86,
    "Consumer Electronics": 0.90,
    "Health & Beauty": 0.93,
    "Home & Kitchen": 0.94,
    "Sporting Goods": 0.93,
    "Tools & Hardware": 0.95,
    "Pet Supplies": 0.97,
    "Office & Stationery": 0.975,
}

# Typical landed cost per selling unit, before brand tier. A monitor and a pack
# of gel pens are both "one unit" and are two orders of magnitude apart, which
# is exactly why a distributor cannot price on a single markup rule.
CATEGORY_UNIT_COST = {
    "Consumer Electronics": 34.0,
    "Home & Kitchen": 18.0,
    "Office & Stationery": 6.5,
    "Tools & Hardware": 22.0,
    "Health & Beauty": 9.0,
    "Sporting Goods": 26.0,
    "Pet Supplies": 12.0,
    "Apparel & Accessories": 14.0,
}

# Freight to the customer, per unit, before the small-drop penalty.
# Outbound parcel and LTL cost per unit, by destination region from the two
# distribution centres. Both DCs are western, so the eastern regions carry a
# zone penalty that no amount of pricing discipline removes.
REGION_FREIGHT_OUT = {
    "West": 0.28, "Southwest": 0.36, "Midwest": 0.52,
    "Southeast": 0.61, "Northeast": 0.67,
}

# Cost of picking, staging and paperwork for one order line, spread over its
# pounds. This is what makes a 12 lb drop unprofitable at any price.
ORDER_HANDLING_PER_LINE = 4.85

# List is a *book* price, not a price anyone pays. Roughly a fifth of it comes
# straight back off as discount, rebate, terms and delivery before the money
# reaches us, so the book price is set above the target-margin price by that
# much -- otherwise the target margin is only achievable at zero discount and
# every real line in the file is loss-making by construction.
TYPICAL_DEDUCTION_RATE = 0.20

OVERHEAD_POOLS = (
    ("Warehouse operations", 0.212), ("Inventory carrying", 0.086),
    ("Returns processing", 0.041), ("Outbound logistics", 0.129),
    ("Selling and admin", 0.164),
)


# ---------------------------------------------------------------------------
# Commodity indices
# ---------------------------------------------------------------------------


def build_commodity_index(rng: np.random.Generator) -> pd.DataFrame:
    """
    Weekly index per input market: drift, volatility and an annual season.

    A geometric random walk rather than a straight line, because a straight
    line makes pass-through trivially estimable and every lag identical. The
    seasonal term is what puts a genuine "cost rose but we could not raise
    price until the review" gap into the data.
    """
    weeks = week_starts()
    rows = []
    for name, (drift, vol, amplitude, peak_month) in COMMODITY_INDICES.items():
        weekly_drift = (1.0 + drift) ** (1 / 52.0) - 1.0
        level = 100.0
        shocks = rng.normal(0.0, vol, len(weeks))
        for i, week in enumerate(weeks):
            level *= 1.0 + weekly_drift + shocks[i]
            phase = 2 * math.pi * ((week.month - peak_month) % 12) / 12.0
            seasonal = 1.0 + amplitude * math.cos(phase)
            rows.append(
                {
                    "week_start": week,
                    "index_name": name,
                    "index_value": round(level * seasonal, 4),
                    "trend_value": round(level, 4),
                }
            )
    df = pd.DataFrame(rows)
    df["month"] = pd.to_datetime(df["week_start"]).values.astype("datetime64[M]")
    return df


def monthly_index(index_df: pd.DataFrame) -> pd.DataFrame:
    """Average the weekly index to months; that is the grain costs are set on."""
    grouped = (
        index_df.groupby(["index_name", "month"], as_index=False)["index_value"]
        .mean()
        .rename(columns={"index_value": "index_value"})
    )
    grouped["index_value"] = grouped["index_value"].round(4)
    return grouped


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------


def build_products(rng: np.random.Generator, n: int) -> pd.DataFrame:
    """The catalogue: identity, cost drivers and the elasticity it will obey."""
    months = month_range()
    rows = []
    # No two SKUs share a sub-category, brand tier and pack format. Without
    # this, 240 products collapsed to 137 distinct descriptions: four separate
    # items all called "27in Monitor - Value", two of them identical in every
    # attribute. A duplicate label is not cosmetic -- it merges rows in any cut
    # keyed on the product, puts two identical entries in a dropdown, and
    # labels two points on a scatter with the same name. 55 sub-categories x 4
    # tiers x 6 packs is 1,320 combinations for 240 draws, so rejection costs
    # almost nothing and stays deterministic under a fixed seed.
    taken: set[tuple[str, str, str]] = set()
    for i in range(n):
        for _ in range(200):
            category = pick(rng, CATEGORIES, CATEGORY_WEIGHTS)
            subcategory = pick(rng, SUBCATEGORIES[category])
            tier = pick(rng, BRAND_TIERS, BRAND_TIER_WEIGHTS)
            pack_name, units_per_case = PACK_FORMATS[int(rng.integers(len(PACK_FORMATS)))]
            if (subcategory, tier, pack_name) not in taken:
                break
        taken.add((subcategory, tier, pack_name))
        lifecycle = pick(rng, LIFECYCLE, LIFECYCLE_WEIGHTS)

        units_per_uom = float(units_per_case)
        base_cost = float(np.clip(
            rng.lognormal(np.log(CATEGORY_UNIT_COST[category]), 0.42), 0.9, 320.0))
        base_cost *= BRAND_TIER_PREMIUM[tier] ** 0.55

        # Sellable rate: units received against units that can actually ship,
        # after inbound damage, shrink and unsellable returns. Per item because
        # a returned monitor and a returned pack of copy paper are not the same
        # problem.
        centre = CATEGORY_SELLABLE_RATE[category]
        recovery = float(np.clip(rng.normal(centre, 0.035), 0.72, 0.995))

        launch_offset = int(rng.integers(0, 6)) if lifecycle == "Launch" else 0
        launch_month = months[min(len(months) - 1, len(months) - 1 - launch_offset)] \
            if lifecycle == "Launch" else months[0]

        rows.append(
            {
                "product_id": str(20000 + i),
                # The pack format is part of the name because it is part of
                # the product: the same monitor as a single unit and as a
                # 48-carton are different lines with different costs.
                "description": f"{subcategory} - {tier}, {pack_name}",
                "category": category,
                "sub_category": subcategory,
                "brand_tier": tier,
                "pack_format": pack_name,
                "units_per_billing_uom": round(units_per_uom, 3),
                "lifecycle": lifecycle,
                "supplier": SUPPLIERS[int(rng.integers(len(SUPPLIERS)))],
                "inbound_lane": VENDOR_LANES[int(rng.integers(len(VENDOR_LANES)))],
                "commodity_index": CATEGORY_INDEX[category],
                "elasticity": round(
                    CATEGORY_ELASTICITY[category] * float(rng.uniform(0.82, 1.18))
                    * (0.82 if tier in ("Premium brand", "Private label") else 1.0),
                    4,
                ),
                "target_margin": round(
                    BRAND_TIER_MARGIN[tier] + float(rng.normal(0.0, 0.014)), 4
                ),
                "standard_sellable_rate": round(recovery, 4),
                "base_input_cost_unit": round(base_cost, 4),
                "handling_cost_unit": round(
                    float(np.clip(rng.normal(0.42, 0.16), 0.06, 1.9)), 4),
                "labelling_cost_unit": 0.09 if rng.random() < 0.42 else 0.0,
                "damage_rate": round(float(np.clip(rng.normal(0.05, 0.03), 0.0, 0.22)), 4),
                "adj_unit": round(float(rng.normal(0.0, 0.14)), 4),
                "base_volume_units": round(
                    float(rng.lognormal(np.log(1900.0), 0.95))
                    * {"Launch": 0.35, "Growth": 0.85, "Mature": 1.0, "Decline": 0.62}[lifecycle],
                    1,
                ),
                "launch_month": launch_month,
            }
        )
    return pd.DataFrame(rows)


def build_customers(rng: np.random.Generator, n: int) -> pd.DataFrame:
    """The customer base, with everything that will drive its deductions."""
    prefixes = ("Cedar", "Harbour", "Granville", "Kitsilano", "Mount", "Fraser", "Salish",
                "Okanagan", "Selkirk", "Burrard", "Chilcotin", "Sunshine", "Stanley",
                "Kootenay", "Alder", "Juniper", "Marine", "Coastal", "Riverside", "Summit")
    suffixes = ("Bistro", "Kitchen Group", "Provisions", "Market", "Hospitality",
                "Grill House", "Larder", "Trading Co", "Foods", "Table", "Butchery",
                "Fine Foods", "Catering", "Public House", "Supply")
    rows = []
    used: set[str] = set()
    for i in range(n):
        segment = pick(rng, SEGMENTS, SEGMENT_WEIGHTS)
        tier = pick(rng, TIERS, TIER_WEIGHTS)
        region = pick(rng, REGIONS, REGION_WEIGHTS)
        for _ in range(40):
            name = f"{pick(rng, prefixes)} {pick(rng, suffixes)}"
            if name not in used:
                break
        used.add(name)
        terms = pick(rng, PAYMENT_TERMS, (0.13, 0.44, 0.17, 0.16, 0.10))
        price_list = pick(rng, PRICE_LISTS, (0.31, 0.34, 0.35)) if tier in ("A", "B") \
            else pick(rng, PRICE_LISTS, (0.08, 0.22, 0.70))
        lo, hi = TIER_RANGE[tier]
        rows.append(
            {
                "customer_id": f"CU{2000 + i}",
                "customer_name": name,
                "segment": segment,
                "channel": SEGMENT_CHANNEL[segment],
                "region": region,
                "tier": tier,
                "price_list": price_list,
                "payment_terms": terms,
                "range_breadth": int(rng.integers(lo, hi)),
                "volume_weight": round(
                    TIER_VOLUME_WEIGHT[tier] * float(rng.uniform(0.62, 1.44)), 4
                ),
                "price_sensitivity": round(
                    SEGMENT_PRICE_SENSITIVITY[segment] * float(rng.uniform(0.85, 1.15)), 3
                ),
                "avg_drop_size_units": round(
                    float(np.clip(rng.lognormal(np.log(210.0), 0.7), 12.0, 4200.0))
                    * {"A": 3.1, "B": 1.7, "C": 1.0, "D": 0.55}[tier], 1
                ),
            }
        )
    return pd.DataFrame(rows)


def build_competitors() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "competitor_id": cid, "competitor_name": name, "positioning": pos,
                "price_multiplier": mult, "catalogue_coverage": coverage,
            }
            for cid, name, pos, mult, coverage in COMPETITORS
        ]
    )


def build_date_dim() -> pd.DataFrame:
    """
    A daily date table with a fiscal calendar, and a contiguous month index.

    ``month_index`` is what time intelligence runs on. A month-grain dimension
    cannot be marked as a date table in a semantic model, so "previous month"
    has to be a subtraction on an integer that never skips -- which is exactly
    what breaks when the index is built from year*12+month on sparse data.
    """
    days = day_range()
    base = DATA_START.year * 12 + DATA_START.month
    rows = []
    for day in days:
        rows.append(
            {
                "date": day,
                "year": day.year,
                "month": date(day.year, day.month, 1),
                "month_name": day.strftime("%b %Y"),
                "month_short": day.strftime("%b"),
                "month_number": day.month,
                "quarter": f"{day.year} Q{(day.month - 1) // 3 + 1}",
                "fiscal_year": fiscal_year(day),
                "fiscal_year_label": f"FY{fiscal_year(day)}",
                "fiscal_period": fiscal_period(day),
                "fiscal_quarter": f"FY{fiscal_year(day)} Q{(fiscal_period(day) - 1) // 3 + 1}",
                "week_start": day.fromordinal(day.toordinal() - day.weekday()),
                "month_index": day.year * 12 + day.month - base,
                "is_month_end": (add_months(date(day.year, day.month, 1), 1)
                                 - date(day.year, day.month, 1)).days == day.day,
            }
        )
    return pd.DataFrame(rows)


def build_month_dim(date_df: pd.DataFrame) -> pd.DataFrame:
    """The month grain the cost, price and budget facts are keyed on."""
    cols = ["month", "month_name", "month_short", "month_number", "year", "quarter",
            "fiscal_year", "fiscal_year_label", "fiscal_period", "fiscal_quarter",
            "month_index"]
    return (
        date_df[cols].drop_duplicates(subset=["month"]).sort_values("month").reset_index(drop=True)
    )


# ---------------------------------------------------------------------------
# The monthly cost and price panel -- the spine everything else hangs off
# ---------------------------------------------------------------------------


def build_price_cost_panel(
    products: pd.DataFrame, index_m: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    One row per product per month: input cost, standard cost, the full cost
    stack, and the list price that quarter's review set.

    The three series move at different speeds on purpose.

    * **Actual input cost** moves every month with the commodity index.
    * **Standard cost** is frozen at each fiscal year start, so purchase price
      variance builds through the year and resets in July -- which is what a
      standard-costing shop actually sees, and why a PPV chart that trends
      smoothly across a year boundary is usually wrong.
    * **List price** only moves at a quarterly review, and then only by the
      category's pass-through share of the index move. Margin therefore
      compresses between reviews in a rising market and recovers at one, which
      is the single most useful pattern in this whole dataset.
    """
    months = month_range()
    index_lookup = {
        (row.index_name, row.month.date() if hasattr(row.month, "date") else row.month):
        row.index_value
        for row in index_m.itertuples()
    }

    def index_at(name: str, month: date) -> float:
        return float(index_lookup.get((name, month), 100.0))

    rows = []
    for product in products.itertuples():
        idx_name = product.commodity_index
        idx_base = index_at(idx_name, months[0])
        idio = rng.normal(0.0, 0.026, len(months)).cumsum()

        standard_cost = None
        list_price = None
        last_review_index = idx_base
        passthrough = CATEGORY_PASSTHROUGH[product.category]
        review_months = REVIEW_CYCLES[int(rng.integers(len(REVIEW_CYCLES)))]
        cadence = pick(rng, tuple(c for c, _ in REVIEW_CADENCES),
                       tuple(w for _, w in REVIEW_CADENCES))
        if cadence == "Semi-annual":
            review_months = review_months[::2]
        elif cadence == "Unmanaged":
            # Reviewed once, early, and then forgotten.
            review_months = (review_months[0],)
        unmanaged_after = 8 if cadence == "Unmanaged" else len(months) + 1

        for m_i, month in enumerate(months):
            idx = index_at(idx_name, month)
            actual_cost = product.base_input_cost_unit * (idx / idx_base) * math.exp(idio[m_i])

            # Standard cost is reset each fiscal year, to the cost as it stood
            # when the budget was built -- one month before the year starts.
            if standard_cost is None or month.month == 7:
                standard_cost = actual_cost * float(rng.uniform(0.985, 1.015))

            stack = build_cost_stack(
                vendor_invoice_price=actual_cost * product.units_per_billing_uom,
                units_per_billing_uom=product.units_per_billing_uom,
                adj=product.adj_unit,
                vendor=product.inbound_lane,
                recovery=product.standard_sellable_rate,
                handling_per_unit=product.handling_cost_unit,
                labelling_per_unit=product.labelling_cost_unit,
                base_margin=product.target_margin,
                list_margin=product.target_margin + 0.08,
            )
            standard_stack = build_cost_stack(
                vendor_invoice_price=standard_cost * product.units_per_billing_uom,
                units_per_billing_uom=product.units_per_billing_uom,
                adj=product.adj_unit,
                vendor=product.inbound_lane,
                recovery=product.standard_sellable_rate,
                handling_per_unit=product.handling_cost_unit,
                labelling_per_unit=product.labelling_cost_unit,
                base_margin=product.target_margin,
                list_margin=product.target_margin + 0.08,
            )

            if list_price is None:
                # Set so a customer taking the typical deduction stack still
                # realises the product's target margin on pocket price.
                list_price = (
                    stack["base_price"]
                    / (1.0 - TYPICAL_DEDUCTION_RATE)
                    * BRAND_TIER_PREMIUM[product.brand_tier] ** 0.35
                )
                last_review_index = idx
            elif (
                list_price is not None
                and (list_price - stack["final_cost"]) / list_price < EMERGENCY_MARGIN_FLOOR
            ):
                # Out-of-cycle correction: straight back to the target margin,
                # which is what actually happens when a thin item is spotted.
                list_price = calculate_price_from_margin(
                    stack["final_cost"], product.target_margin
                ) / (1.0 - TYPICAL_DEDUCTION_RATE)
                last_review_index = idx
            elif month.month in review_months and m_i < unmanaged_after:
                # Three things move a price at a review, and only the first is
                # the index: the pass-through, the reviewer's own judgement
                # about where this line sits, and the occasional correction of
                # a price that had drifted away from the market.
                move = (idx / last_review_index) - 1.0
                judgement = float(rng.normal(0.0, 0.034))
                correction = float(rng.normal(0.0, 0.062)) if rng.random() < 0.15 else 0.0
                list_price *= max(0.72, 1.0 + passthrough * move + judgement + correction)
                last_review_index = idx

            rows.append(
                {
                    "product_id": product.product_id,
                    "month": month,
                    "commodity_index": idx_name,
                    "index_value": round(idx, 4),
                    "actual_input_cost_unit": round(actual_cost, 4),
                    "standard_input_cost_unit": round(standard_cost, 4),
                    "actual_final_cost_unit": round(stack["final_cost"], 4),
                    "standard_final_cost_unit": round(standard_stack["final_cost"], 4),
                    "landed_cost_unit": round(stack["landed_cost"], 4),
                    "sellable_input_cost_unit": round(stack["recovery_input"], 4),
                    "freight_in_unit": round(stack["freight"], 4),
                    "list_price_unit": round(list_price, 4),
                    "review_cadence": cadence,
                }
            )
    panel = pd.DataFrame(rows)
    panel["list_margin_pct"] = (
        (panel["list_price_unit"] - panel["actual_final_cost_unit"]) / panel["list_price_unit"]
    ).round(6)
    return panel


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


def _promo_calendar(products: pd.DataFrame,
                    rng: np.random.Generator) -> dict[tuple[str, date], str]:
    """
    Which product-months are on promotion.

    Roughly one month in nine per product, clustered rather than uniform: a
    promotion runs, it ends, and the same line is rarely promoted twice in a
    quarter. Without the clustering a promo flag is indistinguishable from
    noise and the promotional lift is unrecoverable.
    """
    months = month_range()
    promos: dict[tuple[str, date], str] = {}
    for product in products.itertuples():
        cursor = 0
        while cursor < len(months):
            cursor += int(rng.integers(5, 14))
            if cursor >= len(months):
                break
            length = int(rng.integers(1, 3))
            mechanic = pick(rng, PROMO_MECHANIC_NAMES, PROMO_MECHANIC_WEIGHTS)
            for offset in range(length):
                if cursor + offset < len(months):
                    promos[(product.product_id, months[cursor + offset])] = mechanic
            cursor += length
    return promos


def build_sales(
    products: pd.DataFrame,
    customers: pd.DataFrame,
    panel: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    Invoice lines at month x customer x product, with every waterfall deduction.

    Volume obeys the product's own elasticity against its list price, so the
    elasticity module has something real to recover. Deductions are a function
    of who the customer is, which is what makes the price band for a single
    product wide and -- crucially -- explicable, rather than wide and random.
    """
    months = month_range()
    month_pos = {m: i for i, m in enumerate(months)}
    promos = _promo_calendar(products, rng)

    panel_lookup: dict[tuple[str, date], dict] = {}
    for row in panel.itertuples():
        month = row.month.date() if hasattr(row.month, "date") else row.month
        panel_lookup[(row.product_id, month)] = {
            "list": row.list_price_unit,
            "cost": row.actual_final_cost_unit,
            "std_cost": row.standard_final_cost_unit,
        }

    product_rows = list(products.itertuples())
    n_products = len(product_rows)
    base_list = {
        p.product_id: panel_lookup[(p.product_id, months[0])]["list"] for p in product_rows
    }

    # Which products each customer carries. Sampling indices rather than
    # objects keeps the assignment reproducible across numpy versions.
    # Which products each customer carries. Weighted toward two or three core
    # categories rather than drawn flat across the catalogue: a marketplace
    # seller specialises, a hardware retailer does not stock cosmetics, and a
    # flat draw produces a co-purchase analysis where a mechanical keyboard
    # "travels with" dry dog food -- which is not a finding, it is the absence
    # of one.
    by_category: dict[str, list[int]] = {}
    for index, row in enumerate(product_rows):
        by_category.setdefault(row.category, []).append(index)
    all_categories = sorted(by_category)

    assignments: list[tuple] = []
    for customer in customers.itertuples():
        breadth = min(customer.range_breadth, n_products)
        core_count = int(rng.integers(2, 4))
        core = set(pick(rng, tuple(all_categories), size=core_count))
        # Four fifths of the range comes from the core categories, the rest is
        # the long tail every buyer picks up alongside it.
        weights = np.array([
            (4.0 if row.category in core else 1.0) for row in product_rows
        ])
        weights /= weights.sum()
        chosen = rng.choice(n_products, size=breadth, replace=False, p=weights)
        affinity = rng.uniform(0.35, 1.65, breadth)
        for slot, product_index in enumerate(chosen):
            assignments.append((customer, product_rows[int(product_index)], float(affinity[slot])))

    lines = []
    for customer, product, affinity in assignments:
        seasonal = SEASONALITY[product.category]
        elasticity = product.elasticity
        # The rep's own appetite for discounting, on top of what the tier
        # entitles the customer to. This is the whole reason a salesperson
        # belongs in a pricing model: it moves the realised price without
        # moving anything a product- or customer-level cut would show.
        appetite = getattr(customer, "discount_appetite", 1.0)
        tier_discount = TIER_DISCOUNT[customer.tier] * appetite
        tier_rebate = TIER_REBATE[customer.tier]
        terms_rate = PAYMENT_TERM_DISCOUNT[customer.payment_terms]
        contract_rate = 0.031 if customer.price_list == "Contract" else (
            0.014 if customer.price_list == "Negotiated" else 0.0
        )
        coop_rate = 0.009 if customer.channel == "Retail" else 0.0
        returns_rate = 0.011 if customer.channel == "Retail" else 0.004
        freight_allowance_rate = (
            0.013 if customer.tier in ("A", "B") and customer.region != "BC Lower Mainland" else 0.0
        )
        region_freight = REGION_FREIGHT_OUT[customer.region]
        # Small drops pay more freight per unit: the truck still has to stop.
        # Capped at 3.2x, because past that the order is refused rather than
        # shipped at a loss -- an uncapped penalty puts a fifth of the book
        # under water and drowns the exception report in lines nobody would
        # have accepted in the first place.
        drop_penalty = float(np.clip(320.0 / max(customer.avg_drop_size_units, 25.0), 0.35, 3.2))

        # Customers order on a rhythm, not at random: a line is a weekly staple,
        # a monthly re-order or a quarterly top-up. Modelling that as an
        # independent coin flip each month was the largest single source of
        # noise in the aggregate volume series -- with eleven customers on a
        # product, iid participation swings the monthly total by a quarter, and
        # buries the price response the elasticity page exists to find.
        cadence = int(pick(rng, (1, 1, 2, 3), (0.42, 0.20, 0.24, 0.14)))
        phase = int(rng.integers(cadence))
        missed = rng.random(len(months)) < 0.09
        noise = rng.lognormal(0.0, 0.18, len(months))
        launch = product.launch_month

        for m_i, month in enumerate(months):
            if m_i % cadence != phase or missed[m_i] or month < launch:
                continue
            panel_row = panel_lookup[(product.product_id, month)]
            list_price = panel_row["list"]
            price_ratio = list_price / base_list[product.product_id]

            trend = {
                "Launch": 1.0 + 0.055 * (m_i - month_pos.get(launch, 0)),
                "Growth": 1.0 + 0.012 * m_i,
                "Mature": 1.0 + 0.0015 * m_i,
                "Decline": 1.0 - 0.009 * m_i,
            }[product.lifecycle]

            mechanic = promos.get((product.product_id, month))
            on_promo = mechanic is not None
            promo_lift = MECHANIC_LIFT[mechanic] if on_promo else 1.0

            qty = (
                product.base_volume_units
                / 26.0
                * affinity
                * customer.volume_weight
                * seasonal[month.month - 1]
                * max(0.05, trend)
                * (price_ratio ** elasticity)
                * promo_lift
                * noise[m_i]
            )
            qty = float(np.clip(qty, 15.0, 90000.0))

            promo_rate = (
                float(rng.uniform(*MECHANIC_DEPTH[mechanic])) if on_promo else 0.0)
            volume_discount = list_price * tier_discount * float(rng.uniform(0.86, 1.14))
            contract_discount = list_price * contract_rate
            promo_discount = list_price * promo_rate
            rebate = list_price * tier_rebate
            coop = list_price * coop_rate
            terms = list_price * terms_rate
            freight_allowance = list_price * freight_allowance_rate
            # Neither delivery cost can run away on a small line: together they
            # are held under a sixth of list, which is the point at which a
            # despatcher consolidates the drop instead of sending the truck.
            freight_out = min(region_freight * drop_penalty, list_price * 0.10)
            order_handling = min(ORDER_HANDLING_PER_LINE / qty, list_price * 0.06)
            returns = list_price * returns_rate * float(rng.uniform(0.4, 1.8))

            lines.append(
                {
                    "month": month,
                    "product_id": product.product_id,
                    "customer_id": customer.customer_id,
                    "quantity_units": round(qty, 2),
                    "list_price": round(list_price, 4),
                    "volume_discount": round(volume_discount, 4),
                    "contract_discount": round(contract_discount, 4),
                    "promo_discount": round(promo_discount, 4),
                    "rebate": round(rebate, 4),
                    "coop_marketing": round(coop, 4),
                    "payment_terms_discount": round(terms, 4),
                    "freight_allowance": round(freight_allowance, 4),
                    "freight_out": round(freight_out, 4),
                    "order_handling": round(order_handling, 4),
                    "returns_credits": round(returns, 4),
                    "final_cost": round(panel_row["cost"], 4),
                    "standard_cost": round(panel_row["std_cost"], 4),
                    "on_promotion": int(on_promo),
                    "promo_mechanic": mechanic or "",
                }
            )

    sales = pd.DataFrame(lines)
    return _add_derived_price_levels(sales)


def _add_derived_price_levels(sales: pd.DataFrame) -> pd.DataFrame:
    """
    Materialise the four waterfall levels onto the fact.

    Computed once here rather than left as a measure, because the Power BI
    model, the Streamlit pages and the exception scan all need them and three
    independent re-derivations of the same subtraction is three chances to get
    a different answer.
    """
    on_invoice = sales["volume_discount"] + sales["contract_discount"] + sales["promo_discount"]
    off_invoice = (
        sales["rebate"] + sales["coop_marketing"]
        + sales["payment_terms_discount"] + sales["freight_allowance"]
    )
    cost_to_serve = sales["freight_out"] + sales["order_handling"] + sales["returns_credits"]

    sales["invoice_price"] = (sales["list_price"] - on_invoice).clip(lower=0).round(4)
    sales["net_price"] = (sales["invoice_price"] - off_invoice).clip(lower=0).round(4)
    sales["pocket_price"] = (sales["net_price"] - cost_to_serve).clip(lower=0).round(4)
    sales["on_invoice_discounts"] = on_invoice.round(4)
    sales["off_invoice_deductions"] = off_invoice.round(4)
    sales["cost_to_serve"] = cost_to_serve.round(4)

    sales["revenue"] = (sales["invoice_price"] * sales["quantity_units"]).round(2)
    sales["pocket_revenue"] = (sales["pocket_price"] * sales["quantity_units"]).round(2)
    sales["cogs"] = (sales["final_cost"] * sales["quantity_units"]).round(2)
    sales["standard_cogs"] = (sales["standard_cost"] * sales["quantity_units"]).round(2)
    sales["pocket_margin"] = (sales["pocket_revenue"] - sales["cogs"]).round(2)
    sales["list_value"] = (sales["list_price"] * sales["quantity_units"]).round(2)
    return sales


# ---------------------------------------------------------------------------
# Competitor prices, quotes, cost ledger, budget, overhead
# ---------------------------------------------------------------------------


def build_competitor_prices(
    products: pd.DataFrame, panel: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    What the market was seen charging, product by product, month by month.

    Coverage is partial and uneven -- each competitor is observed on a fraction
    of the catalogue, and only some months -- because a competitive file is
    always partial, and an analysis that silently treats "no observation" as
    "at parity" is the most common way a price index lies.
    """
    competitors = build_competitors()
    months = month_range()
    panel_lookup = {
        (row.product_id, row.month.date() if hasattr(row.month, "date") else row.month):
        row.list_price_unit
        for row in panel.itertuples()
    }
    rows = []
    for product in products.itertuples():
        # A product's own "market reference" is not our price: it is what the
        # category is worth, which we may be above or below.
        reference_offset = float(rng.uniform(0.86, 1.14))
        for competitor in competitors.itertuples():
            if rng.random() > competitor.catalogue_coverage:
                continue
            drift = rng.normal(0.0, 0.018, len(months)).cumsum()
            for m_i, month in enumerate(months):
                if rng.random() > 0.62:          # not observed every month
                    continue
                our_list = panel_lookup[(product.product_id, month)]
                observed = (
                    our_list * reference_offset * competitor.price_multiplier
                    * math.exp(drift[m_i]) * float(rng.uniform(0.97, 1.03))
                )
                rows.append(
                    {
                        "month": month,
                        "product_id": product.product_id,
                        "competitor_id": competitor.competitor_id,
                        "observed_price": round(observed, 4),
                        "our_list_price": round(our_list, 4),
                        "source": OBSERVATION_SOURCES[int(rng.integers(len(OBSERVATION_SOURCES)))],
                        "in_stock": int(rng.random() > 0.08),
                        "observation_age_days": int(rng.integers(1, 46)),
                    }
                )
    return pd.DataFrame(rows)


def build_quotes(
    products: pd.DataFrame,
    customers: pd.DataFrame,
    panel: pd.DataFrame,
    rng: np.random.Generator,
    n: int = 5600,
) -> pd.DataFrame:
    """
    Won and lost quotes -- the only place demand at prices we did not charge
    is observable.

    Win probability is a logistic in the ratio of our quote to the competing
    one, with the slope set by the buyer's segment. A distributor at
    sensitivity 16 falls off a cliff above parity; a hotel at 6.5 barely
    notices. Recovering those slopes back out of this file is what the
    willingness-to-pay page is for.
    """
    months = month_range()
    panel_lookup = {
        (row.product_id, row.month.date() if hasattr(row.month, "date") else row.month):
        (row.list_price_unit, row.actual_final_cost_unit)
        for row in panel.itertuples()
    }
    product_ids = products["product_id"].tolist()
    loss_reasons = ("Price", "Price", "Price", "Lead time", "Specification", "Incumbent supplier")

    rows = []
    for i in range(n):
        customer = customers.iloc[int(rng.integers(len(customers)))]
        product_id = product_ids[int(rng.integers(len(product_ids)))]
        month = months[int(rng.integers(len(months)))]
        list_price, cost = panel_lookup[(product_id, month)]

        # Competitors quote off their own reference, which sits below our list.
        competitor_price = list_price * float(rng.uniform(0.80, 1.06))
        # We quote off list with a discount that depends on how badly we want it.
        quoted = list_price * (1.0 - float(np.clip(rng.normal(0.11, 0.065), -0.02, 0.34)))
        ratio = quoted / competitor_price if competitor_price > 0 else 1.0

        # Logistic in the price ratio. The intercept is set so the book wins
        # about half its quotes -- a generator that wins nine in ten produces
        # a win/loss file with almost no losses in it, and a curve fitted to
        # that is fitted to nothing.
        z = -0.35 - customer.price_sensitivity * (ratio - 1.0)
        won = bool(rng.random() < 1.0 / (1.0 + math.exp(-z)))
        rows.append(
            {
                "quote_id": f"Q{100000 + i}",
                "month": month,
                "customer_id": customer.customer_id,
                "product_id": product_id,
                "segment": customer.segment,
                "quantity_units": round(
                    float(np.clip(rng.lognormal(np.log(340.0), 0.9), 20, 30000)), 1),
                "list_price": round(list_price, 4),
                "quoted_price": round(quoted, 4),
                "competitor_price": round(competitor_price, 4),
                "price_ratio": round(ratio, 5),
                "final_cost": round(cost, 4),
                "won": int(won),
                "outcome": "Won" if won else "Lost",
                "loss_reason": "" if won else loss_reasons[int(rng.integers(len(loss_reasons)))],
            }
        )
    return pd.DataFrame(rows)


def build_cost_ledger(
    products: pd.DataFrame, panel: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    Purchases and production yields, for the standard-costing variances.

    Two variances are seeded here on purpose and they are seeded independently:
    a buying result (actual price against the frozen standard) and a handling
    result (actual recovery against the standard yield). Keeping them separate
    in the data is what lets the analysis attribute a bad month to purchasing
    or to the floor rather than to "cost".
    """
    rows = []
    panel_rows = {
        (row.product_id, row.month.date() if hasattr(row.month, "date") else row.month): row
        for row in panel.itertuples()
    }
    for product in products.itertuples():
        # Yield drifts with operator experience and with the season, but it
        # reverts: a plant that loses six points of recovery notices and fixes
        # it. A pure random walk never does, and after three years it has
        # wandered far enough to dominate every cost variance in the file.
        shocks = rng.normal(0.0, 0.016, len(month_range()))
        yield_drift, level = [], 0.0
        for shock in shocks:
            level = 0.72 * level + shock
            yield_drift.append(level)
        for m_i, month in enumerate(month_range()):
            row = panel_rows[(product.product_id, month)]
            if rng.random() > 0.82:              # not every item is bought every month
                continue
            output_units = float(np.clip(
                rng.lognormal(np.log(product.base_volume_units / 1.9), 0.55),
                                      50.0, 220000.0))
            standard_sellable_rate = product.standard_sellable_rate
            actual_recovery = float(np.clip(
                standard_sellable_rate * (1.0 + yield_drift[m_i] + float(rng.normal(0.0, 0.018))),
                0.35, 0.995,
            ))
            rows.append(
                {
                    "month": month,
                    "product_id": product.product_id,
                    "supplier": product.supplier,
                    "purchased_units": round(output_units / actual_recovery, 1),
                    "output_units": round(output_units, 1),
                    "standard_input_cost_unit": round(row.standard_input_cost_unit, 4),
                    "actual_input_cost_unit": round(row.actual_input_cost_unit, 4),
                    "standard_sellable_rate": round(standard_sellable_rate, 4),
                    "actual_recovery": round(actual_recovery, 4),
                    "standard_handling_unit": round(product.handling_cost_unit, 4),
                    "actual_handling_unit": round(
                        product.handling_cost_unit
                        * float(np.clip(rng.normal(1.02, 0.09), 0.7, 1.5)), 4
                    ),
                    "labour_hours": round(output_units / float(rng.uniform(38.0, 96.0)), 2),
                    "standard_hours": round(output_units / 68.0, 2),
                }
            )
    ledger = pd.DataFrame(rows)
    ledger["purchase_price_variance"] = (
        (ledger["actual_input_cost_unit"] - ledger["standard_input_cost_unit"])
        * ledger["purchased_units"]
    ).round(2)
    ledger["standard_input_units"] = (
        ledger["output_units"] / ledger["standard_sellable_rate"]).round(1)
    ledger["yield_variance"] = (
        (ledger["purchased_units"] - ledger["standard_input_units"])
        * ledger["standard_input_cost_unit"]
    ).round(2)
    ledger["handling_variance"] = (
        (ledger["actual_handling_unit"] - ledger["standard_handling_unit"]) * ledger["output_units"]
    ).round(2)
    return ledger


def build_budget(
    sales: pd.DataFrame, products: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    The plan, set once a year at category x channel x month.

    Built from the prior year's actuals plus a growth assumption, then left
    alone -- which is why the variance grows through the year. The first
    fiscal year has no prior, so it is budgeted from its own actuals with a
    forecasting error applied, which is the honest way to fake a plan that
    was written before the year happened.
    """
    joined = sales.merge(
        products[["product_id", "category"]], on="product_id", how="left"
    )
    joined["month"] = pd.to_datetime(joined["month"])
    joined["fiscal_year"] = joined["month"].apply(lambda d: fiscal_year(d.date()))

    actual = (
        joined.groupby(["fiscal_year", "month", "category"], as_index=False)
        .agg(revenue=("revenue", "sum"), volume_units=("quantity_units", "sum"),
             cogs=("cogs", "sum"), pocket_revenue=("pocket_revenue", "sum"))
    )

    rows = []
    for row in actual.itertuples():
        # A plan is a forecast, so it misses -- more in the categories whose
        # input index moved most, which is where the variance story lives.
        error = float(rng.normal(1.0, 0.085))
        growth = 1.0 + float(rng.normal(0.045, 0.02))
        rows.append(
            {
                "month": row.month.date(),
                "fiscal_year": row.fiscal_year,
                "category": row.category,
                "budget_revenue": round(row.revenue * error * growth / 1.045, 2),
                "budget_volume_units": round(row.volume_units * float(rng.normal(1.0, 0.07)), 1),
                "budget_cogs": round(row.cogs * float(rng.normal(0.99, 0.06)), 2),
                "actual_revenue": round(row.revenue, 2),
                "actual_volume_units": round(row.volume_units, 1),
                "actual_cogs": round(row.cogs, 2),
                "actual_pocket_revenue": round(row.pocket_revenue, 2),
            }
        )
    budget = pd.DataFrame(rows)
    budget["budget_margin"] = (budget["budget_revenue"] - budget["budget_cogs"]).round(2)
    budget["actual_margin"] = (budget["actual_revenue"] - budget["actual_cogs"]).round(2)
    budget["revenue_variance"] = (budget["actual_revenue"] - budget["budget_revenue"]).round(2)
    budget["margin_variance"] = (budget["actual_margin"] - budget["budget_margin"]).round(2)
    return budget


def build_overhead(sales: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Monthly overhead pools with a budgeted absorption rate per unit.

    The volume variance this produces is the one most often mistaken for
    overspending: nobody spent more, the plant just ran below the volume the
    rate was set on, and the unabsorbed pool lands in cost of sales anyway.
    """
    by_month = sales.groupby("month", as_index=False)["quantity_units"].sum()
    budgeted_volume = float(by_month["quantity_units"].mean())
    rows = []
    for row in by_month.itertuples():
        for pool, rate in OVERHEAD_POOLS:
            budgeted = budgeted_volume * rate
            actual = budgeted * float(rng.normal(1.01, 0.055))
            rows.append(
                {
                    "month": row.month,
                    "cost_pool": pool,
                    "budgeted_rate_unit": round(rate, 4),
                    "budgeted_volume_units": round(budgeted_volume, 1),
                    "actual_volume_units": round(row.quantity_units, 1),
                    "budgeted_overhead": round(budgeted, 2),
                    "actual_overhead": round(actual, 2),
                    "absorbed_overhead": round(rate * row.quantity_units, 2),
                }
            )
    overhead = pd.DataFrame(rows)
    overhead["spending_variance"] = (
        overhead["actual_overhead"] - overhead["absorbed_overhead"]
    ).round(2)
    overhead["volume_variance"] = (
        (overhead["budgeted_volume_units"] - overhead["actual_volume_units"])
        * overhead["budgeted_rate_unit"]
    ).round(2)
    return overhead


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate(
    *,
    seed: int = DEFAULT_SEED,
    items: int = DEFAULT_ITEMS,
    customers: int = DEFAULT_CUSTOMERS,
) -> dict[str, pd.DataFrame]:
    """Build every table, in the order their dependencies require."""
    rng = np.random.default_rng(seed)

    index_w = build_commodity_index(rng)
    index_m = monthly_index(index_w)
    products = product_catalogue(seed=seed, items=items)
    customer_df = build_customers(rng, customers)
    salespeople = build_salespeople(rng)
    customer_df = assign_salespeople(customer_df, salespeople, rng)
    customer_df = customer_df.merge(
        salespeople[["salesperson_id", "salesperson", "discount_appetite"]],
        on="salesperson_id", how="left",
    )
    panel = price_cost_panel(products, index_m, seed=seed)
    sales = build_sales(products, customer_df, panel, rng)
    competitor_prices = build_competitor_prices(products, panel, rng)
    quotes = build_quotes(products, customer_df, panel, rng)
    ledger = build_cost_ledger(products, panel, rng)
    budget = build_budget(sales, products, rng)
    overhead = build_overhead(sales, rng)
    cost_elements = build_cost_element_facts(panel, sales, products)
    fixed_costs = build_fixed_costs(sales, rng)
    promotions = build_promotions(sales, products, rng)
    price_changes = build_price_changes(panel, products, salespeople, rng)
    dates = build_date_dim()

    return {
        "dim_product": products.drop(columns=["launch_month"]),
        "dim_customer": customer_df.drop(columns=["discount_appetite"]),
        "dim_salesperson": salespeople,
        "dim_cost_element": build_cost_elements(),
        "dim_competitor": build_competitors(),
        "dim_date": dates,
        "dim_month": build_month_dim(dates),
        "fact_price_cost_panel": panel,
        "fact_sales": sales,
        "fact_competitor_price": competitor_prices,
        "fact_quote": quotes,
        "fact_cost_ledger": ledger,
        "fact_budget": budget,
        "fact_overhead": overhead,
        "fact_commodity_index": index_w.drop(columns=["month"]),
        "fact_cost_element": cost_elements,
        "fact_fixed_cost": fixed_costs,
        "fact_promotion": promotions,
        "fact_price_change": price_changes,
    }


def write(tables: dict[str, pd.DataFrame], out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        # newline="" so the same file lands byte-identical on Windows and Linux;
        # pandas otherwise writes CRLF on Windows and a diff of a regenerated
        # data directory becomes every line of every file.
        frame.to_csv(out_dir / f"{name}.csv", index=False, lineterminator="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--items", type=int, default=DEFAULT_ITEMS)
    ap.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    tables = generate(seed=args.seed, items=args.items, customers=args.customers)
    write(tables, args.out_dir)

    print(f"wrote {len(tables)} tables to {args.out_dir}/")
    for name, frame in tables.items():
        print(f"  {name:28s} {len(frame):>8,} rows x {len(frame.columns):>2} cols")

    sales = tables["fact_sales"]
    print()
    print(f"  window            : {DATA_START} to {DATA_END} "
          f"({len(month_range())} months, 3 fiscal years)")
    print(f"  list value        : ${sales['list_value'].sum():,.0f}")
    print(f"  invoice revenue   : ${sales['revenue'].sum():,.0f}")
    print(f"  pocket revenue    : ${sales['pocket_revenue'].sum():,.0f}")
    leak = 1 - sales["pocket_revenue"].sum() / sales["list_value"].sum()
    print(f"  leakage           : {leak:.1%} of list")
    print(f"  pocket margin     : "
          f"{sales['pocket_margin'].sum() / sales['pocket_revenue'].sum():.1%}")
    print(f"  quote win rate    : {tables['fact_quote']['won'].mean():.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
