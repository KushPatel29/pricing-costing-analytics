"""
What the report contains: eighteen pages, and every visual on them.

Declarative on purpose. A PBIR report is one JSON file per visual, and typing
seventy of them by hand is how a report ends up with three different
``visualContainer`` schema versions, a mistyped property that renders nothing,
and no way to check either. Here the pages are data, the writer is one function,
and the tests assert on this module rather than on the JSON it produces.

Visual shorthand::

    card(measure, subtitle=measure)
    bar / column / line / scatter / table / slicer / donut / treemap / gauge

Fields are written ``table[column]`` for a column and ``[Measure]`` for a
measure, which is the same notation Desktop shows in the field well.
"""

from __future__ import annotations

# Canvas is 1280x720 at FitToPage. A card row sits at y=20 with height 118 --
# a card carrying a reference subtitle needs at least 118px or the category
# label is clipped, and clipping is silent.
CARD_Y = 20
CARD_H = 118
ROW1_Y = 152
ROW2_Y = 442

PAGES: list[dict] = [
    # ----------------------------------------------------------------- 1
    {
        "name": "section_summary",
        "display": "Executive summary",
        "visuals": [
            {"type": "card", "field": "[Pocket revenue]", "subtitle": "[Margin reference]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Pocket revenue: what reaches us after every discount, "
                    "rebate, term and delivery cost."},
            {"type": "card", "field": "[Pocket margin %]",
             "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Pocket margin as a share of pocket revenue."},
            {"type": "card", "field": "[Leakage %]", "subtitle": "[Leakage reference]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Revenue leakage: the share of list price that never "
                    "reaches us."},
            {"type": "card", "field": "[Margin at risk]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Margin at risk: extended margin on lines breaching a "
                    "guardrail."},

            {"type": "line", "x": "dim_month[month_name]",
             "y": ["[List price per unit]", "[Pocket price per unit]", "[Unit cost]"],
             "title": "Realised price against cost",
             "pos": (20, ROW1_Y, 620, 272),
             "alt": "Line chart titled Realised price against cost. Plots List price "
                    "per unit, Pocket price per unit and Unit cost by month."},
            {"type": "line", "x": "dim_month[month_name]", "y": ["[Leakage %]"],
             "title": "Leakage, month by month",
             "pos": (654, ROW1_Y, 606, 272),
             "alt": "Line chart titled Leakage month by month. Plots Leakage percent "
                    "of list by month."},

            {"type": "column", "x": "margin_bridge_effects[effect]", "y": ["[Bridge amount]"],
             "title": "What moved the margin",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Column chart titled What moved the margin. Plots Bridge amount by "
                    "effect: price, cost, volume, mix, new and lost products."},
            {"type": "table", "columns": ["guardrail_summary[code]", "[Guardrail breaches]",
                                          "[Margin at risk]", "guardrail_summary[action]"],
             "title": "Where the work is",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Table titled Where the work is. Lists each guardrail rule with "
                    "its breach count, margin at risk and recommended action."},

            {"type": "slicer", "field": "dim_month[fiscal_year_label]", "title": "Fiscal year",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Filters the page by fiscal year label."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the page by product category."},
        ],
    },
    # ----------------------------------------------------------------- 2
    {
        "name": "section_waterfall",
        "display": "Price waterfall",
        "visuals": [
            {"type": "card", "field": "[List value]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Total list value of everything shipped."},
            {"type": "card", "field": "[Invoice revenue]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Invoice revenue, after on-invoice discounts."},
            {"type": "card", "field": "[Pocket revenue]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Pocket revenue, after every deduction."},
            {"type": "card", "field": "[Leakage %]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Leakage as a share of list price."},

            {"type": "waterfall", "x": "price_waterfall[step]", "y": ["[Waterfall step]"],
             "title": "List value to pocket margin",
             "pos": (20, ROW1_Y, 1240, 288),
             "alt": "Waterfall chart titled List value to pocket margin. Plots Waterfall "
                    "step by step, from list value through each deduction to pocket "
                    "margin. Subtotal bars move the running total by nothing, so the "
                    "bars close on the real figure."},

            {"type": "bar", "x": "leakage_by_dimension[deduction]", "y": ["[Deduction amount]"],
             "title": "Which deduction costs the most",
             "pos": (20, ROW2_Y + 6, 620, 252),
             "alt": "Bar chart titled Which deduction costs the most. Plots Deduction "
                    "amount by deduction."},
            {"type": "column", "x": "leakage_by_dimension[member]",
             "y": ["[Deduction % of list]"],
             "title": "Leakage rate by member",
             "pos": (654, ROW2_Y + 6, 400, 252),
             "alt": "Column chart titled Leakage rate by member. Plots Deduction percent "
                    "of list by member of the selected dimension."},
            {"type": "slicer", "field": "leakage_by_dimension[dimension]", "title": "Cut by",
             "pos": (1068, ROW2_Y + 6, 192, 76),
             "alt": "Slicer. Chooses which dimension the leakage rate chart is cut by."},
            {"type": "slicer", "field": "leakage_by_dimension[bucket]", "title": "Bucket",
             "pos": (1068, ROW2_Y + 92, 192, 76),
             "alt": "Slicer. Filters by deduction bucket: on-invoice, off-invoice "
                    "or cost to serve."},
        ],
    },
    # ----------------------------------------------------------------- 3
    {
        "name": "section_competitive",
        "display": "Competitive position",
        "visuals": [
            {"type": "card", "field": "[Price index]", "subtitle": "[Index reference]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Median price index against the market. 100 is parity."},
            {"type": "card", "field": "[Products under market]",
             "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Products under market: priced below 96 on the index."},
            {"type": "card", "field": "[Products over market]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Products over market: priced above 115 on the index."},
            {"type": "card", "field": "[Pass-through to pocket]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Pass-through to pocket: the share of an input-cost move "
                    "that reached the realised price."},

            {"type": "column", "x": "competitive_index[position]", "y": ["[Pocket revenue]"],
             "title": "Revenue by market position",
             "pos": (20, ROW1_Y, 400, 272),
             "alt": "Column chart titled Revenue by market position. Plots Pocket revenue "
                    "by position band."},
            {"type": "scatter", "x": "[Price index]", "y": ["[Pocket margin %]"],
             "size": "[Pocket revenue]", "category": "dim_product[description]",
             "title": "Index against margin",
             "pos": (434, ROW1_Y, 400, 272),
             "alt": "Scatter chart titled Index against margin. Plots Pocket margin "
                    "percent against Price index for each product, sized by Pocket "
                    "revenue."},
            {"type": "bar", "x": "passthrough[commodity_index]",
             "y": ["[Pass-through to list]", "[Pass-through to pocket]"],
             "title": "How much of a cost move reached the price",
             "pos": (848, ROW1_Y, 412, 272),
             "alt": "Bar chart titled How much of a cost move reached the price. Plots "
                    "Pass-through to list and Pass-through to pocket by commodity index."},

            {"type": "table",
             "columns": ["dim_product[description]", "dim_product[category]",
                         "[Pocket price per unit]", "[Market price]", "[Price index]",
                         "[Pocket margin %]", "[Pocket revenue]"],
             "title": "Every product, ranked by revenue",
             "pos": (20, ROW2_Y, 1030, 258),
             "alt": "Table titled Every product ranked by revenue. Lists description, "
                    "category, pocket price, market price, price index, margin and "
                    "revenue."},
            {"type": "slicer", "field": "competitive_index[position]", "title": "Position",
             "pos": (1064, ROW2_Y, 196, 76),
             "alt": "Slicer. Filters the page by market position band."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1064, ROW2_Y + 86, 196, 76),
             "alt": "Slicer. Filters the page by product category."},
        ],
    },
    # ----------------------------------------------------------------- 4
    {
        "name": "section_elasticity",
        "display": "Elasticity",
        "visuals": [
            {"type": "card", "field": "[Elasticity]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Average own-price elasticity across the selection."},
            {"type": "card", "field": "[Elasticity fit]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. R-squared of the elasticity fit."},
            {"type": "card", "field": "[Break-even volume %]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Break-even volume %: the volume a price change needs to "
                    "hold contribution flat."},
            {"type": "card", "field": "[Expected margin change]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Expected margin change: what the elasticity implies for "
                    "the selected price move."},

            {"type": "bar", "x": "elasticity_estimates[member]", "y": ["[Elasticity]"],
             "title": "Elasticity by category",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Bar chart titled Elasticity by category. Plots Elasticity by "
                    "category member."},
            {"type": "line", "x": "price_response_curve[price_change_pct]",
             "y": ["[Response revenue]", "[Response profit]"],
             "title": "Revenue and profit across a band of prices",
             "pos": (534, ROW1_Y, 726, 272),
             "alt": "Line chart titled Revenue and profit across a band of prices. Plots "
                    "Response revenue and Response profit by price change percent."},

            {"type": "table",
             "columns": ["price_change_hurdles[category]",
                         "price_change_hurdles[price_change_pct]",
                         "[Break-even volume %]", "[Expected volume %]",
                         "[Expected margin change]", "price_change_hurdles[verdict]"],
             "title": "The volume a price change has to find",
             "pos": (20, ROW2_Y, 826, 258),
             "alt": "Table titled The volume a price change has to find. Lists category, "
                    "price change, break-even volume, expected volume, margin change and "
                    "verdict."},
            {"type": "slicer", "field": "elasticity_estimates[scope]", "title": "Scope",
             "pos": (860, ROW2_Y, 200, 76),
             "alt": "Slicer. Chooses the estimate scope: category-level or "
                    "product-level."},
            {"type": "slicer", "field": "price_response_curve[category]", "title": "Category",
             "pos": (1064, ROW2_Y, 196, 76),
             "alt": "Slicer. Filters the response curve to one category."},
        ],
    },
    # ----------------------------------------------------------------- 5
    {
        "name": "section_cost",
        "display": "Cost variance",
        "visuals": [
            {"type": "card", "field": "[Purchase price variance]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Purchase price variance. Positive is unfavourable."},
            {"type": "card", "field": "[Yield variance]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Yield variance: material used beyond standard recovery."},
            {"type": "card", "field": "[Labour variance]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Labour variance: rate and efficiency combined."},
            {"type": "card", "field": "[Margin vs budget]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Margin vs budget: gross margin against plan."},

            {"type": "line", "x": "dim_month[month_name]", "y": ["[Cost variance]"],
             "title": "Variance through the year",
             "pos": (20, ROW1_Y, 620, 272),
             "alt": "Line chart titled Variance through the year. Plots Cost variance by "
                    "month; it resets each July when the standard is re-struck."},
            {"type": "column", "x": "dim_product[category]", "y": ["[Cost variance]"],
             "series": "cost_variance_long[variance_type]",
             "title": "Where the variance sits",
             "pos": (654, ROW1_Y, 606, 272),
             "alt": "Stacked column chart titled Where the variance sits. Plots Cost "
                    "variance by category, split by variance type."},

            {"type": "column", "x": "dim_month[month_name]",
             "y": ["[Budget revenue]", "[Actual revenue]"],
             "title": "Budget against actual",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Column chart titled Budget against actual. Plots Budget revenue and "
                    "Actual revenue by month."},
            {"type": "bar", "x": "overhead_variance[cost_pool]",
             "y": ["[Overhead spending variance]", "[Overhead volume variance]"],
             "title": "Overhead: spending against absorption",
             "pos": (654, ROW2_Y, 400, 258),
             "alt": "Bar chart titled Overhead spending against absorption. Plots "
                    "Overhead spending variance and Overhead volume variance by cost "
                    "pool."},
            {"type": "slicer", "field": "cost_variance_long[variance_type]",
             "title": "Variance type",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Filters the page by variance type."},
            {"type": "slicer", "field": "dim_month[fiscal_year_label]", "title": "Fiscal year",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the page by fiscal year label."},
        ],
    },
    # ----------------------------------------------------------------- 6
    {
        "name": "section_bridge",
        "display": "Margin bridge",
        "visuals": [
            {"type": "card", "field": "[Pocket margin $]", "subtitle": "[Margin reference]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Pocket margin in dollars."},
            {"type": "card", "field": "[Pocket margin $ LY]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Pocket margin $ LY: pocket margin twelve months earlier."},
            {"type": "card", "field": "[Pocket margin YoY pts]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Pocket margin YoY pts: the change in pocket margin, in "
                    "percentage points, against last year."},
            {"type": "card", "field": "[Pocket revenue YoY %]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Pocket revenue YoY %: revenue growth against last year."},

            {"type": "waterfall", "x": "margin_bridge_effects[effect]",
             "y": ["[Bridge amount]"],
             "title": "Price, cost, volume, mix, launches and losses",
             "pos": (20, ROW1_Y, 1240, 288),
             "alt": "Waterfall chart titled Price cost volume mix launches and losses. "
                    "Plots Bridge amount by effect. The six effects sum to the margin "
                    "change exactly."},

            {"type": "table",
             "columns": ["margin_bridge_effects[effect]", "[Bridge amount]",
                         "margin_bridge_effects[pct_of_prior]"],
             "title": "The numbers",
             "pos": (20, ROW2_Y + 6, 500, 252),
             "alt": "Table titled The numbers. Lists each bridge effect with its amount "
                    "and share of prior-year margin."},
            {"type": "line", "x": "dim_month[month_name]",
             "y": ["[Pocket margin $]", "[Pocket margin $ LY]"],
             "title": "This year against last",
             "pos": (534, ROW1_Y + 294, 530, 252),
             "alt": "Line chart titled This year against last. Plots Pocket margin and "
                    "Pocket margin twelve months earlier by month."},
            {"type": "slicer", "field": "margin_bridge_effects[comparison]",
             "title": "Comparison",
             "pos": (1068, ROW1_Y + 294, 192, 76),
             "alt": "Slicer. Chooses the comparison: which pair of fiscal years the "
                    "bridge runs between."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1068, ROW1_Y + 380, 192, 76),
             "alt": "Slicer. Filters the page by product category."},
        ],
    },
    # ----------------------------------------------------------------- 7
    {
        "name": "section_bands",
        "display": "Price bands and WTP",
        "visuals": [
            {"type": "card", "field": "[Realisation opportunity]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Realisation opportunity: the value of moving every "
                    "below-median line up to its own product's median price."},
            {"type": "card", "field": "[Band width %]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Median price band width, tenth to ninetieth percentile."},
            {"type": "card", "field": "[Quote win rate]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Quote win rate: the share of quotes won."},
            {"type": "card", "field": "[Indifference price ratio]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Indifference price ratio: the price ratio at which we win "
                    "half the quotes."},

            {"type": "bar", "x": "price_bands[description]",
             "y": ["[Realisation opportunity]"],
             "title": "Where closing the band is worth the most",
             "pos": (20, ROW1_Y, 620, 272),
             "alt": "Bar chart titled Where closing the band is worth the most. Plots "
                    "Realisation opportunity by product."},
            {"type": "line", "x": "wtp_curve[price_ratio]", "y": ["[Win rate]"],
             "series": "wtp_curve[segment]",
             "title": "Probability of winning against the price we ask",
             "pos": (654, ROW1_Y, 606, 272),
             "alt": "Line chart titled Probability of winning against the price we ask. "
                    "Plots Win rate by price ratio, one line per segment."},

            {"type": "scatter", "x": "[Customer volume (units)]",
             "y": ["[Customer pocket margin %]"], "size": "[Pocket revenue]",
             "category": "dim_customer[customer_name]",
             "title": "Customers: margin against volume",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Scatter chart titled Customers margin against volume. Plots Customer "
                    "pocket margin percent against Customer volume, sized by revenue."},
            {"type": "table",
             "columns": ["segment_profile[member]", "segment_profile[revenue]",
                         "segment_profile[margin_pct]", "segment_profile[band_width_pct]",
                         "segment_profile[realisation_opportunity]"],
             "title": "Level, and consistency",
             "pos": (654, ROW2_Y, 400, 258),
             "alt": "Table titled Level and consistency. Lists each segment with its "
                    "revenue, margin, band width and realisation opportunity."},
            {"type": "slicer", "field": "segment_profile[dimension]", "title": "Cut by",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Chooses which dimension the segment table is cut by."},
            {"type": "slicer", "field": "wtp_curve[kind]", "title": "Fitted or observed",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the win curve by kind: the fitted curve, the "
                    "observed win rates, or both."},
        ],
    },
    # ----------------------------------------------------------------- 8
    {
        "name": "section_guardrails",
        "display": "Deal guardrails",
        "visuals": [
            {"type": "card", "field": "[Lines scored]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Invoice lines scored against the guardrail this month."},
            {"type": "card", "field": "[Within guardrail %]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Within guardrail %: the share of lines clearing the floor "
                    "margin."},
            {"type": "card", "field": "[Guardrail breaches]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Number of guardrail breaches."},
            {"type": "card", "field": "[Margin at risk]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Margin at risk: extended margin on breaching lines."},

            {"type": "column", "x": "deal_scores[verdict]", "y": ["[Lines scored]"],
             "title": "Where this month's lines landed",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Column chart titled Where this months lines landed. Plots Lines "
                    "scored by verdict, from loss-making to above stretch."},
            {"type": "bar", "x": "deal_scores[approver]", "y": ["[Margin gap $]"],
             "title": "Who has to sign, and for how much",
             "pos": (534, ROW1_Y, 400, 272),
             "alt": "Bar chart titled Who has to sign and for how much. Plots Margin gap "
                    "in dollars by approver."},
            {"type": "donut", "x": "guardrail_summary[code]", "y": ["[Margin at risk]"],
             "title": "Margin at risk by rule",
             "pos": (948, ROW1_Y, 312, 272),
             "alt": "Donut chart titled Margin at risk by rule. Splits Margin at risk by "
                    "guardrail rule."},

            {"type": "table",
             "columns": ["dim_product[description]", "dim_customer[customer_name]",
                         "deal_scores[verdict]", "[Deal margin %]", "[Extended margin]",
                         "deal_scores[approver]"],
             "title": "The exception report",
             "pos": (20, ROW2_Y, 1030, 258),
             "alt": "Table titled The exception report. Lists product, customer, verdict, "
                    "margin, extended margin and approver for each scored line."},
            {"type": "slicer", "field": "deal_scores[verdict]", "title": "Verdict",
             "pos": (1064, ROW2_Y, 196, 76),
             "alt": "Slicer. Filters the page by deal verdict."},
            {"type": "slicer", "field": "dim_customer[segment]", "title": "Segment",
             "pos": (1064, ROW2_Y + 86, 196, 76),
             "alt": "Slicer. Filters the page by customer segment."},
        ],
    },
    # ----------------------------------------------------------------- 9
    {
        "name": "section_unit_economics",
        "display": "Unit economics",
        "visuals": [
            {"type": "card", "field": "[Contribution per unit]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Contribution per unit: what one unit leaves after every "
                    "cost that moves with it."},
            {"type": "card", "field": "[Contribution %]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Contribution as a share of pocket revenue."},
            {"type": "card", "field": "[Cost to serve %]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Cost to serve as a share of revenue: freight, handling and "
                    "returns."},
            {"type": "card", "field": "[Margin of safety]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Margin of safety: how far volume can fall before the book "
                    "stops covering its fixed costs."},

            {"type": "line", "x": "break_even_curve[quantity]",
             "y": ["[Curve revenue]", "[Curve fixed cost]", "[Curve variable cost]",
                   "[Curve total cost]"],
             "title": "Break-even: where revenue overtakes total cost",
             "pos": (20, ROW1_Y, 780, 272),
             "alt": "Line chart titled Break-even where revenue overtakes total cost. "
                    "Plots Curve revenue, Curve fixed cost, Curve variable cost and "
                    "Curve total cost against quantity. The crossing point is the "
                    "break-even volume."},
            {"type": "table",
             "columns": ["markup_vs_margin[rate]", "[Margin read as markup]",
                         "[Markup needed]", "[Markup margin gap]"],
             "title": "Markup is not margin",
             "pos": (814, ROW1_Y, 446, 272),
             "alt": "Table titled Markup is not margin. For each rate, shows the Margin "
                    "read as markup, the Markup needed for that margin, and the "
                    "Markup margin gap between the two readings."},

            {"type": "bar", "x": "dim_product[category]", "y": ["[Contribution $]"],
             "title": "Contribution by category",
             "pos": (20, ROW2_Y, 500, 258),
             "alt": "Bar chart titled Contribution by category. Plots Contribution "
                    "dollars by product category."},
            {"type": "table",
             "columns": ["dim_product[description]", "[Contribution per unit]",
                         "[Product break-even units]",
                         "break_even_products[margin_of_safety]"],
             "title": "Break-even by product",
             "pos": (534, ROW2_Y, 530, 258),
             "alt": "Table titled Break-even by product. Lists Contribution per unit, "
                    "Product break-even units and margin of safety for each product."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Filters the page by product category."},
            {"type": "slicer", "field": "break_even_portfolio[member]", "title": "Scope",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Chooses which member the Margin of safety card reports "
                    "on: the whole book, or one category."},
        ],
    },
    # ----------------------------------------------------------------- 10
    {
        "name": "section_scenario",
        "display": "What-if simulator",
        "visuals": [
            {"type": "card", "field": "[Scenario revenue]", "subtitle": "[Scenario reference]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Scenario revenue under the four parameters set below."},
            {"type": "card", "field": "[Scenario margin $]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Scenario margin in dollars."},
            {"type": "card", "field": "[Scenario margin %]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Scenario margin as a share of scenario revenue."},
            {"type": "card", "field": "[Scenario margin delta]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Scenario margin delta: the change against today's margin."},

            {"type": "slicer", "field": "PriceChange[Price change %]", "title": "Price change",
             "pos": (20, ROW1_Y, 300, 76),
             "alt": "Slicer. Sets the Price change % parameter the scenario measures "
                    "read."},
            {"type": "slicer", "field": "CostChange[Cost change %]", "title": "Cost change",
             "pos": (330, ROW1_Y, 296, 76),
             "alt": "Slicer. Sets the Cost change % parameter the scenario measures "
                    "read."},
            {"type": "slicer", "field": "VolumeChange[Volume change %]",
             "title": "Volume change",
             "pos": (634, ROW1_Y, 300, 76),
             "alt": "Slicer. Sets the Volume change % parameter the scenario measures "
                    "read."},
            {"type": "slicer", "field": "DiscountChange[Discount change pts]",
             "title": "Discount change",
             "pos": (942, ROW1_Y, 318, 76),
             "alt": "Slicer. Sets the Discount change pts parameter, which moves the "
                    "realised price in the opposite direction to a price rise."},

            {"type": "column", "x": "scenario_three_point[scenario]",
             "y": ["[Three-point operating profit]"],
             "title": "Worst, base and best",
             "pos": (20, 244, 620, 214),
             "alt": "Column chart titled Worst base and best. Plots Three-point "
                    "operating profit for each scenario. The ends are every assumption "
                    "at its own extreme at once, not a confidence interval."},
            {"type": "bar", "x": "scenario_tornado[input]", "y": ["[Tornado swing]"],
             "title": "What moves profit most",
             "pos": (654, 244, 606, 214),
             "alt": "Bar chart titled What moves profit most. Plots Tornado swing by "
                    "scenario input, ranked."},

            {"type": "table",
             "columns": ["scenario_three_point[scenario]", "[Three-point operating profit]",
                         "[Three-point contribution %]", "[Three-point delta]"],
             "title": "The three cases",
             "pos": (20, 472, 620, 228),
             "alt": "Table titled The three cases. Lists Three-point operating profit, "
                    "Three-point contribution % and Three-point delta per scenario."},
            {"type": "table",
             "columns": ["scenario_thresholds[input]", "[Break-even input]",
                         "scenario_thresholds[reason]"],
             "title": "Where each input breaks even",
             "pos": (654, 472, 606, 228),
             "alt": "Table titled Where each input breaks even. Gives the Break-even "
                    "input: the move at which operating profit crosses zero, and says "
                    "so when it never crosses."},
        ],
    },
    # ----------------------------------------------------------------- 11
    {
        "name": "section_forecast",
        "display": "Forecast against actual",
        "visuals": [
            {"type": "card", "field": "[Revenue next six months]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Revenue next six months: the forecast total for pocket "
                    "revenue."},
            {"type": "card", "field": "[Revenue forecast change %]",
             "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Revenue forecast change %: the next six months against the "
                    "last six."},
            {"type": "card", "field": "[Revenue backtest WAPE]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Revenue backtest WAPE: weighted absolute percentage error "
                    "across the rolling-origin folds."},
            {"type": "card", "field": "[Revenue holdout WAPE]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Revenue holdout WAPE: error on months the fit never saw."},

            {"type": "line", "x": "forecast_series[month]",
             "y": ["[Revenue actual]", "[Revenue forecast]", "[Revenue forecast low]",
                   "[Revenue forecast high]"],
             "title": "Pocket revenue, forecast against actual",
             "pos": (20, ROW1_Y, 780, 272),
             "alt": "Line chart titled Pocket revenue forecast against actual. Plots "
                    "Revenue actual, Revenue forecast, Revenue forecast low and "
                    "Revenue forecast high by month. The interval is empirical, taken "
                    "from backtest residuals rather than assumed."},
            {"type": "bar", "x": "forecast_accuracy[method]", "y": ["[Method WAPE]"],
             "title": "Which method won the backtest",
             "pos": (814, ROW1_Y, 446, 272),
             "alt": "Bar chart titled Which method won the backtest. Plots Method WAPE "
                    "by forecasting method; lower is better."},

            {"type": "line", "x": "forecast_series[month]",
             "y": ["[Volume actual]", "[Volume forecast]"],
             "title": "Volume, forecast against actual",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Line chart titled Volume forecast against actual. Plots Volume "
                    "actual and Volume forecast by month."},
            {"type": "table",
             "columns": ["forecast_summary[measure]", "forecast_summary[method]",
                         "[Backtest WAPE]", "[Holdout WAPE]", "[Forecast change %]"],
             "title": "Every series",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Table titled Every series. Lists the chosen method, Backtest WAPE, "
                    "Holdout WAPE and Forecast change % for each forecast measure."},
            {"type": "slicer", "field": "forecast_accuracy[measure]", "title": "Measure",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Filters the method comparison by which measure was being "
                    "forecast."},
            {"type": "slicer", "field": "forecast_series[period]", "title": "Period",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the series by period: history, held out, or "
                    "forecast."},
        ],
    },
    # ----------------------------------------------------------------- 12
    {
        "name": "section_profitability",
        "display": "Profitability and segmentation",
        "visuals": [
            {"type": "card", "field": "[Pocket revenue]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Pocket revenue: what reaches us after every deduction."},
            {"type": "card", "field": "[Pocket margin %]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Pocket margin as a share of pocket revenue."},
            {"type": "card", "field": "[Operating profit]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Operating profit after fixed costs are allocated."},
            {"type": "card", "field": "[Operating margin %]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Operating margin as a share of pocket revenue."},

            {"type": "treemap", "x": "profit_heatmap[segment]", "y": ["[Segment margin $]"],
             "title": "Where the margin sits",
             "pos": (20, ROW1_Y, 400, 272),
             "alt": "Treemap titled Where the margin sits. Sizes each customer segment "
                    "by Segment margin dollars."},
            {"type": "matrix", "rows": "profit_heatmap[segment]",
             "columns_by": "profit_heatmap[category]", "values": ["[Segment margin %]"],
             "title": "Segment against category",
             "pos": (434, ROW1_Y, 826, 272),
             "alt": "Matrix titled Segment against category. Segment margin % for every "
                    "segment and category pair, segments down the side and categories "
                    "across the top, so the weak cells read as a block rather than as "
                    "sixty-four rows."},

            {"type": "scatter", "category": "profitability[member]",
             "x": "[Profitability revenue]", "y": ["[Profitability margin %]"],
             "size": "[Profitability contribution]",
             "title": "Revenue against margin",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Scatter chart titled Revenue against margin. One point per member "
                    "of the chosen cut: Profitability revenue on the x axis, "
                    "Profitability margin % on the y axis, sized by Profitability "
                    "contribution."},
            {"type": "bar", "x": "profitability[member]",
             "y": ["ProfitMetric[Profit metric Fields]"],
             "title": "Whichever measure you asked for, by member",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Bar chart titled Whichever measure you asked for by member. Its "
                    "value well is Profit metric Fields, a field parameter, so the "
                    "Metric slicer swaps the measure rather than the page carrying "
                    "five charts that differ by one field."},
            {"type": "slicer", "field": "ProfitMetric[Profit metric]", "title": "Metric",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Chooses the Profit metric the bar chart shows: revenue, "
                    "margin in dollars, margin %, operating margin % or leakage %."},
            {"type": "slicer", "field": "profitability[dimension]", "title": "Cut by",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Chooses the dimension every member on this page is cut "
                    "by: product, customer, region, channel, segment or "
                    "salesperson."},
            {"type": "slicer", "field": "profit_heatmap[segment]", "title": "Segment",
             "pos": (1068, ROW2_Y + 172, 192, 76),
             "alt": "Slicer. Filters the segment and category grid by segment."},
        ],
    },
    # ----------------------------------------------------------------- 13
    {
        "name": "section_discount",
        "display": "Discount and promotion",
        "visuals": [
            {"type": "card", "field": "[Banded revenue]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Banded revenue: pocket revenue across every discount band."},
            {"type": "card", "field": "[Banded margin %]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Banded margin % across every discount band."},
            {"type": "card", "field": "[Net promo margin]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Net promo margin: incremental margin less the discount given "
                    "away on volume that would have sold anyway."},
            {"type": "card", "field": "[Promo ROI]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Promo ROI: net promo margin per dollar of discount on "
                    "baseline."},

            {"type": "column", "x": "discount_margin_matrix[discount_band]",
             "y": ["[Banded margin %]"],
             "title": "What each point of discount costs",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Column chart titled What each point of discount costs. Plots "
                    "Banded margin % by discount band."},
            {"type": "matrix", "rows": "discount_margin_matrix[discount_band]",
             "columns_by": "discount_margin_matrix[margin_band]",
             "values": ["[Banded revenue]"],
             "title": "Discount against margin",
             "pos": (534, ROW1_Y, 726, 272),
             "alt": "Matrix titled Discount against margin. Banded revenue for every "
                    "discount band against every margin band. The cells below the "
                    "diagonal are the deals that took a discount and still needed the "
                    "margin."},

            {"type": "scatter", "category": "dim_product[description]",
             "x": "[Price point]", "y": ["[Units sold]"], "size": "[Price point revenue]",
             "title": "Price against units sold",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Scatter chart titled Price against units sold. One point per "
                    "product: Price point on the x axis, Units sold on the y axis, "
                    "sized by Price point revenue."},
            {"type": "bar", "x": "promotion_summary[mechanic]", "y": ["[Net promo margin]"],
             "title": "Which mechanic paid for itself",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Bar chart titled Which mechanic paid for itself. Plots Net promo "
                    "margin by promotional mechanic."},
            {"type": "slicer", "field": "price_vs_volume[promoted]", "title": "Promoted",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Splits the price against units chart into promoted and "
                    "base weeks."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the page by product category."},
        ],
    },
    # ----------------------------------------------------------------- 14
    {
        "name": "section_cost_elements",
        "display": "Cost elements",
        "visuals": [
            {"type": "card", "field": "[Standard cost]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Standard cost: what the book should have cost at standard."},
            {"type": "card", "field": "[Actual cost]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Actual cost incurred."},
            {"type": "card", "field": "[Cost element variance]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Cost element variance. Positive is unfavourable throughout."},
            {"type": "card", "field": "[Cost element variance %]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Cost element variance % against standard."},

            {"type": "waterfall", "x": "cost_element_waterfall[step]",
             "y": ["[Element waterfall step]"],
             "title": "From standard cost to actual, element by element",
             "pos": (20, ROW1_Y, 1240, 272),
             "alt": "Waterfall chart titled From standard cost to actual element by "
                    "element. Plots Element waterfall step by step: goods, shrink, "
                    "freight, handling, packaging and both overhead pools."},

            {"type": "bar", "x": "cost_element_summary[cost_element]",
             "y": ["CostMetric[Cost metric Fields]"],
             "title": "By element, on whichever measure",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Bar chart titled By element on whichever measure. Its value well "
                    "is Cost metric Fields, a field parameter, so the Metric slicer "
                    "swaps between standard cost, actual cost, variance and variance "
                    "percent on one chart."},
            {"type": "table",
             "columns": ["cost_element_by_category[category]",
                         "cost_element_by_category[cost_element]",
                         "[Category standard cost]", "[Category cost variance]"],
             "title": "Which categories carry it",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Table titled Which categories carry it. Category standard cost and "
                    "Category cost variance for every category and element pair."},
            {"type": "slicer", "field": "CostMetric[Cost metric]", "title": "Metric",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Chooses the Cost metric the element chart shows: standard "
                    "cost, actual cost, the variance, or the variance as a percent."},
            {"type": "slicer", "field": "cost_element_summary[behaviour]",
             "title": "Behaviour",
             "pos": (1068, ROW2_Y + 172, 192, 76),
             "alt": "Slicer. Filters by cost behaviour: variable or fixed."},
            {"type": "slicer", "field": "cost_element_by_category[category]",
             "title": "Category",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the category table by category."},
        ],
    },
    # ----------------------------------------------------------------- 15
    {
        "name": "section_operations",
        "display": "Pricing operations",
        "visuals": [
            {"type": "card", "field": "[Price changes]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Price changes made across the period."},
            {"type": "card", "field": "[Median price change %]",
             "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Median price change % across the log."},
            {"type": "card", "field": "[Median days to approve]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Median days to approve a price change."},
            {"type": "card", "field": "[Stale products]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Stale products: items whose price has not been reviewed "
                    "inside its own cadence."},

            {"type": "column", "x": "price_change_log[reason]", "y": ["[Price changes]"],
             "title": "Why prices moved",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Column chart titled Why prices moved. Plots Price changes by the "
                    "reason recorded against each change."},
            {"type": "bar", "x": "price_change_log[approval_state]",
             "y": ["[Median days to approve]"],
             "title": "How long each approval takes",
             "pos": (534, ROW1_Y, 530, 272),
             "alt": "Bar chart titled How long each approval takes. Plots Median days "
                    "to approve by approval state."},
            {"type": "slicer", "field": "price_change_log[direction]", "title": "Direction",
             "pos": (1078, ROW1_Y, 182, 76),
             "alt": "Slicer. Filters the log by direction: an increase or a decrease."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1078, ROW1_Y + 86, 182, 76),
             "alt": "Slicer. Filters the page by product category."},

            {"type": "line", "x": "dim_month[month_name]", "y": ["[Price changes]"],
             "title": "Price changes by month",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Line chart titled Price changes by month. Plots Price changes "
                    "against the month they took effect; the review cycles are "
                    "staggered, so the line is not one quarterly spike."},
            {"type": "table",
             "columns": ["price_list_coverage[review_cadence]",
                         "[Products on the price list]", "[Days since price change]",
                         "[Coverage margin %]", "[Stale products]"],
             "title": "How much of the book each cadence covers",
             "pos": (654, ROW2_Y, 606, 258),
             "alt": "Table titled How much of the book each cadence covers. Products on "
                    "the price list, Days since price change, Coverage margin % and "
                    "Stale products for each review cadence."},
        ],
    },
    # ----------------------------------------------------------------- 16
    {
        "name": "section_quality",
        "display": "Data quality",
        "visuals": [
            {"type": "card", "field": "[Quality score]", "subtitle": "[Quality reference]",
             "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Quality score, row-weighted across every rule."},
            {"type": "card", "field": "[Rows checked]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Rows checked across the twelve rules."},
            {"type": "card", "field": "[Rows failing]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Rows failing at least one rule."},
            {"type": "card", "field": "[Critical checks failing]",
             "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Critical checks failing: rules whose breach stops a line "
                    "being priced at all."},

            {"type": "bar", "x": "data_quality_checks[dimension]", "y": ["[Quality score]"],
             "title": "Score by quality dimension",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Bar chart titled Score by quality dimension. Plots Quality score "
                    "for completeness, validity, consistency, uniqueness, timeliness "
                    "and accuracy."},
            {"type": "table",
             "columns": ["data_quality_checks[code]", "data_quality_checks[check]",
                         "data_quality_checks[severity]", "[Rows failing]", "[Fail rate]",
                         "data_quality_checks[action]"],
             "title": "Every rule, and what to do about it",
             "pos": (534, ROW1_Y, 726, 272),
             "alt": "Table titled Every rule and what to do about it. Lists each check "
                    "with its severity, Rows failing, Fail rate and the action to "
                    "take."},

            {"type": "waterfall", "x": "reconciliation[line]", "y": ["[Reconciliation step]"],
             "title": "Extract to staged, with every exclusion named",
             "pos": (20, ROW2_Y, 620, 258),
             "alt": "Waterfall chart titled Extract to staged with every exclusion "
                    "named. Plots Reconciliation step from the extract total down "
                    "through each exclusion to the staged total."},
            {"type": "table",
             "columns": ["data_quality_score[dimension]", "data_quality_score[checks]",
                         "[Dimension score]"],
             "title": "Rules per dimension",
             "pos": (654, ROW2_Y, 410, 258),
             "alt": "Table titled Rules per dimension. Number of checks and Dimension "
                    "score for each quality dimension, plus the overall row."},
            {"type": "slicer", "field": "data_quality_checks[severity]", "title": "Severity",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Filters the rules by severity."},
            {"type": "slicer", "field": "data_quality_checks[dimension]",
             "title": "Dimension",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the rules by quality dimension."},
        ],
    },
    # ----------------------------------------------------------------- 17
    {
        "name": "section_exceptions",
        "display": "Exceptions and alerts",
        "visuals": [
            {"type": "card", "field": "[Exceptions]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Exceptions raised across the book. A line can breach more "
                    "than one rule, because the fixes differ."},
            {"type": "card", "field": "[Red exceptions]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Red exceptions: margin leaving on today's invoices."},
            {"type": "card", "field": "[Exception margin at risk]",
             "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Exception margin at risk in dollars."},
            {"type": "card", "field": "[Exception margin %]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Exception margin %: the average pocket margin on breaching "
                    "lines."},

            {"type": "column", "x": "guardrail_exceptions[alert]", "y": ["[Exceptions]"],
             "title": "Red, amber, green",
             "pos": (20, ROW1_Y, 400, 272),
             "alt": "Column chart titled Red amber green. Plots Exceptions by alert "
                    "level. Red is money leaving now, amber is a price off its "
                    "position, green is a leading indicator."},
            {"type": "bar", "x": "guardrail_exceptions[code]",
             "y": ["[Exception margin at risk]"],
             "title": "Money at risk by rule",
             "pos": (434, ROW1_Y, 400, 272),
             "alt": "Bar chart titled Money at risk by rule. Plots Exception margin at "
                    "risk for each guardrail code."},
            {"type": "donut", "x": "dim_customer[segment]",
             "y": ["[Exception margin at risk]"],
             "title": "Which segments carry it",
             "pos": (848, ROW1_Y, 412, 272),
             "alt": "Donut chart titled Which segments carry it. Splits Exception "
                    "margin at risk by customer segment."},

            {"type": "table",
             "columns": ["guardrail_exceptions[alert]", "guardrail_exceptions[code]",
                         "dim_product[description]", "dim_customer[customer_name]",
                         "[Exception margin %]", "[Exception margin at risk]",
                         "guardrail_exceptions[action]"],
             "title": "The exception report",
             "pos": (20, ROW2_Y, 1030, 258),
             "alt": "Table titled The exception report. Each breach with its alert "
                    "level, code, product, customer, Exception margin % and Exception "
                    "margin at risk, and the action in plain words."},
            {"type": "slicer", "field": "guardrail_exceptions[alert]", "title": "Alert",
             "pos": (1064, ROW2_Y, 196, 76),
             "alt": "Slicer. Filters the page by alert level."},
            {"type": "slicer", "field": "dim_product[category]", "title": "Category",
             "pos": (1064, ROW2_Y + 86, 196, 76),
             "alt": "Slicer. Filters the page by product category."},
        ],
    },
    # ----------------------------------------------------------------- 18
    {
        "name": "section_recommendations",
        "display": "Recommendations",
        "visuals": [
            {"type": "card", "field": "[Products reviewed]", "pos": (20, CARD_Y, 300, CARD_H),
             "alt": "Card. Products reviewed by the recommendation pass."},
            {"type": "card", "field": "[Margin delta]", "pos": (330, CARD_Y, 296, CARD_H),
             "alt": "Card. Margin delta: annual gross margin the recommended actions "
                    "are worth."},
            {"type": "card", "field": "[Revenue delta]", "pos": (634, CARD_Y, 300, CARD_H),
             "alt": "Card. Revenue delta. It can be negative while margin rises, which "
                    "is the point of a price increase on elastic demand."},
            {"type": "card", "field": "[Recommended move %]", "pos": (942, CARD_Y, 318, CARD_H),
             "alt": "Card. Recommended move %: the average price change proposed."},

            {"type": "column", "x": "recommendation_summary[action]",
             "y": ["[Action margin delta]"],
             "title": "What each action is worth",
             "pos": (20, ROW1_Y, 500, 272),
             "alt": "Column chart titled What each action is worth. Plots Action margin "
                    "delta for increase, maintain, discount, bundle, fix cost and "
                    "discontinue."},
            {"type": "table",
             "columns": ["dim_product[description]", "recommendations[action]",
                         "[Recommended price]", "[Recommended move %]", "[Margin delta]",
                         "recommendations[confidence]"],
             "title": "The book, ranked by what it is worth",
             "pos": (534, ROW1_Y, 726, 272),
             "alt": "Table titled The book ranked by what it is worth. Each product "
                    "with its action, Recommended price, Recommended move %, Margin "
                    "delta and confidence."},

            {"type": "table",
             "columns": ["dim_product[description]", "recommendations[action]",
                         "recommendations[rationale]"],
             "title": "Why",
             "pos": (20, ROW2_Y, 760, 258),
             "alt": "Table titled Why. The action and the written rationale behind it "
                    "for each product, in the words an analyst would use in the "
                    "meeting."},
            {"type": "bar", "x": "recommendation_summary[action]", "y": ["[Action products]"],
             "title": "How many products",
             "pos": (794, ROW2_Y, 270, 258),
             "alt": "Bar chart titled How many products. Plots Action products for each "
                    "recommended action."},
            {"type": "slicer", "field": "recommendations[action]", "title": "Action",
             "pos": (1068, ROW2_Y, 192, 76),
             "alt": "Slicer. Filters the page by recommended action."},
            {"type": "slicer", "field": "recommendations[confidence]", "title": "Confidence",
             "pos": (1068, ROW2_Y + 86, 192, 76),
             "alt": "Slicer. Filters the page by confidence: how much evidence stands "
                    "behind the recommendation."},
        ],
    },
]

# Visual type -> the PBIR visualType string, and which query role each field
# well maps to. Role names matter: a scatter's identity field goes in
# "Category" (not "Details"), and a gauge takes Y plus TargetValue.
VISUAL_TYPES: dict[str, str] = {
    "card": "card",
    "bar": "clusteredBarChart",
    "column": "clusteredColumnChart",
    "stacked_column": "columnChart",
    "line": "lineChart",
    "area": "areaChart",
    "scatter": "scatterChart",
    "donut": "donutChart",
    "treemap": "treemap",
    "waterfall": "waterfallChart",
    "table": "tableEx",
    "matrix": "pivotTable",
    "slicer": "slicer",
    "gauge": "gauge",
}
