# Metric reference

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

## Parameters

Calculated tables, not CSVs, and related to nothing on purpose: a parameter joined to a fact filters that fact to the rows matching the parameter's own value, which is the opposite of what a what-if is for.

| Parameter | Column | Range | Step | Read by |
|---|---|---:|---:|---|
| `PriceChange` | `Price change %` | -15% to +15% | 0.010 | `[Price change value]` |
| `CostChange` | `Cost change %` | -15% to +25% | 0.010 | `[Cost change value]` |
| `VolumeChange` | `Volume change %` | -25% to +25% | 0.010 | `[Volume change value]` |
| `DiscountChange` | `Discount change pts` | -5% to +10% | 0.005 | `[Discount change value]` |
## The 209 measures

Grouped as they appear in the Power BI field list.

### 01 Revenue

The four price levels and the two margins, straight off the invoice-line fact. Everything else on the report is one of these cut a different way.

9 measures.

| Measure | Shows | Definition |
|---|---|---|
| **COGS** | currency | `SUM(fact_sales[cogs])` |
| **Invoice lines** | count | `COUNTROWS(fact_sales)` |
| **Invoice margin %** | percent | `DIVIDE([Invoice revenue] - [COGS], [Invoice revenue])` |
| **Invoice revenue** | currency | `SUM(fact_sales[revenue])` |
| **List value** | currency | `SUM(fact_sales[list_value])` |
| **Pocket margin $** | currency | `[Pocket revenue] - [COGS]` |
| **Pocket margin %** | percent | `DIVIDE([Pocket margin $], [Pocket revenue])` |
| **Pocket revenue** | currency | `SUM(fact_sales[pocket_revenue])` |
| **Volume (units)** | count | `SUM(fact_sales[quantity_units])` |

### 02 Waterfall

List value down to pocket margin. `Waterfall amount` is what a step is *worth*; `Waterfall step` is what it *moves the running total by*, which is zero on a subtotal — charts bind the second, tables the first. See `pricing/waterfall.py`.

9 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Cost to serve** | currency | `SUMX(fact_sales, fact_sales[cost_to_serve] * fact_sales[quantity_units])` |
| **Deduction % of list** | percent | `SUM(leakage_by_dimension[pct_of_list])` |
| **Deduction amount** | currency | `SUM(leakage_by_dimension[amount])` |
| **Leakage $** | currency | `[List value] - [Pocket revenue]` |
| **Leakage %** | percent | `DIVIDE([Leakage $], [List value])` |
| **Off-invoice deductions** | currency | `SUMX(fact_sales, fact_sales[off_invoice_deductions] * fact_sales[quantity_units])` |
| **On-invoice discounts** | currency | `SUMX(fact_sales, fact_sales[on_invoice_discounts] * fact_sales[quantity_units])` |
| **Waterfall amount** | currency | `SUM(price_waterfall[amount])` |
| **Waterfall step** | currency | `SUM(price_waterfall[delta])` |

### 03 Price

Realised price per unit at each level, against unit cost. The gap between the list and pocket lines is the leakage, drawn.

3 measures.

| Measure | Shows | Definition |
|---|---|---|
| **List price per unit** | currency | `DIVIDE([List value], [Volume (units)])` |
| **Pocket price per unit** | currency | `DIVIDE([Pocket revenue], [Volume (units)])` |
| **Unit cost** | currency | `DIVIDE([COGS], [Volume (units)])` |

### 04 Comparison

Period-over-period on `month_index` rather than a date table: every fact here is monthly, and Power BI needs a contiguous *daily* column to mark a date table — so the marking would be rejected, or accepted against a column with thirty-day gaps and quietly wrong.

4 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Pocket margin $ LY** | currency | `VAR vShift = 12 RETURN CALCULATE( [Pocket margin $], ALL(dim_month), TREATAS( SELECTCOLUMNS(VALUES(dim_month[month_index]), "month_index", dim_month[month_index] - vShift), dim_month[month_index] ) )` |
| **Pocket margin YoY pts** | percent | `VAR vNow = [Pocket margin %] VAR vThen = DIVIDE([Pocket margin $ LY], [Pocket revenue LY]) RETURN vNow - vThen` |
| **Pocket revenue LY** | currency | `VAR vShift = 12 RETURN CALCULATE( [Pocket revenue], ALL(dim_month), TREATAS( SELECTCOLUMNS(VALUES(dim_month[month_index]), "month_index", dim_month[month_index] - vShift), dim_month[month_index] ) )` |
| **Pocket revenue YoY %** | percent | `DIVIDE([Pocket revenue] - [Pocket revenue LY], [Pocket revenue LY])` |

