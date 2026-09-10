-- Pricing marts in SQL, over the same CSVs the Python engine reads.
--
-- Not a second implementation for its own sake. Two reasons it earns its place:
--
--  1. Most of this analysis, in a real company, lives in the warehouse rather
--     than in a notebook -- and "can you write it in SQL" is a question the job
--     asks. Every mart here is the same definition as the Python one, and
--     tests/test_sql_matches_python.py asserts the two agree to the cent. A
--     second implementation that disagrees is worth more than one that is never
--     checked, because it finds the disagreement.
--
--  2. Some of it is genuinely better in SQL. The price-band percentiles, the
--     rank-within-category, the running share of revenue and the month-over-
--     month change are window functions, and expressing them as such is
--     shorter and clearer than the pandas equivalent.
--
-- DuckDB dialect. It reads the CSVs directly, so there is no load step and no
-- database to keep in sync -- `read_csv_auto` over data/ is the whole source
-- layer.

-- Every mart below ends on a *total* order -- one that no two rows can tie on.
-- A SELECT with no ORDER BY, or one ordered on a column with ties, is free to
-- come back in any order at all, and DuckDB takes that freedom: these CSVs are
-- committed, and the same query returned a different first row on Linux than
-- on Windows. Nothing was wrong with the numbers; the file simply was not
-- reproducible, which is most of what a committed artefact is for.
--
-- ===========================================================================
-- Source layer
-- ===========================================================================

CREATE OR REPLACE VIEW src_sales AS
SELECT * FROM read_csv_auto('data/fact_sales.csv', header = true);

CREATE OR REPLACE VIEW src_product AS
SELECT * FROM read_csv_auto('data/dim_product.csv', header = true);

CREATE OR REPLACE VIEW src_customer AS
SELECT * FROM read_csv_auto('data/dim_customer.csv', header = true);

CREATE OR REPLACE VIEW src_salesperson AS
SELECT * FROM read_csv_auto('data/dim_salesperson.csv', header = true);

CREATE OR REPLACE VIEW src_month AS
SELECT * FROM read_csv_auto('data/dim_month.csv', header = true);

-- One wide view every mart below builds on, so a join is written once. The
-- fiscal year is derived here rather than joined, because dim_month carries it
-- and a second definition of "which year is this" is exactly the kind of thing
-- that ends up disagreeing between two reports.
CREATE OR REPLACE VIEW fct_sales AS
SELECT
    s.month,
    m.fiscal_year,
    m.fiscal_year_label,
    m.month_index,
    s.product_id,
    p.description,
    p.category,
    p.sub_category,
    p.brand_tier,
    p.lifecycle,
    s.customer_id,
    c.customer_name,
    c.segment,
    c.channel,
    c.region,
    c.tier,
    c.price_list,
    c.salesperson,
    s.quantity_units,
    s.list_price,
    s.invoice_price,
    s.net_price,
    s.pocket_price,
    s.final_cost,
    s.on_invoice_discounts,
    s.off_invoice_deductions,
    s.cost_to_serve,
    s.list_value,
    s.revenue        AS invoice_revenue,
    s.pocket_revenue,
    s.cogs,
    s.pocket_margin,
    s.on_promotion
FROM src_sales   s
JOIN src_product  p ON p.product_id  = s.product_id
JOIN src_customer c ON c.customer_id = s.customer_id
JOIN src_month    m ON m.month       = s.month;

-- ===========================================================================
-- Mart 1: the price waterfall, by fiscal year
-- ===========================================================================
-- The four levels and the two margins. Deductions are stored per unit, so
-- every one of them is extended by quantity before it is summed -- summing the
-- per-unit column would weight a twelve-unit line the same as a twelve-
-- thousand-unit one, which is the single most common way a waterfall is built
-- wrong.

CREATE OR REPLACE VIEW mart_waterfall AS
SELECT
    fiscal_year,
    COUNT(*)                                         AS lines,
    SUM(quantity_units)                              AS volume_units,
    SUM(list_value)                                  AS list_value,
    SUM(on_invoice_discounts * quantity_units)       AS on_invoice_discounts,
    SUM(invoice_revenue)                             AS invoice_revenue,
    SUM(off_invoice_deductions * quantity_units)     AS off_invoice_deductions,
    SUM(invoice_revenue) - SUM(off_invoice_deductions * quantity_units) AS net_revenue,
    SUM(cost_to_serve * quantity_units)              AS cost_to_serve,
    SUM(pocket_revenue)                              AS pocket_revenue,
    SUM(cogs)                                        AS cogs,
    SUM(pocket_revenue) - SUM(cogs)                  AS pocket_margin,
    (SUM(list_value) - SUM(pocket_revenue)) / NULLIF(SUM(list_value), 0) AS leakage_pct,
    (SUM(pocket_revenue) - SUM(cogs)) / NULLIF(SUM(pocket_revenue), 0)   AS pocket_margin_pct
FROM fct_sales
GROUP BY fiscal_year
ORDER BY fiscal_year;

