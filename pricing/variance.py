"""
Cost variances, and the price-volume-mix bridge that explains a margin move.

Two families of arithmetic live here.

**Standard costing variances** -- what a cost analyst raises at month end.
Purchase price, yield, labour rate and efficiency, overhead spending and
volume. Each is signed the same way: *positive is unfavourable*, meaning it
cost more than standard said it would. That convention is the one thing to get
right, because a variance report where half the columns are "good when big"
gets read backwards by everyone who did not build it, and every function here
returns ``favourable`` in words alongside the number.

**The price-volume-mix bridge** -- what a pricing analyst presents when margin
missed. Margin fell four points: how much was price, how much was input cost,
how much was selling less, and how much was selling a different blend of the
same things?

The bridge is where these decompositions usually go wrong. Price, volume and
mix effects are not independent -- the cross-terms have to be assigned to
*something* -- and a decomposition that assigns them by intuition leaves a
residual that gets quietly labelled "other". :func:`margin_bridge` is built so
the effects sum to the actual change exactly, and a test asserts it to the
cent on generated data. The convention it uses:

* **price** at current volume: ``sum(q1 * (p1 - p0))``
* **cost** at current volume: ``-sum(q1 * (c1 - c0))``
* **volume** as pure scale at the old average unit margin
* **mix** as the blend shift, valued at old unit margins
* **new** and **lost** products carry their whole contribution, because there
  is no prior price to compare a product launched this quarter against, and
  folding it into "volume" is how a launch flatters a bridge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import normalize_recovery, safe_float
from pricing.waterfall import add_deltas

FAVOURABLE = "Favourable"
UNFAVOURABLE = "Unfavourable"
NEUTRAL = "On standard"


def _verdict(variance: float, tolerance: float = 0.005) -> str:
    """Positive is unfavourable throughout this module. Near zero is neither."""
    if abs(variance) <= tolerance:
        return NEUTRAL
    return UNFAVOURABLE if variance > 0 else FAVOURABLE


def _variance(name: str, amount: float, **extra: Any) -> dict[str, Any]:
    return {"variance_type": name, "variance": amount, "verdict": _verdict(amount), **extra}


# --------------------------------------------------------------------------
# Standard costing variances
# --------------------------------------------------------------------------

def purchase_price_variance(
    *, actual_price: float, standard_price: float, actual_quantity: float
) -> dict[str, Any]:
    """
    ``(actual - standard) * actual quantity``.

    Priced on *actual* quantity rather than standard, which is the convention
    that isolates it: PPV is a buying result, and charging it on the quantity
    actually bought keeps the usage decision out of it. The usage decision is
    :func:`yield_variance`.
    """
    delta = safe_float(actual_price) - safe_float(standard_price)
    qty = safe_float(actual_quantity)
    return _variance(
        "Purchase price", delta * qty,
        rate_delta=delta, quantity=qty,
        pct_of_standard=(delta / safe_float(standard_price)) if safe_float(standard_price) else 0.0,
    )


def yield_variance(
    *,
    output_units: float,
    actual_input_units: float,
    standard_sellable_rate: Any,
    standard_input_price: float,
) -> dict[str, Any]:
    """
    The material variance that matters in a processing business.

    Standard says a unit shipped needs ``1 / recovery`` pounds of raw
    material. Anything used beyond that is yield loss, valued at standard
    price so a bad buying month does not show up here as a bad cutting month.

    A 68% standard recovery on 1,000 lb of output allows 1,470 lb of input; if
    the floor consumed 1,540 the variance is 70 lb at standard cost, and the
    conversation is with production, not with purchasing.
    """
    recovery = normalize_recovery(standard_sellable_rate)
    output = safe_float(output_units)
    actual = safe_float(actual_input_units)
    price = safe_float(standard_input_price)
    if recovery <= 0:
        return _variance("Yield", 0.0, standard_input_units=0.0, actual_input_units=actual,
                         actual_recovery=0.0, standard_sellable_rate=0.0)
    allowed = output / recovery
    return _variance(
        "Yield", (actual - allowed) * price,
        standard_input_units=allowed,
        actual_input_units=actual,
        excess_units=actual - allowed,
        actual_recovery=(output / actual) if actual > 0 else 0.0,
        standard_sellable_rate=recovery,
    )


def labour_rate_variance(
    *, actual_rate: float, standard_rate: float, actual_hours: float
) -> dict[str, Any]:
    """``(actual rate - standard rate) * actual hours``: what the hour cost."""
    delta = safe_float(actual_rate) - safe_float(standard_rate)
    return _variance("Labour rate", delta * safe_float(actual_hours),
                     rate_delta=delta, hours=safe_float(actual_hours))


def labour_efficiency_variance(
    *, actual_hours: float, standard_hours: float, standard_rate: float
) -> dict[str, Any]:
    """``(actual hours - standard hours) * standard rate``: how many it took."""
    delta = safe_float(actual_hours) - safe_float(standard_hours)
    return _variance("Labour efficiency", delta * safe_float(standard_rate),
                     hours_delta=delta, standard_rate=safe_float(standard_rate))


def overhead_spending_variance(
    *, actual_overhead: float, budgeted_rate: float, actual_driver: float
) -> dict[str, Any]:
    """Did the pool cost more than the rate said it would, at this activity."""
    absorbed = safe_float(budgeted_rate) * safe_float(actual_driver)
    return _variance("Overhead spending", safe_float(actual_overhead) - absorbed,
                     absorbed=absorbed, actual=safe_float(actual_overhead))


def overhead_volume_variance(
    *, budgeted_driver: float, actual_driver: float, budgeted_rate: float
) -> dict[str, Any]:
    """
    Under-absorption from running below the volume the rate was set on.

    Not a spending failure -- nobody overspent -- but it lands in cost of sales
    all the same, and it is the variance most often mistaken for one. A plant
    at 80% of planned throughput carries 20% of its fixed pool with nothing to
    put it on.
    """
    unabsorbed = (
        (safe_float(budgeted_driver) - safe_float(actual_driver))
        * safe_float(budgeted_rate)
    )
    return _variance("Overhead volume", unabsorbed,
                     budgeted_driver=safe_float(budgeted_driver),
                     actual_driver=safe_float(actual_driver))


def total_cost_variance(components: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum a set of variance dicts and keep the breakdown attached to it."""
    total = sum(safe_float(c.get("variance"), 0.0) for c in components)
    return {
        "variance_type": "Total",
        "variance": total,
        "verdict": _verdict(total),
        "components": [
            {"variance_type": c.get("variance_type"),
             "variance": safe_float(c.get("variance"), 0.0),
             "verdict": c.get("verdict")}
            for c in components
        ],
    }


