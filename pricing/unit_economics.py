"""
Unit economics: contribution, markup against margin, and break-even.

Everything here turns on one distinction that costing systems are built to
obscure: **absorbed cost is not variable cost**. A standard cost carries a share
of the fixed pool, which is correct for valuing inventory and wrong for every
decision about the *next* unit. Price it off absorbed cost and you refuse
business that would have paid for itself; break even on absorbed cost and you
are wrong by the whole fixed pool in the direction that flatters you.

So the functions here take variable cost and fixed cost separately, and refuse
to guess which is which.

The other thing this module exists for is **markup against margin**, which is
the single most expensive arithmetic confusion in pricing. A 25% markup is a 20%
margin. A 25% margin is a 33.3% markup. Quoting one where the other is meant
underprices every line by a few points, forever, and it never announces itself
because both numbers are plausible and the tool keeps working.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import safe_float

# Below this contribution per unit, break-even is not a large number -- it does
# not exist, and reporting a very large one implies a volume that would fix it.
MIN_CONTRIBUTION = 1e-9


# --------------------------------------------------------------------------
# Markup and margin
# --------------------------------------------------------------------------

def markup_to_margin(markup: float) -> float:
    """
    ``m = k / (1 + k)``. A 25% markup is a 20% margin.

    Both are fractions, never percentages above 1: passing 25 here means a
    2500% markup, which returns 0.96 and is the caller's problem to have
    avoided. The convention is fractions everywhere in this package.
    """
    k = safe_float(markup)
    if k <= -1:
        return 0.0
    return k / (1.0 + k)


def margin_to_markup(margin: float) -> float:
    """``k = m / (1 - m)``. A 25% margin is a 33.3% markup."""
    m = safe_float(margin)
    if m >= 1:
        return float("inf")
    return m / (1.0 - m)


def price_from_markup(cost: float, markup: float) -> float:
    """``p = c * (1 + k)``. Contrast with cost / (1 - margin)."""
    return safe_float(cost) * (1.0 + safe_float(markup))


def markup_margin_table(rates: Sequence[float]) -> list[dict[str, float]]:
    """
    The two columns side by side at a set of rates, for putting the gap on a
    screen rather than in a sentence. At 40% the two differ by eleven points.
    """
    out = []
    for rate in rates:
        value = safe_float(rate)
        out.append(
            {
                "rate": value,
                "margin_if_read_as_margin": value,
                "markup_needed_for_that_margin": margin_to_markup(value),
                "margin_if_read_as_markup": markup_to_margin(value),
                "gap": value - markup_to_margin(value),
            }
        )
    return out


# --------------------------------------------------------------------------
# Contribution
# --------------------------------------------------------------------------

def contribution(price: float, variable_cost: float, quantity: float = 1.0) -> dict[str, float]:
    """
    What one unit adds before any fixed cost is considered.

    ``contribution_ratio`` is contribution over *price*, which is the number
    break-even divides fixed cost by when working in revenue rather than units.
    """
    p = safe_float(price)
    v = safe_float(variable_cost)
    qty = safe_float(quantity, 1.0)
    per_unit = p - v
    return {
        "price": p,
        "variable_cost": v,
        "contribution_per_unit": per_unit,
        "contribution_ratio": (per_unit / p) if p > 0 else 0.0,
        "variable_cost_ratio": (v / p) if p > 0 else 0.0,
        "quantity": qty,
        "total_contribution": per_unit * qty,
        "revenue": p * qty,
    }


def cost_to_serve(
    *,
    freight_out: float = 0.0,
    order_handling: float = 0.0,
    returns_credits: float = 0.0,
    quantity: float = 1.0,
    price: float = 0.0,
) -> dict[str, float]:
    """
    What it costs to hand the product over, per unit and as a share of price.

    Separated from cost of goods on purpose. Cost to serve is a *logistics*
    decision -- drop size, frequency, lane -- and folding it into product cost
    hides the account that is unprofitable because it orders twelve units at a
    time rather than because the product is priced wrong.
    """
    total = safe_float(freight_out) + safe_float(order_handling) + safe_float(returns_credits)
    p = safe_float(price)
    qty = safe_float(quantity, 1.0)
    return {
        "cost_to_serve_per_unit": total,
        "cost_to_serve_total": total * qty,
        "pct_of_price": (total / p) if p > 0 else 0.0,
        "freight_out": safe_float(freight_out),
        "order_handling": safe_float(order_handling),
        "returns_credits": safe_float(returns_credits),
    }


# --------------------------------------------------------------------------
# Break-even
# --------------------------------------------------------------------------

def break_even(
    *,
    fixed_costs: float,
    price: float,
    variable_cost: float,
    actual_quantity: float = 0.0,
    target_profit: float = 0.0,
) -> dict[str, Any]:
    """
    Break-even in units and in revenue, with the margin of safety beside it.

    Returns ``exists``. At or below zero contribution there is no break-even
    volume -- every additional unit loses money, so the answer is not a large
    number, it is that no volume fixes this. A tool that prints 4,300,000,000
    units has told the reader to sell harder.

    ``margin_of_safety`` is how far current volume could fall before the
    business stops covering its fixed costs, as a fraction of current volume.
    It is the number to quote alongside break-even, because break-even alone
    says nothing about how much room there is.
    """
    fixed = safe_float(fixed_costs)
    p = safe_float(price)
    v = safe_float(variable_cost)
    actual = safe_float(actual_quantity)
    target = safe_float(target_profit)
    per_unit = p - v
    ratio = (per_unit / p) if p > 0 else 0.0

    if per_unit <= MIN_CONTRIBUTION:
        return {
            "exists": False,
            "break_even_units": float("nan"),
            "break_even_revenue": float("nan"),
            "contribution_per_unit": per_unit,
            "contribution_ratio": ratio,
            "fixed_costs": fixed,
            "actual_quantity": actual,
            "margin_of_safety": float("nan"),
            "operating_profit": per_unit * actual - fixed,
            "reason": (
                f"contribution is {per_unit:+,.4f} per unit, so no volume covers the "
                "fixed cost -- the price or the variable cost has to move first"
            ),
        }

    units = (fixed + target) / per_unit
    return {
        "exists": True,
        "break_even_units": units,
        "break_even_revenue": units * p,
        "contribution_per_unit": per_unit,
        "contribution_ratio": ratio,
        "fixed_costs": fixed,
        "target_profit": target,
        "actual_quantity": actual,
        "margin_of_safety": ((actual - units) / actual) if actual > 0 else float("nan"),
        "margin_of_safety_units": actual - units,
        "operating_profit": per_unit * actual - fixed,
        "reason": "ok",
    }


def operating_leverage(contribution_total: float, operating_profit: float) -> dict[str, Any]:
    """
    Contribution over operating profit: how much profit moves for a move in sales.

    A leverage of 4 means a 10% fall in volume takes 40% of the profit. It is
    undefined at break-even, where profit is zero and the ratio goes to
    infinity -- which is not a bug, it is the point, and it is reported rather
    than divided.
    """
    total = safe_float(contribution_total)
    profit = safe_float(operating_profit)
    if abs(profit) < MIN_CONTRIBUTION:
        return {"defined": False, "leverage": float("nan"),
                "reason": "at break-even, so a one percent move in sales is unbounded"}
    return {"defined": True, "leverage": total / profit, "reason": "ok"}


def break_even_curve(
    *,
    fixed_costs: float,
    price: float,
    variable_cost: float,
    max_quantity: float,
    steps: int = 41,
) -> list[dict[str, float]]:
    """
    Points for the classic break-even chart: fixed, variable, total and revenue.

    Four series on one axis because they are all currency, which is exactly
    when several series may share an axis. Each point carries ``is_break_even``
    so the crossing can be marked without recomputing it.
    """
    fixed = safe_float(fixed_costs)
    p = safe_float(price)
    v = safe_float(variable_cost)
    top = safe_float(max_quantity)
    if top <= 0 or steps < 2:
        return []

    result = break_even(fixed_costs=fixed, price=p, variable_cost=v)
    crossing = result["break_even_units"] if result["exists"] else float("nan")
    span = top / (steps - 1)
    points = []
    for i in range(steps):
        quantity = span * i
        revenue = p * quantity
        variable = v * quantity
        points.append(
            {
                "quantity": quantity,
                "revenue": revenue,
                "fixed_cost": fixed,
                "variable_cost": variable,
                "total_cost": fixed + variable,
                "profit": revenue - fixed - variable,
                "is_break_even": 0.0,
            }
        )
    if result["exists"] and 0 <= crossing <= top:
        nearest = min(points, key=lambda row: abs(row["quantity"] - crossing))
        nearest["is_break_even"] = 1.0
    return points


def portfolio_break_even(rows: Sequence[Mapping[str, Any]], fixed_costs: float) -> dict[str, Any]:
    """
    Break-even for a book of products, at the mix it currently sells.

    A weighted-average contribution *ratio* rather than a per-unit one, because
    the products are not comparable units -- a monitor and a pack of pens are
    both one unit and nothing else about them is the same. The
    answer is a break-even *revenue*, and it only holds while the mix holds,
    which is the caveat that belongs next to it.
    """
    revenue = sum(
        safe_float(r.get("price")) * safe_float(r.get("quantity"), 0.0) for r in rows)
    variable = sum(
        safe_float(r.get("variable_cost")) * safe_float(r.get("quantity"), 0.0) for r in rows)
    volume = sum(safe_float(r.get("quantity"), 0.0) for r in rows)
    fixed = safe_float(fixed_costs)

    contribution_total = revenue - variable
    ratio = (contribution_total / revenue) if revenue > 0 else 0.0
    if ratio <= MIN_CONTRIBUTION:
        return {"exists": False, "break_even_revenue": float("nan"),
                "contribution_ratio": ratio, "revenue": revenue,
                "fixed_costs": fixed, "operating_profit": contribution_total - fixed,
                "reason": "the book does not cover its variable cost at this mix"}

    break_even_revenue = fixed / ratio
    return {
        "exists": True,
        "revenue": revenue,
        "variable_cost": variable,
        "contribution": contribution_total,
        "contribution_ratio": ratio,
        "fixed_costs": fixed,
        "break_even_revenue": break_even_revenue,
        "break_even_volume": (volume * break_even_revenue / revenue) if revenue > 0 else 0.0,
        "operating_profit": contribution_total - fixed,
        "margin_of_safety": ((revenue - break_even_revenue) / revenue) if revenue > 0 else 0.0,
        "reason": "ok",
    }
