"""
Price elasticity, the optimal price it implies, and the break-even volume test.

Elasticity is the percentage change in volume for a one percent change in
price. It is negative for anything anyone would want to sell. The estimate here
is the standard log-log regression -- ``ln(q) = a + e*ln(p)`` -- whose slope
*is* the elasticity, because the derivative of a log is a percentage change.
That is the whole reason for the log: on raw prices and quantities the slope
would be "units per dollar", which is not comparable across a $6 pack of gel
pens and a $340 monitor.

Three things this module refuses to guess at.

**Elasticity from observational data is not causal.** Prices move because costs
moved, or because demand moved and someone reacted. A regression of volume on
price picks up both, and the number it returns is a correlation wearing an
economics word. Everything here reports an r-squared and a point count beside
the estimate, and :func:`estimate_elasticity` marks anything thin or flat as
not usable rather than returning a confident-looking slope from four points.

**There is no interior optimum when demand is inelastic.** The markup rule
``p* = c * e / (e + 1)`` only holds for ``e < -1``. At ``e = -0.6`` the formula
returns a *negative* price, and a tool that prints it has just advised paying
customers to take the product. Above -1 the honest answer is "every price rise
raises profit until something outside this model stops you", and that is what
:func:`optimal_price` returns.

**A price cut has a volume hurdle, and it is much higher than people expect.**
Cutting price by ``d`` on a contribution margin of ``m`` needs volume up by
``d / (m - d)`` just to stand still. Five points off a 30% margin needs +20%
volume; five points off a 15% margin needs +50%. :func:`break_even_volume_change`
is the most-used function in this module for a reason.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

from costing.formulas import safe_float

# Below this many usable observations the slope is noise with a decimal point.
MIN_OBSERVATIONS = 8
# Below this much price variation (coefficient of variation) there is nothing
# to regress against: everyone paid the same, so volume differences are
# something other than price.
MIN_PRICE_CV = 0.02
# Elasticities outside this range are almost always a data artefact -- a
# promotion whose volume spike is being attributed to a 2% price move.
PLAUSIBLE_RANGE = (-8.0, -0.05)
# Above this, the constant-elasticity optimum is arithmetic rather than advice.
# 5x cost corresponds to an elasticity of about -1.25.
MAX_CREDIBLE_MARKUP = 5.0


def _clean_pairs(
    prices: Sequence[Any], quantities: Sequence[Any]
) -> tuple[list[float], list[float]]:
    """Keep only pairs where both sides are strictly positive; logs need that."""
    out_p: list[float] = []
    out_q: list[float] = []
    for p, q in zip(prices, quantities, strict=False):
        pf, qf = safe_float(p, 0.0), safe_float(q, 0.0)
        if pf > 0 and qf > 0:
            out_p.append(pf)
            out_q.append(qf)
    return out_p, out_q


def _ols_slope(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float, float]:
    """Least-squares slope, intercept and r-squared, without pulling in numpy."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=False))
    if sxx == 0:
        return 0.0, mean_y, 0.0
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    syy = sum((y - mean_y) ** 2 for y in ys)
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return slope, intercept, r2


def coefficient_of_variation(values: Sequence[Any]) -> float:
    """Standard deviation over mean. Scale-free, so it works on any price."""
    nums = [safe_float(v, 0.0) for v in values]
    nums = [v for v in nums if v > 0]
    if len(nums) < 2:
        return 0.0
    mean = sum(nums) / len(nums)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in nums) / (len(nums) - 1)
    return math.sqrt(var) / mean


def seasonal_factors(
    quantities: Sequence[Any], period_labels: Sequence[Any]
) -> dict[Any, float]:
    """
    Multiplicative season per label (month of year, usually), from the data.

    Computed in logs and exponentiated, because demand is multiplicative: a
    December that runs 30% above average and a February 30% below average
    average to 1.0 in logs and to 1.045 in levels, and the second one leaves a
    slow drift in the deseasonalised series that a trend term then eats.

    Derived from the series itself rather than taken from a table, so it works
    on a customer's own data and not only on the sample.
    """
    sums: dict[Any, list[float]] = {}
    for qty, label in zip(quantities, period_labels, strict=False):
        value = safe_float(qty, 0.0)
        if value > 0:
            sums.setdefault(label, []).append(math.log(value))
    if not sums:
        return {}
    overall = sum(v for values in sums.values() for v in values) / sum(
        len(values) for values in sums.values()
    )
    return {
        label: math.exp(sum(values) / len(values) - overall) for label, values in sums.items()
    }


