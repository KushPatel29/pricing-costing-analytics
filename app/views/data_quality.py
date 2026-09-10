"""What the ERP extract actually contained, and whether the pipeline reconciles."""

from __future__ import annotations

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import streamlit as st

from app import shared as sh
from pricing.quality import failing_rows, traffic_light

sh.page()

st.title("Data quality and reconciliation")
sh.lede(
    "A pricing analyst inherits data from an ERP, and the ERP is not wrong on purpose "
    "-- it is wrong in six recognisable ways, and every one of them changes a price if "
    "nobody looks. A cost that never got maintained prices at margin over zero. A "
    "duplicated billing line doubles a customer's apparent volume and moves it into a "
    "better discount tier. A unit-of-measure mismatch is a price out by the pack size, "
    "and it reads as a bargain."
)

checks = sh.load("data_quality_checks")
score = sh.load("data_quality_score")
log = sh.load("staging_log")
reconciliation = sh.load("reconciliation")
summary = sh.load("staging_summary").set_index("metric")["value"]

overall = float(score.loc[score["dimension"] == "Overall", "score"].iloc[0])
light = traffic_light(overall)

LIGHT_COLOURS = {"Green": sh.GOOD, "Amber": sh.WARNING, "Red": sh.CRITICAL}

sh.kpis(
    [
        ("Overall quality", sh.pct(overall, 2), light, "off"),
        ("Checks passed", f"{int(summary['Checks passed'])} of {int(summary['Checks run'])}",
         None, "off"),
        ("Rows failing a check", sh.num(summary["Rows failing a check"]),
         f"of {summary['Billing lines received']:,.0f} billing lines", "off"),
        ("Lines held for master data", sh.num(summary["Lines held for master data"]),
         "kept, but not priceable", "off"),
    ]
)

if light == "Red":
    st.error(
        f"**{light}.** Thresholds here are high on purpose: at 98% of billing lines "
        "correct, a book this size still has hundreds of wrong ones, and each of them "
        "is a price somebody might set."
    )
elif light == "Amber":
    st.warning(f"**{light}.** Above the floor, below the standard this data needs.")
else:
    st.success(f"**{light}.**")

# --- Scorecard -----------------------------------------------------------
st.markdown("## The scorecard, by dimension")
sh.caption(
    "Organised by dimension -- completeness, validity, consistency, uniqueness, "
    "timeliness, accuracy -- because that is the vocabulary the finding has to be "
    "reported in, and a single score that mixes them tells nobody what to fix. "
    "Row-weighted, not rule-weighted: twelve rules of which one fails on a single row "
    "is not 92% quality."
)

score_left, score_right = st.columns([3, 2])
with score_left:
    plot = score[score["dimension"] != "Overall"].copy()
    plot["light"] = plot["score"].map(traffic_light)
    plot["label"] = plot["score"].map(lambda v: f"{v:.2%}")
    fig = sh.bar(plot, "dimension", "score", text="label",
                 colours=[LIGHT_COLOURS[t] for t in plot["light"]])
    fig.update_yaxes(tickformat=".1%", range=[min(0.9, float(plot["score"].min()) - 0.01), 1.0])
    sh.show(fig, 320, y_title="Rows passing")
    sh.caption(
        "Colour is a status, not a series, and every bar carries its number as a "
        "label -- a traffic light must never be the only thing carrying the meaning."
    )
with score_right:
    display = score.copy()
    display["Dimension"] = display["dimension"]
    display["Score"] = display["score"].map(lambda v: f"{v:.2%}")
    display["Light"] = display["score"].map(traffic_light)
    display["Checks"] = display.apply(
        lambda r: f"{int(r['checks_passed'])} of {int(r['checks'])}", axis=1)
    display["Rows failing"] = display["rows_failing"].map(lambda v: f"{v:,.0f}")
    sh.table(display[["Dimension", "Score", "Light", "Checks", "Rows failing"]])

