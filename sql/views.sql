-- Analytical views
-- -----------------------------------------------------------------------------
-- Rule used everywhere below: AGGREGATE EACH SOURCE TO THE SAME GRAIN (one row
-- per day) BEFORE JOINING. Joining orders -> order_items -> ad rows directly
-- would repeat each order's money once per line item and once per campaign.

-- One row per order line, with the order's reporting date attached.
-- (items -> orders is many-to-one, so no row is duplicated.)
CREATE OR REPLACE VIEW v_order_lines AS
SELECT
    oi.line_item_id,
    oi.order_id,
    o.order_date,
    oi.sku,
    oi.product_title,
    oi.quantity,
    oi.unit_price,
    oi.gross_amount,
    oi.discount_amount,
    oi.net_amount
FROM order_items AS oi
JOIN orders AS o ON o.order_id = oi.order_id;

-- Each line with the unit cost that was effective on the order date.
-- unit_cost is NULL when the cost sheet has no matching row: missing, NOT zero.
CREATE OR REPLACE VIEW v_line_costs AS
SELECT
    l.*,
    pc.unit_cost,
    l.quantity * pc.unit_cost AS line_cogs
FROM v_order_lines AS l
LEFT JOIN product_costs AS pc
    ON pc.sku = l.sku
   AND l.order_date >= pc.effective_from
   AND (pc.effective_to IS NULL OR l.order_date <= pc.effective_to);

-- Daily sales (by order date)
CREATE OR REPLACE VIEW v_daily_sales AS
WITH per_order AS (               -- step 1: one row per order
    SELECT order_id, order_date,
           SUM(quantity)        AS units,
           SUM(gross_amount)    AS gross_sales,
           SUM(discount_amount) AS discounts
    FROM v_order_lines
    GROUP BY order_id, order_date
)
SELECT order_date AS day,         -- step 2: one row per day
       COUNT(*)          AS orders,
       SUM(units)        AS units,
       SUM(gross_sales)  AS gross_sales,
       SUM(discounts)    AS discounts
FROM per_order
GROUP BY order_date;

-- Daily refunds (by refund date)
CREATE OR REPLACE VIEW v_daily_refunds AS
SELECT refund_date AS day,
       COUNT(*)            AS refund_count,
       SUM(quantity)       AS refunded_units,
       SUM(refund_amount)  AS refunds
FROM refunds
GROUP BY refund_date;

-- Daily COGS (by order date) with explicit missing-cost counts
CREATE OR REPLACE VIEW v_daily_cogs AS
SELECT order_date AS day,
       SUM(line_cogs)                                            AS cogs_known,
       SUM(CASE WHEN unit_cost IS NULL THEN quantity ELSE 0 END) AS units_missing_cost,
       COUNT(*) FILTER (WHERE unit_cost IS NULL)                 AS lines_missing_cost
FROM v_line_costs
GROUP BY order_date;

-- Daily fulfillment + payment costs, one column per category
CREATE OR REPLACE VIEW v_daily_fees AS
SELECT cost_date AS day,
       SUM(amount) FILTER (WHERE cost_category = 'pick_pack')          AS pick_pack,
       SUM(amount) FILTER (WHERE cost_category = 'packaging')          AS packaging,
       SUM(amount) FILTER (WHERE cost_category = 'shipping_labels')    AS shipping_labels,
       SUM(amount) FILTER (WHERE cost_category = 'payment_processing') AS payment_processing,
       SUM(amount) FILTER (WHERE cost_category <> 'payment_processing') AS fulfillment_total,
       SUM(amount)                                                      AS fees_total
FROM fulfillment_costs
GROUP BY cost_date;

-- Daily Meta advertising (spend is a cost; platform_* are attributed results)
CREATE OR REPLACE VIEW v_daily_ads AS
SELECT perf_date AS day,
       SUM(spend)                   AS ad_spend,
       SUM(impressions)             AS impressions,
       SUM(clicks)                  AS clicks,
       SUM(platform_purchases)      AS platform_purchases,
       SUM(platform_purchase_value) AS platform_purchase_value
FROM ad_daily_performance
GROUP BY perf_date;

-- Daily contribution profit: every input is already one row per day,
-- so these joins cannot multiply money. A date spine keeps days where a
-- source has no rows, and the NULLs stay NULL (missing is not zero).
CREATE OR REPLACE VIEW v_daily_contribution AS
WITH spine AS (
    SELECT day FROM v_daily_sales
    UNION SELECT day FROM v_daily_refunds
    UNION SELECT day FROM v_daily_fees
    UNION SELECT day FROM v_daily_ads
)
SELECT s.day,
       ds.orders,
       ds.units,
       ds.gross_sales,
       ds.discounts,
       COALESCE(dr.refunds, 0)  AS refunds,   -- no refund rows on a day = no refunds that day
       ds.gross_sales - ds.discounts - COALESCE(dr.refunds, 0) AS net_sales,
       dc.cogs_known,
       dc.units_missing_cost,
       df.fulfillment_total,
       df.payment_processing,
       df.fees_total,
       da.ad_spend
FROM spine AS s
LEFT JOIN v_daily_sales   AS ds ON ds.day = s.day
LEFT JOIN v_daily_refunds AS dr ON dr.day = s.day
LEFT JOIN v_daily_cogs    AS dc ON dc.day = s.day
LEFT JOIN v_daily_fees    AS df ON df.day = s.day
LEFT JOIN v_daily_ads     AS da ON da.day = s.day;

-- Product (SKU) by day: units, sales and known COGS
CREATE OR REPLACE VIEW v_product_daily AS
SELECT order_date AS day,
       sku,
       SUM(quantity)        AS units,
       SUM(gross_amount)    AS gross_sales,
       SUM(discount_amount) AS discounts,
       SUM(net_amount)      AS net_line_sales,
       SUM(line_cogs)       AS cogs_known,
       SUM(CASE WHEN unit_cost IS NULL THEN quantity ELSE 0 END) AS units_missing_cost
FROM v_line_costs
GROUP BY order_date, sku;

-- Refunds by SKU and refund date (refunds join to their line for the SKU)
CREATE OR REPLACE VIEW v_product_refunds AS
SELECT r.refund_date AS day,
       COALESCE(oi.sku, 'UNKNOWN') AS sku,
       COUNT(*)             AS refund_count,
       SUM(r.refund_amount) AS refunds
FROM refunds AS r
LEFT JOIN order_items AS oi ON oi.line_item_id = r.line_item_id
GROUP BY r.refund_date, COALESCE(oi.sku, 'UNKNOWN');

-- Campaign by day with campaign metadata (ad_campaigns is one row per campaign)
CREATE OR REPLACE VIEW v_campaign_daily AS
SELECT p.perf_date AS day,
       p.campaign_id,
       COALESCE(c.campaign_name, p.campaign_name) AS campaign_name,
       c.status,
       c.product_focus,
       p.spend,
       p.impressions,
       p.clicks,
       p.platform_purchases,
       p.platform_purchase_value
FROM ad_daily_performance AS p
LEFT JOIN ad_campaigns AS c ON c.campaign_id = p.campaign_id;
