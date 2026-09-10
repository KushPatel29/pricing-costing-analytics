import io
import logging
import sys
import zipfile
from datetime import datetime
from pathlib import Path

# Streamlit executes each page file directly, so the import root is the page's
# own directory. Add the repository root so the shared, testable costing package
# resolves the same way from the entry script and from any page under app/pages/.
ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import numpy as np
import pandas as pd
import streamlit as st

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cost_to_price")

# Page config is set once by the entry script under st.navigation; the shared
# stylesheet comes from sh.page(). This page used to carry its own -- a centred
# hero in near-black on white, a blue button, green and red status words -- and
# on the shared dark surface it read as a different application bolted on.
from app import shared as sh  # noqa: E402

sh.page()

VERSION = "2.0.0"

# --- Constants ---
DEFAULT_TRIM = 0.0
DEFAULT_LABOUR = 0.0
DEFAULT_STICKER = 0.0

try:
    import xlsxwriter
    EXCEL_ENGINE = "xlsxwriter"
except ImportError:
    EXCEL_ENGINE = "openpyxl"

# --- Helper Functions ---
# The costing maths lives in costing/formulas.py so it can be tested without
# booting Streamlit. Names are re-exported here so the rest of this script is
# unchanged.
from costing.formulas import (  # noqa: E402
    DEFAULT_BASE_MARGIN,
    DEFAULT_LIST_MARGIN,
    DEFAULT_RECOVERY,
    calculate_actual_inv_cost,
    calculate_landed_cost,
    calculate_margin_dollars,
    calculate_market_cost,
    calculate_price_from_margin,
    calculate_recovery_input,
    calculate_trim_recovery,
    calculate_waste_output,
    clean_item_code,
    get_freight_cost,
    safe_float,
)


def compute_totals_for_inputs(row):
    """Qty x unit cost for each of the four raw-material input slots."""
    for i in range(1, 5):
        qty = safe_float(row.get(f"Qty-{i}"), 0.0)
        unit_cost = safe_float(row.get(f"Unit $-{i}"), 0.0)
        row[f"Total $-{i}"] = qty * unit_cost
    return row


def calculate_raw_material_per_lb_cost(row) -> float:
    return sum(safe_float(row.get(f"Total $-{i}"), 0.0) for i in range(1, 5))


def auto_fill_missing_columns(df: pd.DataFrame, required_cols=None, verbose=True) -> pd.DataFrame:
    df.columns = [col.strip().replace('\n', ' ') for col in df.columns]
    fills = []
    # 1. Market Cost = Actual Inv Cost(units) + Adj
    if "Market Cost" not in df.columns and all(col in df.columns for col in ["Actual Inv Cost(units)", "Adj"]):
        df["Market Cost"] = df["Actual Inv Cost(units)"] + df["Adj"]
        fills.append("Market Cost")
    # 2. Landed Cost = Market Cost + Freight
    if "Landed Cost" not in df.columns and all(col in df.columns for col in ["Market Cost", "Freight"]):
        df["Landed Cost"] = df["Market Cost"] + df["Freight"]
        fills.append("Landed Cost")
    # 3. Sellable Input Cost = (Market Cost + Freight) / Sellable %
    if ("Sellable %" in df.columns and "Sellable Input Cost" not in df.columns and
        all(col in df.columns for col in ["Market Cost", "Freight"])):
        recovery = df["Sellable %"].replace(0, np.nan).apply(lambda x: x/100 if x > 1 else x)
        df["Sellable Input Cost"] = (df["Market Cost"] + df["Freight"]) / recovery
        fills.append("Sellable Input Cost")
    # 4. Goods Cost Per Unit = sum Total $-1 to Total $-4
    total_cols = [f"Total $-{i}" for i in range(1, 5)]
    if "Goods Cost Per Unit" not in df.columns and all(col in df.columns for col in total_cols):
        df["Goods Cost Per Unit"] = df[total_cols].sum(axis=1)
        fills.append("Goods Cost Per Unit")
    # 5. Shrink Cost $ = (Goods Cost Per Unit / Sellable %) - Goods Cost Per Unit
    if ("Shrink Cost $" not in df.columns and
        "Goods Cost Per Unit" in df.columns and "Sellable %" in df.columns):
        recovery = df["Sellable %"].replace(0, np.nan).apply(lambda x: x/100 if x > 1 else x)
        df["Shrink Cost $"] = (df["Goods Cost Per Unit"] / recovery) - df["Goods Cost Per Unit"]
        fills.append("Shrink Cost $")
    # 6. Final Cost = Billling UOM Cost + Priced Labelling
    if "Final Cost" not in df.columns and all(col in df.columns for col in ["Billling UOM Cost", "Priced Labelling"]):
        df["Final Cost"] = df["Billling UOM Cost"] + df["Priced Labelling"]
        fills.append("Final Cost")
    # 7. Margin $ = Base Price - Final Cost
    if "Margin $" not in df.columns and all(col in df.columns for col in ["Base Price", "Final Cost"]):
        df["Margin $"] = df["Base Price"] - df["Final Cost"]
        fills.append("Margin $")
    # 8. List Margin % = (List Price - Final Cost) / List Price
    if ("List Margin %" not in df.columns and
        "List Price" in df.columns and "Final Cost" in df.columns):
        with np.errstate(divide='ignore', invalid='ignore'):
            df["List Margin %"] = (df["List Price"] - df["Final Cost"]) / df["List Price"]
        fills.append("List Margin %")
    if verbose and fills:
        st.info(f"Filled missing columns automatically: {', '.join(fills)}")
    still_missing = []
    if required_cols:
        for col in required_cols:
            if col not in df.columns:
                still_missing.append(col)
        if verbose and still_missing:
            st.warning(f"These required columns are missing and could not be auto-filled: {', '.join(still_missing)}")
    return df

