"""
The generated sample sheets must satisfy the app's own column validation.

Without this the README's "generate the sheets and upload them" instruction
can rot silently: someone adds a required column to the app and the sample
data stops loading, which nobody notices until a reader tries it.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd
import pytest

from costing.formulas import calculate_margin_percent, get_freight_cost
from seed.generate_sheets import generate

# Mirrors the `cost_required` set the Streamlit app validates against.
COST_REQUIRED = {
    "Item Code", "Units Per Billling UOM", "Inbound Lane", "Vendor Invoice Price",
    "Actual Inv Cost(units)", "Adj", "Market Cost", "Freight", "Landed Cost",
    "Sellable %", "Sellable Input Cost", "Units Received Per Unit Sold", "Goods Cost Per Unit",
    "Damage %", "Salvage Value/Unit", "Salvage Credit", "Input Cost", "Shrink Cost $",
    "Net Input Cost", "Handling $", "Labelling", "Goods + Handling",
    "New Final Cost (Unit)", "Column1", "Billling UOM Cost", "Priced Labelling", "Final Cost",
    "Base Margin %", "Margin $", "Base Price", "List Price", "List Margin %",
} | {f"{prefix}-{i}" for i in range(1, 5) for prefix in ("Item", "Qty", "Unit $", "Total $")}

EXPORT_REQUIRED = {"Product Code", "Cost Price", "Base Price", "Suggested Price"}


@pytest.fixture(scope="module")
def sheets():
    return generate(items=40)


def test_cost_sheet_has_every_required_column(sheets):
    cost_df, _ = sheets
    missing = COST_REQUIRED - set(cost_df.columns)
    assert not missing, f"cost sheet is missing {sorted(missing)}"


def test_export_sheet_has_every_required_column(sheets):
    _, export_df = sheets
    missing = EXPORT_REQUIRED - set(export_df.columns)
    assert not missing, f"export sheet is missing {sorted(missing)}"


def test_the_cost_build_up_is_internally_consistent(sheets):
    cost_df, _ = sheets
    # Each step of the stack has to be at least the one before it.
    landed = cost_df["Market Cost"] + cost_df["Freight"]
    assert np.allclose(landed, cost_df["Landed Cost"], atol=1e-3)
    assert (cost_df["Sellable Input Cost"] >= cost_df["Landed Cost"] - 1e-3).all()
    assert (cost_df["Final Cost"] >= cost_df["Sellable Input Cost"] - 1e-3).all()
    assert (cost_df["Base Price"] > cost_df["Final Cost"]).all()
    assert (cost_df["List Price"] > cost_df["Base Price"]).all()


def test_prices_realise_the_margin_they_claim(sheets):
    cost_df, _ = sheets
    realised = (cost_df["Base Price"] - cost_df["Final Cost"]) / cost_df["Base Price"]
    assert np.allclose(realised, cost_df["Base Margin %"], atol=1e-3)
    # And the scalar helper agrees with the vectorised check above.
    first = cost_df.iloc[0]
    assert calculate_margin_percent(first["Base Price"], first["Final Cost"]) == pytest.approx(
        first["Base Margin %"], abs=1e-3
    )


def test_recovery_is_a_plausible_yield(sheets):
    cost_df, _ = sheets
    assert cost_df["Sellable %"].between(40, 100).all()


def test_export_sheet_contains_codes_the_cost_sheet_does_not(sheets):
    """
    The tool's job includes reporting codes it could not price, so the sample
    data has to contain some or that path is never exercised.
    """
    cost_df, export_df = sheets
    orphans = set(export_df["Product Code"]) - set(cost_df["Item Code"])
    assert orphans, "export sheet should hold codes absent from the cost sheet"


def test_generation_is_deterministic():
    first, _ = generate(items=20, seed=7)
    second, _ = generate(items=20, seed=7)
    pd.testing.assert_frame_equal(first, second)


def test_the_lane_column_the_app_reads_actually_holds_a_lane():
    """
    The calculator recomputes inbound freight by matching the lane column
    against the freight table. It used to read a column holding the *supplier*
    name -- "Shenzhen Kaiyuan Electronics" matches no lane, so `get_freight_cost`
    returned the zero default and every recalculated row silently lost its
    inbound freight and duty. The sheet's own Freight column was right, which is
    why nothing looked wrong until a row was repriced.

    The two are now separate columns, and this pins it: every lane in the sheet
    has to resolve to a non-zero rate, and to the same rate the sheet stored.
    """
    cost_df, _ = generate(items=40)
    assert "Supplier Name" in cost_df.columns
    lanes = cost_df["Inbound Lane"]
    assert (lanes.map(get_freight_cost) > 0).all(), (
        "a lane that resolves to zero means the app drops freight on recalculation: "
        f"{sorted(set(lanes[lanes.map(get_freight_cost) == 0]))}"
    )
    assert np.allclose(lanes.map(get_freight_cost), cost_df["Freight"], atol=1e-9)


def test_the_app_reads_the_lane_column_by_the_name_the_sheet_writes():
    """A rename on one side of that join is silent: the lookup returns the
    default and the cost is quietly light."""
    source = pathlib.Path("app/views/cost_to_price_calculator.py").read_text(encoding="utf-8")
    assert 'get_freight_cost(row.get("Inbound Lane", ""))' in source
