"""
Generate the two workbooks the calculator expects.

The tool takes a **cost sheet** (one row per item, carrying the whole cost
build-up from vendor invoice to selling price) and an **export sheet** (the
ERP's product list, which is what actually gets repriced). Neither is
something a reader would have, so this writes a synthetic pair with the exact
column names ``validate_columns`` requires.

The catalogue comes from :mod:`seed.catalogue` by way of
:func:`seed.generate_market.product_catalogue`, so item code 20017 is the same
item in the same brand tier here, in ``data/dim_product.csv``, and in the Power BI
model. That shared identity is the difference between one application and three
demos in a trench coat: reprice an item in the calculator and it is the item the
waterfall, the price band and the guardrail scan are all talking about.

Cost and price are a snapshot of the **final month** of the same monthly panel
the analytics run on, rather than a separate draw. A cost sheet showing FY2024
prices next to a dashboard showing FY2026 ones is the kind of inconsistency
nobody notices until a reader adds two numbers that should have matched.

Usage:
    python -m seed.generate_sheets
    python -m seed.generate_sheets --items 120 --seed 3
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from costing.formulas import build_cost_stack, get_freight_cost
from seed.catalogue import DEFAULT_ITEMS, DEFAULT_SEED

OUT_DIR = Path("sample_data")
COST_SHEET = "Cost Sheet"
EXPORT_SHEET = "AllProducts"

# The list margin sits this far above the base margin on every item, which is
# what makes list price a book price and base price the one a customer sees.
LIST_MARGIN_UPLIFT = 0.08


def build_cost_sheet(products: pd.DataFrame, panel: pd.DataFrame,
                     rng: np.random.Generator) -> pd.DataFrame:
    """One row per item, at the final month's cost and price."""
    last_month = panel["month"].max()
    snapshot = panel[panel["month"] == last_month].set_index("product_id")

    rows = []
    for product in products.itertuples():
        state = snapshot.loc[product.product_id]
        cost_per_unit = float(state["actual_input_cost_unit"])
        units_per_uom = product.units_per_billing_uom
        invoice_price = cost_per_unit * units_per_uom
        recovery = product.standard_sellable_rate
        base_margin = product.target_margin
        list_margin = base_margin + LIST_MARGIN_UPLIFT
        damage_rate = product.damage_rate
        salvage_value_unit = cost_per_unit * float(rng.uniform(0.15, 0.45))

        stack = build_cost_stack(
            vendor_invoice_price=invoice_price,
            units_per_billing_uom=units_per_uom,
            adj=product.adj_unit,
            vendor=product.inbound_lane,
            recovery=recovery,
            handling_per_unit=product.handling_cost_unit,
            labelling_per_unit=product.labelling_cost_unit,
            base_margin=base_margin,
            list_margin=list_margin,
        )

        raw_material_per_unit = stack["recovery_input"]
        waste_output = raw_material_per_unit - stack["landed_cost"]

        row = {
            "Item Code": product.product_id,
            "Description": product.description,
            "Category": product.category,
            "Brand Tier": product.brand_tier,
            "Pack Format": product.pack_format,
            "Units Per Billling UOM": round(units_per_uom, 3),
            "Supplier Name": product.supplier,
            "Inbound Lane": product.inbound_lane,
            "Vendor Invoice Price": round(invoice_price, 4),
            "Actual Inv Cost(units)": round(stack["actual_inv_cost"], 4),
            "Adj": round(product.adj_unit, 4),
            "Market Cost": round(stack["market_cost"], 4),
            "Freight": round(get_freight_cost(product.inbound_lane), 4),
            "Landed Cost": round(stack["landed_cost"], 4),
            "Sellable %": round(recovery * 100, 2),
            "Sellable Input Cost": round(stack["recovery_input"], 4),
            "Units Received Per Unit Sold": round(1.0 / recovery, 4),
            "Goods Cost Per Unit": round(raw_material_per_unit, 4),
            "Damage %": round(damage_rate * 100, 2),
            "Salvage Value/Unit": round(salvage_value_unit, 4),
            "Salvage Credit": round(salvage_value_unit * damage_rate / recovery, 4),
            "Input Cost": round(raw_material_per_unit, 4),
            "Shrink Cost $": round(waste_output, 4),
            "Net Input Cost": round(raw_material_per_unit, 4),
            "Handling $": round(product.handling_cost_unit, 4),
            "Labelling": round(product.labelling_cost_unit, 4),
            "Goods + Handling": round(raw_material_per_unit + product.handling_cost_unit, 4),
            "New Final Cost (Unit)": round(stack["final_cost"], 4),
            "Column1": "",
            "Billling UOM Cost": round(stack["final_cost"] - product.labelling_cost_unit, 4),
            "Priced Labelling": round(product.labelling_cost_unit, 4),
            "Final Cost": round(stack["final_cost"], 4),
            "Base Margin %": round(base_margin, 4),
            "Margin $": round(stack["base_margin_dollars"], 4),
            "Base Price": round(stack["base_price"], 4),
            "List Price": round(stack["list_price"], 4),
            "List Margin %": round(list_margin, 4),
        }
        # Four raw-material input slots; most items use one or two.
        used = int(rng.integers(1, 4))
        for slot in range(1, 5):
            if slot <= used:
                qty = round(float(rng.uniform(0.2, 1.0)), 3)
                unit = round(cost_per_unit * float(rng.uniform(0.8, 1.2)), 4)
            else:
                qty, unit = 0.0, 0.0
            row[f"Item-{slot}"] = (
                f"{product.sub_category} input {slot}" if slot <= used else ""
            )
            row[f"Qty-{slot}"] = qty
            row[f"Unit $-{slot}"] = unit
            row[f"Total $-{slot}"] = round(qty * unit, 4)
        rows.append(row)
    return pd.DataFrame(rows)


