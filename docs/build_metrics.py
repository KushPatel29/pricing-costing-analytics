"""
Write ``docs/METRICS.md`` from the measure spec itself.

Every number this project publishes has exactly one definition, and this is
where they are all written down together. Generated rather than maintained, for
the same reason the data dictionary is: a hand-kept metric list is wrong within
two commits and then actively misleading, because a reader trusts it more than
the model.

The DAX comes from ``powerbi.model_spec``; the prose around each group is here,
because "what does pocket margin mean" is a decision somebody made and not
something a tool can read off an expression.

CI runs this with ``--check`` and fails if the committed file has drifted.

Usage::

    python -m docs.build_metrics
    python -m docs.build_metrics --check
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import defaultdict
from pathlib import Path

from powerbi.model_spec import FIELD_PARAMETERS, MEASURES, WHATIF_PARAMETERS

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "METRICS.md"

# The conventions that change a number. Each of these has two defensible
# readings and only one is implemented; the whole point of writing them down is
# that a reader can disagree with the choice rather than with the arithmetic.
PREAMBLE = """# Metric reference

Every number this project publishes, with its definition. Generated from
`powerbi/model_spec.py` by `python -m docs.build_metrics`, and checked in CI, so
a measure renamed in the model is renamed here in the same commit.

The DAX below either aggregates a column the Python engine already computed or
divides two such aggregates. Nothing is calculated twice: a DAX expression
re-deriving "pocket margin" from list price and seven deduction columns would be
a second implementation of a definition that already has tests, and the two
would drift the moment either was edited. Where a definition is genuinely
computed, it lives in `pricing/` as a tested pure function and the module is
named in the group notes below.

## The seven conventions

These are the decisions that change a number rather than its presentation. Each
has two defensible readings; each is implemented one way, stated here, and
pinned by a test.

**1. Which price margin is on.** Pocket price — after every discount, rebate,
term, freight allowance and return. A margin quoted on list or invoice is the
number that lets a deal look healthy while losing money: at 18% leakage, a
15%-on-list margin is under 6% of what is kept.

**2. How discounts combine.** Additive on list, so 10% then 5% is 15% off, not
14.5%. Both conventions exist; quoting systems use the additive one because it
is what a salesperson means by "another five points".

**3. Margin or markup.** Margin is a fraction of *price*: a 25% margin on a $10
cost prices at $13.33, not $12.50. Using markup where margin is meant
underprices every item and never announces itself — the tool keeps working and
the gross margin just comes in light.

**4. Which way a variance points.** Positive is **unfavourable**, for all six
standard-costing variances. The opposite convention is equally common and makes
a variance pack unreadable to anyone who did not build it.

**5. What a percentile is weighted by.** Volume. A band computed over four
hundred small accounts and one large one otherwise describes the four hundred —
it answers "what do customers pay" when the question was "what does volume pay".

**6. What a subtotal bar moves.** Nothing. A waterfall plots each bar as a step
from the running total, so a subtotal carries a delta of zero. Bound to its own
absolute value it is added on top of the total it summarises, and the chart
closes at roughly twice the real figure — drawn, rescaled, and wrong with
nothing raised.

**7. When an optimal price is actionable.** `exists` and `actionable` are
separate fields. The constant-elasticity markup rule returns a finite,
mathematically correct price at five to fifteen times cost for elasticity
between −1.0 and −1.25, and nobody should act on it.

## The cost stack

The floor every price is built from, and the tool the project started as.

```
vendor invoice price
  ÷ units per billing UOM   →  actual invoice cost per unit
  + market adjustment       →  market cost
  + inbound freight by lane →  landed cost
  ÷ sellable rate           →  sellable input cost   ← shrink, damage, returns
  + handling + labelling    →  final cost
  ÷ (1 − margin)            →  base price
```

Implemented in `costing/formulas.py`. The sellable rate is a **divisor**: a
pallet of 100 units yielding 92 saleable ones means buying `1/0.92` units for
every unit sold, so cost is divided by it, not discounted by it.

## The price waterfall

```
list price
  − on-invoice discounts      → invoice price   (what the invoice says)
  − off-invoice deductions    → net price       (what accounting sees)
  − cost to serve             → pocket price    (what is actually kept)
  − final cost                → pocket margin   (what is actually earned)
```

