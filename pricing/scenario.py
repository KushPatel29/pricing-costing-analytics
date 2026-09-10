"""
Scenario modelling: best, base, worst, and which assumption actually matters.

A scenario model is easy to build and easy to build uselessly. The two failures
are both about honesty. The first is moving every input to its optimistic end at
once and calling it "best case" -- costs down, volume up, discounts down and
price up together is not a case, it is a wish, and nobody who has run a business
believes it. The second is presenting three numbers as though the middle one is
a forecast rather than an assumption.

So this module does two things instead.

:func:`evaluate` computes one honest P&L from a stated set of inputs. Every
number in it is derivable from the ones above it, and it reports contribution
separately from operating profit, because those are the two the decision turns
on and absorbed margin hides the difference.

:func:`tornado` moves **one input at a time** across the range somebody is
willing to defend, and ranks the swing. That is the output worth having: it
turns "we modelled a range" into "of the six things we are guessing at, two
account for four-fifths of the spread, and one of them is the input cost we
could hedge".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import safe_float
from pricing.elasticity import demand_at_price

# The inputs a pricing scenario turns on, with the direction that is *good*.
# Used to build a defensible best/worst case: an optimistic case moves each
# input to its own good end, and it is still only as good as the ranges given.
INPUT_DIRECTION = {
    "price_change": +1,
    "volume_change": +1,
    "unit_cost_change": -1,
    "discount_change": -1,
    "fixed_cost_change": -1,
    "elasticity": +1,          # less negative is better for a price rise
}

INPUT_LABELS = {
    "price_change": "List price",
    "volume_change": "Underlying volume",
    "unit_cost_change": "Input cost",
    "discount_change": "Discount depth",
    "fixed_cost_change": "Fixed cost",
    "elasticity": "Elasticity",
}


def evaluate(
    *,
    base_price: float,
    base_volume: float,
    base_unit_cost: float,
    base_discount: float = 0.0,
    fixed_costs: float = 0.0,
    price_change: float = 0.0,
    volume_change: float = 0.0,
    unit_cost_change: float = 0.0,
    discount_change: float = 0.0,
    fixed_cost_change: float = 0.0,
    elasticity: float = 0.0,
    apply_elasticity: bool = True,
) -> dict[str, float]:
    """
    One scenario's P&L, from list price down to operating profit.

    ``volume_change`` is an *underlying* move -- a new listing, a lost account,
    a market that grew -- applied on top of whatever the elasticity implies for
    the price move. Keeping the two apart is what lets a scenario say "we raise
    four points and lose the one big account" without the model quietly
    counting the same volume twice.
    """
    price = safe_float(base_price) * (1 + safe_float(price_change))
    discount = safe_float(base_discount) + safe_float(discount_change)
    discount = min(max(discount, 0.0), 0.95)
    net_price = price * (1 - discount)

    volume = safe_float(base_volume)
    if apply_elasticity and safe_float(price_change):
        volume = demand_at_price(volume, safe_float(base_price), price,
                                 safe_float(elasticity))
    volume *= 1 + safe_float(volume_change)
    volume = max(volume, 0.0)

    unit_cost = safe_float(base_unit_cost) * (1 + safe_float(unit_cost_change))
    fixed = safe_float(fixed_costs) * (1 + safe_float(fixed_cost_change))

    revenue = net_price * volume
    variable_cost = unit_cost * volume
    contribution = revenue - variable_cost
    return {
        "list_price": price,
        "discount": discount,
        "net_price": net_price,
        "volume": volume,
        "unit_cost": unit_cost,
        "revenue": revenue,
        "variable_cost": variable_cost,
        "contribution": contribution,
        "contribution_pct": (contribution / revenue) if revenue > 0 else 0.0,
        "fixed_costs": fixed,
        "operating_profit": contribution - fixed,
        "operating_margin_pct": ((contribution - fixed) / revenue) if revenue > 0 else 0.0,
        "unit_contribution": net_price - unit_cost,
    }


def compare(
    base_inputs: Mapping[str, Any],
    scenarios: Mapping[str, Mapping[str, Any]],
    *,
    measure: str = "operating_profit",
) -> list[dict[str, Any]]:
    """
    Run several named scenarios against the same base and report the deltas.

    The base is always included and always first, because a table of scenarios
    with no baseline in it invites the reader to compare two guesses with each
    other.
    """
    baseline = evaluate(**base_inputs)
    rows = [{
        "scenario": "Base",
        **{k: v for k, v in baseline.items()},
        "delta": 0.0,
        "delta_pct": 0.0,
    }]
    for name, overrides in scenarios.items():
        result = evaluate(**{**base_inputs, **overrides})
        delta = result[measure] - baseline[measure]
        rows.append({
            "scenario": name,
            **result,
            "delta": delta,
            "delta_pct": (delta / abs(baseline[measure])) if baseline[measure] else 0.0,
        })
    return rows


def three_point(
    base_inputs: Mapping[str, Any],
    ranges: Mapping[str, tuple[float, float]],
    *,
    measure: str = "operating_profit",
) -> list[dict[str, Any]]:
    """
    Best, base and worst, built by moving every input to its own good or bad end.

    The caveat belongs with the output and is returned in ``note``: the ends are
    not a confidence interval. Every input landing at its optimistic end at once
    is far less likely than any one of them doing so, so the spread here is
    wider than the real one and is a stress test rather than a forecast.
    """
    optimistic, pessimistic = {}, {}
    for name, (low, high) in ranges.items():
        direction = INPUT_DIRECTION.get(name, +1)
        optimistic[name] = high if direction > 0 else low
        pessimistic[name] = low if direction > 0 else high

    rows = compare(base_inputs, {"Best case": optimistic, "Worst case": pessimistic},
                   measure=measure)
    order = {"Worst case": 0, "Base": 1, "Best case": 2}
    rows.sort(key=lambda r: order.get(r["scenario"], 1))
    for row in rows:
        row["note"] = (
            "Every assumption at its own end at once. A stress test, not an "
            "interval: the joint probability is far below any single one of them."
            if row["scenario"] != "Base" else "The stated assumptions."
        )
    return rows


def tornado(
    base_inputs: Mapping[str, Any],
    ranges: Mapping[str, tuple[float, float]],
    *,
    measure: str = "operating_profit",
) -> list[dict[str, Any]]:
    """
    One input at a time, across its range, ranked by the swing it causes.

    This is the output that earns the modelling. Three numbers say the answer is
    uncertain; this says *which* guess to go and reduce -- and often the biggest
    bar is an input somebody could go and measure for the cost of an afternoon.
    """
    baseline = evaluate(**base_inputs)[measure]
    rows = []
    for name, (low, high) in ranges.items():
        low_result = evaluate(**{**base_inputs, name: low})[measure]
        high_result = evaluate(**{**base_inputs, name: high})[measure]
        downside, upside = min(low_result, high_result), max(low_result, high_result)
        rows.append({
            "input": INPUT_LABELS.get(name, name),
            "key": name,
            "low_value": low,
            "high_value": high,
            "downside": downside,
            "upside": upside,
            "baseline": baseline,
            "downside_delta": downside - baseline,
            "upside_delta": upside - baseline,
            "swing": upside - downside,
        })
    rows.sort(key=lambda r: -r["swing"])
    total = sum(r["swing"] for r in rows) or 1.0
    cumulative = 0.0
    for row in rows:
        row["share_of_swing"] = row["swing"] / total
        cumulative += row["share_of_swing"]
        row["cumulative_share"] = cumulative
    return rows


def sensitivity_grid(
    base_inputs: Mapping[str, Any],
    *,
    x_input: str,
    x_values: Sequence[float],
    y_input: str,
    y_values: Sequence[float],
    measure: str = "operating_profit",
) -> list[dict[str, Any]]:
    """
    A two-way grid, for the heatmap that answers "how far can both move".

    Two inputs is the practical limit of a readable grid. Beyond that the
    answer is the tornado, not a cube nobody can hold in their head.
    """
    baseline = evaluate(**base_inputs)[measure]
    out = []
    for y in y_values:
        for x in x_values:
            value = evaluate(**{**base_inputs, x_input: x, y_input: y})[measure]
            out.append({
                "x_input": x_input, "y_input": y_input,
                "x": x, "y": y, "value": value,
                "delta": value - baseline,
                "delta_pct": (value - baseline) / abs(baseline) if baseline else 0.0,
                "sign": "Better" if value > baseline else
                        "Worse" if value < baseline else "Unchanged",
            })
    return out


def break_even_input(
    base_inputs: Mapping[str, Any],
    *,
    input_name: str,
    measure: str = "operating_profit",
    low: float = -0.9,
    high: float = 0.9,
    tolerance: float = 1e-7,
) -> dict[str, Any]:
    """
    How far one input can move before the measure hits zero, by bisection.

    "How much cost inflation can we absorb before this line stops making money"
    is a better question than any three-point case, and it has one answer.
    Returns ``exists`` when the measure does not cross zero anywhere in the
    range -- which is the good news case and should not be reported as a bound.
    """
    def value_at(x: float) -> float:
        return evaluate(**{**base_inputs, input_name: x})[measure]

    low_value, high_value = value_at(low), value_at(high)
    if (low_value > 0) == (high_value > 0):
        return {
            "exists": False, "input": INPUT_LABELS.get(input_name, input_name),
            "threshold": float("nan"),
            "reason": (
                f"{measure.replace('_', ' ')} does not cross zero between "
                f"{low:+.0%} and {high:+.0%} of this input"
            ),
        }

    left, right = low, high
    for _ in range(80):
        middle = (left + right) / 2
        if (value_at(middle) > 0) == (value_at(left) > 0):
            left = middle
        else:
            right = middle
        if abs(right - left) < tolerance:
            break
    threshold = (left + right) / 2
    return {
        "exists": True,
        "input": INPUT_LABELS.get(input_name, input_name),
        "key": input_name,
        "threshold": threshold,
        "baseline": evaluate(**base_inputs)[measure],
        "reason": "ok",
    }
