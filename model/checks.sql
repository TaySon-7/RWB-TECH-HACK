-- Assertions depend on raw, not on hard-coded counts for one dataset.
DO $$
DECLARE t record; source_count bigint; target_count bigint;
BEGIN
 FOR t IN SELECT * FROM (VALUES
 ('orders','fct_order'),('order_items','fct_order_item'),
 ('order_payments','fct_payment'),('order_reviews','fct_review_order'),
 ('products','dim_product'),('sellers','dim_seller')
 ) v(source_name,target_name) LOOP
  EXECUTE format('SELECT count(*) FROM raw.%I',t.source_name) INTO source_count;
  EXECUTE format('SELECT count(*) FROM core.%I',t.target_name) INTO target_count;
  IF source_count<>target_count THEN
   RAISE EXCEPTION 'Row loss: % -> %, % <> %',t.source_name,t.target_name,source_count,target_count;
  END IF;
 END LOOP;
 IF (SELECT count(*) FROM core.dim_customer) <>
    (SELECT count(DISTINCT customer_unique_id) FROM raw.customers) THEN
  RAISE EXCEPTION 'Customer count does not match source'; END IF;
 IF (SELECT count(*) FROM core.fct_review) <>
    (SELECT count(DISTINCT review_id) FROM raw.order_reviews) THEN
  RAISE EXCEPTION 'Review count does not match source'; END IF;

 IF EXISTS (
  SELECT 1 FROM core.fct_order o
  LEFT JOIN (SELECT order_id,count(*) n,count(DISTINCT seller_id) sellers,
   sum(price) price,sum(freight) freight FROM core.fct_order_item GROUP BY order_id) i USING(order_id)
  LEFT JOIN (SELECT order_id,sum(amount) amount FROM core.fct_payment GROUP BY order_id) p USING(order_id)
  WHERE o.items_count<>coalesce(i.n,0) OR o.sellers_count<>coalesce(i.sellers,0)
   OR o.items_price<>coalesce(i.price,0) OR o.items_freight<>coalesce(i.freight,0)
   OR o.paid_amount IS DISTINCT FROM p.amount
 ) THEN RAISE EXCEPTION 'Order aggregates do not match detail rows'; END IF;

 IF EXISTS (
  SELECT order_id,seller_id,items_count,items_price,items_freight,shipping_limit_at
  FROM core.fct_order_seller
  EXCEPT
  SELECT order_id,seller_id,count(*),sum(price),sum(freight),min(shipping_limit_at)
  FROM core.fct_order_item GROUP BY order_id,seller_id
 ) THEN RAISE EXCEPTION 'Seller aggregates do not match detail rows'; END IF;

 IF EXISTS(SELECT order_id FROM core.fct_review_order GROUP BY order_id
  HAVING count(*) FILTER(WHERE is_order_score)<>1)
 THEN RAISE EXCEPTION 'Expected exactly one selected review per reviewed order'; END IF;

 IF (SELECT sum(price) FROM core.fct_order_item) IS DISTINCT FROM
    (SELECT sum(price::numeric(12,2)) FROM raw.order_items)
 OR (SELECT sum(freight) FROM core.fct_order_item) IS DISTINCT FROM
    (SELECT sum(freight_value::numeric(12,2)) FROM raw.order_items)
 OR (SELECT sum(amount) FROM core.fct_payment) IS DISTINCT FROM
    (SELECT sum(payment_value::numeric(12,2)) FROM raw.order_payments)
 THEN RAISE EXCEPTION 'Money totals differ from raw'; END IF;

 IF EXISTS (
  SELECT 1 FROM core.fct_order o CROSS JOIN LATERAL (VALUES
   (o.purchased_at::date),(o.approved_at::date),(o.carrier_at::date),
   (o.delivered_at::date),(o.estimated_date)
  ) v(d) WHERE v.d IS NOT NULL AND NOT EXISTS(SELECT 1 FROM core.dim_date c WHERE c.date_key=v.d)
 ) THEN RAISE EXCEPTION 'Calendar does not cover order timestamps'; END IF;
 RAISE NOTICE 'Core checks passed: row counts, references, aggregates, money, reviews, calendar.';
END $$;
