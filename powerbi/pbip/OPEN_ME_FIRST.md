# Opening this in Power BI Desktop

`PricingAnalytics.pbip` is a Power BI project in the **PBIR** format: the report
is one JSON file per visual and the semantic model is TMDL, so both are
reviewable in a diff instead of being a binary blob.

## Before you open it

The model reads CSVs off disk through a parameter called **DataPath**, which is
written to an absolute path when the project is generated. On this machine it
points at the repository root. On yours it will not.

Either regenerate the project, which sets the path to wherever the repo is:

```bash
python -m seed.generate_market
python -m seed.generate_erp
python -m engine.build_pricing_analytics
python -m engine.stage_erp
python -m powerbi.build_pbip
```

or open `PricingAnalytics.SemanticModel/definition/expressions.tmdl` and edit
the one line at the top.

## Then

1. Open `PricingAnalytics.pbip` in Power BI Desktop (August 2024 or later; PBIR
   report authoring must be enabled under **Options → Preview features**).
2. **Refresh.** A `.pbip` stores the model *definition*, not its data. A project
   that opens with no "needs refreshing" bar can still have empty tables, and
   the visuals render blank and look finished.
3. Give it about half a minute after the notification bar clears. The visuals
   are still querying while it is gone, and a card captured too early shows
   `(Blank)` or last session's cached value.

## What is in it

Eighteen pages, 180 visuals, 54 tables, 204 measures, 23 relationships, plus six
parameter tables.

| Page | What it answers |
|---|---|
| Executive summary | Where the business stands, and what moved the margin |
| Price waterfall | List value to pocket margin, and which deduction costs the most |
| Competitive position | Our price against the market, and cost pass-through |
| Elasticity | Price response, and the volume a price change has to find |
| Cost variance | Purchase price, yield, labour and overhead against standard |
| Margin bridge | Price, cost, volume, mix, launches and losses |
| Price bands and WTP | What different customers pay, and what they would have paid |
| Deal guardrails | Floor, target and stretch, and who signs for the gap |
| Unit economics | Contribution, markup against margin, and the break-even curve |
| What-if simulator | Four parameters, three cases, and a tornado |
| Forecast against actual | Six methods chosen by backtest, with an empirical interval |
| Profitability and segmentation | Profit by any of six cuts, and the segment grid |
| Discount and promotion | What a point of discount costs, and which mechanic paid |
| Cost elements | Standard to actual, element by element |
| Pricing operations | Why prices moved, how long approval took, what went stale |
| Data quality | Twelve rules, and the reconciliation that balances |
| Exceptions and alerts | Red, amber, green, ranked by margin at risk |
| Recommendations | Increase, maintain, discount, bundle, fix cost or exit |

### The parameter tables

Four **what-if** parameters (price, cost, volume and discount change) are
calculated tables over `GENERATESERIES`, read back by `SELECTEDVALUE`. Two
**field** parameters are calculated tables of `NAMEOF()` references, so one
chart answers five questions rather than the page carrying five charts that
differ by one field.

All six are related to nothing, deliberately. A parameter joined to a fact
filters that fact to the rows matching the parameter's own value, which is the
exact opposite of what a what-if is for -- and it fails quietly, by showing a
smaller number rather than an error.

Every table in the model is a CSV the Python engine wrote. Nothing is computed
twice: a measure either aggregates a column the engine already produced or
divides two such aggregates. A DAX expression re-deriving "pocket margin" from
list price and seven deduction columns would be a second implementation of a
definition that already has tests, and the two drift the moment either is
edited.

## It is generated, not typed

Do not hand-edit the files under `PricingAnalytics.Report/` or
`PricingAnalytics.SemanticModel/`. They come from `powerbi/model_spec.py` and
`powerbi/report_spec.py` via `powerbi/build_pbip.py`, and CI runs
`python -m powerbi.build_pbip --check`, which regenerates into a temporary
directory and fails on any difference.

Saving from Desktop *will* rewrite some of them — Desktop normalises formatting
and rewrites `activePageName` to whatever page was on screen. That is fine while
you are exploring; just do not commit it. Change the spec instead.

## What is verified, and what is not

The tests in `tests/test_powerbi_model.py` and `tests/test_powerbi_report.py`
check every failure mode Power BI does not report:

- every measure names only columns that exist, in tables that are in the model;
- every visual binds a field that exists, with `queryRef` and `nativeQueryRef`
  agreeing with the expression they describe;
- every bound column also appears in the M query's type list (a column left out
  arrives as **text**, still binds, still renders, and sorts alphabetically);
- every numeric formatting literal carries its type suffix (`11D`, not `11`),
  which Desktop otherwise drops on the next save with no error;
- the "one" side of every relationship is actually unique, and the two sides
  actually share values;
- every file validates against the published JSON schema it names — those
  schemas set `additionalProperties: false`, which is what catches a mistyped
  property name;
- every parameter table is a `calculated` partition (a `GENERATESERIES` in an M
  partition is not a syntax error -- it is a table that fails at refresh with a
  message about an unknown function, on a model that opened cleanly), and every
  field parameter carries the `ParameterMetadata` extended property that is the
  entire difference between a field parameter and three columns of strings;
- no two visuals overlap, nothing falls off the 1280×720 canvas in either
  direction, and every slicer reaches something on its own page -- following
  measure references through, because a what-if slicer reaches the page only
  that way.

**Not verified:** nobody has opened this project in Power BI Desktop and looked
at it. Everything above is structural. The visual types used here
(`card`, `clusteredBarChart`, `clusteredColumnChart`, `columnChart`, `lineChart`,
`areaChart`, `scatterChart`, `donutChart`, `treemap`, `waterfallChart`,
`tableEx`, `pivotTable`, `slicer`) have all been seen to render from
hand-authored PBIR in sibling projects, and the role names are copied from
working examples rather than guessed — a scatter's identity field goes in the
role named `Category`, which is the field well Desktop labels "Details", and a
matrix takes `Rows`, `Columns` and `Values`. But structural validity is not the
same as looking right, and this file says so rather than implying otherwise.

The two features most worth a second look on first open are the **field
parameters** and the **theme**. If a field parameter's extended property were
wrong, the bar chart on the profitability page would draw the measure *names*
along its axis rather than their values — scaled, titled, and meaningless. And
the theme is the dark palette shared with the Streamlit app; if Desktop is
rendering the report on a light canvas, the custom theme has not been picked up
and the categorical hues are being shown on a surface they were not validated
against.