def build_export_sheet(cost_df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    The ERP product list. Deliberately not a perfect mirror of the cost sheet:
    it holds a few codes the cost sheet has never seen, and its prices are
    stale, which is the whole reason the tool exists.
    """
    export = pd.DataFrame(
        {
            "Product Code": cost_df["Item Code"],
            "Description": cost_df["Description"],
            "Cost Price": (cost_df["Final Cost"] * rng.uniform(0.9, 1.05, len(cost_df))).round(4),
            "Base Price": (cost_df["Base Price"] * rng.uniform(0.92, 1.04, len(cost_df))).round(4),
            "Suggested Price": (
                cost_df["List Price"] * rng.uniform(0.95, 1.02, len(cost_df))
            ).round(4),
        }
    )
    orphans = pd.DataFrame(
        {
            "Product Code": [str(90000 + i) for i in range(5)],
            "Description": [f"Discontinued line {i}" for i in range(5)],
            "Cost Price": np.round(rng.uniform(4, 20, 5), 4),
            "Base Price": np.round(rng.uniform(6, 28, 5), 4),
            "Suggested Price": np.round(rng.uniform(7, 32, 5), 4),
        }
    )
    return pd.concat([export, orphans], ignore_index=True)


def generate(*, seed: int = DEFAULT_SEED, items: int = DEFAULT_ITEMS):
    """
    The two sheets, on the shared catalogue at the shared final-month costs.

    Imported inside the function rather than at module scope: the market
    generator imports the cost stack and the catalogue, and importing it up here
    would put a circular import between two modules that only need each other
    one way round.
    """
    from seed.generate_market import (
        build_commodity_index,
        monthly_index,
        price_cost_panel,
        product_catalogue,
    )

    products = product_catalogue(seed=seed, items=items)
    index_m = monthly_index(build_commodity_index(np.random.default_rng(seed)))
    panel = price_cost_panel(products, index_m, seed=seed)

    rng = np.random.default_rng(seed)
    cost_df = build_cost_sheet(products, panel, rng)
    export_df = build_export_sheet(cost_df, rng)
    return cost_df, export_df


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--items", type=int, default=DEFAULT_ITEMS)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    cost_df, export_df = generate(seed=args.seed, items=args.items)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    cost_path = args.out_dir / "cost_sheet.xlsx"
    export_path = args.out_dir / "export_sheet.xlsx"
    with pd.ExcelWriter(cost_path, engine="openpyxl") as writer:
        cost_df.to_excel(writer, sheet_name=COST_SHEET, index=False)
    with pd.ExcelWriter(export_path, engine="openpyxl") as writer:
        export_df.to_excel(writer, sheet_name=EXPORT_SHEET, index=False)

    print(f"wrote {cost_path}   ({len(cost_df)} items x {len(cost_df.columns)} cols, "
          f"sheet '{COST_SHEET}')")
    print(f"wrote {export_path} ({len(export_df)} rows x {len(export_df.columns)} cols, "
          f"sheet '{EXPORT_SHEET}')")
    print()
    print(f"  median final cost : ${cost_df['Final Cost'].median():.2f}/unit")
    print(f"  median base price : ${cost_df['Base Price'].median():.2f}/unit")
    print(f"  median recovery   : {cost_df['Sellable %'].median():.1f}%")
    print(f"  codes not in cost sheet: {len(export_df) - len(cost_df)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
