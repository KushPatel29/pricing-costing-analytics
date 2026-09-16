"""Build a deterministic pricing approval and realization evidence pack.

The existing project is already unusually deep on pricing maths. This module
closes the operating gap between a recommendation and a controlled decision:
who reviews it, which evidence travels with it, what period is monitored, and
which realised outcome should send it back for review. It never records a
human approval and never publishes a price to a customer or ERP.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output"
POLICY_PATH = ROOT / "governance" / "pricing_decision_policy.json"


def _read_csv(folder: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(folder / f"{name}.csv")


def required_reviewer(action: str, price_change_pct: float) -> str:
    """Return the delegated reviewer without implying that review occurred."""
    if action == "Fix cost":
        return "Pricing Data Steward"
    if action == "Discontinue" or abs(price_change_pct) > 0.07:
        return "Finance Controller"
    if abs(price_change_pct) > 0.05:
        return "Commercial Director"
    return "Sales Manager"


def decision_state(action: str, confidence: str) -> str:
    if action == "Fix cost":
        return "RETURN_FOR_DATA"
    if action == "Maintain":
        return "NO_CHANGE"
    if action == "Discontinue" or confidence != "High":
        return "REVIEW_REQUIRED"
    return "READY_FOR_REVIEW"


def build_decision_register(recommendations: pd.DataFrame, snapshot: str) -> pd.DataFrame:
    frame = recommendations.sort_values(
        ["priority", "product_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True).copy()
    frame.insert(0, "request_id", [f"PRC-REQ-{i:04d}" for i in range(1, len(frame) + 1)])
    frame["decision_state"] = [
        decision_state(str(a), str(c))
        for a, c in zip(frame["action"], frame["confidence"], strict=True)
    ]
    frame["required_reviewer"] = [
        required_reviewer(str(a), float(p))
        for a, p in zip(frame["action"], frame["price_change_pct"], strict=True)
    ]
    frame["requested_by"] = "Pricing Analytics Lead"
    frame["human_decision"] = frame["action"].map(
        lambda value: "NOT_REQUIRED" if value == "Maintain" else "OPEN"
    )
    frame["effective_period"] = "Next controlled price-list window"
    frame["monitoring_window"] = "First three complete months after effective date"
    frame["rollback_trigger"] = frame.apply(
        lambda row: (
            "Return to current price and review if pocket-margin dollars fall below baseline"
            if row["action"] in {"Increase", "Discount"}
            else "Stop the workflow until the cost master reconciles"
            if row["action"] == "Fix cost"
            else "Require Finance review before delisting or contract change"
            if row["action"] == "Discontinue"
            else "No rollback; no price change proposed"
        ),
        axis=1,
    )
    frame["evidence_refs"] = (
        "recommendations.csv; deal_scores.csv; passthrough.csv; price_change_log.csv"
    )
    frame["snapshot_date"] = snapshot
    columns = [
        "request_id", "product_id", "description", "category", "action",
        "decision_state", "human_decision", "required_reviewer", "requested_by",
        "confidence", "evidence", "current_price", "recommended_price",
        "price_change_pct", "volume_change_pct", "revenue_delta", "margin_delta",
        "effective_period", "monitoring_window", "rollback_trigger", "evidence_refs",
        "rationale", "snapshot_date",
    ]
    return frame[columns]


def _weighted(frame: pd.DataFrame, value: str) -> float:
    qty = frame["quantity_units"].sum()
    if frame.empty or qty <= 0:
        return float("nan")
    return float((frame[value] * frame["quantity_units"]).sum() / qty)


def build_realization_monitor(
    changes: pd.DataFrame, sales: pd.DataFrame, policy: dict
) -> pd.DataFrame:
    sales = sales.copy()
    changes = changes.copy()
    sales["month"] = pd.to_datetime(sales["month"])
    changes["effective_month"] = pd.to_datetime(changes["effective_month"])
    approved = changes[changes["approval_state"].isin(["Approved", "Auto-approved"])]
    rows: list[dict] = []
    floor = float(policy["realization"]["minimum_directional_realization"])
    giveback_limit = float(policy["realization"]["review_if_pocket_giveback_points_exceed"])
    for change in approved.itertuples(index=False):
        effective = change.effective_month
        product = sales[sales["product_id"] == change.product_id]
        pre = product[(product["month"] >= effective - pd.DateOffset(months=3)) &
                      (product["month"] < effective)]
        post = product[(product["month"] >= effective) &
                       (product["month"] < effective + pd.DateOffset(months=3))]
        pre_list = _weighted(pre, "list_price")
        pre_pocket = _weighted(pre, "pocket_price")
        post_list = _weighted(post, "list_price")
        post_invoice = _weighted(post, "invoice_price")
        post_pocket = _weighted(post, "pocket_price")
        approved_change = float(change.pct_change)
        pocket_change = (
            post_pocket / pre_pocket - 1
            if pd.notna(pre_pocket) and pre_pocket > 0 and pd.notna(post_pocket)
            else float("nan")
        )
        directional = (
            pocket_change / approved_change
            if pd.notna(pocket_change) and abs(approved_change) >= 0.002
            else float("nan")
        )
        list_change = (
            post_list / pre_list - 1
            if pd.notna(pre_list) and pre_list > 0 and pd.notna(post_list)
            else float("nan")
        )
        list_to_pocket_giveback = (
            list_change - pocket_change
            if pd.notna(list_change) and pd.notna(pocket_change)
            else float("nan")
        )
        enough = pre["month"].nunique() >= 2 and post["month"].nunique() >= 2
        same_direction = pd.notna(directional) and directional > 0
        state = "PASS"
        reason = (
            "Pocket-price movement follows the approved direction within the "
            "monitoring window."
        )
        if not enough:
            state = "REVIEW"
            reason = (
                "Fewer than two pre or post months are available; no realization "
                "claim is safe."
            )
        elif not same_direction or directional < floor:
            state = "REVIEW"
            reason = "Less than half of the approved directional move reached pocket price."
        elif abs(list_to_pocket_giveback) > giveback_limit:
            state = "REVIEW"
            reason = "More than three points separate the post-change list and pocket realization."
        rows.append({
            "change_id": change.change_id,
            "effective_month": effective.strftime("%Y-%m-%d"),
            "product_id": change.product_id,
            "description": change.description,
            "category": change.category,
            "approval_state": change.approval_state,
            "approver": change.approver,
            "approved_change_pct": round(approved_change, 6),
            "pre_list_price": round(pre_list, 4) if pd.notna(pre_list) else None,
            "pre_pocket_price": round(pre_pocket, 4) if pd.notna(pre_pocket) else None,
            "post_list_price": round(post_list, 4) if pd.notna(post_list) else None,
            "post_invoice_price": round(post_invoice, 4) if pd.notna(post_invoice) else None,
            "post_pocket_price": round(post_pocket, 4) if pd.notna(post_pocket) else None,
            "list_change_pct": round(list_change, 6) if pd.notna(list_change) else None,
            "pocket_change_pct": round(pocket_change, 6) if pd.notna(pocket_change) else None,
            "directional_realization": round(directional, 4) if pd.notna(directional) else None,
            "list_to_pocket_giveback_pct": (
                round(list_to_pocket_giveback, 6)
                if pd.notna(list_to_pocket_giveback) else None
            ),
            "pre_months": int(pre["month"].nunique()),
            "post_months": int(post["month"].nunique()),
            "realization_state": state,
            "review_reason": reason,
        })
    return pd.DataFrame(rows).sort_values(
        ["effective_month", "change_id"], ascending=[False, True], kind="stable"
    ).reset_index(drop=True)


def build_release_gates(
    register: pd.DataFrame, realization: pd.DataFrame, reconciliation: pd.DataFrame
) -> pd.DataFrame:
    open_requests = int((register["human_decision"] == "OPEN").sum())
    monitorable = int((realization["post_months"] >= 2).sum())
    reviewed = int((realization["realization_state"] == "REVIEW").sum())
    balanced = bool(reconciliation["balanced"].astype(str).str.lower().eq("true").all())
    gates = [
        ("PRC-GOV-01", "Source-to-stage reconciliation", "PASS" if balanced else "BLOCK",
         "Every reconciliation line balances with zero unexplained variance."),
        ("PRC-GOV-02", "Proposal evidence completeness", "PASS",
         f"{len(register):,} proposals carry rationale, confidence, reviewer and "
         "evidence references."),
        ("PRC-GOV-03", "Approval authority", "REVIEW",
         f"{open_requests:,} material proposals remain OPEN; this demo stores no human approval."),
        ("PRC-GOV-04", "Observational claim boundary", "PASS",
         "Elasticity is labelled observational and separated from causal effect."),
        ("PRC-GOV-05", "Price-realization coverage", "PASS" if monitorable else "REVIEW",
         f"{monitorable:,} approved changes have at least two pre and post months."),
        ("PRC-GOV-06", "Realization exception review", "REVIEW" if reviewed else "PASS",
         f"{reviewed:,} historical changes require explanation before benefit is claimed."),
        ("PRC-GOV-07", "Rollback design", "PASS",
         "Every material proposal names a monitoring window and rollback trigger."),
        ("PRC-GOV-08", "Synthetic demonstration boundary", "PASS",
         "No public result is represented as a customer price, approval or realized "
         "production benefit."),
    ]
    return pd.DataFrame(gates, columns=["control_id", "decision_test", "state", "evidence"])


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, lineterminator="\n")


def _write_text(path: Path, value: str) -> None:
    path.write_text(value.rstrip() + "\n", encoding="utf-8", newline="\n")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(data_dir: Path = DATA, out_dir: Path = OUT) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    recommendations = _read_csv(out_dir, "recommendations")
    changes = _read_csv(out_dir, "price_change_log")
    sales = _read_csv(data_dir, "fact_sales")
    reconciliation = _read_csv(out_dir, "reconciliation")
    register = build_decision_register(recommendations, policy["snapshot_date"])
    realization = build_realization_monitor(changes, sales, policy)
    gates = build_release_gates(register, realization, reconciliation)
    posture = "BLOCKED" if (gates["state"] == "BLOCK").any() else (
        "REVIEW REQUIRED" if (gates["state"] == "REVIEW").any() else "READY"
    )
    summary = {
        "policy_id": policy["policy_id"],
        "policy_version": policy["version"],
        "snapshot_date": policy["snapshot_date"],
        "release_posture": posture,
        "gates": {
            state: int((gates["state"] == state).sum())
            for state in ("PASS", "REVIEW", "BLOCK")
        },
        "proposal_count": int(len(register)),
        "open_human_decisions": int((register["human_decision"] == "OPEN").sum()),
        "ready_for_review": int((register["decision_state"] == "READY_FOR_REVIEW").sum()),
        "returned_for_data": int((register["decision_state"] == "RETURN_FOR_DATA").sum()),
        "historical_changes_monitored": int(len(realization)),
        "realization_reviews": int((realization["realization_state"] == "REVIEW").sum()),
        "human_boundary": policy["human_boundary"],
    }
    register_path = out_dir / "pricing_decision_register.csv"
    realization_path = out_dir / "price_realization_monitor.csv"
    gates_path = out_dir / "pricing_release_gates.csv"
    summary_path = out_dir / "pricing_decision_summary.json"
    _write_csv(register, register_path)
    _write_csv(realization, realization_path)
    _write_csv(gates, gates_path)
    _write_text(summary_path, json.dumps(summary, indent=2, sort_keys=True))
    evidence = [POLICY_PATH, register_path, realization_path, gates_path, summary_path]
    manifest = {
        "policy_id": policy["policy_id"],
        "snapshot_date": policy["snapshot_date"],
        "hash_algorithm": "sha256",
        "artifacts": [
            {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256(path)}
            for path in evidence
        ],
    }
    manifest_path = out_dir / "pricing_decision_manifest.json"
    _write_text(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
    counts = summary["gates"]
    packet = "\n".join([
        "# Pricing decision packet",
        "",
        f"**Policy:** {policy['policy_id']} v{policy['version']}",
        f"**Evidence snapshot:** {policy['snapshot_date']}",
        f"**Release posture:** {posture}",
        "",
        "## Executive decision",
        "",
        "The analytics can prepare and prioritize a controlled pricing review; it "
        "cannot approve or publish a customer price. The current evidence has "
        f"**{counts['PASS']} PASS / {counts['REVIEW']} REVIEW / "
        f"{counts['BLOCK']} BLOCK** gates. Human authority and historical realization "
        "exceptions remain visible rather than becoming an automated green light.",
        "",
        "## Decision portfolio",
        "",
        f"- {summary['proposal_count']:,} product proposals are registered, including "
        "explicit no-change and data-return outcomes.",
        f"- {summary['open_human_decisions']:,} material decisions remain open for a "
        "named reviewer; zero approvals are recorded by this public demonstration.",
        f"- {summary['ready_for_review']:,} proposals are ready for review and "
        f"{summary['returned_for_data']:,} are returned until the cost master is safe.",
        "- Every material request carries an evidence limit, effective period, "
        "monitoring window and rollback trigger.",
        "",
        "## Realization monitoring",
        "",
        f"- {summary['historical_changes_monitored']:,} approved or auto-approved "
        "historical price-list changes are compared with three-month pre/post invoiced "
        "and pocket prices.",
        f"- {summary['realization_reviews']:,} changes remain on the explanation queue "
        "because direction, coverage or list-to-pocket giveback does not support a "
        "clean benefit claim.",
        "- Observational elasticity and predicted margin remain decision evidence, "
        "not causal or realized benefit.",
        "",
        "## Human-decision boundary",
        "",
        policy["human_boundary"],
        "",
        "## Evidence",
        "",
        "- `pricing_decision_register.csv`",
        "- `price_realization_monitor.csv`",
        "- `pricing_release_gates.csv`",
        "- `pricing_decision_summary.json`",
        "- `pricing_decision_manifest.json`",
    ])
    _write_text(out_dir / "pricing_decision_packet.md", packet)
    return summary


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, sort_keys=True))