# --- The findings --------------------------------------------------------
st.markdown("## Every check, and what it found")
sh.lede(
    "A rule reports rows, not a boolean. 'Data quality: 94%' is not actionable -- "
    "every finding here carries the offending keys, so the output is a work list "
    "rather than a score."
)

display = checks.copy()
display["Check"] = display["code"] + " - " + display["check"]
display["Severity"] = display["severity"]
display["Dimension"] = display["dimension"]
display["Table"] = display["table"]
display["Failing"] = display["rows_failing"].map(lambda v: f"{v:,.0f}")
display["Rate"] = display["fail_rate"].map(lambda v: f"{v:.2%}")
display["Status"] = display["passed"].map(lambda p: "Pass" if p else "Fail")
display["What to do"] = display["action"]
sh.table(display[["Check", "Dimension", "Severity", "Table", "Failing", "Rate",
                  "Status", "What to do"]], height=440)

failed = checks[~checks["passed"]]
if not failed.empty:
    st.markdown("### The offending rows")
    sh.caption("Pick a check to see the keys behind it. This is the work list.")
    chosen = st.selectbox(
        "Check", failed["code"].tolist(),
        format_func=lambda c: f"{c} - {failed.set_index('code').loc[c, 'check']}")
    frames = {
        name: sh.load(name)
        for name in ("erp_billing_items", "erp_material_master",
                     "erp_customer_master", "erp_condition_records")
    }
    rows = failing_rows(frames, chosen, limit=200)
    if rows.empty:
        st.caption("No rows to show for that check.")
    else:
        sh.table(rows, height=300)
        total = int(failed.set_index("code").loc[chosen, "rows_failing"])
        st.caption(f"Showing up to 200 of {total:,}.")

# --- Staging -------------------------------------------------------------
st.markdown("## What the pipeline did about it")
sh.lede(
    "The order of the steps is the design. Deduplicate first, or a duplicated bad row "
    "gets counted twice in every defect statistic below it. Repair before dropping, "
    "because a unit-of-measure mismatch and an arithmetic mismatch are both recoverable "
    "and dropping them throws away real revenue. Drop last, and only what genuinely "
    "cannot be used."
)

display = log.copy()
display["Step"] = display["step"]
display["Action"] = display["action"]
display["Rows"] = display["rows"].map(lambda v: f"{v:,.0f}")
display["Value"] = display["value"].map(lambda v: sh.money(v, 2))
display["Why"] = display["note"]
sh.table(display[["Step", "Action", "Rows", "Value", "Why"]])

# --- Reconciliation ------------------------------------------------------
st.markdown("## And whether it reconciles")
sh.lede(
    "A pipeline that quietly drops bad rows and reports a clean total has hidden the "
    "problem in the direction that flatters it. The difference between the extract and "
    "the staged data is itemised here, and an unexplained remainder is a failure rather "
    "than a rounding note."
)

recon_left, recon_right = st.columns([3, 2])
with recon_left:
    steps = reconciliation.copy()
    steps["kind"] = ["total"] + ["decrease"] * (len(steps) - 2) + ["total"]
    steps["label"] = steps["line"]
    sh.show(sh.waterfall(steps, label="label", value_fmt="si"), 360)
with recon_right:
    display = reconciliation.copy()
    display["Line"] = display["line"]
    display["Amount"] = display["amount"].map(lambda v: sh.money(v, 2))
    sh.table(display[["Line", "Amount"]])
    balanced = bool(reconciliation["balanced"].iloc[0])
    unexplained = float(reconciliation["unexplained"].iloc[0])
    if balanced:
        st.success(
            f"Reconciles. Unexplained difference {sh.dollars(unexplained)} on "
            f"{sh.money(summary['Value received'], 2)} received -- float noise, not a "
            "missing exclusion."
        )
    else:
        st.error(
            f"Does not reconcile: {sh.dollars(unexplained)} unexplained. Every step "
            "that removes value has to name itself, and one of them has not."
        )

sh.footer()
