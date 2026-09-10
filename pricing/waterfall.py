"""
The price waterfall: list price down to pocket margin.

The headline discount is never the whole discount. A customer quoted "8% off
list" also takes a 2% volume rebate settled quarterly, a 1.5% early-payment
term, freight the seller absorbs, and a returns allowance -- none of which
appear on the invoice, and none of which the salesperson quoting the 8% is
usually looking at. The gap between the invoice price and what the seller
actually keeps is *revenue leakage*, and on this kind of business it routinely
runs 12-20% of list.

The waterfall is the standard way to see it::

    list price
      - on-invoice discounts      -> invoice price   (what the invoice says)
      - off-invoice deductions    -> net price       (what accounting sees)
      - cost to serve             -> pocket price    (what we actually keep)
      - final cost                -> pocket margin   (what we actually earn)

Two things this module is deliberate about.

**Discounts are additive on list, not compounding.** 10% then 5% is 15% off
list here, not 14.5%. Both conventions exist in the wild; quoting systems
almost always use the additive one because it is what a salesperson means by
"another five points", and mixing the two silently moves every price by a
fraction of a percent. The convention is stated here once and pinned by a test.

**Cost to serve belongs in the waterfall.** Freight-out, order handling and
credit notes are real reductions in what a customer is worth, and leaving them
out is how a "profitable" small-drop account turns out to be losing money on
every delivery. They are kept in their own bucket, though, because one is a
pricing decision and the other is a logistics one.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

from costing.formulas import safe_float

# The deduction buckets, in the order they appear on the waterfall. The
# grouping matters: on-invoice is what the customer sees, off-invoice is what
# they get later and forget to mention, cost-to-serve is what it costs us to
# hand it over.
ON_INVOICE = ("volume_discount", "contract_discount", "promo_discount")
OFF_INVOICE = ("rebate", "coop_marketing", "payment_terms_discount", "freight_allowance")
COST_TO_SERVE = ("freight_out", "order_handling", "returns_credits")

ALL_DEDUCTIONS = ON_INVOICE + OFF_INVOICE + COST_TO_SERVE

# Human labels for the waterfall chart, keyed the same way.
DEDUCTION_LABELS: dict[str, str] = {
    "volume_discount": "Volume discount",
    "contract_discount": "Contract discount",
    "promo_discount": "Promotional discount",
    "rebate": "Rebate accrual",
    "coop_marketing": "Co-op marketing",
    "payment_terms_discount": "Payment terms",
    "freight_allowance": "Freight allowance",
    "freight_out": "Freight to customer",
    "order_handling": "Order handling",
    "returns_credits": "Returns and credits",
}


def bucket_of(key: str) -> str:
    """Which of the three waterfall bands a deduction belongs to."""
    if key in ON_INVOICE:
        return "On-invoice"
    if key in OFF_INVOICE:
        return "Off-invoice"
    if key in COST_TO_SERVE:
        return "Cost to serve"
    return "Other"


def _sum_deductions(deductions: Mapping[str, Any] | None, keys: Sequence[str]) -> float:
    """Total the named deductions, treating anything missing or junk as zero."""
    if not deductions:
        return 0.0
    return sum(safe_float(deductions.get(key), 0.0) for key in keys)


def invoice_price(list_price: float, deductions: Mapping[str, Any] | None) -> float:
    """
    List less the discounts that print on the invoice.

    Clamped at zero: a stack of discounts adding past 100% is a data error, and
    a negative price would propagate into every margin downstream as a
    plausible-looking positive number once it met a negative cost.
    """
    return max(0.0, safe_float(list_price) - _sum_deductions(deductions, ON_INVOICE))


def net_price(list_price: float, deductions: Mapping[str, Any] | None) -> float:
    """Invoice price less the deductions that settle after the invoice."""
    return max(
        0.0,
        invoice_price(list_price, deductions) - _sum_deductions(deductions, OFF_INVOICE),
    )


def pocket_price(list_price: float, deductions: Mapping[str, Any] | None) -> float:
    """Net price less what it costs to get the product to the customer."""
    return max(0.0, net_price(list_price, deductions) - _sum_deductions(deductions, COST_TO_SERVE))


def pocket_margin(
    list_price: float, deductions: Mapping[str, Any] | None, final_cost: float
) -> float:
    """Pocket price less the cost of goods, per unit."""
    return pocket_price(list_price, deductions) - safe_float(final_cost)


def pocket_margin_percent(
    list_price: float, deductions: Mapping[str, Any] | None, final_cost: float
) -> float:
    """
    Pocket margin as a fraction of *pocket price*.

    Expressed on pocket rather than on list, because a margin quoted on list is
    the number that lets a deal look healthy while losing money: at 20% leakage
    a 15%-on-list margin is under 6% on what we actually keep.
    """
    pocket = pocket_price(list_price, deductions)
    if pocket <= 0:
        return 0.0
    return (pocket - safe_float(final_cost)) / pocket


def leakage(list_price: float, deductions: Mapping[str, Any] | None) -> float:
    """Total distance from list to pocket, in currency."""
    return safe_float(list_price) - pocket_price(list_price, deductions)


def leakage_percent(list_price: float, deductions: Mapping[str, Any] | None) -> float:
    """Leakage as a fraction of list. This is the headline number."""
    price = safe_float(list_price)
    if price <= 0:
        return 0.0
    return leakage(price, deductions) / price


def build_waterfall(
    *,
    list_price: float,
    final_cost: float = 0.0,
    deductions: Mapping[str, Any] | None = None,
    quantity: float = 1.0,
) -> dict[str, float]:
    """
    Every level of one item's waterfall, per unit and extended by quantity.

    Returns the four price levels, the three deduction subtotals, the margin at
    invoice and at pocket, and the leakage -- because when a deal looks thin the
    question is always *which* deduction is eating it.
    """
    price = safe_float(list_price)
    cost = safe_float(final_cost)
    qty = safe_float(quantity, 1.0)

    on_inv = _sum_deductions(deductions, ON_INVOICE)
    off_inv = _sum_deductions(deductions, OFF_INVOICE)
    cts = _sum_deductions(deductions, COST_TO_SERVE)

    inv = max(0.0, price - on_inv)
    net = max(0.0, inv - off_inv)
    pocket = max(0.0, net - cts)

    return {
        "list_price": price,
        "on_invoice_discounts": on_inv,
        "invoice_price": inv,
        "off_invoice_deductions": off_inv,
        "net_price": net,
        "cost_to_serve": cts,
        "pocket_price": pocket,
        "final_cost": cost,
        "invoice_margin": inv - cost,
        "invoice_margin_pct": ((inv - cost) / inv) if inv > 0 else 0.0,
        "pocket_margin": pocket - cost,
        "pocket_margin_pct": ((pocket - cost) / pocket) if pocket > 0 else 0.0,
        "leakage": price - pocket,
        "leakage_pct": ((price - pocket) / price) if price > 0 else 0.0,
        "quantity": qty,
        "extended_list": price * qty,
        "extended_pocket": pocket * qty,
        "extended_margin": (pocket - cost) * qty,
    }


def add_deltas(steps: Sequence[MutableMapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Give each bar the amount it *moves* the running total, not the amount it is.

    Waterfall charts -- Power BI's and everyone else's -- plot each category as
    a step from the running total. A subtotal bar carrying its absolute value
    is therefore added on top of the total it summarises, and the chart runs to
    roughly twice the closing figure. Nothing errors: the bars are drawn, the
    axis rescales, and the picture is wrong in a way that looks deliberate.

    So each row also carries `delta`, which is the change in `running` and is
    zero on a subtotal. Charts plot `delta`; tables and cards read `amount`,
    which stays the number a reader would recognise.
    """
    out: list[dict[str, Any]] = []
    previous = 0.0
    for step in steps:
        running = safe_float(step.get("running"))
        out.append({**step, "delta": running - previous})
        previous = running
    return out


