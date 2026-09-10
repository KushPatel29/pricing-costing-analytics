"""
Turn the analysis into one of five actions, with the reason written out.

Everything else in this package produces a number. This produces a sentence,
because the deliverable of a pricing analyst is not an elasticity -- it is
"raise these forty items by four points, here is what it is worth, and here is
why volume will not walk".

The rules are deliberately **transparent and ordered**. A scoring model would
rank better and would be unusable: the person who has to defend the increase to
the customer needs to know it was recommended because the item is nine points
under the market, has not been repriced in fourteen months, and sits below its
own margin floor -- not because it scored 0.83.

Five actions, in the order they are tested:

1. **Fix cost** -- the master data is wrong, so no price recommendation is
   safe. This has to come first: every rule below it divides by a cost.
2. **Discontinue** -- loses money at every price the market will bear, and is
   too small to be worth fixing.
3. **Increase** -- under the market, under its own floor, or unmanaged while
   costs moved.
4. **Discount** -- priced above the market with margin to spend and demand
   elastic enough to buy volume with it.
5. **Bundle** -- thin on its own, but bought alongside something else.
6. **Maintain** -- everything else. It is the most common answer and it belongs
   in the output, because a recommendation file where every line says "act" is
   a file nobody reads twice.

Confidence is reported separately from the action, and it is about the
*evidence*, not the size of the prize: an increase justified by an elasticity
fitted at an r-squared of 0.03 is a guess with a decimal point.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import calculate_price_from_margin, safe_float
from pricing.elasticity import break_even_volume_change, demand_at_price

ACTIONS = ("Fix cost", "Discontinue", "Increase", "Discount", "Bundle", "Maintain")

# Thresholds. Named, because every one of them is a judgement somebody should
# be able to argue with rather than a constant buried in a condition.
UNDER_MARKET_INDEX = 96.0
OVER_MARKET_INDEX = 116.0
STALE_DAYS = 300
MATERIAL_COST_MOVE = 0.06
SMALL_VOLUME_PERCENTILE = 0.15
MAX_SINGLE_INCREASE = 0.09
MAX_SINGLE_DISCOUNT = 0.07


def _money(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:,.0f}k"
    return f"${value:,.0f}"


def confidence(row: Mapping[str, Any]) -> tuple[str, str]:
    """
    How much the evidence behind a recommendation is worth.

    Three inputs: whether the elasticity fit is usable, how much competitive
    coverage there is, and how many months of the product's own history the
    conclusion rests on. Reported beside the action rather than folded into it,
    so a high-value recommendation on thin evidence is visible as exactly that.
    """
    points = 0
    reasons = []
    if row.get("elasticity_usable"):
        points += 1
    else:
        reasons.append("elasticity not reliably fitted")
    coverage = safe_float(row.get("competitors_seen"), 0.0)
    if coverage >= 3:
        points += 1
    elif coverage >= 1:
        reasons.append(f"only {coverage:.0f} competitor observation(s)")
    else:
        reasons.append("no competitive coverage")
    months = safe_float(row.get("months_of_history"), 0.0)
    if months >= 24:
        points += 1
    else:
        reasons.append(f"{months:.0f} months of history")

    level = ("High", "Medium", "Low", "Low")[3 - points] if points <= 3 else "High"
    return level, "; ".join(reasons) if reasons else "elasticity, market and history all present"


def _impact(row: Mapping[str, Any], new_price: float) -> dict[str, float]:
    """
    What the move is worth, at the product's own elasticity.

    Volume response uses the fitted elasticity when it is usable and the
    category's otherwise; when neither is available it assumes **no** volume
    response and says so through ``volume_assumed``. Assuming zero is the
    conservative direction for an increase and the optimistic one for a cut,
    which is why the assumption is returned rather than hidden.
    """
    price = safe_float(row.get("current_price"))
    cost = safe_float(row.get("unit_cost"))
    volume = safe_float(row.get("volume_units"))
    if price <= 0 or volume <= 0:
        return {"volume_after": volume, "revenue_delta": 0.0, "margin_delta": 0.0,
                "volume_change_pct": 0.0, "volume_assumed": 1.0}

    usable = bool(row.get("elasticity_usable"))
    elasticity = safe_float(row.get("elasticity"), 0.0) if usable else 0.0
    new_volume = (
        demand_at_price(volume, price, new_price, elasticity) if usable else volume
    )
    revenue_before, revenue_after = price * volume, new_price * new_volume
    margin_before = (price - cost) * volume
    margin_after = (new_price - cost) * new_volume
    return {
        "volume_after": new_volume,
        "volume_change_pct": (new_volume / volume - 1) if volume else 0.0,
        "revenue_delta": revenue_after - revenue_before,
        "margin_delta": margin_after - margin_before,
        "volume_assumed": 0.0 if usable else 1.0,
    }


def recommend(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    One product's recommendation: the action, the price, the reason, the worth.

    ``row`` carries what the engine already computed -- current price, unit
    cost, volume, margin, price index, days since the last price change, cost
    movement since then, the elasticity and its usability, and the target and
    floor margins. Nothing is recomputed here; this is the decision layer.
    """
    price = safe_float(row.get("current_price"))
    cost = safe_float(row.get("unit_cost"))
    volume = safe_float(row.get("volume_units"))
    margin_pct = safe_float(row.get("margin_pct"))
    target = safe_float(row.get("target_margin"), 0.22)
    floor = safe_float(row.get("floor_margin"), max(0.05, target - 0.09))
    index = safe_float(row.get("price_index"), float("nan"))
    stale_days = safe_float(row.get("days_since_price_change"), 0.0)
    cost_move = safe_float(row.get("cost_change_pct"), 0.0)
    elasticity = safe_float(row.get("elasticity"), 0.0)
    is_small = bool(row.get("is_small_volume"))
    co_purchase = safe_float(row.get("co_purchase_rate"), 0.0)

    level, evidence = confidence(row)
    base = {
        "product_id": row.get("product_id"),
        "description": row.get("description"),
        "category": row.get("category"),
        "current_price": price,
        "unit_cost": cost,
        "volume_units": volume,
        "margin_pct": margin_pct,
        "price_index": index,
        "confidence": level,
        "evidence": evidence,
    }

    # --- 1. Master data first. Everything below divides by a cost. ---------
    if cost <= 0 or bool(row.get("cost_is_missing")):
        return {
            **base, "action": "Fix cost", "recommended_price": price,
            "price_change_pct": 0.0, "volume_change_pct": 0.0,
            "revenue_delta": 0.0, "margin_delta": 0.0, "priority": volume * price,
            "rationale": (
                "No usable standard cost on the material master, so every margin "
                "on this item is measured against zero. No price recommendation "
                "is safe until costing maintains it."
            ),
        }

    # --- 2. Loses money at any price the market will bear ------------------
    market_price = safe_float(row.get("market_price"), 0.0)
    if margin_pct <= 0 and is_small and (market_price <= 0 or market_price < cost):
        return {
            **base, "action": "Discontinue", "recommended_price": price,
            "price_change_pct": 0.0, "volume_change_pct": -1.0,
            "revenue_delta": -price * volume,
            "margin_delta": -(price - cost) * volume,
            "priority": abs((price - cost) * volume),
            "rationale": (
                f"Margin is {margin_pct:.1%} and the market is charging "
                f"{_money(market_price)}/unit against a cost of {_money(cost)}/unit. "
                f"At {volume:,.0f} units it is too small to be worth re-engineering. "
                "Exit unless it is holding a listing."
            ),
        }

    # --- 3. Increase -------------------------------------------------------
    under_market = not math.isnan(index) and index < UNDER_MARKET_INDEX
    below_floor = margin_pct < floor
    unmanaged = stale_days > STALE_DAYS and cost_move > MATERIAL_COST_MOVE
    if under_market or below_floor or unmanaged:
        # Take the smallest of: to the market, to the target margin, and the cap.
        # A single move larger than the cap is a conversation with the customer,
        # not a price-list change, and recommending it as one gets the whole
        # file ignored.
        candidates = [price * (1 + MAX_SINGLE_INCREASE)]
        if not math.isnan(index) and index > 0:
            candidates.append(price * (UNDER_MARKET_INDEX / index))
        if margin_pct < target:
            # Only a candidate when it is actually *above* today's price. An
            # item that is nine points under the market while already clearing
            # its target margin is the cheapest margin in the book, and taking
            # the minimum against a target-margin price that sits below current
            # cancelled the increase entirely -- the recommendation came back
            # "raise to $20.00 (+0.0%)", which is not a recommendation.
            target_price = calculate_price_from_margin(cost, target)
            if target_price > price:
                candidates.append(target_price)
        new_price = max(price, min(candidates))
        impact = _impact(row, new_price)
        reasons = []
        if under_market:
            reasons.append(f"priced at index {index:.0f} against the market")
        if below_floor:
            reasons.append(f"margin {margin_pct:.1%} is under the {floor:.0%} floor")
        if unmanaged:
            reasons.append(
                f"no price change in {stale_days:.0f} days while cost moved "
                f"{cost_move:+.1%}"
            )
        hurdle = break_even_volume_change(new_price / price - 1, margin_pct)
        return {
            **base, "action": "Increase", "recommended_price": new_price,
            "price_change_pct": new_price / price - 1,
            "volume_change_pct": impact["volume_change_pct"],
            "revenue_delta": impact["revenue_delta"],
            "margin_delta": impact["margin_delta"],
            "priority": abs(impact["margin_delta"]),
            "rationale": (
                f"Raise to {_money(new_price)}/unit ({new_price / price - 1:+.1%}) because "
                + ", and ".join(reasons)
                + f". The rise can afford to lose {abs(hurdle):.1%} of volume before "
                f"contribution falls; the fitted elasticity implies losing "
                f"{abs(impact['volume_change_pct']):.1%}."
                + ("" if not impact["volume_assumed"] else
                   " No usable elasticity, so this assumes volume holds -- the "
                   "conservative direction for an increase.")
            ),
        }

    # --- 4. Discount to buy volume ----------------------------------------
    over_market = not math.isnan(index) and index > OVER_MARKET_INDEX
    elastic = bool(row.get("elasticity_usable")) and elasticity < -1.4
    if over_market and elastic and margin_pct > target + 0.04:
        new_price = max(
            price * (1 - MAX_SINGLE_DISCOUNT),
            price * (OVER_MARKET_INDEX / index) if index > 0 else price,
        )
        impact = _impact(row, new_price)
        hurdle = break_even_volume_change(new_price / price - 1, margin_pct)
        pays = impact["margin_delta"] > 0
        # Only recommend the cut if it pays. Being over the market is the
        # trigger for asking the question, not the answer to it -- and a
        # recommendation file that tells you to discount into a margin loss is
        # a file that gets ignored wholesale.
        if not pays:
            return {
                **base, "action": "Maintain", "recommended_price": price,
                "price_change_pct": 0.0, "volume_change_pct": 0.0,
                "revenue_delta": 0.0, "margin_delta": 0.0, "priority": 0.0,
                "rationale": (
                    f"At index {index:.0f} this sits {index - 100:.0f} points over the "
                    f"market, but coming back would need {hurdle:+.1%} volume to stand "
                    f"still and the elasticity implies only "
                    f"{impact['volume_change_pct']:+.1%}. Hold the price and defend the "
                    "premium."
                ),
            }
        return {
            **base, "action": "Discount", "recommended_price": new_price,
            "price_change_pct": new_price / price - 1,
            "volume_change_pct": impact["volume_change_pct"],
            "revenue_delta": impact["revenue_delta"],
            "margin_delta": impact["margin_delta"],
            "priority": abs(impact["margin_delta"]),
            "rationale": (
                f"At index {index:.0f} this is {index - 100:.0f} points over the market "
                f"on demand that responds ({elasticity:.1f}). Coming back to "
                f"{_money(new_price)}/unit needs {hurdle:+.1%} volume to stand still and "
                f"the elasticity implies {impact['volume_change_pct']:+.1%}, so it pays."
            ),
        }

    # --- 5. Bundle ---------------------------------------------------------
    if is_small and co_purchase > 0.35 and margin_pct > 0:
        return {
            **base, "action": "Bundle", "recommended_price": price,
            "price_change_pct": 0.0, "volume_change_pct": 0.0,
            "revenue_delta": 0.0, "margin_delta": 0.0,
            "priority": price * volume * 0.1,
            "rationale": (
                f"Only {volume:,.0f} units on its own, but {co_purchase:.0%} of the "
                "customers who buy it buy it alongside something else. It is worth "
                "more as an attachment than as a line, and a bundle survives "
                "cannibalisation up to its own margin ratio."
            ),
        }

    # --- 6. Maintain -------------------------------------------------------
    position = (
        "at the market" if math.isnan(index) or 96 <= index <= 116
        else f"at index {index:.0f}"
    )
    return {
        **base, "action": "Maintain", "recommended_price": price,
        "price_change_pct": 0.0, "volume_change_pct": 0.0,
        "revenue_delta": 0.0, "margin_delta": 0.0,
        "priority": 0.0,
        "rationale": (
            f"Margin {margin_pct:.1%} against a {target:.0%} target, priced {position}, "
            f"last changed {stale_days:.0f} days ago. Nothing here needs a decision."
        ),
    }