def read_files(cost_file, export_file, cost_sheet_name: str, export_sheet_name: str):
    try:
        excel_cost = pd.ExcelFile(cost_file)
        df_cost = pd.read_excel(cost_file, sheet_name=cost_sheet_name, engine="openpyxl")
        other_sheets = {
            sheet: excel_cost.parse(sheet)
            for sheet in excel_cost.sheet_names
            if sheet != cost_sheet_name
        }
        df_export = pd.read_excel(export_file, sheet_name=export_sheet_name, engine="openpyxl")
        df_cost.columns = [col.strip().replace('\n', ' ') for col in df_cost.columns]
        df_export.columns = [col.strip().replace('\n', ' ') for col in df_export.columns]
        if "Item Code" in df_cost.columns:
            df_cost["Item Code"] = df_cost["Item Code"].apply(clean_item_code)
        if "Product Code" in df_export.columns:
            df_export["Product Code"] = df_export["Product Code"].apply(clean_item_code)
        if df_cost.empty or df_export.empty:
            st.error("One or both sheets are empty.")
            return None, None, None
        return df_cost, df_export, other_sheets
    except Exception as e:
        st.error(f"Error reading files: {e}")
        return None, None, None

def validate_columns(df: pd.DataFrame, required_cols: set, sheet_name: str) -> bool:
    available_cols = set(df.columns)
    missing = required_cols - available_cols
    if missing:
        st.error(f"{sheet_name} missing columns: {missing}")
        return False
    return True