def waterfall_steps(
    *,
    list_price: float,
    final_cost: float = 0.0,
    deductions: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    The waterfall as bars: a starting total, one negative step per deduction
    that is actually non-zero, and the pocket price and margin as subtotals.

    Zero deductions are dropped rather than drawn flat -- a waterfall with nine
    invisible bars in it is unreadable, and a deduction a customer does not
    take is not information.
    """
    price = safe_float(list_price)
    steps: list[dict[str, Any]] = [
        {"label": "List price", "kind": "total", "amount": price,
         "running": price, "bucket": "List"}
    ]
    running = price
    for key in ALL_DEDUCTIONS:
        amount = safe_float((deductions or {}).get(key), 0.0)
        if amount == 0:
            continue
        running -= amount
        steps.append(
            {
                "label": DEDUCTION_LABELS[key],
                "kind": "decrease",
                "amount": -amount,
                "running": running,
                "bucket": bucket_of(key),
            }
        )
    pocket = max(0.0, running)
    steps.append({"label": "Pocket price", "kind": "total", "amount": pocket,
                  "running": pocket, "bucket": "Pocket"})
    cost = safe_float(final_cost)
    if cost:
        steps.append(
            {"label": "Cost of goods", "kind": "decrease", "amount": -cost,
             "running": pocket - cost, "bucket": "Cost"}
        )
        steps.append(
            {"label": "Pocket margin", "kind": "total", "amount": pocket - cost,
             "running": pocket - cost, "bucket": "Margin"}
        )
    return add_deltas(steps)


def aggregate_waterfall(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """
    Roll a set of transaction lines into one waterfall, weighted by volume.

    Each row supplies ``list_price``, ``final_cost``, ``quantity`` and the
    deduction keys as *per-unit* amounts. Everything is extended by quantity
    before summing and divided back out at the end, so the result is a genuine
    volume-weighted average price rather than an average of averages -- those
    differ whenever the big customers get the big discounts, which is always.
    """
    total_qty = sum(safe_float(r.get("quantity"), 0.0) for r in rows)
    if total_qty <= 0:
        return build_waterfall(list_price=0.0)

    ext_list = ext_cost = 0.0
    ext: dict[str, float] = dict.fromkeys(ALL_DEDUCTIONS, 0.0)
    for row in rows:
        qty = safe_float(row.get("quantity"), 0.0)
        if qty <= 0:
            continue
        ext_list += safe_float(row.get("list_price")) * qty
        ext_cost += safe_float(row.get("final_cost")) * qty
        for key in ALL_DEDUCTIONS:
            ext[key] += safe_float(row.get(key), 0.0) * qty

    result = build_waterfall(
        list_price=ext_list / total_qty,
        final_cost=ext_cost / total_qty,
        deductions={k: v / total_qty for k, v in ext.items()},
        quantity=total_qty,
    )
    result["lines"] = float(len(rows))
    return result


def leakage_by_bucket(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Which deduction is costing the most, ranked, in extended dollars.

    The ranking is what makes the waterfall actionable: a 0.4%-of-list rebate
    running across the whole book is worth more than a 6% discount on one
    account, and only one of those two gets argued about in meetings.
    """
    total_list = sum(
        safe_float(r.get("list_price")) * safe_float(r.get("quantity"), 0.0) for r in rows
    )
    out = []
    for key in ALL_DEDUCTIONS:
        amount = sum(
            safe_float(r.get(key), 0.0) * safe_float(r.get("quantity"), 0.0) for r in rows
        )
        if amount == 0:
            continue
        out.append(
            {
                "deduction": DEDUCTION_LABELS[key],
                "key": key,
                "bucket": bucket_of(key),
                "amount": amount,
                "pct_of_list": (amount / total_list) if total_list > 0 else 0.0,
            }
        )
    return sorted(out, key=lambda r: r["amount"], reverse=True)