Implemented in `pricing/waterfall.py`. Ten deductions across three buckets;
every one is extended by quantity before it is summed, because summing a
per-unit column weights a twelve-unit line the same as a twelve-thousand-unit
one.
"""

# One paragraph per display folder: what this group of measures is for, and
# where the definition behind it lives.
FOLDER_NOTES: dict[str, str] = {
    "01 Revenue": "The four price levels and the two margins, straight off the "
                  "invoice-line fact. Everything else on the report is one of "
                  "these cut a different way.",
    "02 Waterfall": "List value down to pocket margin. `Waterfall amount` is what "
                    "a step is *worth*; `Waterfall step` is what it *moves the "
                    "running total by*, which is zero on a subtotal — charts bind "
                    "the second, tables the first. See `pricing/waterfall.py`.",
    "03 Price": "Realised price per unit at each level, against unit cost. The "
                "gap between the list and pocket lines is the leakage, drawn.",
    "04 Comparison": "Period-over-period on `month_index` rather than a date "
                     "table: every fact here is monthly, and Power BI needs a "
                     "contiguous *daily* column to mark a date table — so the "
                     "marking would be rejected, or accepted against a column "
                     "with thirty-day gaps and quietly wrong.",
    "05 Competitive": "Our price over the market's, times 100. Observations are "
                      "weighted by freshness on a 45-day half-life and the "
                      "central tendency is the median, because an unweighted mean "
                      "gives a discount brand with 2% share the same vote as the "
                      "category leader. Pass-through is measured twice — against "
                      "list it is a decision, against pocket it is an outcome, and "
                      "the gap is what discounting handed back. "
                      "See `pricing/competitive.py`.",
    "06 Cost": "Standard-costing variances and budget against actual. Positive is "
               "unfavourable throughout. See `pricing/variance.py`.",
    "07 Bridge": "The year-on-year margin move split into price, cost, volume, "
                 "mix, new products and lost products. The six sum to the actual "
                 "change exactly; products present in only one year are pulled "
                 "out first, because there is no prior price to compare a launch "
                 "against and folding it into volume flatters the bridge.",
    "08 Elasticity": "`ln(q) = a + e·ln(p)`, whose slope *is* the elasticity. "
                     "Fitted on list price, promotional months excluded, "
                     "seasonality divided out, linear trend controlled for. "
                     "See `pricing/elasticity.py`.",
    "09 Bands": "The spread of pocket prices one product achieves across its "
                "customers, volume-weighted, plus the win curve fitted on won "
                "*and lost* quotes — transaction data contains no losses at all. "
                "See `pricing/segmentation.py`.",
    "10 Guardrails": "Floor, target and stretch on **pocket** price, with approval "
                     "tiers on the gap to target margin rather than on the "
                     "discount. See `pricing/guardrails.py`.",
    "11 Bundles": "Bundle economics judged on incremental margin. Setting it to "
                  "zero and solving for the cannibalisation rate gives "
                  "`c* = bundle margin / standalone margin` — the share of bundle "
                  "buyers who would have bought every component anyway, above "
                  "which the bundle loses money. See `pricing/bundles.py`.",
    "12 Labels": "Text measures used as card subtitles, so a card and the sentence "
                 "under it cannot disagree. These carry no format string by "
                 "design.",
    "13 What-if": "Four parameters read by `SELECTEDVALUE`, combined into a "
                  "scenario. Price and discount share one factor because they move "
                  "the realised price in opposite directions; keeping them apart "
                  "is how a simulator reports a price rise and a deeper discount "
                  "as though both were good news. See `pricing/scenario.py`.",
    "14 Unit economics": "Contribution, markup against margin, cost to serve, and "
                         "break-even in both units and revenue. See "
                         "`pricing/unit_economics.py`.",
    "15 Forecast": "Six methods, chosen per measure by rolling-origin backtest "
                   "rather than by whichever fit the history best. WAPE rather "
                   "than MAPE, because MAPE divides by the actual and one small "
                   "month dominates the average. The scoped `Revenue …` and "
                   "`Volume …` measures exist because the source holds four "
                   "measures in three different units, and anything that adds "
                   "across them reports dollars plus units plus dollars again. "
                   "See `pricing/forecast.py`.",
    "16 Profitability": "Eleven cuts of the same book down to operating profit. "
                        "The fixed allocation is by revenue share and the column "
                        "says so: every allocation is arbitrary, and the argument "
                        "is always about which arbitrary one was used.",
    "17 Discount": "What a point of discount costs, and whether a promotional "
                   "mechanic paid for the volume it bought.",
    "18 Cost elements": "The eight elements the cost stack is built from, standard "
                        "against actual.",
    "19 Operations": "Price-list maintenance: what moved, why, who approved it, "
                     "how long it took, and what has gone stale.",
    "20 Data quality": "Twelve rules across the six quality dimensions, run "
                       "against the raw ERP extract rather than the clean tables. "
                       "The score is row-weighted, not an average of the "
                       "per-dimension scores — twelve rules over wildly different "
                       "row counts average to a number that flatters whichever "
                       "rule ran over the smallest table. See `pricing/quality.py`.",
    "21 Recommendations": "Six ordered actions per product, each with what it is "
                          "worth and the confidence behind the *evidence* rather "
                          "than the size of the prize. See `pricing/recommend.py`.",
}

FORMAT_MEANING: dict[str, str] = {
    "": "text",
    "0": "integer",
    "#,0": "count",
    "0.00": "ratio",
    "0.000": "ratio",
    "#,0.0": "index",
    "0.0%": "percent",
    "0.00%": "percent",
    "\\$#,0": "currency",
    "\\$#,0.00": "currency",
}


def one_line(dax: str) -> str:
    """DAX collapsed for a table cell, with pipes escaped."""
    flat = re.sub(r"\s+", " ", dax).strip()
    return flat.replace("|", "\\|")


def build() -> str:
    by_folder: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for name, dax, fmt, folder in MEASURES:
        by_folder[folder].append((name, dax, fmt))

    lines = [PREAMBLE.rstrip(), ""]

    lines += [
        "## Parameters",
        "",
        "Calculated tables, not CSVs, and related to nothing on purpose: a "
        "parameter joined to a fact filters that fact to the rows matching the "
        "parameter's own value, which is the opposite of what a what-if is for.",
        "",
        "| Parameter | Column | Range | Step | Read by |",
        "|---|---|---:|---:|---|",
    ]
    for table, column, low, high, step, _fmt, measure in WHATIF_PARAMETERS:
        lines.append(
            f"| `{table}` | `{column}` | {low:+.0%} to {high:+.0%} | "
            f"{step:.3f} | `[{measure}]` |"
        )
    if FIELD_PARAMETERS:
        lines += ["", "Field parameters swap the measure a visual shows, so one "
                      "chart answers several questions instead of the page carrying "
                      "several charts that differ by one field.", ""]
        lines += ["| Field parameter | Column | Offers |", "|---|---|---|"]
        for table, column, entries in FIELD_PARAMETERS:
            offers = ", ".join(f"`{label}`" for label, _ in entries)
            lines.append(f"| `{table}` | `{column}` | {offers} |")
        lines.append("")

    total = len(MEASURES)
    lines += [
        f"## The {total} measures",
        "",
        "Grouped as they appear in the Power BI field list.",
        "",
    ]

    for folder in sorted(by_folder):
        measures = sorted(by_folder[folder])
        lines += [f"### {folder}", ""]
        note = FOLDER_NOTES.get(folder)
        if note:
            lines += [note, ""]
        lines += [
            f"{len(measures)} measure{'s' if len(measures) != 1 else ''}.",
            "",
            "| Measure | Shows | Definition |",
            "|---|---|---|",
        ]
        for name, dax, fmt in measures:
            kind = FORMAT_MEANING.get(fmt, fmt or "text")
            lines.append(f"| **{name}** | {kind} | `{one_line(dax)}` |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)

    content = build()
    if args.check:
        if not TARGET.exists():
            print(f"{TARGET} does not exist. Run: python -m docs.build_metrics",
                  file=sys.stderr)
            return 1
        committed = TARGET.read_text(encoding="utf-8")
        if committed != content:
            print("docs/METRICS.md is out of date.\n"
                  "run: python -m docs.build_metrics\n", file=sys.stderr)
            diff = difflib.unified_diff(
                committed.splitlines(), content.splitlines(),
                fromfile="committed", tofile="regenerated", lineterm="", n=1,
            )
            for line in list(diff)[:40]:
                print(line, file=sys.stderr)
            return 1
        print(f"metric reference matches the model ({len(MEASURES)} measures)")
        return 0

    with open(TARGET, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    print(f"wrote {TARGET} ({len(MEASURES)} measures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