def recommend_book(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every product, ranked by what the recommendation is worth."""
    out = [recommend(row) for row in rows]
    return sorted(out, key=lambda r: -safe_float(r.get("priority"), 0.0))


def summarise(recommendations: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per action: how many, and what it is worth."""
    buckets: dict[str, dict[str, Any]] = {}
    for row in recommendations:
        action = row.get("action", "Maintain")
        entry = buckets.setdefault(
            action, {"action": action, "products": 0, "volume_units": 0.0,
                     "revenue_delta": 0.0, "margin_delta": 0.0})
        entry["products"] += 1
        entry["volume_units"] += safe_float(row.get("volume_units"), 0.0)
        entry["revenue_delta"] += safe_float(row.get("revenue_delta"), 0.0)
        entry["margin_delta"] += safe_float(row.get("margin_delta"), 0.0)
    order = {name: i for i, name in enumerate(ACTIONS)}
    for entry in buckets.values():
        entry["sort_order"] = order.get(entry["action"], len(ACTIONS))
    return sorted(buckets.values(), key=lambda r: order.get(r["action"], 99))


def _items(count: int, adjective: str = "") -> str:
    """
    "1 item" and "2 items", because a generated paragraph that says "1 small
    items lose money" tells the reader it was generated, and then they discount
    everything else in it.
    """
    word = "item" if int(count) == 1 else "items"
    return f"{int(count):,} {adjective} {word}".replace("  ", " ")


def executive_note(recommendations: Sequence[Mapping[str, Any]]) -> str:
    """
    The paragraph that goes at the top of the pack.

    Generated from the recommendations rather than written, so it cannot drift
    from the table underneath it -- which is the usual failure of an executive
    summary, and the reason nobody trusts the ones that are typed.
    """
    if not recommendations:
        return "No products met the criteria for a recommendation."
    summary = {row["action"]: row for row in summarise(recommendations)}
    increases = summary.get("Increase", {})
    discounts = summary.get("Discount", {})
    fixes = summary.get("Fix cost", {})
    exits = summary.get("Discontinue", {})
    total_margin = sum(safe_float(r.get("margin_delta"), 0.0) for r in recommendations)

    parts = [
        f"{len(recommendations):,} products reviewed. The recommended actions are worth "
        f"{_money(total_margin)} of annual gross margin."
    ]
    if increases:
        parts.append(
            f"{_items(increases['products'])} "
            f"{'carries' if increases['products'] == 1 else 'carry'} a price "
            "increase worth "
            f"{_money(increases['margin_delta'])}, concentrated where the item is "
            "under the market or has gone unmanaged while its input cost moved."
        )
    if discounts:
        parts.append(
            f"{_items(discounts['products'])} "
            f"{'is' if discounts['products'] == 1 else 'are'} priced far enough "
            "above the market, "
            "on demand elastic enough, that coming back toward it buys more volume "
            f"than it costs -- {_money(discounts['margin_delta'])}."
        )
    if fixes:
        parts.append(
            f"{_items(fixes['products'])} cannot be priced at all until costing "
            "maintains a standard cost for them."
        )
    if exits:
        parts.append(
            f"{_items(exits['products'], 'small')} "
            f"{'loses' if exits['products'] == 1 else 'lose'} money at any price "
            "the market will bear and should be exited unless "
            f"{'it holds' if exits['products'] == 1 else 'they hold'} a listing."
        )
    return " ".join(parts)