### 05 Competitive

Our price over the market's, times 100. Observations are weighted by freshness on a 45-day half-life and the central tendency is the median, because an unweighted mean gives a discount brand with 2% share the same vote as the category leader. Pass-through is measured twice — against list it is a decision, against pocket it is an outcome, and the gap is what discounting handed back. See `pricing/competitive.py`.

8 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Gap to market %** | percent | `AVERAGE(competitive_index[gap_pct])` |
| **Market price** | currency | `AVERAGE(competitive_index[market_price])` |
| **Pass-through to list** | ratio | `AVERAGE(passthrough[passthrough_to_list])` |
| **Pass-through to pocket** | ratio | `AVERAGE(passthrough[passthrough_to_pocket])` |
| **Price index** | index | `MEDIANX(competitive_index, competitive_index[price_index])` |
| **Price index target** | count | `100` |
| **Products over market** | count | `CALCULATE(DISTINCTCOUNT(competitive_index[product_id]), competitive_index[price_index] > 115)` |
| **Products under market** | count | `CALCULATE(DISTINCTCOUNT(competitive_index[product_id]), competitive_index[price_index] < 96)` |

### 06 Cost

Standard-costing variances and budget against actual. Positive is unfavourable throughout. See `pricing/variance.py`.

10 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Actual revenue** | currency | `SUM(budget_variance[actual_revenue])` |
| **Budget revenue** | currency | `SUM(budget_variance[budget_revenue])` |
| **Cost variance** | currency | `SUM(cost_variance_long[variance])` |
| **Labour variance** | currency | `CALCULATE([Cost variance], cost_variance_long[variance_type] IN {"Labour rate", "Labour efficiency"})` |
| **Margin vs budget** | currency | `SUM(budget_variance[actual_margin]) - SUM(budget_variance[budget_margin])` |
| **Overhead spending variance** | currency | `SUM(overhead_variance[spending_variance])` |
| **Overhead volume variance** | currency | `SUM(overhead_variance[volume_variance])` |
| **Purchase price variance** | currency | `CALCULATE([Cost variance], cost_variance_long[variance_type] = "Purchase price")` |
| **Revenue vs budget** | currency | `[Actual revenue] - [Budget revenue]` |
| **Yield variance** | currency | `CALCULATE([Cost variance], cost_variance_long[variance_type] = "Yield")` |

### 07 Bridge

The year-on-year margin move split into price, cost, volume, mix, new products and lost products. The six sum to the actual change exactly; products present in only one year are pulled out first, because there is no prior price to compare a launch against and folding it into volume flatters the bridge.

1 measure.

| Measure | Shows | Definition |
|---|---|---|
| **Bridge amount** | currency | `SUM(margin_bridge_effects[amount])` |

### 08 Elasticity

`ln(q) = a + e·ln(p)`, whose slope *is* the elasticity. Fitted on list price, promotional months excluded, seasonality divided out, linear trend controlled for. See `pricing/elasticity.py`.

8 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Break-even volume %** | percent | `AVERAGE(price_change_hurdles[break_even_volume_pct])` |
| **Elasticity** | ratio | `AVERAGE(elasticity_estimates[elasticity])` |
| **Elasticity fit** | ratio | `AVERAGE(elasticity_estimates[r_squared])` |
| **Expected margin change** | currency | `SUM(price_change_hurdles[expected_margin_change])` |
| **Expected volume %** | percent | `AVERAGE(price_change_hurdles[expected_volume_pct])` |
| **Response profit** | currency | `SUM(price_response_curve[profit])` |
| **Response revenue** | currency | `SUM(price_response_curve[revenue])` |
| **Response volume** | count | `SUM(price_response_curve[quantity])` |

### 09 Bands

The spread of pocket prices one product achieves across its customers, volume-weighted, plus the win curve fitted on won *and lost* quotes — transaction data contains no losses at all. See `pricing/segmentation.py`.