-- ===========================================================================
-- Mart 2: profitability by every dimension, in one pass
-- ===========================================================================
-- GROUPING SETS rather than nine separate queries unioned together. It is one
-- scan of the fact instead of nine, and -- more usefully -- adding a tenth cut
-- is one line rather than a copy-paste that drifts.

CREATE OR REPLACE VIEW mart_profitability AS
WITH cuts AS (
    SELECT
        COALESCE(category,    '(all)') AS category,
        COALESCE(brand_tier,  '(all)') AS brand_tier,
        COALESCE(segment,     '(all)') AS segment,
        COALESCE(channel,     '(all)') AS channel,
        COALESCE(region,      '(all)') AS region,
        COALESCE(salesperson, '(all)') AS salesperson,
        SUM(quantity_units)                        AS volume_units,
        SUM(list_value)                            AS list_value,
        SUM(pocket_revenue)                        AS pocket_revenue,
        SUM(cogs)                                  AS cogs,
        SUM(cost_to_serve * quantity_units)        AS cost_to_serve,
        COUNT(*)                                   AS lines,
        COUNT(DISTINCT product_id)                 AS products,
        COUNT(DISTINCT customer_id)                AS customers
    FROM fct_sales
    WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM fct_sales)
    GROUP BY GROUPING SETS (
        (category), (brand_tier), (segment), (channel), (region), (salesperson)
    )
)
SELECT
    CASE
        WHEN category    <> '(all)' THEN 'category'
        WHEN brand_tier  <> '(all)' THEN 'brand_tier'
        WHEN segment     <> '(all)' THEN 'segment'
        WHEN channel     <> '(all)' THEN 'channel'
        WHEN region      <> '(all)' THEN 'region'
        ELSE 'salesperson'
    END AS dimension,
    CASE
        WHEN category    <> '(all)' THEN category
        WHEN brand_tier  <> '(all)' THEN brand_tier
        WHEN segment     <> '(all)' THEN segment
        WHEN channel     <> '(all)' THEN channel
        WHEN region      <> '(all)' THEN region
        ELSE salesperson
    END AS member,
    volume_units,
    list_value,
    pocket_revenue,
    cogs,
    cost_to_serve,
    lines,
    products,
    customers,
    pocket_revenue - cogs                                        AS gross_margin,
    (pocket_revenue - cogs) / NULLIF(pocket_revenue, 0)          AS gross_margin_pct,
    1 - pocket_revenue / NULLIF(list_value, 0)                   AS leakage_pct,
    pocket_revenue / NULLIF(volume_units, 0)                     AS price_unit,
    pocket_revenue / SUM(pocket_revenue) OVER (PARTITION BY
        CASE
            WHEN category   <> '(all)' THEN 'category'
            WHEN brand_tier <> '(all)' THEN 'brand_tier'
            WHEN segment    <> '(all)' THEN 'segment'
            WHEN channel    <> '(all)' THEN 'channel'
            WHEN region     <> '(all)' THEN 'region'
            ELSE 'salesperson'
        END)                                                     AS revenue_share
FROM cuts
ORDER BY dimension, member;

-- ===========================================================================
-- Mart 3: the price band per product
-- ===========================================================================
-- Percentiles across the customers who bought one product. The width of that
-- band is the most reliable margin opportunity in any book of business, and
-- `PERCENTILE_CONT` says it in one line where the pandas version needs a
-- weighted-quantile helper.
--
-- Deliberately *unweighted* here, unlike the Python version which weights by
-- volume. The two answer different questions -- "what do customers pay" versus
-- "what does volume pay" -- and the test compares each against its own
-- definition rather than pretending they should match.

CREATE OR REPLACE VIEW mart_price_bands AS
SELECT
    product_id,
    ANY_VALUE(description)                                   AS description,
    ANY_VALUE(category)                                      AS category,
    COUNT(DISTINCT customer_id)                              AS customers,
    SUM(quantity_units)                                      AS volume_units,
    PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY pocket_price) AS p10_price,
    PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY pocket_price) AS median_price,
    PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY pocket_price) AS p90_price,
    (PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY pocket_price)
     - PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY pocket_price))
        / NULLIF(PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY pocket_price), 0)
                                                             AS band_width_pct
FROM fct_sales
WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM fct_sales)
GROUP BY product_id
HAVING COUNT(*) >= 8
ORDER BY product_id;

-- ===========================================================================
-- Mart 4: month-over-month and year-over-year, with window functions
-- ===========================================================================
-- LAG over a contiguous month index. The index has to be contiguous or LAG 12
-- silently reaches the wrong month -- which is why dim_month builds it as a
-- running integer rather than as year*12+month over whatever months happen to
-- have rows.

