-- Data validation queries
-- -----------------------------------------------------------------------------
-- Each query returns rows ONLY when something is wrong. The loader and the
-- get_data_quality_report tool run them all. Format:
--   -- name: <check_name> | severity: <info|warning|error> | <description>

-- name: order_subtotal_mismatch | severity: error | Order subtotal differs from the sum of its line items (net of discounts)
SELECT o.order_id, o.subtotal_price, SUM(oi.net_amount) AS items_net
FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.order_id
GROUP BY o.order_id, o.subtotal_price
HAVING o.subtotal_price <> SUM(oi.net_amount);

-- name: order_discount_mismatch | severity: error | Order-level total_discounts differs from line-level discounts (same discounts at two grains: subtract only once)
SELECT o.order_id, o.total_discounts, SUM(oi.discount_amount) AS line_discounts
FROM orders AS o JOIN order_items AS oi ON oi.order_id = o.order_id
GROUP BY o.order_id, o.total_discounts
HAVING o.total_discounts <> SUM(oi.discount_amount);

-- name: orders_without_items | severity: error | Orders that have no line items
SELECT o.order_id FROM orders AS o
LEFT JOIN order_items AS oi ON oi.order_id = o.order_id
WHERE oi.order_id IS NULL;

-- name: items_without_orders | severity: error | Line items whose order is missing
SELECT oi.line_item_id, oi.order_id FROM order_items AS oi
LEFT JOIN orders AS o ON o.order_id = oi.order_id
WHERE o.order_id IS NULL;

-- name: refunds_unknown_order | severity: error | Refunds that reference an order not in the dataset
SELECT r.refund_id, r.order_id FROM refunds AS r
LEFT JOIN orders AS o ON o.order_id = r.order_id
WHERE o.order_id IS NULL;

-- name: refunds_exceed_line_value | severity: error | Refunded amount for a line is larger than what was paid for it
SELECT r.line_item_id, SUM(r.refund_amount) AS refunded, MAX(oi.net_amount) AS paid
FROM refunds AS r JOIN order_items AS oi ON oi.line_item_id = r.line_item_id
GROUP BY r.line_item_id
HAVING SUM(r.refund_amount) > MAX(oi.net_amount);

-- name: refund_before_order | severity: error | Refund processed before the order was placed
SELECT r.refund_id, r.processed_at_utc, o.created_at_utc FROM refunds AS r
JOIN orders AS o ON o.order_id = r.order_id
WHERE r.processed_at_utc < o.created_at_utc;

-- name: lines_missing_product_cost | severity: error | Sold SKUs with no unit cost effective on the order date (COGS incomplete)
SELECT sku, COUNT(*) AS lines, SUM(quantity) AS units, MIN(order_date) AS first_date, MAX(order_date) AS last_date
FROM v_line_costs WHERE unit_cost IS NULL
GROUP BY sku;

-- name: overlapping_cost_ranges | severity: error | A SKU has two cost rows effective on the same day (would double-count COGS)
SELECT a.sku, a.effective_from, b.effective_from AS overlaps_with
FROM product_costs AS a JOIN product_costs AS b
  ON a.sku = b.sku AND a.effective_from < b.effective_from
 AND (a.effective_to IS NULL OR a.effective_to >= b.effective_from);

-- name: unknown_skus | severity: warning | Line items whose SKU is not in the product catalog
SELECT oi.sku, COUNT(*) AS lines FROM order_items AS oi
LEFT JOIN products AS p ON p.sku = oi.sku
WHERE p.sku IS NULL GROUP BY oi.sku;

-- name: negative_amounts | severity: error | Unexpected negative money values
SELECT 'order_items' AS tbl, CAST(line_item_id AS VARCHAR) AS ref FROM order_items WHERE gross_amount < 0 OR discount_amount < 0 OR net_amount < 0
UNION ALL SELECT 'refunds', CAST(refund_id AS VARCHAR) FROM refunds WHERE refund_amount < 0
UNION ALL SELECT 'ad_daily_performance', CAST(perf_date AS VARCHAR) || ':' || campaign_id FROM ad_daily_performance WHERE spend < 0
UNION ALL SELECT 'fulfillment_costs', CAST(cost_date AS VARCHAR) || ':' || cost_category FROM fulfillment_costs WHERE amount < 0;

-- name: non_usd_currency | severity: error | Records not in the reporting currency (USD); no FX conversion is modeled
SELECT 'orders' AS tbl, currency, COUNT(*) AS n FROM orders WHERE currency <> 'USD' GROUP BY currency
UNION ALL SELECT 'refunds', currency, COUNT(*) FROM refunds WHERE currency <> 'USD' GROUP BY currency
UNION ALL SELECT 'ad_daily_performance', currency, COUNT(*) FROM ad_daily_performance WHERE currency <> 'USD' GROUP BY currency;

-- name: ad_rows_without_campaign | severity: warning | Ad performance rows whose campaign is missing from campaign metadata
SELECT p.campaign_id, COUNT(*) AS days FROM ad_daily_performance AS p
LEFT JOIN ad_campaigns AS c ON c.campaign_id = p.campaign_id
WHERE c.campaign_id IS NULL GROUP BY p.campaign_id;

-- name: fee_orders_covered_mismatch | severity: warning | The cost sheet's "orders covered" differs from Shopify's order count for that day
SELECT f.cost_date, MAX(f.orders_covered) AS sheet_orders, ds.orders AS shopify_orders
FROM fulfillment_costs AS f LEFT JOIN v_daily_sales AS ds ON ds.day = f.cost_date
GROUP BY f.cost_date, ds.orders
HAVING MAX(f.orders_covered) <> COALESCE(ds.orders, 0);

-- name: payment_fees_vs_expected | severity: warning | Recorded payment fees differ from 2.9% + $0.30 of charged totals by more than 1%
WITH expected AS (
    SELECT order_date AS day, SUM(ROUND(total_price * 0.029, 2) + 0.30) AS expected_fees
    FROM orders GROUP BY order_date
)
SELECT e.day, e.expected_fees, df.payment_processing AS recorded_fees
FROM expected AS e JOIN v_daily_fees AS df ON df.day = e.day
WHERE ABS(df.payment_processing - e.expected_fees) > 0.01 * e.expected_fees;
