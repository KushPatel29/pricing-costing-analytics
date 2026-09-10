"""
Where our price sits against the market, and what the market is doing.

A price index is our price over the market's, times 100. Index 100 is parity,
112 is a twelve-point premium, 94 is undercutting. It is the single number a
pricing analyst gets asked for most, and the two ways it goes wrong are both
about what "the market" means.

**Which competitors, and weighted how.** An unweighted mean over five
competitors gives a discount brand with 2% share the same vote as the category
leader. Everything here takes an optional weight per observation and defaults
to the *median* rather than the mean when no weights are given, because one
clearance price shifts a five-point mean by two points and the median not at
all.

**How old the observation is.** A shelf price scraped in March is not evidence
about June. :func:`freshness_weight` decays an observation's weight with age,
and :func:`price_index` reports the age of the data it used, so an index built
from stale scrapes announces itself instead of looking like this morning's.

The other half of this module is the market itself: index-linked input costs,
how much of a cost move actually reached price (:func:`passthrough_ratio`), and
at what lag. A distributor whose ocean freight index rose 14% and whose realised
price rose 4% has a pass-through of 0.29 and a margin problem it can name.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import safe_float

# Index bands. Deliberately asymmetric: being 3 points expensive is a normal
# premium-brand position, being 3 points cheap is money left on the table.
POSITION_BANDS: tuple[tuple[float, str], ...] = (
    (85.0, "Deep discount"),
    (96.0, "Below market"),
    (104.0, "At market"),
    (115.0, "Premium"),
    (float("inf"), "Super-premium"),
)

# An observation loses half its weight every this many days.
FRESHNESS_HALF_LIFE_DAYS = 45.0

# The smallest spread of period-over-period index changes that can carry a
# pass-through estimate. Below this the regressor is a constant.
MIN_INDEX_VARIATION = 1e-6
# Two years of quarters. Pass-through is estimated on quarterly changes
# because that is how often prices are reviewed; differencing monthly
# between two reviews measures customer mix, not pass-through.
MIN_PASSTHROUGH_OBSERVATIONS = 8
MIN_PASSTHROUGH_FIT = 0.10
# A clause that passes on more than double a cost move, or that moves price the
# other way, is not a pass-through -- it is a regression finding something else.
PLAUSIBLE_PASSTHROUGH = (-0.25, 2.0)


def freshness_weight(age_days: float, half_life: float = FRESHNESS_HALF_LIFE_DAYS) -> float:
    """
    Exponential decay on an observation's age. Age 0 weighs 1, one half-life
    weighs 0.5. Negative ages (a future-dated scrape, which happens) weigh 1
    rather than more than 1.
    """
    age = max(0.0, safe_float(age_days))
    if half_life <= 0:
        return 1.0
    return 0.5 ** (age / half_life)


def market_price(
    observations: Sequence[Mapping[str, Any]],
    *,
    price_key: str = "price",
    weight_key: str | None = None,
) -> float:
    """
    The market's price from a set of competitor observations.

    Weighted mean when weights are supplied, median when they are not. The
    median default is the important half: competitor scrapes contain clearance
    prices, misread pack sizes and the odd decimal in the wrong place, and a
    mean carries all of them into the index.
    """
    prices = [safe_float(o.get(price_key), 0.0) for o in observations]
    if weight_key is None:
        usable = [p for p in prices if p > 0]
        return statistics.median(usable) if usable else 0.0

    num = den = 0.0
    for obs, price in zip(observations, prices, strict=False):
        weight = safe_float(obs.get(weight_key), 0.0)
        if price > 0 and weight > 0:
            num += price * weight
            den += weight
    return num / den if den > 0 else 0.0


def price_index(
    our_price: float,
    observations: Sequence[Mapping[str, Any]],
    *,
    price_key: str = "price",
    weight_key: str | None = None,
    age_key: str | None = None,
) -> dict[str, Any]:
    """
    Our price against the market, as an index with its provenance attached.

    When ``age_key`` is given, each observation's weight is multiplied by its
    freshness, so a fresh scrape outvotes a stale one without either being
    thrown away. ``max_age_days`` comes back in the result so a caller can
    refuse to act on an index built from last quarter's data.
    """
    if not observations:
        return {
            "our_price": safe_float(our_price), "market_price": 0.0, "index": float("nan"),
            "gap": 0.0, "gap_pct": 0.0, "position": "No coverage",
            "observations": 0, "competitors": 0, "max_age_days": float("nan"),
        }

    scored = []
    for obs in observations:
        weight = safe_float(obs.get(weight_key), 1.0) if weight_key else 1.0
        if age_key is not None:
            weight *= freshness_weight(safe_float(obs.get(age_key), 0.0))
        scored.append({**obs, "_w": weight})

    use_weights = weight_key is not None or age_key is not None
    market = market_price(scored, price_key=price_key, weight_key="_w" if use_weights else None)

    ours = safe_float(our_price)
    index = (ours / market * 100.0) if market > 0 else float("nan")
    ages = [safe_float(o.get(age_key), 0.0) for o in observations] if age_key else [0.0]

    return {
        "our_price": ours,
        "market_price": market,
        "index": index,
        "gap": ours - market,
        "gap_pct": ((ours - market) / market) if market > 0 else 0.0,
        "position": market_position(index),
        "observations": len(observations),
        "competitors": len({o.get("competitor") for o in observations if o.get("competitor")}),
        "max_age_days": max(ages) if ages else 0.0,
    }


def market_position(index: float) -> str:
    """Name the band an index falls in. NaN means we could not see the market."""
    value = safe_float(index, float("nan"))
    if math.isnan(value) or value <= 0:
        return "No coverage"
    for ceiling, label in POSITION_BANDS:
        if value < ceiling:
            return label
    return POSITION_BANDS[-1][1]


def price_dispersion(prices: Sequence[Any]) -> dict[str, float]:
    """
    How wide the market's own spread is.

    A tight spread means the index is a real position. A spread of 40% means
    the products are not actually comparable and the index is arithmetic on
    incommensurable things -- which is worth knowing before quoting it.
    """
    nums = sorted(p for p in (safe_float(v, 0.0) for v in prices) if p > 0)
    if not nums:
        return {"n": 0, "min": 0.0, "p25": 0.0, "median": 0.0, "p75": 0.0,
                "max": 0.0, "spread_pct": 0.0}

    def pct(q: float) -> float:
        if len(nums) == 1:
            return nums[0]
        pos = q * (len(nums) - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, len(nums) - 1)
        return nums[lo] + (nums[hi] - nums[lo]) * (pos - lo)

    median = pct(0.5)
    return {
        "n": len(nums),
        "min": nums[0],
        "p25": pct(0.25),
        "median": median,
        "p75": pct(0.75),
        "max": nums[-1],
        "spread_pct": ((nums[-1] - nums[0]) / median) if median > 0 else 0.0,
    }


def competitive_gaps(
    rows: Sequence[Mapping[str, Any]],
    *,
    index_key: str = "index",
    revenue_key: str = "revenue",
) -> dict[str, Any]:
    """
    Summarise a book of indexed products into the two lists a pricing meeting
    needs: where we are leaving money on the table, and where we are exposed.

    Both are ranked by *revenue at risk*, not by the size of the gap. A
    fourteen-point premium on a product nobody buys is a curiosity; a
    three-point premium on the top line is the whole conversation.
    """
    priced = [r for r in rows if not math.isnan(safe_float(r.get(index_key), float("nan")))]
    if not priced:
        return {"underpriced": [], "overpriced": [], "median_index": float("nan"),
                "revenue_at_risk": 0.0, "coverage": 0.0}

    def revenue(row: Mapping[str, Any]) -> float:
        return safe_float(row.get(revenue_key), 0.0)

    under = sorted(
        (r for r in priced if safe_float(r.get(index_key)) < 96.0),
        key=revenue, reverse=True,
    )
    over = sorted(
        (r for r in priced if safe_float(r.get(index_key)) > 115.0),
        key=revenue, reverse=True,
    )
    return {
        "underpriced": list(under),
        "overpriced": list(over),
        "median_index": statistics.median(safe_float(r.get(index_key)) for r in priced),
        "revenue_at_risk": sum(revenue(r) for r in over),
        "opportunity": sum(revenue(r) for r in under),
        "coverage": len(priced) / len(rows) if rows else 0.0,
    }


def index_series(
    values: Sequence[Any], *, base: float | None = None
) -> list[float]:
    """Rebase a series to 100 at its first point (or at a given base)."""
    nums = [safe_float(v, 0.0) for v in values]
    anchor = safe_float(base) if base is not None else next((v for v in nums if v > 0), 0.0)
    if anchor <= 0:
        return [float("nan")] * len(nums)
    return [v / anchor * 100.0 for v in nums]


def passthrough_ratio(
    input_index: Sequence[Any], price_index_series: Sequence[Any], *, lag: int = 0
) -> dict[str, Any]:
    """
    How much of an input-cost movement reached our price, at a given lag.

    Regresses period-over-period price changes on input changes. A ratio of 1.0
    is full pass-through, 0.0 is absorbing everything. Ratios above 1 happen and
    are usually real -- costs are the excuse for a price rise that was going to
    happen anyway -- but they are also what a two-observation regression returns,
    so ``observations`` comes back with it.

    ``lag`` shifts the price series later: at lag 2 the ratio answers "did a cost
    move show up in price two periods on", which is the question, because it
    never shows up the same month.
    """
    costs = [safe_float(v, 0.0) for v in input_index]
    prices = [safe_float(v, 0.0) for v in price_index_series]
    n = min(len(costs), len(prices) - lag) if lag >= 0 else 0
    if n < 3:
        return {"passthrough": float("nan"), "r_squared": 0.0, "observations": 0,
                "lag": lag, "usable": False, "reason": "not enough periods"}

    cost_chg: list[float] = []
    price_chg: list[float] = []
    for i in range(1, n):
        if costs[i - 1] > 0 and prices[i - 1 + lag] > 0:
            cost_chg.append(costs[i] / costs[i - 1] - 1.0)
            price_chg.append(prices[i + lag] / prices[i - 1 + lag] - 1.0)

    if len(cost_chg) < 3:
        return {"passthrough": float("nan"), "r_squared": 0.0,
                "observations": len(cost_chg), "lag": lag,
                "usable": False, "reason": "not enough periods"}

    # An index that moved by the same amount every period has no variation to
    # regress against, and the slope is then whatever floating-point noise is
    # left in the denominator -- on a perfectly smooth 2%-a-month series that
    # came out as -0.25, which reads as a seller cutting price into a rising
    # market. Refuse it rather than return it.
    spread = max(cost_chg) - min(cost_chg)
    if spread < MIN_INDEX_VARIATION:
        return {"passthrough": float("nan"), "r_squared": 0.0,
                "observations": len(cost_chg), "lag": lag, "usable": False,
                "reason": "the index moved by the same amount every period"}

    mean_c = sum(cost_chg) / len(cost_chg)
    mean_p = sum(price_chg) / len(price_chg)
    scc = sum((c - mean_c) ** 2 for c in cost_chg)
    scp = sum((c - mean_c) * (p - mean_p)
              for c, p in zip(cost_chg, price_chg, strict=False))
    spp = sum((p - mean_p) ** 2 for p in price_chg)
    if scc == 0:
        return {"passthrough": float("nan"), "r_squared": 0.0,
                "observations": len(cost_chg), "lag": lag, "usable": False,
                "reason": "no variation in the index"}

    ratio = scp / scc
    r_squared = (scp * scp) / (scc * spp) if spp > 0 else 0.0
    return {
        "passthrough": ratio,
        "r_squared": r_squared,
        "observations": len(cost_chg),
        "lag": lag,
        **_passthrough_verdict(ratio, r_squared, len(cost_chg)),
    }


def _passthrough_verdict(ratio: float, r_squared: float, observations: int) -> dict[str, Any]:
    """
    Whether a pass-through estimate is worth quoting, and why not when it is not.

    An index that barely moves cannot carry one. The agricultural input index
    in the sample drifts under four percent a year with almost no volatility, and
    the regression on it returns -2.15 at an r-squared of 0.07 -- a number that
    says the seller cut price into a rising market, and means only that there
    was nothing to fit. Reporting it beside its r-squared is not enough; a
    reader takes the coefficient and leaves the diagnostic.
    """
    if observations < MIN_PASSTHROUGH_OBSERVATIONS:
        return {"usable": False,
                "reason": f"only {observations} periods, need {MIN_PASSTHROUGH_OBSERVATIONS}"}
    if r_squared < MIN_PASSTHROUGH_FIT:
        return {"usable": False,
                "reason": f"the fit explains {r_squared:.1%} of the variation; the index "
                          "did not move enough to price against"}
    low, high = PLAUSIBLE_PASSTHROUGH
    if not low <= ratio <= high:
        return {"usable": False,
                "reason": f"{ratio:+.2f} is outside anything a pricing clause would say"}
    return {"usable": True, "reason": "ok"}


def best_passthrough_lag(
    input_index: Sequence[Any],
    price_index_series: Sequence[Any],
    *,
    max_lag: int = 4,
) -> dict[str, Any]:
    """
    Try every lag up to ``max_lag`` and keep the best-fitting one.

    Chosen on r-squared, not on the size of the ratio: the point is *when* cost
    reaches price, and picking the lag with the biggest coefficient would just
    find the noisiest one.
    """
    fits = [
        passthrough_ratio(input_index, price_index_series, lag=lag)
        for lag in range(max_lag + 1)
    ]
    # Prefer a lag whose fit is worth quoting. Falling back to the best of the
    # unusable ones keeps the reason attached rather than returning a bare NaN
    # that the caller has to interpret.
    usable = [f for f in fits if f.get("usable")]
    if not usable:
        usable = [f for f in fits if not math.isnan(f["passthrough"])]
    if not usable:
        return {"passthrough": float("nan"), "r_squared": 0.0, "observations": 0,
                "lag": 0, "usable": False, "reason": "no lag produced a fit",
                "all_lags": fits}
    best = max(usable, key=lambda f: f["r_squared"])
    return {**best, "all_lags": fits}


def indexed_price(
    base_price: float,
    index_now: float,
    index_base: float,
    passthrough: float = 1.0,
) -> float:
    """
    The price an index-linked contract implies today.

    ``base * (1 + passthrough * (index_now/index_base - 1))``. At
    ``passthrough = 1`` this is a full-pass-through clause; at 0.6 the seller
    eats 40% of every move, which is what most negotiated clauses actually say.
    """
    base = safe_float(base_price)
    now = safe_float(index_now)
    ref = safe_float(index_base)
    if base <= 0 or ref <= 0 or now <= 0:
        return base
    return base * (1.0 + safe_float(passthrough, 1.0) * (now / ref - 1.0))
