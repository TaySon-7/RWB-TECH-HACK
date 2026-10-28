-- Canonical views use FINAL: Kafka is at-least-once, so duplicate deliveries
-- must never inflate money even before background ReplacingMergeTree merges.
CREATE DATABASE IF NOT EXISTS core;
CREATE DATABASE IF NOT EXISTS mart;
CREATE DATABASE IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.load_runs (
 dataset_hash String, source_version UInt64, rows UInt64,
 protobuf_bytes UInt64, json_bytes UInt64, elapsed_seconds Float64,
 completed_at DateTime DEFAULT now()
) ENGINE = MergeTree ORDER BY (completed_at, dataset_hash);

CREATE OR REPLACE VIEW core.dim_customer AS
SELECT customer_unique_id AS customer_key,
 min(fromUnixTimestamp64Milli(order_purchase_timestamp_ms, 'UTC')) AS first_order_at,
 count() AS orders_count
FROM raw.orders AS o FINAL
INNER JOIN raw.customers AS c FINAL ON o.customer_id = c.customer_id
GROUP BY customer_key;

CREATE OR REPLACE VIEW core.dim_seller AS
SELECT seller_id, leftPad(seller_zip_code_prefix, 5, '0') AS zip,
 seller_city AS city_raw, lowerUTF8(trimBoth(replaceRegexpAll(seller_city, '\\s+', ' '))) AS city,
 seller_state AS state FROM raw.sellers FINAL;

CREATE OR REPLACE VIEW core.dim_category AS
SELECT concat('pt:', product_category_name) AS category_key,
 product_category_name AS name_pt, t.product_category_name_english AS name_en,
 t.product_category_name_english != '' AS is_translated, 0 AS is_unknown
FROM (
 SELECT product_category_name FROM raw.products FINAL WHERE product_category_name != ''
 UNION DISTINCT
 SELECT product_category_name FROM raw.product_category_translation FINAL
) AS categories
LEFT JOIN raw.product_category_translation AS t FINAL USING (product_category_name)
UNION ALL SELECT 'unknown', '', '', 0, 1;

CREATE OR REPLACE VIEW core.dim_product AS
SELECT product_id,
 if(product_category_name = '', 'unknown', concat('pt:', product_category_name)) AS category_key,
 nullIf(product_name_lenght, -1) AS name_length,
 nullIf(product_description_lenght, -1) AS description_length,
 nullIf(product_photos_qty, -1) AS photos_qty,
 nullIf(product_weight_g, -1) AS weight_g,
 nullIf(product_length_cm, -1) AS length_cm,
 nullIf(product_height_cm, -1) AS height_cm,
 nullIf(product_width_cm, -1) AS width_cm
FROM raw.products FINAL;

CREATE OR REPLACE VIEW core.fct_order_item AS
SELECT order_id, order_item_id, product_id, seller_id,
 fromUnixTimestamp64Milli(shipping_limit_date_ms, 'UTC') AS shipping_limit_at,
 toDecimal64(price_cents, 2) / 100 AS price,
 toDecimal64(freight_cents, 2) / 100 AS freight,
 price_cents, freight_cents
FROM raw.order_items FINAL;

CREATE OR REPLACE VIEW core.fct_payment AS
SELECT order_id, payment_sequential AS payment_seq, payment_type,
 payment_installments AS installments,
 toDecimal64(amount_cents, 2) / 100 AS amount, amount_cents
FROM raw.order_payments FINAL;

CREATE OR REPLACE VIEW core.fct_order AS
SELECT o.order_id AS order_id, c.customer_unique_id AS customer_key,
 o.customer_id AS source_customer_id, o.order_status AS status_code,
 toDate(fromUnixTimestamp64Milli(o.order_purchase_timestamp_ms, 'UTC')) AS purchase_date,
 fromUnixTimestamp64Milli(o.order_purchase_timestamp_ms, 'UTC') AS purchased_at,
 fromUnixTimestamp64Milli(nullIf(o.order_approved_at_ms, 0), 'UTC') AS approved_at,
 fromUnixTimestamp64Milli(nullIf(o.order_delivered_carrier_date_ms, 0), 'UTC') AS carrier_at,
 fromUnixTimestamp64Milli(nullIf(o.order_delivered_customer_date_ms, 0), 'UTC') AS delivered_at,
 toDate(fromUnixTimestamp64Milli(nullIf(o.order_estimated_delivery_date_ms, 0), 'UTC')) AS estimated_date,
 leftPad(c.customer_zip_code_prefix, 5, '0') AS ship_zip,
 lowerUTF8(trimBoth(replaceRegexpAll(c.customer_city, '\\s+', ' '))) AS ship_city,
 c.customer_state AS ship_state,
 i.items_count AS items_count, i.sellers_count AS sellers_count,
 i.items_price_cents AS items_price_cents, i.items_freight_cents AS items_freight_cents,
 toDecimal64(i.items_price_cents, 2) / 100 AS items_price,
 toDecimal64(i.items_freight_cents, 2) / 100 AS items_freight,
 if(p.payment_count = 0, NULL, toDecimal64(p.paid_cents, 2) / 100) AS paid_amount