def deseasonalise(
    quantities: Sequence[Any],
    period_labels: Sequence[Any],
    factors: Mapping[Any, float] | None = None,
) -> list[float]:
    """Divide out the season, so what is left can be attributed to price."""
    table = dict(factors) if factors is not None else seasonal_factors(quantities, period_labels)
    out = []
    for qty, label in zip(quantities, period_labels, strict=False):
        factor = table.get(label, 1.0)
        out.append(safe_float(qty, 0.0) / factor if factor else safe_float(qty, 0.0))
    return out


def estimate_elasticity(
    prices: Sequence[Any],
    quantities: Sequence[Any],
    *,
    min_observations: int = MIN_OBSERVATIONS,
    controls: Mapping[str, Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """
    Fit ``ln(q) = a + e*ln(p) [+ controls]`` and report the slope with its caveats.

    ``controls`` are extra regressors held constant while the price coefficient
    is read -- a linear time trend, most usefully. Without one, any category
    whose price rose steadily over the window has its elasticity attenuated
    toward zero by whatever else was trending at the same time, and on generated
    data with a known answer that attenuation is measurable: the electronics
    estimate moves about four tenths of a point when the trend is included.

    Returns ``usable`` alongside the estimate. A caller that ignores it and
    prices off ``elasticity`` will price off a slope fitted to five points with
    no price variation, which is exactly the failure this is here to prevent --
    so the reason is returned in plain words rather than as a flag.
    """
    pairs = [
        (safe_float(p, 0.0), safe_float(q, 0.0), i)
        for i, (p, q) in enumerate(zip(prices, quantities, strict=False))
    ]
    kept = [(p, q, i) for p, q, i in pairs if p > 0 and q > 0]
    p = [row[0] for row in kept]
    q = [row[1] for row in kept]
    keep_index = [row[2] for row in kept]
    n = len(p)

    result: dict[str, Any] = {
        "elasticity": float("nan"),
        "intercept": float("nan"),
        "r_squared": 0.0,
        "observations": n,
        "price_cv": coefficient_of_variation(p),
        "controls": sorted(controls) if controls else [],
        "usable": False,
        "reason": "",
    }
    if n < min_observations:
        result["reason"] = f"only {n} usable observations, need {min_observations}"
        return result
    if result["price_cv"] < MIN_PRICE_CV:
        result["reason"] = (
            f"price varies by {result['price_cv']:.1%}; there is nothing to regress against"
        )
        return result

    log_p = [math.log(x) for x in p]
    log_q = [math.log(y) for y in q]

    if controls:
        columns = [np.ones(n), np.asarray(log_p, dtype=float)]
        for name in sorted(controls):
            series = [safe_float(controls[name][i], 0.0) for i in keep_index]
            columns.append(np.asarray(series, dtype=float))
        design = np.column_stack(columns)
        # lstsq rather than the normal equations: a control that turns out to be
        # collinear with price (a time trend, when every price moved on the same
        # quarterly cycle) makes X'X singular, and lstsq returns the minimum-norm
        # solution instead of raising.
        beta, *_ = np.linalg.lstsq(design, np.asarray(log_q, dtype=float), rcond=None)
        fitted = design @ beta
        residual = np.asarray(log_q) - fitted
        total = np.asarray(log_q) - np.mean(log_q)
        slope = float(beta[1])
        intercept = float(beta[0])
        denominator = float(total @ total)
        r2 = 1.0 - float(residual @ residual) / denominator if denominator > 0 else 0.0
    else:
        slope, intercept, r2 = _ols_slope(log_p, log_q)

    result.update({"elasticity": slope, "intercept": intercept, "r_squared": r2})

    lo, hi = PLAUSIBLE_RANGE
    if slope > hi:
        result["reason"] = (
            f"slope {slope:+.2f} is not a downward demand curve; "
            "volume rose with price, so something other than price is driving it"
        )
    elif slope < lo:
        result["reason"] = f"slope {slope:+.2f} is implausibly steep, probably a promotion artefact"
    else:
        result["usable"] = True
        result["reason"] = "ok"
    return result


def demand_at_price(
    base_quantity: float, base_price: float, new_price: float, elasticity: float
) -> float:
    """
    Constant-elasticity demand: ``q = q0 * (p/p0) ** e``.

    Constant elasticity is a local approximation. It is fine a few points
    either side of the observed price and nonsense at ten times it, which is
    why :func:`price_response_curve` defaults to a narrow band.
    """
    q0 = safe_float(base_quantity)
    p0 = safe_float(base_price)
    p1 = safe_float(new_price)
    e = safe_float(elasticity)
    if p0 <= 0 or p1 <= 0 or q0 <= 0:
        return 0.0
    return q0 * (p1 / p0) ** e


def revenue_at_price(
    base_quantity: float, base_price: float, new_price: float, elasticity: float
) -> float:
    return safe_float(new_price) * demand_at_price(base_quantity, base_price, new_price, elasticity)


def profit_at_price(
    base_quantity: float,
    base_price: float,
    new_price: float,
    elasticity: float,
    unit_cost: float,
) -> float:
    qty = demand_at_price(base_quantity, base_price, new_price, elasticity)
    return (safe_float(new_price) - safe_float(unit_cost)) * qty


def optimal_price(unit_cost: float, elasticity: float) -> dict[str, Any]:
    """
    The profit-maximising price under constant elasticity.

    ``p* = c * e / (e + 1)``, which for ``e = -2`` is twice cost and for
    ``e = -1.2`` is six times it. The implied markup explodes as elasticity
    approaches -1 from below and passes through infinity at exactly -1, so
    ``exists`` is returned rather than a number that would look like advice.

    ``exists`` and ``actionable`` are separate on purpose. Between about -1.0
    and -1.2 the formula returns a finite price that is five to six times cost
    -- mathematically correct, and worthless, because constant elasticity was
    only ever a local approximation and nothing about a fit around today's
    price licenses an extrapolation to six times it. A tool that prints that
    number is telling a pricing analyst to quintuple a price on the strength of
    an r-squared of 0.09. So the price is still returned, and ``actionable``
    says whether to act on it.
    """
    c = safe_float(unit_cost)
    e = safe_float(elasticity)
    if c <= 0:
        return {"exists": False, "actionable": False, "price": 0.0, "markup": 0.0,
                "implied_margin": 0.0, "reason": "no cost to mark up"}
    if e >= -1:
        return {
            "exists": False,
            "actionable": False,
            "price": 0.0,
            "markup": 0.0,
            "implied_margin": 0.0,
            "reason": (
                f"demand is inelastic (e = {e:+.2f}); profit rises with every price increase, "
                "so the ceiling is competitive or contractual, not mathematical"
            ),
        }
    markup = e / (e + 1)
    actionable = markup <= MAX_CREDIBLE_MARKUP
    return {
        "exists": True,
        "actionable": actionable,
        "price": c * markup,
        "markup": markup,
        "implied_margin": 1.0 / (-e),
        "reason": "ok" if actionable else (
            f"e = {e:+.2f} implies a markup of {markup:.1f}x cost. That is what the "
            "formula says and it is not a recommendation: the elasticity was fitted "
            "around today's price and says nothing about demand at several times it"
        ),
    }


def break_even_volume_change(price_change_pct: float, contribution_margin_pct: float) -> float:
    """
    The volume move a price change needs just to hold contribution flat.

    ``-d / (m + d)`` where ``d`` is the fractional price change and ``m`` the
    contribution margin. A cut (``d`` negative) returns a positive hurdle; a
    rise returns the volume you can afford to lose.

    Returns infinity when the cut takes the margin to zero or below: no volume
    saves a price that no longer covers variable cost.
    """
    d = safe_float(price_change_pct)
    m = safe_float(contribution_margin_pct)
    if m + d <= 0:
        return float("inf")
    return -d / (m + d)


def price_change_impact(
    *,
    base_quantity: float,
    base_price: float,
    unit_cost: float,
    price_change_pct: float,
    elasticity: float,
) -> dict[str, float]:
    """
    Forecast the revenue and margin effect of a price move.

    Reports the elasticity-implied volume response *and* the break-even hurdle
    beside it, because the decision is not "what does the model say" but "is
    the volume we would have to hold plausible".
    """
    p0 = safe_float(base_price)
    q0 = safe_float(base_quantity)
    c = safe_float(unit_cost)
    d = safe_float(price_change_pct)
    p1 = p0 * (1 + d)
    q1 = demand_at_price(q0, p0, p1, elasticity)

    rev0, rev1 = p0 * q0, p1 * q1
    margin0, margin1 = (p0 - c) * q0, (p1 - c) * q1
    contribution_pct = ((p0 - c) / p0) if p0 > 0 else 0.0

    return {
        "new_price": p1,
        "new_quantity": q1,
        "volume_change_pct": ((q1 / q0) - 1) if q0 > 0 else 0.0,
        "break_even_volume_change_pct": break_even_volume_change(d, contribution_pct),
        "revenue_before": rev0,
        "revenue_after": rev1,
        "revenue_change": rev1 - rev0,
        "revenue_change_pct": ((rev1 / rev0) - 1) if rev0 > 0 else 0.0,
        "margin_before": margin0,
        "margin_after": margin1,
        "margin_change": margin1 - margin0,
        "margin_change_pct": ((margin1 / margin0) - 1) if margin0 > 0 else 0.0,
        "contribution_margin_pct": contribution_pct,
    }


def price_response_curve(
    *,
    base_quantity: float,
    base_price: float,
    unit_cost: float,
    elasticity: float,
    low: float = -0.25,
    high: float = 0.25,
    steps: int = 21,
) -> list[dict[str, float]]:
    """
    Volume, revenue and profit across a band of prices around today's.

    Defaults to +/-25%, which is about as far as a constant-elasticity
    extrapolation deserves to be trusted. Each point carries ``is_current`` and
    ``is_profit_max`` so a chart can mark both without recomputing them.
    """
    q0 = safe_float(base_quantity)
    p0 = safe_float(base_price)
    c = safe_float(unit_cost)
    e = safe_float(elasticity)
    if p0 <= 0 or steps < 2:
        return []

    span = (high - low) / (steps - 1)
    points: list[dict[str, float]] = []
    for i in range(steps):
        change = low + span * i
        price = p0 * (1 + change)
        qty = demand_at_price(q0, p0, price, e)
        points.append(
            {
                "price_change_pct": change,
                "price": price,
                "quantity": qty,
                "revenue": price * qty,
                "profit": (price - c) * qty,
                "margin_pct": ((price - c) / price) if price > 0 else 0.0,
                "is_current": 0.0,
                "is_profit_max": 0.0,
            }
        )

    # Mark the point nearest today's price, and the one with the most profit.
    nearest = min(points, key=lambda r: abs(r["price_change_pct"]))
    nearest["is_current"] = 1.0
    best = max(points, key=lambda r: r["profit"])
    best["is_profit_max"] = 1.0
    return points


def cross_elasticity(
    own_prices: Sequence[Any],
    other_quantities: Sequence[Any],
) -> dict[str, Any]:
    """
    How our price moves someone else's volume.

    Same regression, different pairing. A *positive* slope means substitutes --
    our price up, their volume up -- which is the signal that two items are
    competing for the same order line, and the reason a price rise on one can
    be free while on another it just moves volume next door.
    """
    result = estimate_elasticity(own_prices, other_quantities)
    result["relationship"] = (
        "substitutes" if result["elasticity"] > 0.15
        else "complements" if result["elasticity"] < -0.15
        else "independent"
    )
    # The plausibility band above is written for own-price elasticity, which is
    # negative by construction. A positive cross-elasticity is the interesting
    # case here, so re-judge usability on sample size and variation only.
    result["usable"] = (
        result["observations"] >= MIN_OBSERVATIONS and result["price_cv"] >= MIN_PRICE_CV
    )
    return result
