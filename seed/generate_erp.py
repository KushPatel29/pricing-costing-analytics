"""
Write an ERP-shaped extract into ``raw/``, defects included.

Everything else in this repo starts from clean, internally consistent tables.
That is the wrong starting point for a data-quality exercise, and data quality
is most of the first fortnight of any pricing job: you are handed an extract,
and before a single price can be recommended somebody has to establish which
rows are usable.

So this writes the extract a pricing analyst would actually be sent -- billing
items, material master, customer master and condition records, in the shape and
vocabulary an ERP uses -- and injects the defects that extract really contains,
at rates a real one really has. ``engine/stage_erp.py`` then cleanses it and
reconciles back, and ``pricing/quality.py`` reports what it found.

The naming follows SAP SD/MM, because that is the vocabulary the job posting
means by "ERP data familiarity": a **billing document** with **items**, a
**sold-to party**, a **material** with a **base unit of measure**, and
**condition records** holding the price. An Oracle or NetSuite extract differs
in spelling and not in shape.

Scope is the **most recent fiscal year only**. Nobody re-validates three years
of history; you validate the extract you were handed.

Usage::

    python -m seed.generate_erp
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from seed.catalogue import DEFAULT_SEED, fiscal_year

RAW_DIR = Path("raw")
DATA_DIR = Path("data")

# The extract was pulled on the first working day after the window closed,
# which is why a posting dated after it is a defect rather than just late.
EXTRACT_DATE = date(2026, 7, 2)

# Defect rates. Low enough to be realistic, high enough that every rule in
# pricing/quality.py has something to find -- a check that can never fire is a
# check nobody maintains, and a demo that shows twelve green lights proves
# nothing about the checks.
DEFECT_RATES = {
    "missing_standard_cost": 0.021,
    "missing_condition_record": 0.017,
    "duplicate_line": 0.0032,
    "bad_quantity": 0.0021,
    "uom_mismatch": 0.0028,
    "value_mismatch": 0.0024,
    "orphan_customer": 0.0018,
    "bad_recovery": 0.017,
    "future_posting": 0.0009,
    "missing_district": 0.013,
}

DOCUMENT_TYPES = ("Invoice", "Invoice", "Invoice", "Invoice", "Credit memo")
SALES_ORGS = ("1000",)
DISTRIBUTION_CHANNELS = {"Foodservice": "10", "Retail": "20", "Wholesale": "30"}
PLANTS = ("PL01", "PL02")


def _month_dates(month: date, rng: np.random.Generator, n: int) -> list[date]:
    """Spread a month's lines over plausible posting days."""
    days = rng.integers(1, 29, size=n)
    return [date(month.year, month.month, int(d)) for d in days]


def build_material_master(products: pd.DataFrame, panel: pd.DataFrame,
                          rng: np.random.Generator) -> pd.DataFrame:
    latest = panel[panel["month"] == panel["month"].max()].set_index("product_id")
    rows = []
    for product in products.itertuples():
        state = latest.loc[product.product_id]
        rows.append(
            {
                "material": product.product_id,
                "material_description": product.description,
                "material_group": product.category,
                "base_uom": "EA",
                "units_per_case": round(product.units_per_billing_uom, 3),
                "plant": PLANTS[int(rng.integers(len(PLANTS)))],
                "standard_cost": round(float(state["standard_final_cost_unit"]), 4),
                "moving_average_price": round(float(state["actual_final_cost_unit"]), 4),
                "recovery": round(float(product.standard_sellable_rate), 4),
                "profit_center": f"PC{product.category[:3].upper()}",
                "mrp_controller": f"M{int(rng.integers(1, 6)):02d}",
                "deletion_flag": "",
            }
        )
    master = pd.DataFrame(rows)

    # --- defects -----------------------------------------------------------
    n = len(master)
    blanked = rng.random(n) < DEFECT_RATES["missing_standard_cost"]
    # Two flavours, because both occur and only one is obvious: a null, and a
    # zero somebody typed to get past a required field.
    master.loc[blanked & (rng.random(n) < 0.5), "standard_cost"] = np.nan
    master.loc[blanked & master["standard_cost"].notna(), "standard_cost"] = 0.0

    bad_yield = rng.random(n) < DEFECT_RATES["bad_recovery"]
    # A recovery above 1.0 creates material out of nothing; a decimal in the
    # wrong place makes an item look three times as expensive as it is.
    master.loc[bad_yield & (rng.random(n) < 0.5), "recovery"] = round(
        float(rng.uniform(1.02, 1.35)), 4)
    master.loc[bad_yield & (master["recovery"] <= 1.0), "recovery"] = round(
        float(rng.uniform(0.04, 0.29)), 4)
    return master