10 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Band median** | currency | `AVERAGE(price_bands[median_price])` |
| **Band p10** | currency | `AVERAGE(price_bands[p10_price])` |
| **Band p90** | currency | `AVERAGE(price_bands[p90_price])` |
| **Band width %** | percent | `AVERAGE(price_bands[band_width_pct])` |
| **Customer pocket margin %** | percent | `AVERAGE(customer_profitability[pocket_margin_pct])` |
| **Customer volume (units)** | count | `SUM(customer_profitability[volume_units])` |
| **Indifference price ratio** | ratio | `AVERAGE(wtp_fits[indifference_price_ratio])` |
| **Quote win rate** | percent | `AVERAGE(wtp_fits[win_rate])` |
| **Realisation opportunity** | currency | `SUM(price_bands[realisation_opportunity])` |
| **Win rate** | percent | `AVERAGE(wtp_curve[win_rate])` |

### 10 Guardrails

Floor, target and stretch on **pocket** price, with approval tiers on the gap to target margin rather than on the discount. See `pricing/guardrails.py`.

7 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Deal margin %** | percent | `AVERAGE(deal_scores[pocket_margin_pct])` |
| **Extended margin** | currency | `SUM(deal_scores[extended_margin])` |
| **Guardrail breaches** | count | `SUM(guardrail_summary[count])` |
| **Lines scored** | count | `COUNTROWS(deal_scores)` |
| **Margin at risk** | currency | `SUM(guardrail_summary[margin_at_risk])` |
| **Margin gap $** | currency | `SUM(deal_scores[margin_gap_dollars])` |
| **Within guardrail %** | percent | `VAR vClear = CALCULATE(COUNTROWS(deal_scores), deal_scores[within_guardrail] = TRUE()) RETURN DIVIDE(vClear, [Lines scored])` |

### 11 Bundles

Bundle economics judged on incremental margin. Setting it to zero and solving for the cannibalisation rate gives `c* = bundle margin / standalone margin` — the share of bundle buyers who would have bought every component anyway, above which the bundle loses money. See `pricing/bundles.py`.

7 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Break-even cannibalisation** | percent | `AVERAGE(bundle_candidates[break_even_cannibalisation])` |
| **Bundle margin %** | percent | `AVERAGE(bundle_candidates[bundle_margin_pct])` |
| **Bundle price** | currency | `AVERAGE(bundle_candidates[bundle_price])` |
| **Bundles considered** | count | `COUNTROWS(bundle_candidates)` |
| **Cannibalisation headroom** | percent | `AVERAGE(bundle_candidates[headroom])` |
| **Incremental margin** | currency | `SUM(bundle_candidates[incremental_margin])` |
| **Standalone margin %** | percent | `AVERAGE(bundle_candidates[standalone_margin_pct])` |

### 12 Labels

Text measures used as card subtitles, so a card and the sentence under it cannot disagree. These carry no format string by design.

5 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Index reference** | text | `VAR vIndex = [Price index] RETURN FORMAT(vIndex - 100, "+0.0;-0.0;0.0") & " against parity"` |
| **Leakage reference** | text | `"every point is " & FORMAT([List value] * 0.01, "\$#,0")` |
| **Margin reference** | text | `VAR vGap = [Pocket margin YoY pts] RETURN IF(ISBLANK(vGap), "no prior year", FORMAT(vGap, "+0.0%;-0.0%;0.0%") & " on last year")` |
| **Quality reference** | text | `VAR vFailing = [Rows failing] RETURN FORMAT(vFailing, "#,0") & " rows of " & FORMAT([Rows checked], "#,0") & " need a fix"` |
| **Scenario reference** | text | `"price " & FORMAT([Price change value], "+0.0%;-0.0%;0.0%") & ", cost " & FORMAT([Cost change value], "+0.0%;-0.0%;0.0%") & ", volume " & FORMAT([Volume change value], "+0.0%;-0.0%;0.0%")` |

### 13 What-if

Four parameters read by `SELECTEDVALUE`, combined into a scenario. Price and discount share one factor because they move the realised price in opposite directions; keeping them apart is how a simulator reports a price rise and a deeper discount as though both were good news. See `pricing/scenario.py`.

