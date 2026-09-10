# Pricing and Costing Analytics

[![CI](https://github.com/KushPatel29/cost-to-price-calculator/actions/workflows/ci.yml/badge.svg)](https://github.com/KushPatel29/cost-to-price-calculator/actions/workflows/ci.yml)
![tests](https://img.shields.io/badge/tests-2356-brightgreen)
![python](https://img.shields.io/badge/python-3.12-blue)

**Live app:** [cost-to-price-calculator.streamlit.app](https://cost-to-price-calculator.streamlit.app/)

A pricing analyst's working set for a multi-category B2B wholesale distributor:
seventeen Streamlit pages, a Power BI project of eighteen pages and 180 visuals,
and three complete fiscal years of generated market, transaction, cost and ERP
data underneath both.

It started as one tool — walk a product from a vendor invoice to a selling
price, and push the result back into the ERP's price list. That tool is still
here, unchanged in what it does. Everything around it is the rest of the job:
what the market charges, what the customer actually pays after every deduction,
how much volume moves when the price does, which of last year's margin miss was
price rather than mix, and what to do about any of it on Monday.

```bash
pip install -r requirements.txt
streamlit run app/streamlit_app.py
```

It opens on generated data, so there is nothing to prepare.

---

## The business

**Meridian Supply Co** — a wholesale distributor across eight categories:
consumer electronics, home and kitchen, office and stationery, tools and
hardware, health and beauty, sporting goods, pet supplies, apparel. 240 items in
four brand tiers, 150 customers across eight segments from marketplace sellers
to government and education, twelve salespeople, five competitors, six inbound
lanes from ocean LCL to domestic FTL.

Where the numbers land on the generated book:

| | |
|---|---:|
| Pocket revenue | $260.4M |
| Pocket margin | 22.4% |
| Revenue leakage | 18.2% of list |
| Price-band realisation opportunity | $10.5M |
| Margin at risk on guardrail breaches | $4.1M |
| Purchase price variance | −$5.6M |
| Data quality score | 98.33% |
| Recommended actions worth | $1.73M of annual margin |

---

## The cost stack

The original tool, and still the floor everything else is built on.

```
vendor invoice price
  ÷ units per billing UOM   →  actual invoice cost per unit
  + market adjustment       →  market cost
  + inbound freight by lane →  landed cost
  ÷ sellable rate           →  sellable input cost   ← shrink, damage, returns
  + handling + labelling    →  final cost
  ÷ (1 − margin)            →  base price
```

Two of those steps are where this kind of tool usually goes wrong.

**The sellable rate is a divisor, not a discount.** A pallet of 100 units that
yields 92 saleable ones after damage, shrink and returns has a sellable rate of
0.92. You have to buy `1/0.92` units for every unit you sell, so cost is
*divided* by it. At 60% sellable a $10 input costs $16.67; at 90% it costs
$11.11.

**Margin is a fraction of price, not of cost.** At a 25% margin a $10 cost
prices at `10 / 0.75 = $13.33`, not `10 × 1.25 = $12.50`. Using markup where
margin is meant underprices every item, and it never announces itself — the tool
keeps working, the gross margin just comes in light.

There is a page in the app for exactly this argument, because in practice the
disagreement is never about the arithmetic. It is about which of the two
somebody meant by "we work on 30".

---

## The pricing stack

Cost tells you the floor. It tells you nothing about the price. `pricing/` is
twelve modules of pure functions — lists and floats in, dicts out — that answer
the rest. Every one is importable without Streamlit and tested without it.

### The price waterfall — `pricing/waterfall.py`

The headline discount is never the whole discount. A customer quoted "8% off
list" also takes a quarterly rebate, an early-payment term, freight we absorb
and a returns allowance, none of which appear on the invoice the salesperson is
looking at.

```
list price
  − on-invoice discounts      → invoice price   (what the invoice says)
  − off-invoice deductions    → net price       (what accounting sees)
  − cost to serve             → pocket price    (what we actually keep)
  − final cost                → pocket margin   (what we actually earn)
```

On the generated book that gap runs to **18.2% of list**. Margin is expressed on
pocket, not on list, because a margin quoted on list is the number that lets a
deal look healthy while losing money: at 20% leakage a 15%-on-list margin is
under 6% on what we keep.

Discounts are additive on list, not compounding — 10% then 5% is 15% off, not
14.5%. Both conventions exist; quoting systems use the additive one because it
is what a salesperson means by "another five points". The convention is stated
once and pinned by a test.

Each bar also carries the amount it *moves* the running total by, which is zero
on a subtotal. Waterfall charts plot steps, so a subtotal bound to its own
absolute value is added on top of the total it summarises and the chart closes
at roughly twice the real figure — drawn, rescaled, and wrong with nothing
raised anywhere.

### Elasticity and the optimal price — `pricing/elasticity.py`

`ln(q) = a + e·ln(p)`, whose slope *is* the elasticity, because the derivative
of a log is a percentage change. Fitted on list price, with promotional months
excluded, seasonality divided out and a linear trend controlled for. Each of
those four is a correction for a specific confound, and each one moves the
answer.

Three things the module refuses to do:

- **Guess when there is nothing to fit.** Thin samples and flat prices come back
  `usable: False` with the reason in plain words, not as a confident-looking
  slope from four points.
- **Print a price nobody should act on.** The markup rule `p* = c·e/(e+1)` has
  no interior solution above −1, and between −1.0 and −1.25 it returns a
  mathematically correct price at five to fifteen times cost. `exists` and
  `actionable` are separate fields for exactly that reason.
- **Hide the volume hurdle.** Cutting price by `d` on a contribution margin of
  `m` needs volume up by `d/(m−d)` just to stand still. Five points off a 30%
  margin needs +20% volume; off a 15% margin it needs +50%.

### Competitive position and pass-through — `pricing/competitive.py`

A price index is our price over the market's, times 100. The two ways it lies
are both about what "the market" means: an unweighted mean gives a discount
brand with 2% share the same vote as the category leader, and a shelf price
scraped in March is not evidence about June. So observations are weighted by
freshness with a 45-day half-life, the default central tendency is the median,
and the index reports the age of the data it used.

Pass-through is measured **twice**: against list price it is a decision — how
much of an input move the last review passed on — and against realised pocket
price it is an outcome. The gap between them is the share of an announced
increase that discounting handed straight back.

It is estimated on **quarterly** changes, because that is how often prices are
reviewed. Differencing monthly between two reviews measures customer mix: on
this data the monthly fit explained 7% of the variation and the quarterly fit
59%, and the monthly coefficient for the one index whose price barely tracks it
came out at −2.15 — a number that says the seller cut price into a rising
market, and means only that there was nothing to fit.

### Unit economics and break-even — `pricing/unit_economics.py`

Contribution, contribution per unit, cost to serve, markup against margin, and
break-even in both units and revenue. The break-even curve is a volume grid with
fixed, variable and total cost against revenue, which is the chart everyone
draws on a whiteboard and almost nobody has to hand.

Operating leverage comes out of the same numbers and is the reason two products
at the same margin are not the same product: the one carrying more fixed cost
gains more on the way up and loses more on the way down.

### Cost variance and the margin bridge — `pricing/variance.py`

Standard costing variances — purchase price, yield, labour rate and efficiency,
overhead spending and volume — all signed the same way: **positive is
unfavourable**. That one convention is what makes a variance pack readable by
someone who did not build it.

The margin bridge decomposes a year-on-year move into **price, cost, volume,
mix, new products and lost products**, and the six sum to the actual change
exactly — the residual is reported so you can check, and a test asserts it stays
at float-noise scale. Products present in only one year are pulled out before the
price/volume/mix split runs: there is no prior price to compare a launch
against, and folding it into "volume" is how a launch flatters a bridge.

### Scenarios — `pricing/scenario.py`

Best, base and worst; a tornado ranking each input by how far moving it alone
moves operating profit; a two-input sensitivity grid; and the value at which each
input drives profit through zero, with a stated reason where it never does.

The three-point case says out loud that it is every assumption at its own end at
once — a stress test, not an interval. Presenting three numbers as though the
middle one were a forecast is the failure this is written against.

### Forecasting — `pricing/forecast.py`

Six methods, chosen per measure by **rolling-origin backtest** rather than by
whichever fit the history best. WAPE rather than MAPE, because MAPE divides by
the actual and a single small month can dominate the average. The interval is
empirical, taken from backtest residuals, rather than assumed normal.

### Bundles — `pricing/bundles.py`

A bundle at 12% off that sells 400 units looks like a win until you ask how many
of those 400 customers were going to buy every component anyway. Setting
incremental margin to zero and solving for the cannibalisation rate gives

```
c* = bundle margin / standalone margin
```

so a bundle keeping 80% of standalone margin survives up to 80%
cannibalisation, and one discounted to 55% dies above 55%. That turns "is this
bundle a good idea" into a question about the customer base, which somebody in
sales can actually answer.

### Guardrails — `pricing/guardrails.py`

The part that has to survive contact with a salesperson at ten to five on a
Friday. The floor is on **pocket** price, not list — a rule saying "no more than
15% off list" says nothing about rebates, freight or terms, which is where the
margin actually goes. Approval tiers are on the **gap to target margin**, not on
the discount: two deals at 10% off are not the same deal when one product carries
38 points of margin and the other 19.

The exception scan ranks by margin at risk and colours by *when the money moves*:
red is margin leaving on today's invoices, amber is a price off its position,
green is a leading indicator with nothing lost yet. Ranking by dollars inside a
colour is what makes the list workable; colouring by dollars would put a large
stale listing above a small loss-making one, which is the wrong morning.

### Price bands and willingness to pay — `pricing/segmentation.py`

The spread of pocket prices one product achieves across its customers is the
*price band*, and its width is the most reliable margin opportunity in any book
of business. The realisation gap values moving every below-median line up to its
own product's volume-weighted median — a price half that product's volume
already pays — which is the conservative version that survives a room containing
the salespeople who own those accounts.

Willingness to pay comes from won and lost quotes, because transaction data
contains no losses at all and a curve fitted to it is fitted entirely to prices
customers accepted. A logistic on the ratio of our quote to the competing one,
solved by Newton–Raphson in about forty lines, returns the ratio at which we win
half the time.

### Data quality and reconciliation — `pricing/quality.py`

Twelve rules across the six quality dimensions — completeness, validity,
consistency, uniqueness, timeliness, accuracy — run against the raw ERP extract
rather than against the clean tables, which is the only way either of them means
anything. The score is **row-weighted**: twelve rules over wildly different row
counts average to a number that flatters whichever rule ran over the smallest
table.

The reconciliation walks the extract total down to the staged total with every
exclusion named, and balances to $0.00. A staging step that drops rows without
saying so is how a dashboard ends up 3% below the general ledger and nobody can
say which 3%.

### Recommendations — `pricing/recommend.py`

Six ordered actions — fix cost, discontinue, increase, discount, bundle,
maintain — each with the price it implies, what it is worth, the confidence
behind it and a rationale in the words an analyst would use in the meeting. On
the generated book: 111 increases worth $1.7M, 102 maintains, 16 items that
cannot be priced at all until costing maintains a standard cost for them, 7
bundle candidates, 2 discounts and 2 exits.

"Maintain" is a real answer here, not a fallthrough. An item priced above the
market on demand inelastic enough that coming back would cost more volume than
it buys gets a *defend the premium* rationale, not a discount.

---

## The data

Three complete fiscal years, July 2023 to June 2026. Complete matters: a range
that stops mid-year makes every year-on-year comparison a 277-day period against
a 365-day one, and the resulting "sales are down 24%" is an artefact of the
calendar.

| Table | Rows | What it is |
|---|---:|---|
| `dim_product` | 240 | Catalogue: category, sub-category, brand tier, pack, lifecycle |
| `dim_customer` | 150 | 8 segments, 3 channels, 5 regions, 4 volume tiers |
| `dim_salesperson` | 12 | Region, segment focus, and a discount appetite |
| `dim_competitor` | 5 | Discount, mainstream and premium positions |
| `dim_month` | 36 | Fiscal calendar with a contiguous month index |
| `fact_sales` | 51,670 | Invoice lines with all ten waterfall deductions |
| `fact_cost_element` | 48,528 | Standard against actual for eight cost elements |
| `fact_competitor_price` | 13,890 | Partial, uneven competitive coverage |
| `fact_price_cost_panel` | 8,640 | Monthly cost and price per product |
| `fact_cost_ledger` | 7,004 | Purchases and sellable rates against standard |
| `fact_quote` | 5,600 | Won and lost, with the competing price |
| `fact_price_change` | 2,277 | The price-list change log, with approvals |
| `fact_commodity_index` | 1,422 | Eight weekly input indices |
| `fact_promotion` | 1,026 | Five mechanics, with depth and duration |
| `fact_budget`, `fact_overhead`, `fact_fixed_cost` | 612 | Plan, absorption, fixed pools |

### And the same business as an ERP would hand it over

`raw/` is the SAP SD shape — billing documents and items, sold-to party,
material, base UOM, condition records — with **defects injected on purpose**:
missing standard costs, missing condition records, duplicated lines, negative
quantities, UOM mismatches, value mismatches, orphan customers, implausible
sellable rates, postings dated after the extract, and missing districts.

| Table | Rows |
|---|---:|
| `erp_billing_items` | 17,643 |
| `erp_material_master` | 240 |
| `erp_customer_master` | 150 |
| `erp_condition_records` | 234 |

Every one of the twelve quality rules finds something, which is the point: a
quality page where every check is green has not been tested, it has been fitted.
`engine/stage_erp.py` stages the extract, runs the rules, and reconciles — the
staged total is $259,859,112.25 against an extract of $261,314,697.02, with all
four exclusions named and $0.00 unexplained.

### It is a generative model, not a shuffle of random columns

That is the point: the relationships the analysis claims to find are put in
deliberately, so finding them is a real test of the code.

- Input costs follow a commodity index with its own drift, volatility and
  seasonality. List prices follow it at a **partial pass-through and a lag**, so
  margin compresses between reviews in a rising market and recovers at one.
- Volume responds to price at a **known elasticity per category**.
- Price reviews are **staggered** across the catalogue, with per-review
  idiosyncratic judgement. Without that, price moves only with time, `ln(p)` is
  collinear with the trend, and no elasticity is recoverable at all — the fits
  came back at r² 0.01 and estimates off by a factor of two.
- Standard cost freezes each July while actual cost keeps moving, so **purchase
  price variance builds through the year and resets** at the boundary.
- Discounts are a function of customer tier, channel, terms and the
  salesperson's own appetite, so the **price band for one product is wide and
  explicable** rather than wide and random.
- Customers buy from two or three **core categories**, so the products bought
  together are products that belong together.
- Quotes are won and lost on a logistic in price ratio with a **segment-specific
  sensitivity**.

`tests/test_generated_data.py` takes those relationships back out through the
real analysis code. Estimated category elasticities preserve the seeded ordering
at a rank correlation of **0.86** across eight categories (Consumer Electronics
most elastic, Pet Supplies least); the recovered segment price sensitivities
preserve theirs; all eight pass-through estimates are usable and land in the
0.24–0.64 band the indices were seeded across.

Every table and every column is listed in
[`docs/DATA_DICTIONARY.md`](docs/DATA_DICTIONARY.md), which is generated from the
CSVs and checked in CI — a hand-maintained data dictionary is wrong within two
commits and then actively misleading, because a reader trusts it more than the
file.

Everything is reproducible from a fixed seed and reads neither the clock nor the
network.

```bash
python -m seed.generate_market            # data/
python -m seed.generate_erp               # raw/
python -m engine.build_pricing_analytics  # output/
python -m engine.stage_erp                # output/ quality, reconciliation
python -m engine.run_sql                  # output/ the SQL marts
python -m seed.generate_sheets            # sample_data/*.xlsx
```

**One catalogue, four consumers.** Item code 20017 is the same product in the
cost sheet, in `data/dim_product.csv`, in the ERP extract and in the Power BI
model. That took a defect to get right: the panel used to ride the shared random
stream, downstream of customer generation, so the cost sheet — which rebuilds it
alone — landed on a different draw. Codes matched, descriptions matched, and the
costs were quietly unrelated. Both the catalogue and the panel now have their own
derived streams, and a test compares the workbook against the model.

---

## The SQL layer

`sql/pricing_marts.sql` computes six of the marts a second time in DuckDB —
waterfall, profitability with `GROUPING SETS`, price bands with
`PERCENTILE_CONT`, a monthly trend with `LAG` at one and twelve months, margin
concentration with a running share, and the exception list.

Two implementations that agree are worth more than one that is merely asserted.
`tests/test_sql_matches_python.py` holds every mart to the pandas version at a
**relative** tolerance of 1e-8 — relative, because the two engines sum the same
numbers in different orders and differ in the last bits of a float, so an
absolute threshold is a number tuned to one dataset that fails on the next
regeneration for a reason nobody can act on.

---

## The app

Seventeen pages behind `st.navigation`, grouped the way the work is.

| Group | Page | What it answers |
|---|---|---|
| Overview | Executive summary | Where the business stands, and what moved the margin |
| | Recommendations | Increase, maintain, discount, bundle, fix cost or exit |
| Data | Data quality and reconciliation | Whether any of the rest can be trusted |
| Cost | Cost and variance analysis | Purchase price, yield, labour and overhead against standard |
| | Unit economics and break-even | Contribution, markup against margin, break-even volume |
| | Cost-to-price calculator | The original tool: reprice a book, push it back to the ERP |
| Market | Market and competitor benchmarking | Our price against the market, and cost pass-through |
| | Price bands and willingness to pay | What customers pay for the same thing, and what they'd have paid |
| Profitability | Profitability and segmentation | Profit by product, customer, region, channel and salesperson |
| | Price waterfall | List to pocket, and which deduction costs the most |
| | Margin bridge | Price, cost, volume, mix, launches and losses |
| Modelling | Pricing simulator | Move six assumptions and watch the operating profit |
| | Elasticity and optimal price | Price response, and the volume a cut has to find |
| | Forecast against actual | Six methods, chosen by backtest, with an empirical interval |
| | Bundles and ladders | Bundle economics judged on incremental margin |
| Execution | Deal guardrails | Floor, target and stretch, and who signs for the gap |
| | Promotions and price-list operations | Which mechanic paid, and what has gone stale |

Charts use one fixed categorical palette, assigned by slot and never cycled. The
app is dark, and the palette is the **dark steps** of the same eight hues —
re-stepped for the `#141416` surface and re-validated against it: worst adjacent
colour-vision ΔE 8.4, worst adjacent normal-vision ΔE 19.3, all eight clear 3:1.
Inverting a light palette instead is the failure this guards against; every hue
comes out too dark to separate and nothing reports it, because the chart still
draws.

The theme is set in `.streamlit/config.toml` rather than in CSS, with one
`[theme]` table and no light/dark variants. The variants let the *visitor's*
operating system pick a surface, and only one surface here has a validated
palette on it. It has to be config rather than CSS because `st.dataframe`
renders to a canvas from the theme object in JavaScript — no stylesheet reaches
inside it, and getting that wrong leaves every table a white block in a dark app.

Every view is executed headlessly in CI by `tests/test_app_views_render.py`.
That gate found eight pages that raised on load the first time it ran — four
selecting a column before renaming it into existence, one indexing on a
description that is not unique, one merging text against integers, and two
loaders — none of which any of the maths tests could see.

---

## The dashboard

`powerbi/pbip/PricingAnalytics.pbip` — PBIR format, so the report is one JSON
file per visual and the model is TMDL, both reviewable in a diff. **Eighteen
pages, 180 visuals, 54 tables, 204 measures, 23 relationships**, plus six
parameter tables. See
[`powerbi/pbip/OPEN_ME_FIRST.md`](powerbi/pbip/OPEN_ME_FIRST.md).

It is **generated**, from `powerbi/model_spec.py` and `powerbi/report_spec.py`.
Typing a hundred and eighty visual JSON files by hand is how a report ends up
carrying three different `visualContainer` schema versions and a property name
Desktop silently drops on the next save. CI runs
`python -m powerbi.build_pbip --check`, which regenerates into a temp directory
and fails on any difference.

Four things in it are worth the name "advanced":

- **What-if parameters** — four calculated tables over `GENERATESERIES`, read
  back by `SELECTEDVALUE`, driving a scenario page. Price and discount share one
  factor, because they move the realised price in opposite directions and
  keeping them apart is how a simulator reports a price rise and a deeper
  discount as if both were good news.
- **Field parameters** — calculated tables of `NAMEOF()` references, so one
  chart answers five questions instead of the page carrying five charts that
  differ by one field. The `ParameterMetadata` extended property is the entire
  mechanism; without it the visual draws the measure *names* along an axis, and
  a test asserts it is there.
- **A real matrix**, with Rows, Columns and Values, for the segment-by-category
  profitability grid and the discount-to-margin cross-tab. A matrix with no
  Columns role is a table with extra chrome.
- **Deliberate disconnection.** Thirty-eight tables are unrelated on purpose,
  each with the reason written into the spec beside it, because a disconnected
  table is usually a modelling mistake and these are not.

Nothing is computed twice: a measure either aggregates a column the Python
engine already produced or divides two such aggregates. A DAX expression
re-deriving "pocket margin" from list price and seven deduction columns would be
a second implementation of a definition that already has tests, and the two
drift the moment either is edited.

Power BI fails **silently** on a report definition, so the tests check the things
it will not tell you about:

- every measure names only columns that exist, in tables that are in the model;
- every visual binds a field that exists, with `queryRef` and `nativeQueryRef`
  agreeing with the expression they describe;
- every bound column also appears in the M query's type list — a column left out
  arrives as text, still binds, still renders, and sorts alphabetically;
- every numeric formatting literal carries its type suffix (`11D`, not `11`),
  which Desktop otherwise drops on the next save with no error;
- every `VAR` is prefixed `v` + a capital, because Power BI reserves far more
  words for VAR names than the four that are documented and there is no
  published list — a measure that trips one renders "Something's wrong with one
  or more fields" at runtime on a report that built cleanly;
- every parameter table is a `calculated` partition and is related to nothing,
  because a parameter joined to a fact filters that fact to the parameter's own
  value, which is the opposite of what a what-if is for;
- the "one" side of every relationship is unique and the two sides share values;
- nothing falls off the canvas, in either direction — a 76px slicer whose top is
  at y=712 on a 720px page shows eight pixels of itself and reads as a missing
  filter rather than a clipped one. Two of them did;
- every slicer reaches something on its own page, following measure references
  through, because a what-if slicer reaches the page only that way;
- every visual carries alt text that names a field it actually binds;
- every file validates against the published JSON schema it names, and those
  schemas set `additionalProperties: false`, which is what catches a typo.

**Not verified:** nobody has opened the project in Power BI Desktop and looked at
it. Everything above is structural, and structural validity is not the same as
looking right.

---

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate     # Windows
python -m venv .venv && source .venv/bin/activate  # macOS / Linux
pip install -r requirements.txt

python -m seed.generate_market
python -m seed.generate_erp
python -m engine.build_pricing_analytics
python -m engine.stage_erp
streamlit run app/streamlit_app.py
```

```bash
pytest -q
```

2,356 tests. The ones worth reading are the convention tests — additive versus
compounding discounts, margin on pocket versus on list, which side of a variance
is unfavourable, whether a subtotal bar moves a waterfall — because those are
where two defensible readings exist and only one is implemented.

---

## Notes

Originally built for a live ERP. Employer identifiers, vendor names and real
cost data have been removed; the lanes and suppliers are generic, and everything
in `data/`, `raw/`, `output/` and `sample_data/` is generated.