def build_customer_master(customers: pd.DataFrame, salespeople: pd.DataFrame,
                          rng: np.random.Generator) -> pd.DataFrame:
    # dim_customer already carries the rep's name; merging it again would give
    # pandas two columns called `salesperson` and suffix both.
    joined = customers if "salesperson" in customers.columns else customers.merge(
        salespeople[["salesperson_id", "salesperson"]], on="salesperson_id", how="left")
    master = pd.DataFrame(
        {
            "customer": joined["customer_id"],
            "customer_name": joined["customer_name"],
            "account_group": joined["segment"],
            "sales_org": SALES_ORGS[0],
            "distribution_channel": joined["channel"].map(DISTRIBUTION_CHANNELS),
            "sales_district": joined["region"],
            "payment_terms": joined["payment_terms"],
            "price_group": joined["tier"],
            "price_list_type": joined["price_list"],
            "sales_employee": joined["salesperson"],
            "created_on": "2019-04-01",
        }
    )
    missing = rng.random(len(master)) < DEFECT_RATES["missing_district"]
    master.loc[missing, "sales_district"] = ""
    return master


def build_condition_records(panel: pd.DataFrame, price_changes: pd.DataFrame,
                            rng: np.random.Generator) -> pd.DataFrame:
    """
    Condition records: where an ERP actually keeps the price.

    ``days_since_change`` comes from the price-change log where there is one,
    so the timeliness rule finds the items whose price genuinely has not been
    touched -- rather than a number invented to make the check fire.
    """
    latest = panel[panel["month"] == panel["month"].max()].copy()
    last_change = (
        price_changes.groupby("product_id")["effective_month"].max()
        .rename("last_change")
    )
    latest = latest.merge(last_change, left_on="product_id", right_index=True, how="left")
    reference = pd.Timestamp(EXTRACT_DATE)
    days = (reference - pd.to_datetime(latest["last_change"])).dt.days
    # An item that never changed price has been on the same condition record
    # since the price list was loaded.
    days = days.fillna(int((reference - pd.Timestamp("2023-06-01")).days))

    records = pd.DataFrame(
        {
            "condition_record": [f"CR{700000 + i}" for i in range(len(latest))],
            "condition_type": "PR00",
            "material": latest["product_id"].to_numpy(),
            "sales_org": SALES_ORGS[0],
            "condition_value": latest["list_price_unit"].round(4).to_numpy(),
            "condition_uom": "EA",
            "valid_from": "2023-07-01",
            "valid_to": "9999-12-31",
            "days_since_change": days.astype(int).to_numpy(),
            "created_by": "PRICING",
        }
    )
    keep = rng.random(len(records)) >= DEFECT_RATES["missing_condition_record"]
    return records[keep].reset_index(drop=True)