# --------------------------------------------------------------------------
# Budget vs actual
# --------------------------------------------------------------------------

def budget_variance(
    *, actual: float, budget: float, higher_is_better: bool = True
) -> dict[str, Any]:
    """
    Actual against budget, with the sign interpreted for the line item.

    Revenue over budget is favourable; cost over budget is not. Passing
    ``higher_is_better`` rather than expecting the caller to negate the number
    keeps a variance report from silently colouring the expense rows the wrong
    way -- the single most common defect in a hand-built budget pack.
    """
    act, bud = safe_float(actual), safe_float(budget)
    delta = act - bud
    good = delta >= 0 if higher_is_better else delta <= 0
    return {
        "actual": act,
        "budget": bud,
        "variance": delta,
        "variance_pct": (delta / abs(bud)) if bud else 0.0,
        "verdict": NEUTRAL if abs(delta) < 0.005 else (FAVOURABLE if good else UNFAVOURABLE),
        "higher_is_better": higher_is_better,
    }


# --------------------------------------------------------------------------
# Price-volume-mix
# --------------------------------------------------------------------------

def _keyed(rows: Sequence[Mapping[str, Any]], key: str) -> dict[Any, dict[str, float]]:
    """Collapse rows to one entry per key, summing volume and value."""
    out: dict[Any, dict[str, float]] = {}
    for row in rows:
        k = row.get(key)
        qty = safe_float(row.get("quantity"), 0.0)
        price = safe_float(row.get("price"), 0.0)
        cost = safe_float(row.get("cost"), 0.0)
        entry = out.setdefault(k, {"quantity": 0.0, "revenue": 0.0, "cogs": 0.0})
        entry["quantity"] += qty
        entry["revenue"] += price * qty
        entry["cogs"] += cost * qty
    for entry in out.values():
        q = entry["quantity"]
        entry["price"] = entry["revenue"] / q if q else 0.0
        entry["cost"] = entry["cogs"] / q if q else 0.0
        entry["unit_margin"] = entry["price"] - entry["cost"]
    return out


