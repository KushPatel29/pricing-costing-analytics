"""Governed pricing requests, approval boundary and realization monitoring."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app import shared as sh

ROOT = Path(__file__).resolve().parents[2]
sh.page()

summary = json.loads((ROOT / "output" / "pricing_decision_summary.json").read_text())
policy = json.loads((ROOT / "governance" / "pricing_decision_policy.json").read_text())
gates = sh.load("pricing_release_gates")
register = sh.load("pricing_decision_register")
realization = sh.load("price_realization_monitor")
manifest = json.loads((ROOT / "output" / "pricing_decision_manifest.json").read_text())

st.title("Pricing decision room")
sh.lede(
    "Move from a price recommendation to a controlled review, then compare the "
    "approved list move with what reached invoice and pocket price. This public "
    "surface prepares evidence; it cannot approve or publish a customer price."
)

posture = summary["release_posture"]
st.warning(
    f"**{posture}.** Analytics has prepared the case, but {summary['open_human_decisions']:,} "
    "material proposals still require named human authority. Zero approvals are stored here."
)
sh.kpis([
    (
        "Release gates",
        f"{summary['gates']['PASS']}/{summary['gates']['REVIEW']}/"
        f"{summary['gates']['BLOCK']}",
        "pass / review / block",
        "off",
    ),
    ("Product proposals", sh.num(summary["proposal_count"]), "including explicit no-change", "off"),
    ("Ready for review", sh.num(summary["ready_for_review"]), "not approved", "off"),
    (
        "Historical changes",
        sh.num(summary["historical_changes_monitored"]),
        f"{summary['realization_reviews']:,} need explanation",
        "off",
    ),
])

release_tab, request_tab, realization_tab, evidence_tab = st.tabs(
    ["Release decision", "Approval register", "Price realization", "Evidence map"]
)

with release_tab:
    st.markdown("## Is this portfolio ready for controlled review?")
    display = gates.rename(columns={
        "control_id": "Control", "decision_test": "Decision test",
        "state": "State", "evidence": "Evidence",
    })
    sh.table(display[["Control", "Decision test", "State", "Evidence"]], height=360)
    st.info(
        "**Decision boundary:** READY FOR REVIEW means the evidence packet can enter the "
        "delegated approval process. It does not mean the price is approved, communicated "
        "to a customer, or published to an ERP."
    )
    st.download_button(
        "Download decision packet",
        data=(ROOT / "output" / "pricing_decision_packet.md").read_text(encoding="utf-8"),
        file_name="pricing_decision_packet.md",
        mime="text/markdown",
    )

with request_tab:
    st.markdown("## One request, one owner, one explicit next state")
    left, right = st.columns(2)
    with left:
        state = st.multiselect(
            "Decision state", sorted(register["decision_state"].unique()),
            default=sorted(register["decision_state"].unique()),
        )
    with right:
        action = st.multiselect(
            "Action", sorted(register["action"].unique()),
            default=sorted(register["action"].unique()),
        )
    filtered = register[
        register["decision_state"].isin(state) & register["action"].isin(action)
    ].copy()
    filtered["Proposed change"] = filtered["price_change_pct"].map(sh.pct)
    filtered["Estimated margin"] = filtered["margin_delta"].map(lambda v: sh.money(v, 2))
    sh.table(filtered.rename(columns={
        "request_id": "Request", "description": "Product", "action": "Action",
        "decision_state": "State", "required_reviewer": "Reviewer",
        "confidence": "Evidence confidence",
    })[["Request", "Product", "Action", "State", "Reviewer", "Evidence confidence",
        "Proposed change", "Estimated margin"]], height=460)
    sh.caption(
        "Predicted margin is scenario evidence, not a booked benefit. The named reviewer "
        "must consider contracts, channel conflict, inventory and customer context."
    )

with realization_tab:
    st.markdown("## Did the approved move reach the pocket?")
    states = realization.groupby("realization_state", as_index=False).agg(
        changes=("change_id", "count")
    )
    fig = sh.bar(
        states, "realization_state", "changes", text="changes",
        colours=[sh.GOOD if x == "PASS" else sh.WARNING for x in states["realization_state"]],
    )
    sh.show(fig, 280, y_title="Historical approved changes")
    queue = realization.sort_values(
        ["realization_state", "list_to_pocket_giveback_pct"],
        ascending=[False, False], kind="stable"
    ).head(120).copy()
    queue["Approved"] = queue["approved_change_pct"].map(sh.pct)
    queue["Pocket move"] = queue["pocket_change_pct"].map(
        lambda v: "n/a" if pd.isna(v) else sh.pct(v)
    )
    queue["Directional realization"] = queue["directional_realization"].map(
        lambda v: "n/a" if pd.isna(v) else f"{v:.2f}x"
    )
    sh.table(queue.rename(columns={
        "change_id": "Change", "description": "Product",
        "realization_state": "State", "review_reason": "Why",
    })[["Change", "Product", "Approved", "Pocket move", "Directional realization",
        "State", "Why"]], height=460)
    st.info(
        "This is observational monitoring. A weak or reversed result starts an explanation "
        "workflow; it is not proof that the approved list change caused the outcome."
    )

with evidence_tab:
    st.markdown("## Authority, evidence and reproducibility")
    st.markdown(f"**Policy:** `{policy['policy_id']}` v{policy['version']}  ")
    st.markdown(f"**Decision owner:** {policy['decision_owner']}  ")
    st.markdown(f"**Risk owner:** {policy['risk_owner']}")
    st.markdown("### Never allowed")
    for item in policy["prohibited"]:
        st.markdown(f"- {item}")
    st.markdown("### Hash-verified release artifacts")
    evidence = pd.DataFrame(manifest["artifacts"]).rename(
        columns={"path": "Artifact", "sha256": "SHA-256"}
    )
    sh.table(evidence, height=260)
    sh.caption(
        "The manifest fingerprints the policy, decision register, realization monitor, "
        "gate docket and summary. CI rebuilds them before tests."
    )

sh.footer()