FROM raw.orders AS o FINAL
LEFT JOIN raw.customers AS c FINAL ON o.customer_id = c.customer_id
LEFT JOIN (
 SELECT order_id, count() AS items_count, uniqExact(seller_id) AS sellers_count,
 sum(price_cents) AS items_price_cents, sum(freight_cents) AS items_freight_cents
 FROM raw.order_items FINAL GROUP BY order_id
) AS i ON o.order_id = i.order_id
LEFT JOIN (
 SELECT order_id, count() AS payment_count, sum(amount_cents) AS paid_cents
 FROM raw.order_payments FINAL GROUP BY order_id
) AS p ON o.order_id = p.order_id;

CREATE OR REPLACE VIEW core.fct_order_seller AS
SELECT order_id, seller_id, min(shipping_limit_at) AS shipping_limit_at,
 count() AS items_count, sum(price) AS items_price, sum(freight) AS items_freight
FROM core.fct_order_item GROUP BY order_id, seller_id;

CREATE OR REPLACE VIEW core.fct_review AS
SELECT review_id, any(review_score) AS score,
 any(review_comment_title) AS title, any(review_comment_message) AS message,
 toDate(fromUnixTimestamp64Milli(any(review_creation_date_ms), 'UTC')) AS created_date,
 fromUnixTimestamp64Milli(any(review_answer_timestamp_ms), 'UTC') AS answered_at
FROM raw.order_reviews FINAL GROUP BY review_id;

CREATE OR REPLACE VIEW core.fct_review_order AS
SELECT review_id, order_id,
 row_number() OVER (PARTITION BY order_id ORDER BY review_answer_timestamp_ms DESC,
 review_creation_date_ms DESC, review_id DESC) = 1 AS is_order_score
FROM raw.order_reviews FINAL;

CREATE OR REPLACE VIEW core.dim_date AS
SELECT toDate(day_number) AS date_key, toStartOfMonth(date_key) AS month_start,
 toYear(date_key) AS year, toQuarter(date_key) AS quarter, toDayOfWeek(date_key) AS iso_dow
FROM (
 SELECT arrayJoin(range(
  toUInt32(toDate(toStartOfYear(min(purchased_at)))),
  toUInt32(toDate(addYears(toStartOfYear(max(purchased_at)), 1)))
 )) AS day_number FROM core.fct_order
);

CREATE OR REPLACE VIEW core.dim_order_status AS
SELECT status_code, status_code = 'delivered' AS is_revenue,
 status_code = 'canceled' AS is_cancelled,
 status_code IN ('delivered', 'canceled', 'unavailable') AS is_final
FROM (SELECT arrayJoin(['created','approved','invoiced','processing','shipped',
                       'delivered','canceled','unavailable']) AS status_code);

CREATE OR REPLACE VIEW core.dim_payment_type AS
SELECT DISTINCT payment_type FROM raw.order_payments FINAL;

CREATE OR REPLACE VIEW core.dq_issue AS
SELECT 'payment_items_mismatch' AS check_id, order_id AS entity_key
FROM core.fct_order
WHERE paid_amount IS NOT NULL AND abs(paid_amount - items_price - items_freight) > 0.01
UNION ALL
SELECT 'missing_payment', order_id FROM core.fct_order WHERE paid_amount IS NULL
UNION ALL
SELECT 'missing_items', order_id FROM core.fct_order WHERE items_count = 0
UNION ALL
SELECT 'invalid_delivery_chronology', order_id FROM core.fct_order WHERE delivered_at < purchased_at;

-- Publishing uses EXCHANGE TABLES: all dashboard panels query one validated snapshot.
CREATE TABLE IF NOT EXISTS mart.sales_daily (
 day Date, revenue_cents Int64, freight_cents Int64, orders_count UInt64
) ENGINE = MergeTree ORDER BY day;

CREATE OR REPLACE VIEW mart.sales_metrics AS
SELECT day, toDecimal64(revenue_cents, 2) / 100 AS revenue,
 toDecimal64(freight_cents, 2) / 100 AS freight, orders_count
FROM mart.sales_daily;
