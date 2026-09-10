"""
What customers will actually pay, and how much of that we are collecting.

Two questions, two halves of this module.

**Willingness to pay**, from quotes we won and lost. A win/loss file is the only
place a seller observes demand at prices it did not charge, which makes it the
only honest source for a demand curve -- transaction data contains no losses at
all, so a regression on it is fitted entirely to prices customers accepted.
:func:`fit_win_curve` fits a logistic on the price a quote was pitched at
(usually relative to the competing quote) and returns the price at which we win
half the time, which is the number a sales director recognises.

**Realisation**, from what different customers pay for the same thing. The
spread of pocket prices across a customer base for one product is the *price
band*, and its width is the most reliable margin opportunity in any book of
business -- not because the low end is wrong, but because a band 30 points wide
usually cannot be explained by anything the seller would defend out loud.
:func:`realisation_gap` values the move of everyone below the median up to it,
which is the conservative version of that opportunity: it does not assume
anybody pays more than someone comparable already does.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import safe_float

# Newton steps for the logistic fit. It converges in about five on this shape
# of data; the cap is a guard against a separable sample, not a tuning knob.
MAX_NEWTON_STEPS = 40
NEWTON_TOLERANCE = 1e-9
# Ridge added to the Hessian diagonal. Without it a perfectly separated sample
# (we won every cheap quote and lost every dear one) sends the slope to
# infinity and the fit returns a coefficient with no meaning.
RIDGE = 1e-6


def _sigmoid(z: float) -> float:
    """Numerically safe logistic. exp overflows around z = -745."""
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def fit_logistic(xs: Sequence[float], ys: Sequence[float]) -> dict[str, Any]:
    """
    One-variable logistic regression by Newton-Raphson (IRLS).

    Returns intercept, slope and the fit's log-likelihood, plus ``converged``.
    Written out rather than imported because the whole model is two parameters
    and a 2x2 inverse, and a scikit-learn dependency for that would be the
    largest thing in the requirements file by an order of magnitude.
    """
    pairs = [
        (safe_float(x), 1.0 if safe_float(y) > 0.5 else 0.0)
        for x, y in zip(xs, ys, strict=False)
        if x is not None and y is not None
    ]
    n = len(pairs)
    result: dict[str, Any] = {
        "intercept": float("nan"), "slope": float("nan"),
        "observations": n, "converged": False, "log_likelihood": float("nan"),
        "wins": sum(1 for _, y in pairs if y == 1.0),
    }
    if n < 10 or result["wins"] in (0, n):
        result["reason"] = (
            f"{n} quotes with {result['wins']} wins -- no variation to fit"
            if n >= 10 else f"only {n} quotes"
        )
        return result

    b0, b1 = 0.0, 0.0
    for _ in range(MAX_NEWTON_STEPS):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in pairs:
            p = _sigmoid(b0 + b1 * x)
            r = y - p
            w = p * (1.0 - p)
            g0 += r
            g1 += r * x
            h00 += w
            h01 += w * x
            h11 += w * x * x
        h00 += RIDGE
        h11 += RIDGE
        det = h00 * h11 - h01 * h01
        if abs(det) < 1e-14:
            break
        step0 = (h11 * g0 - h01 * g1) / det
        step1 = (h00 * g1 - h01 * g0) / det
        b0 += step0
        b1 += step1
        if abs(step0) < NEWTON_TOLERANCE and abs(step1) < NEWTON_TOLERANCE:
            result["converged"] = True
            break

    ll = 0.0
    for x, y in pairs:
        p = min(max(_sigmoid(b0 + b1 * x), 1e-12), 1 - 1e-12)
        ll += y * math.log(p) + (1 - y) * math.log(1 - p)

    result.update({"intercept": b0, "slope": b1, "log_likelihood": ll, "reason": "ok"})
    return result


def fit_win_curve(
    quotes: Sequence[Mapping[str, Any]],
    *,
    price_key: str = "price_ratio",
    outcome_key: str = "won",
) -> dict[str, Any]:
    """
    The probability of winning a quote as a function of what we asked for.

    ``price_key`` defaults to the ratio of our quote to the competing one,
    which is the variable that transfers across products -- an absolute price
    curve fitted over a $340 monitor and a $6 pack of pens at once is fitted to
    the difference between a monitor and a pen.

    The headline output is ``indifference_price``: the value of the price
    variable at which we win exactly half the time. The slope should be
    negative; a positive one means we won the expensive quotes, which happens
    when the sample is confounded by something else (usually urgency), and is
    reported as unusable rather than inverted.
    """
    xs = [safe_float(q.get(price_key)) for q in quotes]
    ys = [1.0 if q.get(outcome_key) else 0.0 for q in quotes]
    fit = fit_logistic(xs, ys)

    indifference = float("nan")
    if fit["converged"] and fit["slope"] not in (0.0, float("nan")) and fit["slope"] < 0:
        indifference = -fit["intercept"] / fit["slope"]

    usable = fit["converged"] and fit["slope"] < 0 and not math.isnan(indifference)
    return {
        **fit,
        "indifference_price": indifference,
        "win_rate": (sum(ys) / len(ys)) if ys else 0.0,
        "usable": usable,
        "reason": fit.get("reason", "ok") if usable else (
            "slope is not downward -- winning the dearer quotes means something "
            "other than price is driving this sample"
            if fit["converged"] else fit.get("reason", "did not converge")
        ),
    }


def win_curve_points(
    fit: Mapping[str, Any], *, low: float = 0.80, high: float = 1.30, steps: int = 26
) -> list[dict[str, float]]:
    """The fitted curve as points, for drawing over the observed win rates."""
    b0, b1 = safe_float(fit.get("intercept")), safe_float(fit.get("slope"))
    if math.isnan(b0) or math.isnan(b1) or steps < 2:
        return []
    span = (high - low) / (steps - 1)
    return [
        {"price_ratio": low + span * i, "win_probability": _sigmoid(b0 + b1 * (low + span * i))}
        for i in range(steps)
    ]


def observed_win_rates(
    quotes: Sequence[Mapping[str, Any]],
    *,
    price_key: str = "price_ratio",
    outcome_key: str = "won",
    bucket_width: float = 0.05,
    min_quotes: int = 5,
) -> list[dict[str, Any]]:
    """
    Win rate in price buckets, for plotting the raw data under the fitted curve.

    Buckets holding fewer than ``min_quotes`` are dropped: a bucket of two with
    one win reads as a 50% win rate and is drawn the same size as a bucket of
    four hundred, which is how a chart lies without anyone typing a wrong number.
    """
    buckets: dict[int, dict[str, Any]] = {}
    for quote in quotes:
        ratio = safe_float(quote.get(price_key))
        if ratio <= 0:
            continue
        index = int(math.floor(ratio / bucket_width))
        entry = buckets.setdefault(index, {"quotes": 0, "wins": 0})
        entry["quotes"] += 1
        entry["wins"] += 1 if quote.get(outcome_key) else 0

    out = []
    for index, entry in sorted(buckets.items()):
        if entry["quotes"] < min_quotes:
            continue
        out.append(
            {
                "price_ratio": (index + 0.5) * bucket_width,
                "quotes": entry["quotes"],
                "wins": entry["wins"],
                "win_rate": entry["wins"] / entry["quotes"],
            }
        )
    return out


# --------------------------------------------------------------------------
# Price realisation
# --------------------------------------------------------------------------

def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def price_band_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    price_key: str = "pocket_price",
    quantity_key: str = "quantity",
) -> dict[str, Any]:
    """
    The shape of one product's price band across its customers.

    Percentiles are on the *volume-weighted* distribution: each line is
    repeated in proportion to what it shipped, so a band computed over four
    hundred small accounts and one large one does not describe the four
    hundred. Weighting is done by carrying the weight through the percentile
    rather than by materialising repeats, so a million-unit line does not
    allocate a million entries.
    """
    entries = sorted(
        (
            (safe_float(r.get(price_key)), max(0.0, safe_float(r.get(quantity_key), 0.0)))
            for r in rows
            if safe_float(r.get(price_key)) > 0
        ),
        key=lambda t: t[0],
    )
    if not entries:
        return {"n": 0, "p10": 0.0, "median": 0.0, "p90": 0.0, "mean": 0.0,
                "band_width_pct": 0.0, "volume": 0.0}

    total_weight = sum(w for _, w in entries)
    if total_weight <= 0:
        prices = [p for p, _ in entries]
        median = statistics.median(prices)
        return {
            "n": len(prices), "p10": _percentile(prices, 0.10), "median": median,
            "p90": _percentile(prices, 0.90), "mean": sum(prices) / len(prices),
            "band_width_pct": ((_percentile(prices, 0.90) - _percentile(prices, 0.10)) / median)
            if median else 0.0,
            "volume": 0.0,
        }

    def weighted_percentile(q: float) -> float:
        target = q * total_weight
        cumulative = 0.0
        for price, weight in entries:
            cumulative += weight
            if cumulative >= target:
                return price
        return entries[-1][0]

    median = weighted_percentile(0.50)
    p10, p90 = weighted_percentile(0.10), weighted_percentile(0.90)
    return {
        "n": len(entries),
        "p10": p10,
        "median": median,
        "p90": p90,
        "mean": sum(p * w for p, w in entries) / total_weight,
        "band_width_pct": ((p90 - p10) / median) if median else 0.0,
        "volume": total_weight,
    }


def realisation_gap(
    rows: Sequence[Mapping[str, Any]],
    *,
    price_key: str = "pocket_price",
    quantity_key: str = "quantity",
    target: str = "median",
) -> dict[str, Any]:
    """
    What moving the low end of a price band up to the middle would be worth.

    Deliberately conservative. ``target="median"`` values only the gap up to
    the volume-weighted median -- a price half this product's own volume
    already pays -- so the number is defensible in a room containing the
    salespeople who own those accounts. ``target="p75"`` is the stretch case;
    it is available and it is not the default.
    """
    stats = price_band_stats(rows, price_key=price_key, quantity_key=quantity_key)
    if stats["n"] == 0:
        return {**stats, "target_price": 0.0, "opportunity": 0.0, "lines_below": 0}

    entries = sorted(
        (
            (safe_float(r.get(price_key)), max(0.0, safe_float(r.get(quantity_key), 0.0)))
            for r in rows
            if safe_float(r.get(price_key)) > 0
        ),
        key=lambda t: t[0],
    )
    total_weight = sum(w for _, w in entries) or float(len(entries))

    if target == "p75":
        cumulative, target_price = 0.0, entries[-1][0]
        for price, weight in entries:
            cumulative += weight
            if cumulative >= 0.75 * total_weight:
                target_price = price
                break
    else:
        target_price = stats["median"]

    below = [(p, w) for p, w in entries if p < target_price]
    return {
        **stats,
        "target_price": target_price,
        "target": target,
        "opportunity": sum((target_price - p) * w for p, w in below),
        "lines_below": len(below),
        "volume_below": sum(w for _, w in below),
    }


def segment_profile(
    rows: Sequence[Mapping[str, Any]],
    *,
    key: str = "segment",
    price_key: str = "pocket_price",
    quantity_key: str = "quantity",
    cost_key: str = "final_cost",
) -> list[dict[str, Any]]:
    """
    One row per segment: volume, realised price, margin, and the band width.

    Band width is what makes this a pricing view rather than a sales report. A
    segment paying a low average price *consistently* is a positioning
    decision; a segment paying a low average price with a 40-point spread is an
    execution failure, and the two need different meetings.
    """
    groups: dict[Any, list[Mapping[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row.get(key), []).append(row)

    out = []
    for name, group in groups.items():
        volume = sum(safe_float(r.get(quantity_key), 0.0) for r in group)
        revenue = sum(
            safe_float(r.get(price_key)) * safe_float(r.get(quantity_key), 0.0) for r in group
        )
        cogs = sum(
            safe_float(r.get(cost_key)) * safe_float(r.get(quantity_key), 0.0) for r in group
        )
        stats = price_band_stats(group, price_key=price_key, quantity_key=quantity_key)
        gap = realisation_gap(group, price_key=price_key, quantity_key=quantity_key)
        out.append(
            {
                key: name,
                "lines": len(group),
                "volume": volume,
                "revenue": revenue,
                "cogs": cogs,
                "margin": revenue - cogs,
                "margin_pct": ((revenue - cogs) / revenue) if revenue > 0 else 0.0,
                "avg_price": (revenue / volume) if volume > 0 else 0.0,
                "median_price": stats["median"],
                "band_width_pct": stats["band_width_pct"],
                "realisation_opportunity": gap["opportunity"],
            }
        )
    return sorted(out, key=lambda r: -r["revenue"])