def update_cost_row(row: pd.Series, new_cost_price: float = None, original_row: pd.Series = None) -> pd.Series:
    old_vendor_invoice = safe_float(original_row.get("Vendor Invoice Price") if original_row is not None else row.get("Vendor Invoice Price"))
    old_final_cost = safe_float(original_row.get("Final Cost") if original_row is not None else row.get("Final Cost"))
    old_base_price = safe_float(original_row.get("Base Price") if original_row is not None else row.get("Base Price"))
    old_list_price = safe_float(original_row.get("List Price") if original_row is not None else row.get("List Price"))
    row = compute_totals_for_inputs(row)
    units_per_billing_uom = safe_float(row.get("Units Per Billling UOM", 1), 1)
    vendor_invoice_price = new_cost_price if new_cost_price is not None else safe_float(row.get("Vendor Invoice Price"))
    actual_inv_cost = calculate_actual_inv_cost(vendor_invoice_price, units_per_billing_uom)
    adj = safe_float(row.get("Adj", 0))
    market_cost = calculate_market_cost(actual_inv_cost, adj)
    freight = get_freight_cost(row.get("Inbound Lane", ""))
    landed_cost = calculate_landed_cost(market_cost, freight)
    raw_recovery_val = safe_float(row.get("Sellable %", DEFAULT_RECOVERY), DEFAULT_RECOVERY)
    if raw_recovery_val > 1.0:
        raw_recovery_val = raw_recovery_val / 100.0
    if raw_recovery_val <= 0.0:
        raw_recovery_val = DEFAULT_RECOVERY
    recovery_percent = raw_recovery_val
    recovery_input = calculate_recovery_input(market_cost, freight, recovery_percent)
    raw_material_cost = calculate_raw_material_per_lb_cost(row)
    waste_output = calculate_waste_output(raw_material_cost, recovery_percent)
    trim_percent = safe_float(row.get("Damage %", DEFAULT_TRIM))
    salvage_value_unit = safe_float(row.get("Salvage Value/Unit", 0.0))
    recovery = calculate_trim_recovery(salvage_value_unit, trim_percent, recovery_percent)
    input_cost = recovery_input + raw_material_cost + waste_output
    net_input_cost = input_cost - recovery
    labour = safe_float(row.get("Handling $", DEFAULT_LABOUR))
    sticker = safe_float(row.get("Labelling", DEFAULT_STICKER))
    new_final_cost_unit = net_input_cost + labour + sticker
    column1_value = safe_float(row.get("Column1", 0))
    billling_uom_cost = new_final_cost_unit * units_per_billing_uom + column1_value
    priced_sticker = safe_float(row.get("Priced Labelling", 0))
    final_cost = billling_uom_cost + priced_sticker
    base_margin_value = safe_float(row.get("Base Margin %", 0.0))
    if base_margin_value == 0.0:
        base_margin_value = DEFAULT_BASE_MARGIN
    if base_margin_value > 1.0:
        base_margin_decimal = base_margin_value / 100.0
    else:
        base_margin_decimal = base_margin_value
    base_price = calculate_price_from_margin(final_cost, base_margin_decimal)
    list_margin_value = safe_float(row.get("List Margin %", 0.0))
    if list_margin_value == 0.0:
        list_margin_value = DEFAULT_LIST_MARGIN
    elif list_margin_value > 1.0:
        list_margin_value = list_margin_value / 100.0
    list_price = calculate_price_from_margin(final_cost, list_margin_value)
    margin_dollars = calculate_margin_dollars(base_price, final_cost)
    row["Price Change Date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row["Old Vendor Invoice Price"] = old_vendor_invoice
    row["Vendor Invoice Price"] = vendor_invoice_price
    row["Actual Inv Cost(units)"] = actual_inv_cost
    row["Adj"] = adj
    row["Market Cost"] = market_cost
    row["Freight"] = freight
    row["Landed Cost"] = landed_cost
    row["Sellable %"] = recovery_percent * 100  # as %
    row["Sellable Input Cost"] = recovery_input
    row["Goods Cost Per Unit"] = raw_material_cost
    row["Shrink Cost $"] = waste_output
    row["Damage %"] = trim_percent
    row["Salvage Value/Unit"] = salvage_value_unit
    row["Salvage Credit"] = recovery
    row["Input Cost"] = input_cost
    row["Net Input Cost"] = net_input_cost
    row["Handling $"] = labour
    row["Labelling"] = sticker
    row["Goods + Handling"] = new_final_cost_unit
    row["New Final Cost (Unit)"] = new_final_cost_unit
    row["Column1"] = column1_value
    row["Billling UOM Cost"] = billling_uom_cost
    row["Priced Labelling"] = priced_sticker
    row["Final Cost"] = final_cost
    row["Old Final Cost"] = old_final_cost
    row["Old Base Price"] = old_base_price
    row["Base Price"] = base_price
    row["Old List Price"] = old_list_price
    row["List Price"] = list_price
    row["Margin $"] = margin_dollars
    return row

def update_cost_sheet(df_cost: pd.DataFrame, item_code: str, new_cost_price: float):
    df_updated = df_cost.copy()
    item_code_clean = clean_item_code(item_code)
    updated_flag = False
    updated_item_codes = set()
    df_updated["Item Code"] = df_updated["Item Code"].apply(clean_item_code)
    for col in ["Old Vendor Invoice Price", "Old Final Cost", "Old Base Price", "Old List Price", "Price Change Date"]:
        if col not in df_updated.columns:
            df_updated[col] = None
    mask_main = df_updated["Item Code"] == item_code_clean
    if mask_main.any():
        for idx in df_updated[mask_main].index:
            original_row = df_cost.loc[idx]
            df_updated.loc[idx] = update_cost_row(
                row=df_updated.loc[idx],
                new_cost_price=new_cost_price,
                original_row=original_row
            )
        updated_item_codes.update(df_updated.loc[mask_main, "Item Code"])
        updated_flag = True
    item_cols = [c for c in df_updated.columns if c.startswith("Item-")]
    to_update = True
    iteration = 0
    while to_update and iteration < 10:
        to_update = False
        composite_mask = pd.Series(False, index=df_updated.index)
        for item_col in item_cols:
            unit_col = f"Unit $-{item_col.split('-')[1]}"
            if unit_col not in df_updated.columns:
                continue
            df_updated[item_col] = df_updated[item_col].apply(clean_item_code)
            mask_item = df_updated[item_col].isin(updated_item_codes)
            if mask_item.any():
                for idx in df_updated[mask_item].index:
                    item_code_item = df_updated.loc[idx, item_col]
                    matching_row = df_updated[df_updated["Item Code"] == item_code_item]
                    if not matching_row.empty:
                        # ←─── UPDATED LINE ────
                        df_updated.loc[idx, unit_col] = safe_float(matching_row.iloc[0]["Net Input Cost"])
                        # ────────────────────────
                composite_mask |= mask_item
                to_update = True
        if composite_mask.any():
            updated_item_codes.update(df_updated.loc[composite_mask, "Item Code"])
            for idx in df_updated[composite_mask].index:
                original_row = df_cost.loc[idx]
                df_updated.loc[idx] = update_cost_row(
                    row=df_updated.loc[idx],
                    new_cost_price=None,
                    original_row=original_row
                )
            updated_flag = True
        iteration += 1
    return df_updated, updated_flag, updated_item_codes

def update_export_sheet(df_export: pd.DataFrame, df_cost_updated: pd.DataFrame, updated_item_codes: set):
    df_export_updated = df_export.copy()
    updated_flag = False
    df_export_updated["Product Code"] = df_export_updated["Product Code"].apply(clean_item_code)
    for item_code in updated_item_codes:
        cost_row = df_cost_updated[df_cost_updated["Item Code"] == item_code]
        if cost_row.empty:
            continue
        final_cost = safe_float(cost_row.iloc[0]["Final Cost"])
        final_base = safe_float(cost_row.iloc[0]["Base Price"])
        final_list = safe_float(cost_row.iloc[0]["List Price"])
        mask_export = df_export_updated["Product Code"] == item_code
        if mask_export.any():
            df_export_updated.loc[mask_export, "Cost Price"] = final_cost
            df_export_updated.loc[mask_export, "Base Price"] = final_base
            df_export_updated.loc[mask_export, "Suggested Price"] = final_list
            updated_flag = True
    return df_export_updated, updated_flag

# --- MAIN APP Logic ---
st.title("Cost-to-price calculator")
sh.lede(
    "The tool the rest of this app is built around: take a cost sheet, move the "
    f"input costs, and get every price back at the margin you asked for. Version "
    f"{VERSION}."
)

with st.sidebar:
    st.header("Cost-to-price calculator")
    st.markdown("""
    - Starts on **generated sample data**, so you can try it straight away.
    - To use your own, switch to **Upload my own files** and supply the
      **Cost Sheet** and **Export Sheet**; set the sheet names if they differ.
    - Update prices by editing the table, or by uploading a CSV/Excel of new prices.
    - Review all changes, then click **Apply All Cost Changes**.
    - Download both updated sheets as a single ZIP.
    """)

SOURCE_SAMPLE = "Sample data (generated)"
SOURCE_UPLOAD = "Upload my own files"


@st.cache_data(show_spinner="Generating sample sheets…")
def _sample_frames(items: int = 240, seed: int = 613):
    """
    Build the two sheets the tool expects, in memory.

    The calculator needs a cost sheet and an ERP export before it can do
    anything, and a visitor following a link has neither. Generating them means
    the app is usable on arrival instead of showing two empty file pickers.
    """
    from seed.generate_sheets import generate

    return generate(seed=seed, items=items)


st.markdown("## Step 1: choose your data")
source = st.radio(
    "Data source",
    (SOURCE_SAMPLE, SOURCE_UPLOAD),
    horizontal=True,
    label_visibility="collapsed",
    key="data_source",
)

df_cost = df_export = None
other_sheets = {}
# The sheet names are only collected on the upload path, but the download at the
# bottom writes with them on BOTH paths — so the sample flow, which is the one a
# visitor lands on, raised NameError: cost_sheet_name the moment they clicked
# download. Defaulted here to the same values the upload inputs start with;
# that branch overwrites them when a file is actually supplied.
cost_sheet_name = "Cost Sheet"
export_sheet_name = "AllProducts"

if source == SOURCE_SAMPLE:
    st.info(
        "Showing generated sample data — the same 240-item catalogue every other page "
        "in this app runs on, built by `seed/generate_sheets.py` with a fixed seed at "
        "the final month's costs. Nothing here comes from a real business. "
        "Switch to **Upload my own files** to run the tool on your own sheets."
    )
    df_cost, df_export = _sample_frames()
    df_cost = df_cost.copy()
    df_export = df_export.copy()
else:
    col1, col2 = st.columns(2)
    with col1:
        cost_file = st.file_uploader("Upload Cost Sheet (XLSX)", type=["xlsx"], key="cost")
    with col2:
        export_file = st.file_uploader("Upload Export Sheet (XLSX)", type=["xlsx"], key="export")

    if cost_file and export_file:
        cost_sheet_name = st.text_input("Cost Sheet Name:", value="Cost Sheet").strip()
        export_sheet_name = st.text_input("Export Sheet Name:", value="AllProducts").strip()
        df_cost, df_export, other_sheets = read_files(
            cost_file, export_file, cost_sheet_name, export_sheet_name
        )
        if df_cost is None or df_export is None:
            st.stop()
    else:
        st.caption("Upload both sheets to continue, or switch back to the sample data.")

if df_cost is not None and df_export is not None:
    cost_required = {
        "Item Code", "Units Per Billling UOM", "Inbound Lane", "Vendor Invoice Price",
        "Actual Inv Cost(units)", "Adj", "Market Cost", "Freight", "Landed Cost",
        "Sellable %", "Sellable Input Cost", "Units Received Per Unit Sold", "Goods Cost Per Unit",
        "Damage %", "Salvage Value/Unit", "Salvage Credit", "Input Cost", "Shrink Cost $",
        "Net Input Cost", "Handling $", "Labelling", "Goods + Handling",
        "New Final Cost (Unit)", "Column1", "Billling UOM Cost", "Priced Labelling", "Final Cost",
        "Base Margin %", "Margin $", "Base Price", "List Price", "List Margin %"
    }
    for i in range(1, 5):
        cost_required.update({f"Item-{i}", f"Qty-{i}", f"Unit $-{i}", f"Total $-{i}"})
    export_required = {"Product Code", "Cost Price", "Base Price", "Suggested Price"}
    df_cost = auto_fill_missing_columns(df_cost, required_cols=cost_required)
    if not validate_columns(df_cost, cost_required, "Cost Sheet"):
        st.stop()
    if not validate_columns(df_export, export_required, "Export Sheet"):
        st.stop()
    if source == SOURCE_SAMPLE:
        st.success(f"Loaded {len(df_cost):,} generated items — ready to reprice.")
    else:
        st.success("Files uploaded successfully!")
    st.markdown("## Preview Sheets")
    c1, c2 = st.columns(2)
    with c1:
        st.dataframe(df_cost.head(8), use_container_width=True)
    with c2:
        st.dataframe(df_export.head(8), use_container_width=True)

    # --- Cost Update Section ---
    st.markdown("## Step 2: bulk update product costs")
    st.info("You can edit prices below, **or** upload a simple two-column file (Item Code, New Cost Price).")
    sample_data = pd.DataFrame({"Item Code": ["", "", ""], "New Cost Price": [None, None, None]})
    edit_df = st.data_editor(
        sample_data,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        key="bulk_cost_editor",
        column_order=["Item Code", "New Cost Price"]
    )
    # --- File upload alternative ---
    sh.caption("Upload a CSV or Excel file with two columns: Item Code and New Cost Price.")
    uploaded_price_file = st.file_uploader("Bulk Price Update File", type=["csv", "xlsx"], key="pricefile")
    if uploaded_price_file:
        try:
            if uploaded_price_file.name.endswith('.csv'):
                price_df = pd.read_csv(uploaded_price_file)
            else:
                price_df = pd.read_excel(uploaded_price_file)
            st.success("Bulk price update file loaded!")
            st.dataframe(price_df)
            # Clean and convert input
            price_df["Item Code"] = price_df["Item Code"].astype(str).apply(clean_item_code)
            price_df["New Cost Price"] = pd.to_numeric(price_df["New Cost Price"], errors="coerce")
            price_df = price_df[price_df["Item Code"].str.strip() != ""]
            price_df = price_df[price_df["New Cost Price"].notna()]
            price_df = price_df[price_df["New Cost Price"] > 0]
            changes_to_apply = price_df
        except Exception as e:
            st.error(f"Could not read uploaded file: {e}")
            changes_to_apply = pd.DataFrame()
    else:
        # Use editable table
        changes_to_apply = edit_df.dropna(subset=["Item Code", "New Cost Price"])
        changes_to_apply["New Cost Price"] = pd.to_numeric(changes_to_apply["New Cost Price"], errors="coerce")
        changes_to_apply = changes_to_apply[changes_to_apply["Item Code"].str.strip() != ""]
        changes_to_apply = changes_to_apply[changes_to_apply["New Cost Price"].notna()]
        changes_to_apply = changes_to_apply[changes_to_apply["New Cost Price"] > 0]

    # --- Apply All Changes ---
    if changes_to_apply.empty:
        st.info("Add at least one valid Item Code and Cost Price above or upload a file to enable the Update button.")
    if st.button("Apply All Cost Changes", type="primary", disabled=changes_to_apply.empty):
        summary_rows = []
        all_updated_codes = set()
        df_cost_updated = df_cost.copy()
        df_export_updated = df_export.copy()
        try:
            for i, row in changes_to_apply.iterrows():
                code, price = str(row["Item Code"]).strip(), float(row["New Cost Price"])
                df_cost_updated, cost_updated, updated_codes = update_cost_sheet(df_cost_updated, code, price)
                df_export_updated, export_updated = update_export_sheet(df_export_updated, df_cost_updated, updated_codes)
                all_updated_codes.update(updated_codes)
                summary_rows.append({
                    "Item Code": code,
                    "New Cost Price": price,
                    "Updated?": "Yes" if cost_updated or export_updated else "No"
                })
            st.markdown("## Summary of Updates")
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)
            if all_updated_codes:
                st.success(f"Updated pricing for {len(all_updated_codes)} Item Code(s).")
                col1, col2 = st.columns(2)
                with col1:
                    st.markdown("## Updated Cost Sheet Rows")
                    st.dataframe(df_cost_updated[df_cost_updated["Item Code"].isin(all_updated_codes)])
                with col2:
                    st.markdown("## Updated Export Sheet Rows")
                    st.dataframe(df_export_updated[df_export_updated["Product Code"].isin(all_updated_codes)])
            else:
                st.warning("No rows were updated. Please check your input.")
            # --- Download Excel/ZIP ---
            cost_buf = io.BytesIO()
            export_buf = io.BytesIO()
            with pd.ExcelWriter(cost_buf, engine=EXCEL_ENGINE) as writer:
                df_cost_updated.to_excel(writer, sheet_name=cost_sheet_name, index=False)
                for sheet_name, df in other_sheets.items():
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
            cost_buf.seek(0)
            with pd.ExcelWriter(export_buf, engine=EXCEL_ENGINE) as writer:
                df_export_updated.to_excel(writer, sheet_name=export_sheet_name, index=False)
            export_buf.seek(0)
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                zip_file.writestr("Updated_Cost_Sheet_BULK.xlsx", cost_buf.getvalue())
                zip_file.writestr("Updated_Export_Sheet_BULK.xlsx", export_buf.getvalue())
            zip_buf.seek(0)
            st.markdown("## Download Updated Files")
            st.download_button(
                label="⬇️ Download Updated Files (ZIP)",
                data=zip_buf.getvalue(),
                file_name="Updated_Files_BULK.zip",
                mime="application/zip"
            )
        except Exception as e:
            st.error(f"Error during update: {e}")
    sh.footer()
else:
    st.info("Please upload both Cost Sheet and Export Sheet to proceed.")
