"""
Cleanse the ERP extract in ``raw/`` and reconcile the result back to it.

The order of the steps is the design. Deduplicate before anything else, or a
duplicated bad row gets counted twice in the defect stats. Repair before
dropping, because a unit-of-measure mismatch and an arithmetic mismatch are
both recoverable and dropping them loses real revenue. Drop last, and only what
genuinely cannot be used.

Every step records what it touched and what it was worth, and the run ends with
a reconciliation that has to balance. That is the point of the whole exercise: a
pipeline that quietly drops bad rows and reports a clean total has hidden the
problem in the direction that flatters it. Here the difference between the
extract and the staged data is itemised, and an unexplained remainder fails.

Usage::

    python -m engine.stage_erp
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from pricing.quality import quality_score, reconcile, reconciliation_rows, run_checks

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "raw"
OUT_DIR = ROOT / "output"

RAW_TABLES = ("erp_billing_items", "erp_material_master",
              "erp_customer_master", "erp_condition_records")


def load_raw(raw_dir: Path = RAW_DIR) -> dict[str, pd.DataFrame]:
    missing = [t for t in RAW_TABLES if not (raw_dir / f"{t}.csv").exists()]
    if missing:
        raise FileNotFoundError(
            f"missing ERP extracts: {missing}. Run: python -m seed.generate_erp"
        )
    return {name: pd.read_csv(raw_dir / f"{name}.csv") for name in RAW_TABLES}


def stage(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, list[dict], dict[str, float]]:
    """
    Return the staged billing items, the step log, and the named exclusions.

    The exclusions dict is what the reconciliation is checked against, so a
    step that removes value without naming it here will fail the balance -- by
    construction, not by anybody remembering to update a comment.
    """
    items = frames["erp_billing_items"].copy()
    materials = frames["erp_material_master"]
    customers = frames["erp_customer_master"]

    items["billing_date"] = pd.to_datetime(items["billing_date"], errors="coerce")
    items["extract_date"] = pd.to_datetime(items["extract_date"], errors="coerce")
    opening_value = float(items["net_value"].sum())
    opening_rows = len(items)

    log: list[dict] = []
    exclusions: dict[str, float] = {}

    def record(step: str, kind: str, removed: pd.DataFrame | None,
               note: str, value: float | None = None) -> None:
        amount = float(removed["net_value"].sum()) if removed is not None else (value or 0.0)
        log.append(
            {
                "step": step,
                "action": kind,
                "rows": 0 if removed is None else len(removed),
                "value": round(amount, 2),
                "note": note,
            }
        )
        if kind == "Dropped" and amount:
            exclusions[step] = amount

    # --- 1. Deduplicate first ---------------------------------------------
    # Before anything else, or a duplicated bad row is counted twice in every
    # defect statistic below it.
    duplicated = items[items.duplicated(subset=["billing_document", "item_number"],
                                        keep="first")]
    record("Duplicate billing lines", "Dropped", duplicated,
           "Same document and item posted twice. Kept the first.")
    items = items.drop_duplicates(subset=["billing_document", "item_number"], keep="first")

    # --- 2. Repair what can be repaired -----------------------------------
    # A case billed against a material mastered in eaches is a real sale at a
    # real price; only the unit is wrong. Converting recovers the revenue that
    # dropping it would throw away.
    uom_wrong = items["sales_uom"].ne("EA") & items["pack_size_units"].gt(0)
    # Value is unchanged by the conversion -- cases times price-per-case is the
    # same money as eaches times price-per-each -- so this is the value the
    # repair *rescued from being wrong per unit*, not value excluded.
    repaired_value = float(items.loc[uom_wrong, "net_value"].sum())
    items.loc[uom_wrong, "billed_quantity"] = (
        items.loc[uom_wrong, "billed_quantity"] * items.loc[uom_wrong, "pack_size_units"])
    items.loc[uom_wrong, "net_price"] = (
        items.loc[uom_wrong, "net_price"] / items.loc[uom_wrong, "pack_size_units"])
    items.loc[uom_wrong, "sales_uom"] = "EA"
    record("Unit of measure converted", "Repaired", None,
           f"{int(uom_wrong.sum())} lines billed in cases against a material "
           "mastered in eaches. Converted rather than dropped.", repaired_value)

    # The extract's own arithmetic disagreeing is recoverable too: quantity and
    # unit price are the source of truth, and the extended value is derived.
    recomputed = (items["billed_quantity"] * items["net_price"]).round(2)
    mismatched = (items["net_value"] - recomputed).abs() > 0.05
    drift = float((items.loc[mismatched, "net_value"] - recomputed[mismatched]).sum())
    items.loc[mismatched, "net_value"] = recomputed[mismatched]
    record("Extended value recomputed", "Repaired", None,
           f"{int(mismatched.sum())} lines where net value did not equal quantity "
           "times price. Recomputed from the two source fields.", drift)
    exclusions["Extended value recomputed"] = drift

    # --- 3. Drop only what cannot be used ---------------------------------
    unusable = items[(items["billed_quantity"] <= 0)
                     & (items["document_type"] != "Credit memo")]
    record("Zero or negative quantity", "Dropped", unusable,
           "Divides by zero in every per-unit measure. Not a credit note.")
    items = items.drop(unusable.index)

    future = items[items["billing_date"] > items["extract_date"]]
    record("Posted after the extract date", "Dropped", future,
           "A future posting is a test document or a keying error, and it lands "
           "in a period already reported.")
    items = items.drop(future.index)

    known_customers = set(customers["customer"])
    orphans = items[~items["sold_to_party"].isin(known_customers)]
    record("Customer not in the master", "Dropped", orphans,
           "Cannot be segmented, so its margin would land in no cut of the book.")
    items = items.drop(orphans.index)

    # --- 4. Flag what is kept but not yet usable for pricing --------------
    costed = items.merge(materials[["material", "standard_cost", "recovery"]],
                         on="material", how="left")
    costed["cost_is_missing"] = (
        costed["standard_cost"].isna() | (costed["standard_cost"] <= 0))
    costed["recovery_is_implausible"] = (
        (costed["recovery"] <= 0.30) | (costed["recovery"] > 1.0))
    costed["priceable"] = ~(costed["cost_is_missing"] | costed["recovery_is_implausible"])
    record("Held back: no usable standard cost", "Flagged", None,
           f"{int(costed['cost_is_missing'].sum())} lines whose material has no "
           "standard cost, and "
           f"{int(costed['recovery_is_implausible'].sum())} whose recovery is "
           "outside anything a distribution centre produces. Kept in the staged data "
           "and excluded from repricing until master data is fixed.",
           float(costed.loc[~costed["priceable"], "net_value"].sum()))

    costed["margin"] = (costed["net_value"]
                        - costed["billed_quantity"] * costed["standard_cost"].fillna(0)).round(2)
    log.insert(0, {"step": "Extract as received", "action": "Opening",
                   "rows": opening_rows, "value": round(opening_value, 2),
                   "note": f"{opening_rows:,} billing lines from the ERP."})
    log.append({"step": "Staged and usable", "action": "Closing",
                "rows": len(costed), "value": round(float(costed["net_value"].sum()), 2),
                "note": f"{int(costed['priceable'].sum()):,} of {len(costed):,} lines "
                        "are priceable."})
    return costed, log, exclusions


def build(raw_dir: Path = RAW_DIR) -> dict[str, pd.DataFrame]:
    frames = load_raw(raw_dir)
    checks = run_checks(frames)
    score = quality_score(checks)
    staged, log, exclusions = stage(frames)

    result = reconcile(
        frames["erp_billing_items"], staged, measure="net_value",
        tolerance=0.51, exclusions=exclusions,
    )
    statement = reconciliation_rows(result)
    statement["balanced"] = result["balanced"]
    statement["unexplained"] = round(result["unexplained"], 2)

    summary = pd.DataFrame([
        {"metric": "Billing lines received", "value": len(frames["erp_billing_items"]),
         "unit": "count"},
        {"metric": "Billing lines staged", "value": len(staged), "unit": "count"},
        {"metric": "Lines priceable", "value": int(staged["priceable"].sum()),
         "unit": "count"},
        {"metric": "Lines held for master data",
         "value": int((~staged["priceable"]).sum()), "unit": "count"},
        {"metric": "Checks run", "value": len(checks), "unit": "count"},
        {"metric": "Checks passed", "value": int(checks["passed"].sum()), "unit": "count"},
        {"metric": "Rows failing a check",
         "value": int(checks["rows_failing"].clip(lower=0).sum()), "unit": "count"},
        {"metric": "Overall quality score",
         "value": float(score.loc[score["dimension"] == "Overall", "score"].iloc[0]),
         "unit": "percent"},
        {"metric": "Value received", "value": result["source_total"], "unit": "currency"},
        {"metric": "Value staged", "value": result["staged_total"], "unit": "currency"},
        {"metric": "Value excluded", "value": result["explained"], "unit": "currency"},
        {"metric": "Unexplained difference", "value": result["unexplained"],
         "unit": "currency"},
    ])

    return {
        "data_quality_checks": checks,
        "data_quality_score": score,
        "staging_log": pd.DataFrame(log),
        "reconciliation": statement,
        "staging_summary": summary,
        "staged_billing_items": staged.drop(columns=["extract_date"]),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    outputs = build(args.raw_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in outputs.items():
        frame.to_csv(args.out_dir / f"{name}.csv", index=False, lineterminator="\n")

    log = outputs["staging_log"]
    print(f"staged the ERP extract into {args.out_dir}/\n")
    for row in log.itertuples():
        print(f"  {row.action:9s} {row.step:34s} {row.rows:>7,} rows  "
              f"${row.value:>14,.2f}")
    statement = outputs["reconciliation"]
    print()
    for row in statement.itertuples():
        print(f"  {row.line:34s} ${row.amount:>16,.2f}")
    balanced = bool(statement["balanced"].iloc[0])
    print(f"\n  reconciliation {'BALANCES' if balanced else 'DOES NOT BALANCE'}")
    return 0 if balanced else 1


if __name__ == "__main__":
    raise SystemExit(main())
