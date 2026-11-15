-- Northstar Supply analytical schema (DuckDB)
-- -----------------------------------------------------------------------------
-- Each table keeps ONE grain (one row = one "thing"). Mixing grains in a join
-- is the most common way to double-count money, so the grain is written above
-- every table. Money uses DECIMAL so sums are exact to the cent.
-- Dates called *_date are calendar dates in the reporting timezone
-- (America/New_York); *_utc columns keep the original UTC timestamps.

-- Grain: one row per product (Shopify catalog)
CREATE TABLE products (
    product_id      VARCHAR PRIMARY KEY,
    sku             VARCHAR NOT NULL UNIQUE,
    title           VARCHAR NOT NULL,
    category        VARCHAR,
    list_price      DECIMAL(12, 2),
    status          VARCHAR
);

-- Grain: one row per Shopify order
-- subtotal_price is AFTER discounts and excludes shipping and tax.
-- total_price includes shipping and tax, so it is NOT product sales.
CREATE TABLE orders (
    order_id            BIGINT PRIMARY KEY,
    order_name          VARCHAR,
    created_at_utc      TIMESTAMP NOT NULL,
    order_date          DATE NOT NULL,
    customer_id         VARCHAR,
    currency            VARCHAR NOT NULL,
    financial_status    VARCHAR,
    fulfillment_status  VARCHAR,
    discount_codes      VARCHAR,
    subtotal_price      DECIMAL(12, 2) NOT NULL,
    total_discounts     DECIMAL(12, 2) NOT NULL,
    shipping_charged    DECIMAL(12, 2) NOT NULL,
    total_tax           DECIMAL(12, 2) NOT NULL,
    total_price         DECIMAL(12, 2) NOT NULL,
    source_name         VARCHAR,
    payment_gateway     VARCHAR
);

-- Grain: one row per order line (a product within an order)
-- gross_amount = quantity * unit_price; net_amount = gross_amount - discount_amount
CREATE TABLE order_items (
    line_item_id     BIGINT PRIMARY KEY,
    order_id         BIGINT NOT NULL,
    product_id       VARCHAR,
    sku              VARCHAR NOT NULL,
    product_title    VARCHAR,
    quantity         INTEGER NOT NULL,
    unit_price       DECIMAL(12, 2) NOT NULL,
    gross_amount     DECIMAL(12, 2) NOT NULL,
    discount_amount  DECIMAL(12, 2) NOT NULL,
    net_amount       DECIMAL(12, 2) NOT NULL
);

-- Grain: one row per refund line. refund_date (reporting timezone) decides
-- which reporting period the refund belongs to — NOT the original order date.
CREATE TABLE refunds (
    refund_id         BIGINT PRIMARY KEY,
    order_id          BIGINT NOT NULL,
    line_item_id      BIGINT,
    processed_at_utc  TIMESTAMP NOT NULL,
    refund_date       DATE NOT NULL,
    quantity          INTEGER,
    refund_amount     DECIMAL(12, 2) NOT NULL,
    restocked         BOOLEAN,
    reason            VARCHAR,   -- free text: untrusted
    currency          VARCHAR NOT NULL
);

-- Grain: one row per SKU per effective-date range (Google Sheet)
CREATE TABLE product_costs (
    sku             VARCHAR NOT NULL,
    product_name    VARCHAR,
    unit_cost       DECIMAL(12, 2) NOT NULL,
    effective_from  DATE NOT NULL,
    effective_to    DATE,        -- NULL = still in effect
    notes           VARCHAR,
    PRIMARY KEY (sku, effective_from)
);

-- Grain: one row per day per cost category (Google Sheet of 3PL, carrier and
-- payment-processor invoices). Costs are assigned to the ORDER date.
CREATE TABLE fulfillment_costs (
    cost_date       DATE NOT NULL,
    cost_category   VARCHAR NOT NULL,  -- pick_pack | packaging | shipping_labels | payment_processing
    amount          DECIMAL(12, 2) NOT NULL,
    orders_covered  INTEGER,
    source          VARCHAR,
    notes           VARCHAR,
    PRIMARY KEY (cost_date, cost_category)
);

-- Grain: one row per Meta campaign
CREATE TABLE ad_campaigns (
    campaign_id     VARCHAR PRIMARY KEY,
    campaign_name   VARCHAR,           -- free text: untrusted
    objective       VARCHAR,
    status          VARCHAR,
    start_date      DATE,
    stop_date       DATE,
    daily_budget    DECIMAL(12, 2),
    product_focus   VARCHAR,
    notes           VARCHAR            -- free text: untrusted
);

-- Grain: one row per campaign per day (Meta Insights, level=campaign, time_increment=1)
-- platform_* columns are Meta's ATTRIBUTED results. They overlap with Shopify
-- orders and must never be added to Shopify sales.
CREATE TABLE ad_daily_performance (
    perf_date                 DATE NOT NULL,
    campaign_id               VARCHAR NOT NULL,
    campaign_name             VARCHAR,
    spend                     DECIMAL(12, 2) NOT NULL,
    impressions               BIGINT,
    clicks                    BIGINT,
    platform_purchases        INTEGER,
    platform_purchase_value   DECIMAL(12, 2),
    currency                  VARCHAR NOT NULL,
    PRIMARY KEY (perf_date, campaign_id)
);

-- Grain: one row per target metric (fictional targets from a Google Sheet)
CREATE TABLE business_targets (
    metric        VARCHAR PRIMARY KEY,
    target_value  DOUBLE NOT NULL,
    unit          VARCHAR,
    direction     VARCHAR,   -- min | max
    period        VARCHAR,
    notes         VARCHAR
);

-- Issues found while ingesting the sources (written by the loader only)
CREATE TABLE data_quality_issues (
    issue_id    INTEGER PRIMARY KEY,
    source      VARCHAR,
    check_name  VARCHAR,
    severity    VARCHAR,
    message     VARCHAR,
    record_ref  VARCHAR,
    issue_count INTEGER
);

-- One row describing how this database was built
CREATE TABLE ingestion_runs (
    run_id        VARCHAR PRIMARY KEY,
    loaded_at_utc TIMESTAMP,
    data_source   VARCHAR,
    scenario      VARCHAR,
    reporting_tz  VARCHAR,
    row_counts    VARCHAR    -- JSON text
);
