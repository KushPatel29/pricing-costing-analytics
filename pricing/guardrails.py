"""
Deal guardrails: the floor, the target, and who has to sign for the gap.

Everything above this line in the package is analysis. This module is the part
that has to survive contact with a salesperson at 4:50pm on a Friday, which
means it has to answer three questions in one screen: what is the least I can
charge, what should I be charging, and who do I need if I want to go lower.

**The floor is on pocket price, not list.** A floor expressed as "no more than
15% off list" is not a floor at all -- it says nothing about rebates, freight
or terms, which is where the margin actually goes. :func:`price_band` takes the
same deduction dict the waterfall does and returns a list price whose *pocket*
realisation clears the floor margin.

**Approval tiers are on the gap to target, not on the discount.** Two deals at
"10% off" are not the same deal if one product carries a 38% margin and the
other 19%. Tiering on the margin gap puts the escalation where the money is.

**Exceptions are ranked by dollars, not by severity.** A scan that reports
1,400 breaches sorted by how far below floor they are gets the analyst reading
about a $40 account for the first twenty minutes. :func:`scan_exceptions` ranks
by margin at risk, and every rule carries a plain sentence naming what to do.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from costing.formulas import calculate_price_from_margin, safe_float
from pricing.waterfall import ALL_DEDUCTIONS, pocket_price

# Escalation ladder, in margin points below target. First match wins, so the
# order matters and the last entry is the catch-all.
APPROVAL_TIERS: tuple[tuple[float, str], ...] = (
    (0.02, "Rep"),
    (0.05, "Sales manager"),
    (0.10, "Commercial director"),
    (float("inf"), "VP Finance"),
)

# Default margin band. Floor is where the deal stops being worth doing; target
# is the plan; stretch is what a strong account should be paying.
DEFAULT_FLOOR_MARGIN = 0.12
DEFAULT_TARGET_MARGIN = 0.22
DEFAULT_STRETCH_MARGIN = 0.30

# Margin comparisons are made to this tolerance. See score_deal.
MARGIN_TOLERANCE = 1e-9


def price_band(
    final_cost: float,
    *,
    floor_margin: float = DEFAULT_FLOOR_MARGIN,
    target_margin: float = DEFAULT_TARGET_MARGIN,
    stretch_margin: float = DEFAULT_STRETCH_MARGIN,
    deductions: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """
    Floor, target and stretch list prices for one item.

    When ``deductions`` are supplied the returned prices are *grossed up* so the
    pocket price after every deduction still realises the stated margin. Quoting
    a floor that ignores the deductions this customer always takes is how a deal
    clears the guardrail on the screen and misses it in the ledger.
    """
    cost = safe_float(final_cost)
    # Per-unit deductions are absolute, so grossing up is an addition, not a
    # division: the rebate is $0.14/unit whatever the list price ends up being.
    uplift = sum(safe_float((deductions or {}).get(k), 0.0) for k in ALL_DEDUCTIONS)
    return {
        "final_cost": cost,
        "deduction_uplift": uplift,
        "floor_price": calculate_price_from_margin(cost, floor_margin) + uplift,
        "target_price": calculate_price_from_margin(cost, target_margin) + uplift,
        "stretch_price": calculate_price_from_margin(cost, stretch_margin) + uplift,
        "floor_margin": safe_float(floor_margin),
        "target_margin": safe_float(target_margin),
        "stretch_margin": safe_float(stretch_margin),
    }


def approval_tier(
    realised_margin: float,
    target_margin: float = DEFAULT_TARGET_MARGIN,
    tiers: Sequence[tuple[float, str]] = APPROVAL_TIERS,
) -> dict[str, Any]:
    """
    Who signs, given how far under target the deal lands.

    At or above target nobody signs. The gap is in margin *points*, so a deal
    at 19% against a 22% target is three points down and needs a manager, not a
    director -- and the same three points on a different product needs the same
    manager, which is the point of tiering this way.
    """
    gap = safe_float(target_margin) - safe_float(realised_margin)
    if gap <= 0:
        return {"gap": gap, "approver": "None", "escalated": False}
    for threshold, approver in tiers:
        if gap <= threshold:
            return {"gap": gap, "approver": approver, "escalated": True}
    return {"gap": gap, "approver": tiers[-1][1], "escalated": True}


def score_deal(
    *,
    list_price: float,
    final_cost: float,
    deductions: Mapping[str, Any] | None = None,
    floor_margin: float = DEFAULT_FLOOR_MARGIN,
    target_margin: float = DEFAULT_TARGET_MARGIN,
    stretch_margin: float = DEFAULT_STRETCH_MARGIN,
    quantity: float = 1.0,
) -> dict[str, Any]:
    """
    Everything needed to accept, escalate or refuse one quote.

    Returns the realised pocket margin, where it sits against the band, who has
    to approve it, and the margin the deal is short of target -- extended by
    quantity, because that is the number the approver is actually being asked
    to give up.
    """
    band = price_band(
        final_cost,
        floor_margin=floor_margin,
        target_margin=target_margin,
        stretch_margin=stretch_margin,
        deductions=deductions,
    )
    pocket = pocket_price(list_price, deductions)
    cost = safe_float(final_cost)
    margin = pocket - cost
    margin_pct = (margin / pocket) if pocket > 0 else 0.0
    qty = safe_float(quantity, 1.0)

    # Compared with a tolerance, not exactly. `price_band` and `score_deal` are
    # meant to compose -- quote at the floor price and the deal should clear the
    # floor -- and a price computed as cost/(1-m) then grossed up and divided
    # back down lands a few parts in 10^16 under it. Without the tolerance a
    # deal priced exactly at the floor is reported as breaching it.
    if margin_pct >= stretch_margin - MARGIN_TOLERANCE:
        verdict = "Above stretch"
    elif margin_pct >= target_margin - MARGIN_TOLERANCE:
        verdict = "At target"
    elif margin_pct >= floor_margin - MARGIN_TOLERANCE:
        verdict = "Below target"
    elif margin > 0:
        verdict = "Below floor"
    else:
        verdict = "Loss-making"

    approval = approval_tier(margin_pct, target_margin)
    return {
        **band,
        "list_price": safe_float(list_price),
        "pocket_price": pocket,
        "pocket_margin": margin,
        "pocket_margin_pct": margin_pct,
        "verdict": verdict,
        "within_guardrail": margin_pct >= floor_margin - MARGIN_TOLERANCE,
        "approver": approval["approver"],
        "margin_gap_pct": approval["gap"],
        "margin_gap_dollars": max(0.0, approval["gap"]) * pocket * qty,
        "quantity": qty,
        "extended_margin": margin * qty,
    }


# --------------------------------------------------------------------------
# Book-wide exception scanning
# --------------------------------------------------------------------------

# Each rule is (code, severity, predicate, message). Severity orders the ties;
# the primary sort is always money.
#
# Severity also drives the red/amber/green alert on the exception report, via
# ALERT_LEVELS below. The split is by *when* the money moves, not by how large
# it is: red is margin leaving on today's invoices, amber is a price that is off
# its position and will cost volume or margin at the next order, green is a
# leading indicator with nothing lost yet. Ranking by dollars inside a colour is
# what makes the list workable; colouring by dollars would put a large stale
# listing above a small loss-making one, which is the wrong morning.
ALERT_LEVELS: dict[int, str] = {
    1: "Red", 2: "Red",
    3: "Amber", 4: "Amber", 5: "Amber",
    6: "Green", 7: "Green",
}
ALERT_ORDER: tuple[str, ...] = ("Red", "Amber", "Green")
# A rank, because a chart axis sorts text alphabetically and "Amber, Green,
# Red" is not a traffic light.
ALERT_RANK: dict[str, int] = {name: i for i, name in enumerate(ALERT_ORDER)}


def alert_level(severity: int) -> str:
    """Red/amber/green for a rule severity. Anything unmapped is amber."""
    return ALERT_LEVELS.get(int(severity), "Amber")


def _rules() -> tuple[tuple[str, int, Any, str], ...]:
    return (
        (
            "LOSS", 1,
            lambda r: safe_float(r.get("pocket_margin_pct")) <= 0,
            "Selling below cost once every deduction is counted. Stop quoting this "
            "combination until the cost or the deductions change.",
        ),
        (
            "BELOW_FLOOR", 2,
            lambda r: 0 < safe_float(r.get("pocket_margin_pct")) < safe_float(
                r.get("floor_margin"), DEFAULT_FLOOR_MARGIN),
            "Pocket margin is under the floor. Reprice, or get the exception signed.",
        ),
        (
            "LEAKAGE", 3,
            lambda r: safe_float(r.get("leakage_pct")) > 0.25,
            "More than a quarter of list never reaches us. The off-invoice "
            "deductions are where to look first.",
        ),
        (
            "UNDER_MARKET", 4,
            lambda r: 0 < safe_float(r.get("price_index"), 0.0) < 92.0,
            "Priced well under the market with margin to spare. A price rise here "
            "is the cheapest margin in the book.",
        ),
        (
            "OVER_MARKET", 5,
            lambda r: safe_float(r.get("price_index"), 0.0) > 118.0,
            "Priced well over the market. Volume is exposed unless the premium is "
            "one the customer can name.",
        ),
        (
            "STALE", 6,
            lambda r: safe_float(r.get("days_since_price_change"), 0.0) > 365,
            "No price change in over a year while input costs moved. Review.",
        ),
        (
            "COST_SPIKE", 7,
            lambda r: safe_float(r.get("cost_change_pct"), 0.0) > 0.10,
            "Input cost up more than ten points since the price was last set.",
        ),
    )


def scan_exceptions(
    rows: Sequence[Mapping[str, Any]],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """
    Run every guardrail rule over a book of priced rows.

    A row can breach several rules and appears once per breach, because the
    fixes differ -- a loss-making line that is also under-priced against market
    needs both conversations. Ranked by margin at risk so the top of the list is
    worth the analyst's morning.
    """
    findings: list[dict[str, Any]] = []
    for row in rows:
        at_risk = abs(safe_float(row.get("margin_gap_dollars"), 0.0)) or abs(
            safe_float(row.get("extended_margin"), 0.0)
        )
        for code, severity, predicate, message in _rules():
            try:
                hit = bool(predicate(row))
            except (TypeError, ValueError):
                hit = False
            if not hit:
                continue
            findings.append(
                {
                    "code": code,
                    "severity": severity,
                    "alert": alert_level(severity),
                    "alert_rank": ALERT_RANK[alert_level(severity)],
                    "product_id": row.get("product_id"),
                    "customer_id": row.get("customer_id"),
                    "description": row.get("description"),
                    "pocket_margin_pct": safe_float(row.get("pocket_margin_pct")),
                    "price_index": safe_float(row.get("price_index"), float("nan")),
                    "margin_at_risk": at_risk,
                    "action": message,
                }
            )
    findings.sort(key=lambda f: (-f["margin_at_risk"], f["severity"]))
    return findings[:limit] if limit else findings


def exception_summary(findings: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per rule: how many breaches and how much money, ranked by money."""
    buckets: dict[str, dict[str, Any]] = {}
    for finding in findings:
        code = finding.get("code")
        entry = buckets.setdefault(
            code, {"code": code, "count": 0, "margin_at_risk": 0.0,
                   "severity": finding.get("severity", 9),
                   "alert": finding.get("alert", alert_level(finding.get("severity", 9))),
                   "alert_rank": ALERT_RANK[finding.get(
                       "alert", alert_level(finding.get("severity", 9)))],
                   "action": finding.get("action", "")}
        )
        entry["count"] += 1
        entry["margin_at_risk"] += safe_float(finding.get("margin_at_risk"), 0.0)
    return sorted(buckets.values(), key=lambda r: -r["margin_at_risk"])