21 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Break-even input** | percent | `AVERAGE(scenario_thresholds[threshold])` |
| **Cost change value** | percent | `SELECTEDVALUE(CostChange[Cost change %], 0)` |
| **Discount change value** | percent | `SELECTEDVALUE(DiscountChange[Discount change pts], 0)` |
| **Price change value** | percent | `SELECTEDVALUE(PriceChange[Price change %], 0)` |
| **Scenario cost** | currency | `[COGS] * (1 + [Cost change value]) * [Scenario volume factor]` |
| **Scenario margin $** | currency | `[Scenario revenue] - [Scenario cost]` |
| **Scenario margin %** | percent | `DIVIDE([Scenario margin $], [Scenario revenue])` |
| **Scenario margin delta** | currency | `[Scenario margin $] - [Pocket margin $]` |
| **Scenario margin pts** | percent | `[Scenario margin %] - [Pocket margin %]` |
| **Scenario price factor** | ratio | `1 + [Price change value] - [Discount change value]` |
| **Scenario revenue** | currency | `[Pocket revenue] * [Scenario price factor] * [Scenario volume factor]` |
| **Scenario revenue delta** | currency | `[Scenario revenue] - [Pocket revenue]` |
| **Scenario volume factor** | ratio | `1 + [Volume change value]` |
| **Three-point contribution %** | percent | `AVERAGE(scenario_three_point[contribution_pct])` |
| **Three-point delta** | currency | `SUM(scenario_three_point[delta])` |
| **Three-point operating profit** | currency | `SUM(scenario_three_point[operating_profit])` |
| **Tornado downside** | currency | `SUM(scenario_tornado[downside_delta])` |
| **Tornado share of swing** | percent | `AVERAGE(scenario_tornado[share_of_swing])` |
| **Tornado swing** | currency | `SUM(scenario_tornado[swing])` |
| **Tornado upside** | currency | `SUM(scenario_tornado[upside_delta])` |
| **Volume change value** | percent | `SELECTEDVALUE(VolumeChange[Volume change %], 0)` |

### 14 Unit economics

Contribution, markup against margin, cost to serve, and break-even in both units and revenue. See `pricing/unit_economics.py`.

21 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Break-even revenue** | currency | `SUM(break_even_portfolio[break_even_revenue])` |
| **Break-even units** | count | `SUM(break_even_portfolio[break_even_volume])` |
| **Contribution $** | currency | `SUM(unit_economics[contribution])` |
| **Contribution %** | percent | `DIVIDE([Contribution $], SUM(unit_economics[pocket_revenue]))` |
| **Contribution per unit** | currency | `DIVIDE([Contribution $], SUM(unit_economics[volume_units]))` |
| **Cost to serve %** | percent | `AVERAGE(unit_economics[cost_to_serve_pct])` |
| **Curve fixed cost** | currency | `SUM(break_even_curve[fixed_cost])` |
| **Curve profit** | currency | `SUM(break_even_curve[profit])` |
| **Curve revenue** | currency | `SUM(break_even_curve[revenue])` |
| **Curve total cost** | currency | `SUM(break_even_curve[total_cost])` |
| **Curve variable cost** | currency | `SUM(break_even_curve[variable_cost])` |
| **Margin of safety** | percent | `AVERAGE(break_even_portfolio[margin_of_safety])` |
| **Margin read as markup** | percent | `AVERAGE(markup_vs_margin[margin_if_read_as_markup])` |
| **Markup %** | percent | `AVERAGE(unit_economics[markup_pct])` |
| **Markup margin gap** | percent | `AVERAGE(markup_vs_margin[gap])` |
| **Markup needed** | percent | `AVERAGE(markup_vs_margin[markup_needed_for_that_margin])` |
| **Operating leverage** | ratio | `AVERAGE(break_even_portfolio[operating_leverage])` |
| **Operating margin %** | percent | `DIVIDE([Operating profit], SUM(unit_economics[pocket_revenue]))` |
| **Operating profit** | currency | `SUM(unit_economics[operating_profit])` |
| **Product break-even units** | count | `SUM(break_even_products[break_even_units])` |
| **Variable cost per unit** | currency | `DIVIDE(SUM(unit_economics[variable_cost]), SUM(unit_economics[volume_units]))` |

### 15 Forecast

Six methods, chosen per measure by rolling-origin backtest rather than by whichever fit the history best. WAPE rather than MAPE, because MAPE divides by the actual and one small month dominates the average. The scoped `Revenue …` and `Volume …` measures exist because the source holds four measures in three different units, and anything that adds across them reports dollars plus units plus dollars again. See `pricing/forecast.py`.

