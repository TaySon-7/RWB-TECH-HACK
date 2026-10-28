-- Read-only semantic views for sales/buyer panels; no ingestion changes.
-- Definer security exposes these named projections without granting Grafana
-- direct access to raw/core or write privileges.
CREATE OR REPLACE VIEW mart.commerce_orders SQL SECURITY DEFINER AS
SELECT order_id, purchase_date AS day, purchased_at, customer_key, ship_state,
       items_price_cents, items_freight_cents, items_count,
       min(purchased_at) OVER (PARTITION BY customer_key) AS first_delivered_purchase_at,
       toUInt8(purchased_at > first_delivered_purchase_at) AS is_returning
FROM core.fct_order WHERE status_code='delivered';

CREATE OR REPLACE VIEW mart.commerce_items SQL SECURITY DEFINER AS
SELECT i.order_id AS order_id, i.order_item_id AS order_item_id,
       o.purchase_date AS day, i.seller_id AS seller_id,
       s.city AS seller_city, s.state AS seller_state,
       i.product_id AS product_id, p.category_key AS category_key,
       coalesce(nullIf(c.name_en,''),nullIf(c.name_pt,''),'Не указана') AS category_name,
       i.price_cents AS price_cents, i.freight_cents AS freight_cents
FROM core.fct_order_item AS i
INNER JOIN core.fct_order AS o ON i.order_id=o.order_id
LEFT JOIN core.dim_product AS p ON i.product_id=p.product_id
LEFT JOIN core.dim_category AS c ON p.category_key=c.category_key
LEFT JOIN core.dim_seller AS s ON i.seller_id=s.seller_id
WHERE o.status_code='delivered';
