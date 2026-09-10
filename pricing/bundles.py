"""
Bundle and add-on pricing, judged on incremental margin rather than on the
bundle's own margin.

A bundle at a 12% discount that sells 400 units looks like a win until you ask
how many of those 400 customers were going to buy every component anyway. Those
customers did not bring new business; they took 12% off business already
booked. The bundle only creates value if the margin from genuinely new demand
covers the discount handed to the demand that was never at risk.

That gives the one number this module exists for. Setting incremental margin to
zero and solving for the cannibalisation rate::

    (1 - c) * B  =  c * (S - B)      ->      c* = B / S

where ``B`` is the bundle's unit margin and ``S`` the summed standalone margin.
So a bundle keeping 80% of standalone margin survives up to 80% cannibalisation,
and one discounted to 55% dies above 55%. :func:`break_even_cannibalisation` is
that ratio, and it turns "is this bundle a good idea" into a question about the
customer base that someone in sales can actually answer.

Add-ons are the same arithmetic with one component and an attach rate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import calculate_price_from_margin, safe_float


def _component_totals(components: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Standalone price, cost and margin for a set of bundle components."""
    price = cost = 0.0
    for comp in components:
        qty = safe_float(comp.get("quantity"), 1.0)
        price += safe_float(comp.get("price")) * qty
        cost += safe_float(comp.get("cost")) * qty
    return {"standalone_price": price, "standalone_cost": cost,
            "standalone_margin": price - cost}


def bundle_price(components: Sequence[Mapping[str, Any]], discount: float) -> float:
    """Summed standalone prices less a percentage discount on the whole bundle."""
    total = _component_totals(components)["standalone_price"]
    rate = safe_float(discount)
    if rate > 1:
        rate = rate / 100.0
    return max(0.0, total * (1.0 - rate))


def build_bundle(
    components: Sequence[Mapping[str, Any]],
    *,
    discount: float = 0.0,
    price_override: float | None = None,
) -> dict[str, Any]:
    """
    Price and margin for a bundle, standalone and bundled side by side.

    ``price_override`` sets the bundle price directly and back-solves the
    discount, which is how these are usually specified in practice -- a round
    price point first, the discount whatever it turns out to be.
    """
    totals = _component_totals(components)
    standalone = totals["standalone_price"]
    price = (
        safe_float(price_override)
        if price_override is not None
        else bundle_price(components, discount)
    )
    margin = price - totals["standalone_cost"]
    return {
        **totals,
        "bundle_price": price,
        "bundle_cost": totals["standalone_cost"],
        "bundle_margin": margin,
        "bundle_margin_pct": (margin / price) if price > 0 else 0.0,
        "standalone_margin_pct": (
            totals["standalone_margin"] / standalone if standalone > 0 else 0.0
        ),
        "discount": ((standalone - price) / standalone) if standalone > 0 else 0.0,
        "discount_amount": standalone - price,
        "components": len(components),
    }


def break_even_cannibalisation(bundle: Mapping[str, Any]) -> float:
    """
    The share of bundle buyers who could already have bought everything before
    the bundle stops paying for itself. Equals bundle margin / standalone margin.

    Zero when standalone margin is zero or negative -- there is nothing to
    protect, so any cannibalisation is fine and the ratio would mislead.
    """
    standalone = safe_float(bundle.get("standalone_margin"))
    if standalone <= 0:
        return 0.0
    return max(0.0, safe_float(bundle.get("bundle_margin")) / standalone)


def incremental_margin(
    bundle: Mapping[str, Any],
    *,
    expected_units: float,
    cannibalisation_rate: float,
) -> dict[str, Any]:
    """
    What the bundle actually adds, once cannibalised units are charged for the
    discount they took.

    ``incremental = new_units * bundle_margin - cannibalised_units * (standalone
    margin - bundle margin)``. The verdict, the break-even rate and the headroom
    to it come back too, because "worth doing" and "worth doing at this
    discount" are different questions.
    """
    units = max(0.0, safe_float(expected_units))
    rate = safe_float(cannibalisation_rate)
    if rate > 1:
        rate = rate / 100.0
    rate = min(max(rate, 0.0), 1.0)

    bundle_m = safe_float(bundle.get("bundle_margin"))
    standalone_m = safe_float(bundle.get("standalone_margin"))

    new_units = units * (1.0 - rate)
    cannibalised = units * rate
    gained = new_units * bundle_m
    given_away = cannibalised * (standalone_m - bundle_m)
    total = gained - given_away
    break_even = break_even_cannibalisation(bundle)

    return {
        "expected_units": units,
        "cannibalisation_rate": rate,
        "incremental_units": new_units,
        "cannibalised_units": cannibalised,
        "margin_from_new_demand": gained,
        "discount_given_to_existing": given_away,
        "incremental_margin": total,
        "break_even_cannibalisation": break_even,
        "headroom": break_even - rate,
        "verdict": "Creates value" if total > 0 else "Destroys value",
    }


