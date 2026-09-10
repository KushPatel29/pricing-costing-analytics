"""
The costing and pricing maths, with no Streamlit in sight.

These functions used to live inside the Streamlit script, which meant the
pricing arithmetic could not be tested without booting a UI. They are pure
functions of their arguments now, so `tests/` can assert on them directly.

Vocabulary, because two of these are routinely confused:

- **recovery** (also called yield, or the sellable rate) is the fraction of
  what you buy that you can actually sell. In distribution that is units
  received against units that ship, after inbound damage, warehouse shrink and
  the returns that come back unsellable -- for e-commerce apparel it runs
  around 0.86. Costs are *divided* by recovery, because you have to buy 1/0.86
  units for every one you sell.
- **margin** is profit as a fraction of *price*, not of cost. Price is
  therefore `cost / (1 - margin)`, not `cost * (1 + margin)`. The second is
  markup, and using it where margin is meant understates price.
"""

from __future__ import annotations

import math
from typing import Any

# Inbound freight and duty per selling unit, by lane. The spread between them
# is the point: an air-freighted unit carries more than five times the inbound
# cost of the same unit on a full container, so lane mix is one of the largest
# single drivers of landed cost in a distribution business -- and it is a
# sourcing decision that pricing inherits rather than makes.
FREIGHT_RATES: dict[str, float] = {
    "Domestic FTL": 0.19,
    "Cross-dock Consolidator": 0.26,
    "Domestic LTL": 0.31,
    "Import Ocean FCL": 0.42,
    "Import Ocean LCL": 0.78,
    "Import Air Freight": 2.35,
}

DEFAULT_FREIGHT = 0.0
DEFAULT_RECOVERY = 1.0
DEFAULT_BASE_MARGIN = 0.17
DEFAULT_LIST_MARGIN = 0.25