CREATE OR REPLACE VIEW mart_monthly_trend AS
WITH monthly AS (
    SELECT
        month,
        month_index,
        fiscal_year,
        SUM(quantity_units) AS volume_units,
        SUM(list_value)     AS list_value,
        SUM(pocket_revenue) AS pocket_revenue,
        SUM(cogs)           AS cogs
    FROM fct_sales
    GROUP BY month, month_index, fiscal_year
)
SELECT
    month,
    month_index,
    fiscal_year,
    volume_units,
    pocket_revenue,
    pocket_revenue - cogs                                     AS gross_margin,
    (pocket_revenue - cogs) / NULLIF(pocket_revenue, 0)       AS gross_margin_pct,
    1 - pocket_revenue / NULLIF(list_value, 0)                AS leakage_pct,
    pocket_revenue / NULLIF(volume_units, 0)                  AS realised_price_unit,
    LAG(pocket_revenue, 1)  OVER (ORDER BY month_index)       AS pocket_revenue_prior_month,
    LAG(pocket_revenue, 12) OVER (ORDER BY month_index)       AS pocket_revenue_prior_year,
    pocket_revenue / NULLIF(LAG(pocket_revenue, 12)
        OVER (ORDER BY month_index), 0) - 1                   AS yoy_pct,
    AVG(pocket_revenue) OVER (
        ORDER BY month_index ROWS BETWEEN 2 PRECEDING AND CURRENT ROW
    )                                                         AS revenue_3m_average
FROM monthly
ORDER BY month_index;

-- ===========================================================================
-- Mart 5: margin concentration
-- ===========================================================================
-- Which products carry the margin, and how concentrated it is. The running
-- share is the number worth having: "the top 8% of SKUs carry half the margin"
-- changes what a pricing analyst spends the week on.

CREATE OR REPLACE VIEW mart_margin_concentration AS
WITH by_product AS (
    SELECT
        product_id,
        ANY_VALUE(description) AS description,
        ANY_VALUE(category)    AS category,
        SUM(pocket_revenue)    AS pocket_revenue,
        SUM(pocket_revenue) - SUM(cogs) AS gross_margin
    FROM fct_sales
    WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM fct_sales)
    GROUP BY product_id
)
SELECT
    product_id,
    description,
    category,
    pocket_revenue,
    gross_margin,
    gross_margin / NULLIF(pocket_revenue, 0) AS gross_margin_pct,
    ROW_NUMBER() OVER (ORDER BY gross_margin DESC, product_id)       AS margin_rank,
    RANK()       OVER (PARTITION BY category ORDER BY gross_margin DESC)
                                                                     AS rank_in_category,
    -- ROWS, not the default RANGE. With RANGE, tied margins are lumped into
    -- one step and the concentration curve jumps; ROWS advances one product at
    -- a time, which is what a Pareto curve means.
    SUM(gross_margin) OVER (
        ORDER BY gross_margin DESC, product_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
        / NULLIF(SUM(gross_margin) OVER (), 0)                       AS running_margin_share,
    ROW_NUMBER() OVER (ORDER BY gross_margin DESC, product_id)
        * 1.0 / COUNT(*) OVER ()                                     AS running_product_share
FROM by_product
ORDER BY gross_margin DESC, product_id;

-- ===========================================================================
-- Mart 6: exceptions
-- ===========================================================================
-- The same guardrail idea as pricing/guardrails.py, expressed as a query.
-- Ranked by money at risk rather than by severity, for the same reason: a scan
-- sorted by how far below floor a line sits puts the analyst on a forty-dollar
-- account for the first twenty minutes.

CREATE OR REPLACE VIEW mart_exceptions AS
WITH scored AS (
    SELECT
        s.month,
        s.product_id,
        s.description,
        s.category,
        s.customer_id,
        s.customer_name,
        s.segment,
        s.salesperson,
        s.quantity_units,
        s.list_price,
        s.pocket_price,
        s.final_cost,
        (s.pocket_price - s.final_cost) / NULLIF(s.pocket_price, 0) AS pocket_margin_pct,
        1 - s.pocket_price / NULLIF(s.list_price, 0)                AS leakage_pct,
        (s.pocket_price - s.final_cost) * s.quantity_units          AS extended_margin,
        p.target_margin,
        GREATEST(0.05, p.target_margin - 0.09)                      AS floor_margin
    FROM fct_sales s
    JOIN src_product p ON p.product_id = s.product_id
    WHERE s.month = (SELECT MAX(month) FROM fct_sales)
)
SELECT
    *,
    CASE
        WHEN pocket_margin_pct <= 0                 THEN 'Loss-making'
        WHEN pocket_margin_pct < floor_margin       THEN 'Below floor'
        WHEN leakage_pct > 0.25                     THEN 'Excessive leakage'
        WHEN pocket_margin_pct < target_margin      THEN 'Below target'
        ELSE 'Within guardrail'
    END AS verdict,
    GREATEST(0, target_margin - pocket_margin_pct) * pocket_price * quantity_units
        AS margin_gap_dollars
FROM scored
WHERE pocket_margin_pct < floor_margin OR leakage_pct > 0.25
ORDER BY extended_margin DESC, month, product_id, customer_id;
