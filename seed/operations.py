"""
The operational tables: sales team, cost structure, promotions and price changes.

Split out of :mod:`seed.generate_market` because these are a different kind of
data. The market generator models *demand* -- what the index did, what customers
bought, what competitors charged. These four model the **business's own
operations**: who sold it, what it cost to make in elements somebody owns, which
promotions ran, and who signed off each price change.

That distinction is not filing. Three of the four questions a pricing analyst is
actually asked cannot be answered without them:

* "Which reps are giving away margin?" needs a salesperson on the invoice line.
  Two reps in the same segment at the same volume can sit four margin points
  apart, and no product- or customer-level cut of the data will show it.
* "What is our break-even?" needs cost split by **behaviour**, not by absorption.
  A break-even computed on fully absorbed cost is wrong by the whole fixed pool.
* "Did that promotion pay?" needs the baseline it displaced, not the volume it
  sold.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from seed.catalogue import (
    COST_ELEMENTS,
    PRICE_CHANGE_REASONS,
    SALESPEOPLE,
    pick,
)

# Variable warehouse cost per unit shipped, and the fixed pools per month.
# The old model absorbed everything at a rate per unit, which is fine for
# costing a unit and useless for break-even: it makes fixed cost look like it
# disappears when volume falls.
VARIABLE_OVERHEAD_UNIT = 0.145
PACKAGING_UNIT = 0.088
FIXED_POOLS: tuple[tuple[str, str, float], ...] = (
    ("DC facility and equipment", "Fixed warehouse overhead", 118_000.0),
    ("Inventory carrying and insurance", "Fixed warehouse overhead", 52_000.0),
    ("Technology and marketplace fees", "Fixed warehouse overhead", 34_000.0),
    ("Selling and admin", "Selling and admin", 142_000.0),
)


def build_salespeople(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for i, (name, region, tenure, appetite) in enumerate(SALESPEOPLE):
        rows.append(
            {
                "salesperson_id": f"SP{100 + i}",
                "salesperson": name,
                "home_region": region,
                "tenure_years": tenure,
                # How much more (or less) than the tier entitlement this rep
                # tends to grant. Below 1.0 is a rep who holds price.
                "discount_appetite": appetite,
                "quota_units": round(float(rng.uniform(0.9, 1.35)) * 120_000, 0),
            }
        )
    return pd.DataFrame(rows)


def assign_salespeople(
    customers: pd.DataFrame, salespeople: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    Give every customer an owner, preferring a rep who covers its region.

    Not strictly by region: about one account in eight is handled by someone
    from another patch, which is both true of real books and the reason a
    salesperson analysis cannot just be a region analysis with different labels.
    """
    assigned = customers.copy()
    owners = []
    for customer in customers.itertuples():
        local = salespeople[salespeople["home_region"] == customer.region]
        pool = local if len(local) and rng.random() > 0.13 else salespeople
        owners.append(pool["salesperson_id"].iloc[int(rng.integers(len(pool)))])
    assigned["salesperson_id"] = owners
    return assigned


def build_cost_elements() -> pd.DataFrame:
    """The cost element dimension: what it is, how it behaves, who owns it."""
    return pd.DataFrame(
        [{"cost_element": name, "behaviour": behaviour, "owner": owner,
          "sort_order": i}
         for i, (name, behaviour, owner) in enumerate(COST_ELEMENTS)]
    )