21 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Actual** | count | `SUM(forecast_series[actual])` |
| **Backtest WAPE** | percent | `AVERAGE(forecast_summary[backtest_wape])` |
| **Forecast** | count | `SUM(forecast_series[forecast])` |
| **Forecast change %** | percent | `AVERAGE(forecast_summary[change_pct])` |
| **Forecast high** | count | `SUM(forecast_series[high])` |
| **Forecast low** | count | `SUM(forecast_series[low])` |
| **Holdout WAPE** | percent | `AVERAGE(forecast_summary[holdout_wape])` |
| **Last six months** | count | `SUM(forecast_summary[last_6_actual])` |
| **Method WAPE** | percent | `AVERAGE(forecast_accuracy[wape])` |
| **Method bias** | percent | `AVERAGE(forecast_accuracy[bias])` |
| **Next six months** | count | `SUM(forecast_summary[next_6_total])` |
| **Revenue actual** | currency | `CALCULATE([Actual], forecast_series[measure] = "Pocket revenue")` |
| **Revenue backtest WAPE** | percent | `CALCULATE([Backtest WAPE], forecast_summary[measure] = "Pocket revenue")` |
| **Revenue forecast** | currency | `CALCULATE([Forecast], forecast_series[measure] = "Pocket revenue")` |
| **Revenue forecast change %** | percent | `CALCULATE([Forecast change %], forecast_summary[measure] = "Pocket revenue")` |
| **Revenue forecast high** | currency | `CALCULATE([Forecast high], forecast_series[measure] = "Pocket revenue")` |
| **Revenue forecast low** | currency | `CALCULATE([Forecast low], forecast_series[measure] = "Pocket revenue")` |
| **Revenue holdout WAPE** | percent | `CALCULATE([Holdout WAPE], forecast_summary[measure] = "Pocket revenue")` |
| **Revenue next six months** | currency | `CALCULATE([Next six months], forecast_summary[measure] = "Pocket revenue")` |
| **Volume actual** | count | `CALCULATE([Actual], forecast_series[measure] = "Volume (units)")` |
| **Volume forecast** | count | `CALCULATE([Forecast], forecast_series[measure] = "Volume (units)")` |

### 16 Profitability

Eleven cuts of the same book down to operating profit. The fixed allocation is by revenue share and the column says so: every allocation is arbitrary, and the argument is always about which arbitrary one was used.

11 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Profitability contribution** | currency | `SUM(profitability[contribution])` |
| **Profitability leakage %** | percent | `AVERAGE(profitability[leakage_pct])` |
| **Profitability margin $** | currency | `SUM(profitability[gross_margin])` |
| **Profitability margin %** | percent | `DIVIDE([Profitability margin $], [Profitability revenue])` |
| **Profitability operating margin %** | percent | `DIVIDE(SUM(profitability[operating_profit]), [Profitability revenue])` |
| **Profitability revenue** | currency | `SUM(profitability[pocket_revenue])` |
| **Revenue share** | percent | `SUM(profitability[revenue_share])` |
| **Segment leakage %** | percent | `AVERAGE(profit_heatmap[leakage_pct])` |
| **Segment margin $** | currency | `SUM(profit_heatmap[gross_margin])` |
| **Segment margin %** | percent | `DIVIDE([Segment margin $], [Segment revenue])` |
| **Segment revenue** | currency | `SUM(profit_heatmap[pocket_revenue])` |

### 17 Discount

What a point of discount costs, and whether a promotional mechanic paid for the volume it bought.

14 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Banded lines** | count | `SUM(discount_margin_matrix[lines])` |
| **Banded margin $** | currency | `SUM(discount_margin_matrix[gross_margin])` |
| **Banded margin %** | percent | `DIVIDE([Banded margin $], [Banded revenue])` |
| **Banded revenue** | currency | `SUM(discount_margin_matrix[pocket_revenue])` |
| **Banded share of revenue** | percent | `SUM(discount_margin_matrix[share_of_revenue])` |
| **Discount on baseline** | currency | `SUM(promotion_summary[discount_on_baseline])` |
| **Incremental promo margin** | currency | `SUM(promotion_summary[incremental_margin])` |
| **Incremental promo units** | count | `SUM(promotion_summary[incremental_volume_units])` |
| **Net promo margin** | currency | `SUM(promotion_summary[net_promo_margin])` |
| **Price point** | currency | `AVERAGE(price_vs_volume[list_price])` |
| **Price point revenue** | currency | `SUM(price_vs_volume[pocket_revenue])` |
| **Promo ROI** | ratio | `DIVIDE([Net promo margin], [Discount on baseline])` |
| **Promotions run** | count | `SUM(promotion_summary[promotions])` |
| **Units sold** | count | `SUM(price_vs_volume[volume_units])` |