def build_billing_items(sales: pd.DataFrame, products: pd.DataFrame,
                        customers: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    The billing extract: one row per invoice line, in ERP columns.

    Only the most recent fiscal year. Nobody re-validates three years of
    history; you validate the extract you were handed.
    """
    sales = sales.copy()
    sales["month"] = pd.to_datetime(sales["month"])
    sales["fiscal_year"] = sales["month"].apply(lambda m: fiscal_year(m.date()))
    recent = sales[sales["fiscal_year"] == sales["fiscal_year"].max()].reset_index(drop=True)

    pack = products.set_index("product_id")["units_per_billing_uom"].to_dict()
    channel = customers.set_index("customer_id")["channel"].to_dict()

    months = recent["month"].dt.date.tolist()
    posting = _month_dates(date(2000, 1, 1), rng, len(recent))
    posting = [date(m.year, m.month, min(d.day, 28)) for m, d in zip(months, posting,
                                                                     strict=False)]

    items = pd.DataFrame(
        {
            "billing_document": [f"90{600000 + i}" for i in range(len(recent))],
            "item_number": [f"{((i % 9) + 1) * 10:03d}" for i in range(len(recent))],
            "document_type": [DOCUMENT_TYPES[int(rng.integers(len(DOCUMENT_TYPES) - 1))]
                              for _ in range(len(recent))],
            "billing_date": [d.isoformat() for d in posting],
            "company_code": "CA10",
            "sales_org": SALES_ORGS[0],
            "distribution_channel": recent["customer_id"].map(channel)
                                   .map(DISTRIBUTION_CHANNELS).fillna("10"),
            "sold_to_party": recent["customer_id"],
            "material": recent["product_id"],
            "plant": [PLANTS[int(rng.integers(len(PLANTS)))] for _ in range(len(recent))],
            "billed_quantity": recent["quantity_units"].round(3),
            "sales_uom": "EA",
            "gross_price": recent["list_price"].round(4),
            "net_price": recent["pocket_price"].round(4),
            "invoice_price": recent["invoice_price"].round(4),
            "discount_value": (recent["on_invoice_discounts"]
                               * recent["quantity_units"]).round(2),
            "rebate_accrual": (recent["off_invoice_deductions"]
                               * recent["quantity_units"]).round(2),
            "freight_value": (recent["cost_to_serve"] * recent["quantity_units"]).round(2),
            "cost_value": recent["cogs"].round(2),
            "extract_date": EXTRACT_DATE.isoformat(),
        }
    )
    items["net_value"] = (items["billed_quantity"] * items["net_price"]).round(2)
    items["pack_size_units"] = items["material"].map(pack).round(3)

    n = len(items)
    # --- defects -----------------------------------------------------------
    bad_qty = rng.random(n) < DEFECT_RATES["bad_quantity"]
    items.loc[bad_qty, "billed_quantity"] = 0.0

    # Billed by the case against a material mastered in eaches. The extended
    # value is right -- cases times price-per-case -- so the line reconciles and
    # nothing looks wrong in total. What is wrong is the per-unit price, which
    # comes out pack-size times too high, and the volume, which comes out
    # pack-size times too low. Uncaught, this item reads as a premium seller
    # nobody buys instead of as a keying error.
    uom = (rng.random(n) < DEFECT_RATES["uom_mismatch"]) & items["pack_size_units"].gt(1.5)
    items.loc[uom, "billed_quantity"] = (
        items.loc[uom, "billed_quantity"] / items.loc[uom, "pack_size_units"]).round(3)
    items.loc[uom, "net_price"] = (
        items.loc[uom, "net_price"] * items.loc[uom, "pack_size_units"]).round(4)
    items.loc[uom, "sales_uom"] = "CS"

    mismatch = rng.random(n) < DEFECT_RATES["value_mismatch"]
    items.loc[mismatch, "net_value"] = (
        items.loc[mismatch, "net_value"] * rng.uniform(1.05, 1.4, int(mismatch.sum()))
    ).round(2)

    orphan = rng.random(n) < DEFECT_RATES["orphan_customer"]
    items.loc[orphan, "sold_to_party"] = [
        f"CU9{int(rng.integers(100, 999))}" for _ in range(int(orphan.sum()))
    ]

    future = rng.random(n) < DEFECT_RATES["future_posting"]
    items.loc[future, "billing_date"] = [
        (EXTRACT_DATE + timedelta(days=int(rng.integers(1, 40)))).isoformat()
        for _ in range(int(future.sum()))
    ]

    # Duplicates last, so a duplicated row carries whatever defects it had.
    duplicated = items[rng.random(n) < DEFECT_RATES["duplicate_line"]]
    return pd.concat([items, duplicated], ignore_index=True)


def generate(*, seed: int = DEFAULT_SEED, data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed ^ 0xE12F)
    read = lambda name: pd.read_csv(  # noqa: E731 - a local alias, not a policy
        data_dir / f"{name}.csv", dtype={"product_id": str, "customer_id": str})

    products = read("dim_product")
    customers = read("dim_customer")
    salespeople = read("dim_salesperson")
    panel = read("fact_price_cost_panel")
    sales = read("fact_sales")
    price_changes = read("fact_price_change")

    return {
        "erp_material_master": build_material_master(products, panel, rng),
        "erp_customer_master": build_customer_master(customers, salespeople, rng),
        "erp_condition_records": build_condition_records(panel, price_changes, rng),
        "erp_billing_items": build_billing_items(sales, products, customers, rng),
    }


def write(tables: dict[str, pd.DataFrame], out_dir: Path = RAW_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        frame.to_csv(out_dir / f"{name}.csv", index=False, lineterminator="\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--out-dir", type=Path, default=RAW_DIR)
    args = ap.parse_args(argv)

    tables = generate(seed=args.seed, data_dir=args.data_dir)
    write(tables, args.out_dir)
    print(f"wrote {len(tables)} ERP extracts to {args.out_dir}/")
    for name, frame in tables.items():
        print(f"  {name:26s} {len(frame):>7,} rows x {len(frame.columns):>2} cols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