def margin_bridge(
    prior: Sequence[Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
    *,
    key: str = "product_id",
) -> dict[str, Any]:
    """
    Decompose a gross-margin change into price, cost, volume, mix, new and lost.

    Each row needs ``key``, ``quantity``, ``price`` and ``cost``, all per unit
    except quantity. Rows are aggregated by key first, so transaction-level
    input is fine.

    The six effects sum to the margin change exactly. Products appearing in only
    one period are pulled out into ``new`` and ``lost`` before the price/volume/
    mix split runs, so the split is over a like-for-like set -- a comparison
    that includes a product with no prior price is not a price comparison.
    """
    p0, p1 = _keyed(prior, key), _keyed(current, key)
    common = sorted(set(p0) & set(p1), key=lambda k: str(k))
    new_keys = sorted(set(p1) - set(p0), key=lambda k: str(k))
    lost_keys = sorted(set(p0) - set(p1), key=lambda k: str(k))

    margin0 = sum(e["revenue"] - e["cogs"] for e in p0.values())
    margin1 = sum(e["revenue"] - e["cogs"] for e in p1.values())

    new_effect = sum(p1[k]["revenue"] - p1[k]["cogs"] for k in new_keys)
    lost_effect = -sum(p0[k]["revenue"] - p0[k]["cogs"] for k in lost_keys)

    # Like-for-like totals
    q0_total = sum(p0[k]["quantity"] for k in common)
    q1_total = sum(p1[k]["quantity"] for k in common)
    margin0_common = sum(p0[k]["unit_margin"] * p0[k]["quantity"] for k in common)
    avg_unit_margin0 = margin0_common / q0_total if q0_total else 0.0

    price_effect = sum(p1[k]["quantity"] * (p1[k]["price"] - p0[k]["price"]) for k in common)
    cost_effect = -sum(p1[k]["quantity"] * (p1[k]["cost"] - p0[k]["cost"]) for k in common)
    volume_effect = (q1_total - q0_total) * avg_unit_margin0
    mix_effect = (
        sum(p1[k]["quantity"] * p0[k]["unit_margin"] for k in common)
        - q1_total * avg_unit_margin0
    )

    effects = [
        {"effect": "Price", "amount": price_effect},
        {"effect": "Cost", "amount": cost_effect},
        {"effect": "Volume", "amount": volume_effect},
        {"effect": "Mix", "amount": mix_effect},
        {"effect": "New products", "amount": new_effect},
        {"effect": "Lost products", "amount": lost_effect},
    ]
    total = sum(e["amount"] for e in effects)

    return {
        "margin_prior": margin0,
        "margin_current": margin1,
        "margin_change": margin1 - margin0,
        "effects": effects,
        "explained": total,
        # Float noise only. A non-trivial residual means the arithmetic above
        # drifted, and the test asserts this stays at rounding scale.
        "residual": (margin1 - margin0) - total,
        "products_common": len(common),
        "products_new": len(new_keys),
        "products_lost": len(lost_keys),
    }


def revenue_bridge(
    prior: Sequence[Mapping[str, Any]],
    current: Sequence[Mapping[str, Any]],
    *,
    key: str = "product_id",
) -> dict[str, Any]:
    """
    The same decomposition on revenue rather than margin: price, volume, mix,
    new and lost. No cost effect, because revenue does not have one.
    """
    p0, p1 = _keyed(prior, key), _keyed(current, key)
    common = sorted(set(p0) & set(p1), key=lambda k: str(k))
    new_keys = sorted(set(p1) - set(p0), key=lambda k: str(k))
    lost_keys = sorted(set(p0) - set(p1), key=lambda k: str(k))

    rev0 = sum(e["revenue"] for e in p0.values())
    rev1 = sum(e["revenue"] for e in p1.values())

    q0_total = sum(p0[k]["quantity"] for k in common)
    q1_total = sum(p1[k]["quantity"] for k in common)
    rev0_common = sum(p0[k]["revenue"] for k in common)
    avg_price0 = rev0_common / q0_total if q0_total else 0.0

    effects = [
        {"effect": "Price",
         "amount": sum(p1[k]["quantity"] * (p1[k]["price"] - p0[k]["price"]) for k in common)},
        {"effect": "Volume", "amount": (q1_total - q0_total) * avg_price0},
        {"effect": "Mix",
         "amount": sum(p1[k]["quantity"] * p0[k]["price"] for k in common) - q1_total * avg_price0},
        {"effect": "New products", "amount": sum(p1[k]["revenue"] for k in new_keys)},
        {"effect": "Lost products", "amount": -sum(p0[k]["revenue"] for k in lost_keys)},
    ]
    total = sum(e["amount"] for e in effects)
    return {
        "revenue_prior": rev0,
        "revenue_current": rev1,
        "revenue_change": rev1 - rev0,
        "effects": effects,
        "explained": total,
        "residual": (rev1 - rev0) - total,
        "products_common": len(common),
        "products_new": len(new_keys),
        "products_lost": len(lost_keys),
    }


def bridge_steps(bridge: Mapping[str, Any], *, value_key: str = "margin") -> list[dict[str, Any]]:
    """
    Turn a bridge into waterfall bars: opening total, one bar per effect,
    closing total. Effects that are exactly zero are kept here (unlike the
    price waterfall) because an absent mix effect is itself the finding.
    """
    opening = safe_float(bridge.get(f"{value_key}_prior"))
    steps = [{"label": "Prior", "kind": "total", "amount": opening, "running": opening}]
    running = opening
    for effect in bridge.get("effects", []):
        amount = safe_float(effect.get("amount"))
        running += amount
        steps.append(
            {
                "label": effect.get("effect"),
                "kind": "increase" if amount >= 0 else "decrease",
                "amount": amount,
                "running": running,
            }
        )
    closing = safe_float(bridge.get(f"{value_key}_current"))
    steps.append({"label": "Current", "kind": "total", "amount": closing, "running": closing})
    return add_deltas(steps)