def optimal_bundle_discount(
    components: Sequence[Mapping[str, Any]],
    *,
    expected_units: float,
    cannibalisation_rate: float,
    demand_lift_per_point: float = 0.0,
    max_discount: float = 0.35,
    steps: int = 36,
) -> list[dict[str, Any]]:
    """
    Sweep the discount and report incremental margin at each point.

    ``demand_lift_per_point`` is how many extra units each percentage point of
    discount is expected to bring. With it at zero the sweep is monotonically
    decreasing and the answer is trivially "discount nothing" -- which is the
    honest answer when nobody can put a number on the lift, and is why the
    parameter has no default guess baked in.
    """
    out: list[dict[str, Any]] = []
    if steps < 2:
        return out
    for i in range(steps + 1):
        discount = max_discount * i / steps
        units = safe_float(expected_units) + safe_float(demand_lift_per_point) * discount * 100.0
        bundle = build_bundle(components, discount=discount)
        result = incremental_margin(
            bundle, expected_units=units, cannibalisation_rate=cannibalisation_rate
        )
        out.append(
            {
                "discount": discount,
                "bundle_price": bundle["bundle_price"],
                "bundle_margin_pct": bundle["bundle_margin_pct"],
                "expected_units": units,
                "incremental_margin": result["incremental_margin"],
                "break_even_cannibalisation": result["break_even_cannibalisation"],
                "is_best": 0.0,
            }
        )
    best = max(out, key=lambda r: r["incremental_margin"])
    best["is_best"] = 1.0
    return out


def attach_value(
    *,
    anchor_units: float,
    attach_rate: float,
    addon_price: float,
    addon_cost: float,
) -> dict[str, Any]:
    """
    What an add-on is worth against the anchor product it rides on.

    Reported per anchor unit as well as in total: "adds $0.34 to every case of
    the anchor" is the form that survives a pricing meeting, because it can be
    compared directly with a price change on the anchor itself.
    """
    anchors = max(0.0, safe_float(anchor_units))
    rate = safe_float(attach_rate)
    if rate > 1:
        rate = rate / 100.0
    rate = min(max(rate, 0.0), 1.0)
    unit_margin = safe_float(addon_price) - safe_float(addon_cost)
    units = anchors * rate
    return {
        "anchor_units": anchors,
        "attach_rate": rate,
        "addon_units": units,
        "addon_unit_margin": unit_margin,
        "addon_margin": units * unit_margin,
        "margin_per_anchor_unit": rate * unit_margin,
        "addon_revenue": units * safe_float(addon_price),
    }


def price_ladder(
    final_cost: float,
    tiers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """
    A good/better/best ladder built from one cost and a margin per tier.

    Each tier supplies ``name``, ``margin`` and optionally a ``cost_uplift``
    for packaging or portioning that the higher tier actually incurs. The step
    up to the next tier comes back with each rung: a ladder whose rungs are two
    cents apart is not a ladder, and that is visible here rather than after it
    ships.
    """
    base = safe_float(final_cost)
    out: list[dict[str, Any]] = []
    previous: float | None = None
    for tier in tiers:
        cost = base + safe_float(tier.get("cost_uplift"), 0.0)
        price = calculate_price_from_margin(cost, tier.get("margin"))
        row = {
            "tier": tier.get("name"),
            "cost": cost,
            "target_margin": safe_float(tier.get("margin")),
            "price": price,
            "margin": price - cost,
            "step_from_previous": (price - previous) if previous is not None else 0.0,
            "step_pct": ((price / previous - 1.0) if previous else 0.0),
        }
        out.append(row)
        previous = price
    return out