### 18 Cost elements

The eight elements the cost stack is built from, standard against actual.

8 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Actual cost** | currency | `SUM(cost_element_summary[actual_cost])` |
| **Category cost variance** | currency | `SUM(cost_element_by_category[variance])` |
| **Category standard cost** | currency | `SUM(cost_element_by_category[standard_cost])` |
| **Cost element variance** | currency | `SUM(cost_element_summary[variance])` |
| **Cost element variance %** | percent | `DIVIDE([Cost element variance], [Standard cost])` |
| **Element waterfall amount** | currency | `SUM(cost_element_waterfall[amount])` |
| **Element waterfall step** | currency | `SUM(cost_element_waterfall[delta])` |
| **Standard cost** | currency | `SUM(cost_element_summary[standard_cost])` |

### 19 Operations

Price-list maintenance: what moved, why, who approved it, how long it took, and what has gone stale.

9 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Coverage margin %** | percent | `AVERAGE(price_list_coverage[median_margin_pct])` |
| **Days since price change** | count | `MEDIANX(price_list_coverage, price_list_coverage[median_days_since_change])` |
| **Median days to approve** | count | `MEDIANX(price_change_log, price_change_log[days_to_approve])` |
| **Median price change %** | percent | `MEDIANX(price_change_log, price_change_log[pct_change])` |
| **Price changes** | count | `COUNTROWS(price_change_log)` |
| **Price decreases** | count | `CALCULATE([Price changes], price_change_log[direction] = "Decrease")` |
| **Price increases** | count | `CALCULATE([Price changes], price_change_log[direction] = "Increase")` |
| **Products on the price list** | count | `SUM(price_list_coverage[products])` |
| **Stale products** | count | `SUM(price_list_coverage[stale])` |

### 20 Data quality

Twelve rules across the six quality dimensions, run against the raw ERP extract rather than the clean tables. The score is row-weighted, not an average of the per-dimension scores — twelve rules over wildly different row counts average to a number that flatters whichever rule ran over the smallest table. See `pricing/quality.py`.

10 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Checks run** | count | `COUNTROWS(data_quality_checks)` |
| **Critical checks failing** | count | `CALCULATE([Checks run], data_quality_checks[severity] = "Critical")` |
| **Dimension score** | percent | `AVERAGE(data_quality_score[score])` |
| **Fail rate** | percent | `AVERAGE(data_quality_checks[fail_rate])` |
| **Quality score** | percent | `DIVIDE([Rows checked] - [Rows failing], [Rows checked])` |
| **Reconciliation amount** | currency | `SUM(reconciliation[amount])` |
| **Reconciliation step** | currency | `SUM(reconciliation[delta])` |
| **Rows checked** | count | `SUM(data_quality_checks[rows_checked])` |
| **Rows failing** | count | `SUM(data_quality_checks[rows_failing])` |
| **Unexplained** | currency | `SUM(reconciliation[unexplained])` |

### 21 Recommendations

Six ordered actions per product, each with what it is worth and the confidence behind the *evidence* rather than the size of the prize. See `pricing/recommend.py`.

13 measures.

| Measure | Shows | Definition |
|---|---|---|
| **Action margin delta** | currency | `SUM(recommendation_summary[margin_delta])` |
| **Action products** | count | `SUM(recommendation_summary[products])` |
| **Action revenue delta** | currency | `SUM(recommendation_summary[revenue_delta])` |
| **Action volume** | count | `SUM(recommendation_summary[volume_units])` |
| **Exception margin %** | percent | `AVERAGE(guardrail_exceptions[pocket_margin_pct])` |
| **Exception margin at risk** | currency | `SUM(guardrail_exceptions[margin_at_risk])` |
| **Exceptions** | count | `COUNTROWS(guardrail_exceptions)` |
| **Margin delta** | currency | `SUM(recommendations[margin_delta])` |
| **Products reviewed** | count | `COUNTROWS(recommendations)` |
| **Recommended move %** | percent | `AVERAGE(recommendations[price_change_pct])` |
| **Recommended price** | currency | `AVERAGE(recommendations[recommended_price])` |
| **Red exceptions** | count | `CALCULATE([Exceptions], guardrail_exceptions[alert] = "Red")` |
| **Revenue delta** | currency | `SUM(recommendations[revenue_delta])` |
