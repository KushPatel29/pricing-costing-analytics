"""
Data quality and reconciliation, as rules rather than as spot checks.

A pricing analyst inherits data from an ERP, and the ERP is not wrong on
purpose -- it is wrong in six recognisable ways, and every one of them changes
a price if nobody looks. A cost that never got maintained prices at margin over
zero. A duplicated billing line doubles a customer's apparent volume and moves
it into a better discount tier. A unit-of-measure mismatch is a price out by a
factor of twenty and reads as a bargain.

So the checks here are organised by **dimension** -- completeness, validity,
consistency, uniqueness, timeliness, accuracy -- because that is the vocabulary
the finding has to be reported in, and because a score that mixes them tells
nobody what to fix.

Two things this module is careful about.

**A rule reports rows, not a boolean.** "Data quality: 94%" is not actionable.
Every finding here carries the offending keys, so the output is a work list.

**Reconciliation states its exclusions.** A pipeline that quietly drops bad rows
and reports a clean total has hidden the problem in the direction that flatters
it. :func:`reconcile` returns the difference *and* the rows that explain it, and
a difference it cannot explain is a failure rather than a rounding note.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from pricing import waterfall

# The six dimensions, in the order a data-quality report is normally read.
DIMENSIONS = ("Completeness", "Validity", "Consistency", "Uniqueness",
              "Timeliness", "Accuracy")

# Severity drives the traffic light, not the sort order -- findings are ranked
# by how many rows they touch and what they are worth, the same way guardrail
# exceptions are.
SEVERITIES = {"Critical": 1, "High": 2, "Medium": 3, "Low": 4}


@dataclass(frozen=True)
class Rule:
    """One check. ``failing`` returns the rows that break it."""

    code: str
    dimension: str
    severity: str
    table: str
    description: str
    failing: Callable[[Mapping[str, pd.DataFrame]], pd.DataFrame]
    action: str
    keys: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.dimension not in DIMENSIONS:
            raise ValueError(f"{self.code}: unknown dimension {self.dimension!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.code}: unknown severity {self.severity!r}")


def _safe(frames: Mapping[str, pd.DataFrame], name: str) -> pd.DataFrame:
    return frames.get(name, pd.DataFrame())


def default_rules() -> list[Rule]:
    """
    The checks this dataset earns. Each one exists because the raw extract
    contains the defect it looks for -- a rule that can never fire is a rule
    nobody maintains.
    """
    return [
        Rule(
            "DQ01", "Completeness", "Critical", "erp_billing_items",
            "Billing lines with no cost on the material master",
            lambda f: (
                _safe(f, "erp_billing_items")
                .merge(_safe(f, "erp_material_master")[["material", "standard_cost"]],
                       on="material", how="left")
                .pipe(lambda d: d[d["standard_cost"].isna() | (d["standard_cost"] <= 0)])
            ),
            "A missing standard cost prices at margin over zero. Get it maintained "
            "before the line is repriced.",
            keys=("billing_document", "material"),
        ),
        Rule(
            "DQ02", "Completeness", "High", "erp_billing_items",
            "Billing lines with no condition record, so no list price",
            lambda f: (
                _safe(f, "erp_billing_items")
                .merge(_safe(f, "erp_condition_records")[["material", "condition_value"]]
                       .drop_duplicates("material"),
                       on="material", how="left")
                .pipe(lambda d: d[d["condition_value"].isna()])
            ),
            "Without a condition record there is no list price to measure the "
            "discount against, so the line cannot enter the waterfall.",
            keys=("billing_document", "material"),
        ),
        Rule(
            "DQ03", "Uniqueness", "Critical", "erp_billing_items",
            "Duplicated billing lines",
            lambda f: (
                _safe(f, "erp_billing_items")
                .pipe(lambda d: d[d.duplicated(
                    subset=["billing_document", "item_number"], keep=False)])
            ),
            "A duplicate doubles the customer's apparent volume and can move it "
            "into a better discount tier. Deduplicate on document and item.",
            keys=("billing_document", "item_number"),
        ),
        Rule(
            "DQ04", "Validity", "High", "erp_billing_items",
            "Quantities that are zero or negative outside a credit note",
            lambda f: (
                _safe(f, "erp_billing_items")
                .pipe(lambda d: d[(d["billed_quantity"] <= 0)
                                  & (d["document_type"] != "Credit memo")])
            ),
            "A zero-quantity line divides by zero in every per-unit measure.",
            keys=("billing_document", "item_number"),
        ),
        Rule(
            "DQ05", "Consistency", "Critical", "erp_billing_items",
            "Unit of measure that does not match the material master",
            lambda f: (
                _safe(f, "erp_billing_items")
                .merge(_safe(f, "erp_material_master")[["material", "base_uom"]],
                       on="material", how="inner")
                .pipe(lambda d: d[d["sales_uom"] != d["base_uom"]])
            ),
            "A case billed as an each is a price out by the pack size, and it "
            "reads as a bargain rather than as an error.",
            keys=("billing_document", "material"),
        ),
        Rule(
            "DQ06", "Validity", "High", "erp_billing_items",
            "Net value that does not equal quantity times net price",
            lambda f: (
                _safe(f, "erp_billing_items")
                .pipe(lambda d: d[(d["net_value"]
                                   - d["billed_quantity"] * d["net_price"]).abs() > 0.05])
            ),
            "The extract's own arithmetic disagrees. Until it is reconciled, "
            "neither number can be used.",
            keys=("billing_document", "item_number"),
        ),
        Rule(
            "DQ07", "Consistency", "Medium", "erp_billing_items",
            "Customers on a billing line that the customer master has never heard of",
            lambda f: (
                _safe(f, "erp_billing_items")
                .pipe(lambda d: d[~d["sold_to_party"].isin(
                    set(_safe(f, "erp_customer_master").get("customer", pd.Series(dtype=str))))])
            ),
            "An orphan account cannot be segmented, so its margin lands in no "
            "cut of the book and quietly disappears.",
            keys=("billing_document", "sold_to_party"),
        ),
        Rule(
            "DQ08", "Timeliness", "Medium", "erp_condition_records",
            "Condition records not touched in over a year",
            lambda f: (
                _safe(f, "erp_condition_records")
                .pipe(lambda d: d[d["days_since_change"] > 365])
            ),
            "A price nobody has reviewed in a year, in a market whose input "
            "index moved. Put it on the review list.",
            keys=("condition_record", "material"),
        ),
        Rule(
            "DQ09", "Accuracy", "High", "erp_material_master",
            "Sellable rates outside anything a distribution centre produces",
            lambda f: (
                _safe(f, "erp_material_master")
                .pipe(lambda d: d[(d["recovery"] <= 0.30) | (d["recovery"] > 1.0)])
            ),
            "A sellable rate above 1.0 ships more units than were received, and "
            "a very low one triples the cost. Both are keying errors.",
            keys=("material",),
        ),
        Rule(
            "DQ10", "Validity", "Medium", "erp_billing_items",
            "Billing documents posted after the extract date",
            lambda f: (
                _safe(f, "erp_billing_items")
                .pipe(lambda d: d[pd.to_datetime(d["billing_date"])
                                  > pd.to_datetime(d["extract_date"])])
            ),
            "A future posting date is a test document or a keying error, and it "
            "lands in a period that has already been reported.",
            keys=("billing_document",),
        ),
        Rule(
            "DQ11", "Accuracy", "Medium", "erp_billing_items",
            "Lines invoiced below the material's standard cost",
            lambda f: (
                _safe(f, "erp_billing_items")
                .merge(_safe(f, "erp_material_master")[["material", "standard_cost"]],
                       on="material", how="inner")
                .pipe(lambda d: d[(d["standard_cost"] > 0)
                                  & (d["net_price"] < d["standard_cost"])])
            ),
            "Not always an error -- clearance and contractual loss-leaders exist "
            "-- but every one of these should be somebody's decision.",
            keys=("billing_document", "material"),
        ),
        Rule(
            "DQ12", "Completeness", "Low", "erp_customer_master",
            "Customer master rows with no sales district",
            lambda f: (
                _safe(f, "erp_customer_master")
                .pipe(lambda d: d[d["sales_district"].isna()
                                  | (d["sales_district"].astype(str).str.strip() == "")])
            ),
            "An account with no district cannot be reported regionally or "
            "assigned to a rep.",
            keys=("customer",),
        ),
    ]


def run_checks(
    frames: Mapping[str, pd.DataFrame], rules: Sequence[Rule] | None = None
) -> pd.DataFrame:
    """
    Run every rule and return one row per rule with its failure count.

    A rule that raises is reported as an error rather than skipped: a check
    silently not running is worse than a check failing, because the report
    still says the data is clean.
    """
    results = []
    for rule in rules or default_rules():
        table = _safe(frames, rule.table)
        total = len(table)
        try:
            failing = rule.failing(frames)
            count = len(failing)
            error = ""
        except Exception as problem:                     # noqa: BLE001 - reported
            count, error = -1, f"{type(problem).__name__}: {problem}"
        results.append(
            {
                "code": rule.code,
                "dimension": rule.dimension,
                "severity": rule.severity,
                "severity_rank": SEVERITIES[rule.severity],
                "table": rule.table,
                "check": rule.description,
                "rows_checked": total,
                "rows_failing": count,
                "fail_rate": (count / total) if total and count >= 0 else 0.0,
                "passed": count == 0,
                "action": rule.action,
                "error": error,
            }
        )
    frame = pd.DataFrame(results)
    return frame.sort_values(["severity_rank", "rows_failing"],
                             ascending=[True, False]).reset_index(drop=True)


def failing_rows(
    frames: Mapping[str, pd.DataFrame], code: str, rules: Sequence[Rule] | None = None,
    limit: int = 200,
) -> pd.DataFrame:
    """The actual offending rows for one rule, so the report is a work list."""
    for rule in rules or default_rules():
        if rule.code == code:
            failing = rule.failing(frames)
            columns = [c for c in rule.keys if c in failing.columns]
            return (failing[columns] if columns else failing).head(limit)
    return pd.DataFrame()


def quality_score(results: pd.DataFrame) -> pd.DataFrame:
    """
    A pass rate per dimension, plus one overall.

    Row-weighted rather than rule-weighted: twelve rules of which one fails on
    a single row is not 92% quality, and averaging the rules would say it was.
    """
    if results.empty:
        return pd.DataFrame(columns=["dimension", "checks", "checks_passed",
                                     "rows_checked", "rows_failing", "score"])
    grouped = (
        results.groupby("dimension", as_index=False)
        .agg(checks=("code", "count"),
             checks_passed=("passed", "sum"),
             rows_checked=("rows_checked", "sum"),
             rows_failing=("rows_failing", lambda s: s.clip(lower=0).sum()))
    )
    grouped["score"] = 1 - grouped["rows_failing"] / grouped["rows_checked"].replace(0, 1)
    overall = pd.DataFrame([{
        "dimension": "Overall",
        "checks": int(results["code"].count()),
        "checks_passed": int(results["passed"].sum()),
        "rows_checked": int(results["rows_checked"].sum()),
        "rows_failing": int(results["rows_failing"].clip(lower=0).sum()),
    }])
    overall["score"] = 1 - overall["rows_failing"] / max(int(overall["rows_checked"].iloc[0]), 1)
    ordered = pd.Categorical(grouped["dimension"], categories=DIMENSIONS, ordered=True)
    grouped = grouped.assign(dimension=ordered).sort_values("dimension")
    return pd.concat([grouped, overall], ignore_index=True)


def traffic_light(score: float, *, green: float = 0.995, amber: float = 0.98) -> str:
    """
    Green, Amber or Red. Thresholds are high on purpose: at 98% of billing
    lines correct, a book this size still has a thousand wrong ones.
    """
    value = float(score)
    if value >= green:
        return "Green"
    return "Amber" if value >= amber else "Red"


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------

def reconcile(
    source: pd.DataFrame,
    staged: pd.DataFrame,
    *,
    measure: str,
    source_measure: str | None = None,
    tolerance: float = 0.01,
    exclusions: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """
    Tie a staged total back to the extract it came from, with its exclusions.

    A pipeline that drops bad rows and reports a clean total has hidden the
    problem in the direction that flatters it. ``exclusions`` is what the
    cleansing removed and why; the reconciliation only balances if those
    account for the whole difference, and an unexplained remainder is a
    failure rather than a rounding note.
    """
    source_total = float(source[source_measure or measure].sum()) if len(source) else 0.0
    staged_total = float(staged[measure].sum()) if len(staged) else 0.0
    named = dict(exclusions or {})
    explained = sum(named.values())
    difference = source_total - staged_total
    unexplained = difference - explained
    return {
        "measure": measure,
        "source_total": source_total,
        "staged_total": staged_total,
        "difference": difference,
        "explained": explained,
        "unexplained": unexplained,
        "exclusions": named,
        "balanced": abs(unexplained) <= tolerance,
        "tolerance": tolerance,
    }


def reconciliation_rows(result: Mapping[str, Any]) -> pd.DataFrame:
    """The reconciliation as a statement, one line per movement."""
    running = float(result["source_total"])
    rows = [{"line": "Extract total", "amount": running, "running": running}]
    for name, amount in result["exclusions"].items():
        running -= float(amount)
        rows.append({"line": name, "amount": -amount, "running": running})
    running -= float(result["unexplained"])
    rows.append({"line": "Unexplained", "amount": -result["unexplained"],
                 "running": running})
    rows.append({"line": "Staged total", "amount": result["staged_total"],
                 "running": float(result["staged_total"])})
    return pd.DataFrame(waterfall.add_deltas(rows))