def build_cost_element_facts(
    panel: pd.DataFrame, sales: pd.DataFrame, products: pd.DataFrame
) -> pd.DataFrame:
    """
    Cost split into elements at product x month, standard and actual.

    Built from the panel rather than drawn independently, so the elements sum
    back to the cost the rest of the model already uses. A cost breakdown that
    does not reconcile to the cost stack is worse than no breakdown: it gives
    every meeting two totals to argue about.
    """
    volumes = (
        sales.groupby(["product_id", "month"], as_index=False)["quantity_units"].sum()
        .rename(columns={"quantity_units": "volume_units"})
    )
    joined = panel.merge(volumes, on=["product_id", "month"], how="inner")
    joined = joined.merge(
        products[["product_id", "standard_sellable_rate",
                  "handling_cost_unit", "labelling_cost_unit"]],
        on="product_id", how="left",
    )
    joined = joined[joined["volume_units"] > 0]

    def rows_for(actual: bool) -> pd.DataFrame:
        prefix = "actual" if actual else "standard"
        input_cost = joined[f"{prefix}_input_cost_unit"]
        # A unit shipped needs 1/sellable-rate units received; the difference
        # between what the goods cost at a perfect rate and what they cost at
        # the real one is shrink and damage. It is its own element because it
        # is an operations result, not a buying one -- and the two get
        # different meetings.
        grossed = input_cost / joined["standard_sellable_rate"]
        frame = pd.DataFrame(
            {
                "product_id": joined["product_id"],
                "month": joined["month"],
                "volume_units": joined["volume_units"],
                "Goods": input_cost,
                "Shrink and damage": grossed - input_cost,
                "Inbound freight and duty": joined["freight_in_unit"],
                "Pick, pack and handling": joined["handling_cost_unit"],
                "Packaging and labelling": joined["labelling_cost_unit"] + PACKAGING_UNIT,
                "Variable warehouse overhead": VARIABLE_OVERHEAD_UNIT,
            }
        )
        long = frame.melt(
            id_vars=["product_id", "month", "volume_units"],
            var_name="cost_element", value_name="cost_per_unit",
        )
        long[f"{prefix}_cost_per_unit"] = long["cost_per_unit"]
        return long.drop(columns=["cost_per_unit"])

    standard = rows_for(actual=False)
    actual = rows_for(actual=True)
    merged = standard.merge(
        actual[["product_id", "month", "cost_element", "actual_cost_per_unit"]],
        on=["product_id", "month", "cost_element"], how="inner",
    )
    merged["standard_cost"] = (merged["standard_cost_per_unit"] * merged["volume_units"]).round(2)
    merged["actual_cost"] = (merged["actual_cost_per_unit"] * merged["volume_units"]).round(2)
    merged["variance"] = (merged["actual_cost"] - merged["standard_cost"]).round(2)
    merged["behaviour"] = "Variable"
    for column in ("standard_cost_per_unit", "actual_cost_per_unit"):
        merged[column] = merged[column].round(5)
    return merged