def safe_float(value: Any, default: float = 0.0) -> float:
    """Coerce to float, returning `default` for None, NaN and junk."""
    try:
        if value is None:
            return default
        result = float(value)
        if math.isnan(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def clean_item_code(code: Any) -> str:
    """
    Normalise an item code that may have arrived from Excel as a number.

    Excel turns a code like 13667 into the float 13667.0, and a code with a
    thousands separator into "13,667". Both have to land on the same string or
    the two uploaded sheets will not join.
    """
    text = str(code).strip()
    try:
        value = float(text.replace(",", ""))
        return str(int(value)) if value.is_integer() else str(value)
    except (TypeError, ValueError):
        return text.replace(",", "")


def normalize_recovery(value: Any, default: float = DEFAULT_RECOVERY) -> float:
    """
    Accept recovery as either a percentage (85) or a fraction (0.85).

    Spreadsheets carry it both ways, so the single source of truth for the
    convention lives here rather than being re-implemented at each call site.
    Anything above 1 is read as a percentage. Exactly 1.0 is read as 100%,
    which is the reading that matters: a 1% recovery is not a real process.
    """
    recovery = safe_float(value, default)
    if recovery <= 0:
        return 0.0
    return recovery / 100.0 if recovery > 1 else recovery


def get_freight_cost(vendor: Any) -> float:
    """
    Inbound freight and duty per unit for a lane, matched loosely on the text.

    Loose matching because the lane arrives as free text from a purchasing
    system and is spelled six ways -- "Import Ocean FCL", "OCEAN-FCL", "FCL
    Shanghai". Order matters: air is tested before the ocean lanes because
    "Import Air Freight" contains neither FCL nor LCL but an unanchored
    "Import" test would swallow it.
    """
    text = str(vendor or "").strip().upper()
    if not text:
        return DEFAULT_FREIGHT
    if "AIR" in text:
        return FREIGHT_RATES["Import Air Freight"]
    if "LCL" in text:
        return FREIGHT_RATES["Import Ocean LCL"]
    if "FCL" in text or "OCEAN" in text:
        return FREIGHT_RATES["Import Ocean FCL"]
    if "CROSS" in text or "CONSOLIDAT" in text:
        return FREIGHT_RATES["Cross-dock Consolidator"]
    if "FTL" in text:
        return FREIGHT_RATES["Domestic FTL"]
    if "LTL" in text or "DOMESTIC" in text:
        return FREIGHT_RATES["Domestic LTL"]
    return DEFAULT_FREIGHT


def calculate_actual_inv_cost(vendor_invoice_price: float, units_per_billing_uom: float) -> float:
    """
    Invoice price converted to a cost per selling unit.

    A distributor buys by the case and sells by the each, and this conversion
    is where a price goes wrong by a factor of twelve without anybody noticing.
    """
    if units_per_billing_uom == 0:
        return vendor_invoice_price
    return vendor_invoice_price / units_per_billing_uom


def calculate_market_cost(actual_inv_cost: float, adj: float) -> float:
    return actual_inv_cost + adj


def calculate_landed_cost(market_cost: float, freight: float) -> float:
    return market_cost + freight


def calculate_recovery_input(market_cost: float, freight: float, recovery: Any) -> float:
    """
    Landed cost grossed up for the units that never ship.

    `recovery` is normalised here, so passing 85 and passing 0.85 give the same
    answer. They previously did not: this function divided by the raw value
    while the spreadsheet auto-fill path divided by the normalised one, so the
    same input produced two different costs depending on which path ran.
    """
    rate = normalize_recovery(recovery)
    if rate == 0:
        return 0.0
    return (market_cost + freight) / rate


def calculate_waste_output(raw_material_cost: float, recovery: Any) -> float:
    """The cost of the units lost to damage, shrink and unsellable returns."""
    rate = normalize_recovery(recovery)
    if rate == 0:
        return 0.0
    return (raw_material_cost / rate) - raw_material_cost


def calculate_trim_recovery(salvage_value_unit: float, trim_percent: Any, recovery: Any) -> float:
    """Credit for trim that is sold on rather than thrown away."""
    rate = normalize_recovery(recovery)
    if rate == 0:
        return 0.0
    damaged = normalize_recovery(trim_percent, default=0.0)
    return (salvage_value_unit * damaged) / rate


def calculate_price_from_margin(cost: float, margin: Any) -> float:
    """
    Price that yields `margin` as a fraction of price.

    A margin at or above 100% has no finite price, so the cost is returned
    unchanged rather than dividing by zero or going negative.
    """
    rate = safe_float(margin, 0.0)
    if rate > 1:
        rate = rate / 100.0
    if rate >= 1:
        return cost
    if rate <= 0:
        return cost
    return cost / (1 - rate)


def calculate_margin_dollars(base_price: float, final_cost: float) -> float:
    return base_price - final_cost


def calculate_margin_percent(price: float, cost: float) -> float:
    """Realised margin as a fraction of price. Zero price has no margin."""
    if price == 0:
        return 0.0
    return (price - cost) / price


def build_cost_stack(
    *,
    vendor_invoice_price: float,
    units_per_billing_uom: float,
    adj: float = 0.0,
    vendor: str = "",
    recovery: Any = DEFAULT_RECOVERY,
    handling_per_unit: float = 0.0,
    labelling_per_unit: float = 0.0,
    base_margin: Any = DEFAULT_BASE_MARGIN,
    list_margin: Any = DEFAULT_LIST_MARGIN,
) -> dict[str, float]:
    """
    Walk one item from vendor invoice to selling price.

    Returns every intermediate step, because when a price looks wrong the
    question is always *which* step moved.
    """
    actual_inv_cost = calculate_actual_inv_cost(vendor_invoice_price, units_per_billing_uom)
    market_cost = calculate_market_cost(actual_inv_cost, adj)
    freight = get_freight_cost(vendor)
    landed_cost = calculate_landed_cost(market_cost, freight)
    recovery_input = calculate_recovery_input(market_cost, freight, recovery)
    final_cost = recovery_input + handling_per_unit + labelling_per_unit
    base_price = calculate_price_from_margin(final_cost, base_margin)
    list_price = calculate_price_from_margin(final_cost, list_margin)
    return {
        "actual_inv_cost": actual_inv_cost,
        "market_cost": market_cost,
        "freight": freight,
        "landed_cost": landed_cost,
        "recovery_input": recovery_input,
        "final_cost": final_cost,
        "base_price": base_price,
        "list_price": list_price,
        "base_margin_dollars": calculate_margin_dollars(base_price, final_cost),
        "realised_base_margin": calculate_margin_percent(base_price, final_cost),
    }
