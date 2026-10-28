-- Full, atomic rebuild of this model's tables. raw is never modified.
\set ON_ERROR_STOP on
BEGIN;
SELECT pg_advisory_xact_lock(74192318);
CREATE SCHEMA IF NOT EXISTS core;
\ir tables.sql
\ir audit.sql
TRUNCATE core.fct_review_order, core.fct_review, core.fct_payment,
 core.fct_order_item, core.fct_order_seller, core.fct_order,
 core.dim_customer, core.dim_product, core.dim_category, core.dim_seller,
 core.dim_order_status, core.dim_payment_type, core.dim_date,
 core.dq_issue RESTART IDENTITY;
\ir load.sql
\ir quality.sql
ANALYZE core.dim_customer;
ANALYZE core.dim_product;
ANALYZE core.fct_order;
ANALYZE core.fct_order_item;
ANALYZE core.fct_payment;
ANALYZE core.fct_order_seller;
ANALYZE core.fct_review;
ANALYZE core.fct_review_order;
\ir checks.sql
INSERT INTO audit.active_dataset(singleton,version_id)
 SELECT true,version_id FROM _version
 ON CONFLICT(singleton) DO UPDATE SET version_id=excluded.version_id;
\ir audit_views.sql
COMMIT;
SELECT 'orders' AS entity, count(*) FROM core.fct_order
UNION ALL SELECT 'customers', count(*) FROM core.dim_customer
UNION ALL SELECT 'items', count(*) FROM core.fct_order_item
UNION ALL SELECT 'payments', count(*) FROM core.fct_payment
UNION ALL SELECT 'reviews', count(*) FROM core.fct_review
UNION ALL SELECT 'review_order', count(*) FROM core.fct_review_order
UNION ALL SELECT 'quality_issues', count(*) FROM core.dq_issue;