def build_fixed_costs(sales: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    The monthly fixed pools, which are what break-even divides into.

    Fixed does not mean constant. A DC's fixed pool steps when a mezzanine or
    a second shift goes in, and drifts with rent reviews; modelling it as a
    flat number is what makes a break-even chart look like homework rather
    than a warehouse.
    """
    months = sorted(sales["month"].unique())
    rows = []
    for i, month in enumerate(months):
        # A step change: a second distribution centre opened a third of the
        # way through the window, which raises the fixed pool and the
        # break-even volume with it.
        step = 1.0 if i < 14 else 1.11
        for pool, element, base in FIXED_POOLS:
            budgeted = base * step * (1.0 + 0.0022 * i)
            rows.append(
                {
                    "month": month,
                    "cost_pool": pool,
                    "cost_element": element,
                    "behaviour": "Fixed",
                    "budgeted_fixed_cost": round(budgeted, 2),
                    "actual_fixed_cost": round(
                        budgeted * float(rng.normal(1.008, 0.031)), 2),
                }
            )
    frame = pd.DataFrame(rows)
    frame["variance"] = (frame["actual_fixed_cost"] - frame["budgeted_fixed_cost"]).round(2)
    return frame


def build_promotions(
    sales: pd.DataFrame, products: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """
    One row per promoted product-month, with the baseline it displaced.

    The baseline is the whole analysis. Volume during a promotion is not
    incremental volume -- most of it would have sold anyway, some of it is a
    forward buy that empties next month, and the difference between those and
    genuine new demand is the difference between a promotion that paid and one
    that bought its own sales at a discount. Baseline here is the median
    non-promoted month for the same product, which is the estimate a pricing
    analyst can actually defend to the person who ran the promotion.
    """
    promoted = sales[sales["on_promotion"] == 1]
    if promoted.empty:
        return pd.DataFrame()

    baseline = (
        sales[sales["on_promotion"] == 0]
        .groupby(["product_id", "month"], as_index=False)["quantity_units"].sum()
        .groupby("product_id")["quantity_units"].median()
        .rename("baseline_volume_units")
    )

    grouped = (
        promoted.groupby(["product_id", "month"], as_index=False)
        .agg(volume_units=("quantity_units", "sum"),
             pocket_revenue=("pocket_revenue", "sum"),
             cogs=("cogs", "sum"),
             list_value=("list_value", "sum"),
             promo_discount=("promo_discount", "mean"),
             list_price=("list_price", "mean"),
             mechanic=("promo_mechanic", "first"),
             customers=("customer_id", "nunique"))
        .merge(baseline, on="product_id", how="left")
        .merge(products[["product_id", "description", "category", "brand_tier"]],
               on="product_id", how="left")
    )
    grouped["baseline_volume_units"] = grouped["baseline_volume_units"].fillna(
        grouped["volume_units"] * 0.8)

    grouped["promo_id"] = [f"PR{5000 + i}" for i in range(len(grouped))]
    grouped["discount_depth"] = (grouped["promo_discount"] / grouped["list_price"]).round(4)
    grouped["incremental_volume_units"] = (
        grouped["volume_units"] - grouped["baseline_volume_units"]).round(1)
    grouped["unit_margin"] = (
        (grouped["pocket_revenue"] - grouped["cogs"]) / grouped["volume_units"]).round(4)
    grouped["incremental_margin"] = (
        grouped["incremental_volume_units"] * grouped["unit_margin"]).round(2)
    # What the discount cost on the volume that would have sold anyway.
    grouped["discount_on_baseline"] = (
        grouped["baseline_volume_units"] * grouped["promo_discount"]).round(2)
    grouped["net_promo_margin"] = (
        grouped["incremental_margin"] - grouped["discount_on_baseline"]).round(2)
    grouped["promo_roi"] = (
        grouped["net_promo_margin"] / grouped["discount_on_baseline"].replace(0, np.nan)
    ).round(4)
    grouped["verdict"] = np.where(
        grouped["net_promo_margin"] > 0, "Paid for itself", "Bought its own volume")
    return grouped.drop(columns=["promo_discount"])


def build_price_changes(
    panel: pd.DataFrame, products: pd.DataFrame, salespeople: pd.DataFrame,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """
    The price-list change log, with who asked and who signed.

    This is the table that makes "pricing operations" a real part of the
    project rather than a bullet. Every list price move in the panel becomes a
    request with a reason, a requester, an approval state and a turnaround --
    so the app can answer how long approvals take, which reasons get rejected,
    and how much of the book has not been touched in a year.
    """
    panel = panel.sort_values(["product_id", "month"]).copy()
    panel["previous_price"] = panel.groupby("product_id")["list_price_unit"].shift()
    changes = panel[
        panel["previous_price"].notna()
        & ((panel["list_price_unit"] - panel["previous_price"]).abs() > 0.0005)
    ].copy()

    changes = changes.merge(
        products[["product_id", "description", "category", "brand_tier"]],
        on="product_id", how="left",
    )
    changes["change_id"] = [f"PC{20000 + i}" for i in range(len(changes))]
    changes["pct_change"] = (
        changes["list_price_unit"] / changes["previous_price"] - 1).round(5)

    reasons, requesters, states, approvers, turnaround = [], [], [], [], []
    for row in changes.itertuples():
        rise = row.pct_change > 0
        # A rise is a pass-through or a review; a cut is a market or customer
        # request. Getting that the wrong way round produces a log where
        # nobody ever asks for a discount, which nobody would believe.
        reason = pick(
            rng, PRICE_CHANGE_REASONS,
            (0.42, 0.24, 0.14, 0.06, 0.06, 0.08) if rise
            else (0.06, 0.10, 0.26, 0.32, 0.12, 0.14),
        )
        requester = "Pricing" if reason in ("Cost pass-through", "Annual review") else "Sales"
        if requester == "Sales":
            rep = salespeople.iloc[int(rng.integers(len(salespeople)))]
            requesters.append(rep["salesperson"])
        else:
            requesters.append("Pricing team")

        magnitude = abs(row.pct_change)
        if magnitude < 0.02 and requester == "Pricing":
            state, days = "Auto-approved", 0
        else:
            roll = rng.random()
            if roll < 0.07:
                state = "Rejected"
            elif roll < 0.12:
                state = "Pending"
            else:
                state = "Approved"
            days = int(np.clip(rng.lognormal(np.log(3.0), 0.8), 1, 45))
        reasons.append(reason)
        states.append(state)
        approvers.append(
            "None" if state == "Auto-approved"
            else "Commercial director" if magnitude > 0.06 else "Sales manager"
        )
        turnaround.append(days)

    changes["reason"] = reasons
    changes["requested_by"] = requesters
    changes["approval_state"] = states
    changes["approver"] = approvers
    changes["days_to_approve"] = turnaround
    changes["direction"] = np.where(changes["pct_change"] > 0, "Increase", "Decrease")
    return changes[
        ["change_id", "month", "product_id", "description", "category", "brand_tier",
         "previous_price", "list_price_unit", "pct_change", "direction", "reason",
         "requested_by", "approval_state", "approver", "days_to_approve"]
    ].rename(columns={"list_price_unit": "new_price", "month": "effective_month"})


def build_cost_element_dim() -> pd.DataFrame:
    return build_cost_elements()


__all__ = [
    "FIXED_POOLS",
    "PACKAGING_UNIT",
    "VARIABLE_OVERHEAD_UNIT",
    "assign_salespeople",
    "build_cost_element_facts",
    "build_cost_elements",
    "build_fixed_costs",
    "build_price_changes",
    "build_promotions",
    "build_salespeople",
]
