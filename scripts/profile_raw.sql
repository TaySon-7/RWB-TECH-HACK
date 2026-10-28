-- Read-only audit of source tables. Only temporary objects are created.
-- Run: docker compose exec -T postgres psql -U hack -d marketplace -v ON_ERROR_STOP=1 < scripts/profile_raw.sql
\set ON_ERROR_STOP on
BEGIN;
CREATE TEMP TABLE audit_results (section text, check_name text, affected_rows bigint);
DO $$
DECLARE t text; c record; k text; typ text; n bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['orders','order_items','order_payments','order_reviews','products','sellers','customers','product_category_translation'] LOOP
    EXECUTE format('SELECT count(*) FROM raw.%I',t) INTO n;
    INSERT INTO audit_results VALUES ('rows',t,n);
    FOR c IN SELECT column_name FROM information_schema.columns WHERE table_schema='raw' AND table_name=t AND column_name NOT LIKE '\_%' ESCAPE '\' LOOP
      EXECUTE format('SELECT count(*) FROM raw.%I WHERE nullif(btrim(%I),'''') IS NULL',t,c.column_name) INTO n;
      INSERT INTO audit_results VALUES ('missing',t||'.'||c.column_name,n);
      typ := CASE
        WHEN c.column_name IN ('price','freight_value','payment_value') THEN 'numeric(12,2)'
        WHEN c.column_name IN ('order_item_id','payment_sequential','payment_installments','review_score','product_name_lenght','product_description_lenght','product_photos_qty','product_weight_g','product_length_cm','product_height_cm','product_width_cm') THEN 'integer'
        WHEN c.column_name ~ '(timestamp|_date|_at)$' THEN 'timestamp'
        ELSE NULL END;
      IF typ IS NOT NULL THEN
        EXECUTE format('SELECT count(*) FROM raw.%I WHERE nullif(btrim(%I),'''') IS NOT NULL AND NOT pg_input_is_valid(%I,%L)',t,c.column_name,c.column_name,typ) INTO n;
        INSERT INTO audit_results VALUES ('invalid_type',t||'.'||c.column_name||' -> '||typ,n);
      END IF;
    END LOOP;
    k := CASE t WHEN 'orders' THEN 'order_id' WHEN 'order_items' THEN 'order_id,order_item_id' WHEN 'order_payments' THEN 'order_id,payment_sequential' WHEN 'order_reviews' THEN 'review_id,order_id' WHEN 'products' THEN 'product_id' WHEN 'sellers' THEN 'seller_id' WHEN 'customers' THEN 'customer_id' ELSE 'product_category_name' END;
    EXECUTE format('SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM raw.%I GROUP BY %s HAVING count(*)>1) d',t,k) INTO n;
    INSERT INTO audit_results VALUES ('duplicate_key',t||' ('||k||')',n);
    EXECUTE format('SELECT coalesce(sum(n-1),0) FROM (SELECT count(*) n FROM raw.%I x GROUP BY to_jsonb(x)-''_loaded_at''-''_source_file'' HAVING count(*)>1) d',t) INTO n;
    INSERT INTO audit_results VALUES ('duplicate_row',t,n);
  END LOOP;
  FOR c IN SELECT * FROM (VALUES
    ('orders','customer_id','customers','customer_id'),
    ('order_items','order_id','orders','order_id'),
    ('order_items','product_id','products','product_id'),
    ('order_items','seller_id','sellers','seller_id'),
    ('order_payments','order_id','orders','order_id'),
    ('order_reviews','order_id','orders','order_id'),
    ('products','product_category_name','product_category_translation','product_category_name')
  ) v(child,col,parent,pcol) LOOP
    EXECUTE format('SELECT count(*) FROM raw.%I x WHERE nullif(btrim(x.%I),'''') IS NOT NULL AND NOT EXISTS(SELECT 1 FROM raw.%I p WHERE p.%I=x.%I)',c.child,c.col,c.parent,c.pcol,c.col) INTO n;
    INSERT INTO audit_results VALUES ('missing_reference',c.child||'.'||c.col||' -> '||c.parent,n);
  END LOOP;
END $$;

INSERT INTO audit_results
SELECT 'coverage','orders without items',count(*) FROM raw.orders o WHERE NOT EXISTS(SELECT 1 FROM raw.order_items i WHERE i.order_id=o.order_id)
UNION ALL SELECT 'coverage','orders without payments',count(*) FROM raw.orders o WHERE NOT EXISTS(SELECT 1 FROM raw.order_payments p WHERE p.order_id=o.order_id)
UNION ALL SELECT 'coverage','orders without reviews',count(*) FROM raw.orders o WHERE NOT EXISTS(SELECT 1 FROM raw.order_reviews r WHERE r.order_id=o.order_id)
UNION ALL SELECT 'domain','unknown order status',count(*) FROM raw.orders WHERE order_status NOT IN ('delivered','shipped','canceled','unavailable','invoiced','processing','created','approved')
UNION ALL SELECT 'domain','unknown payment type',count(*) FROM raw.order_payments WHERE payment_type NOT IN ('credit_card','boleto','voucher','debit_card','not_defined')
UNION ALL SELECT 'domain','invalid state format (customers)',count(*) FROM raw.customers WHERE customer_state !~ '^[A-Z]{2}$'
UNION ALL SELECT 'domain','invalid state format (sellers)',count(*) FROM raw.sellers WHERE seller_state !~ '^[A-Z]{2}$'
UNION ALL SELECT 'domain','invalid zip prefix (customers)',count(*) FROM raw.customers WHERE customer_zip_code_prefix !~ '^[0-9]{1,5}$'
UNION ALL SELECT 'domain','invalid zip prefix (sellers)',count(*) FROM raw.sellers WHERE seller_zip_code_prefix !~ '^[0-9]{1,5}$';

-- Guard casts so the audit still runs if malformed values appear.
DO $$
DECLARE c record; n bigint;
BEGIN
 FOR c IN SELECT * FROM (VALUES
 ('order_items','price','< 0'),('order_items','freight_value','< 0'),
 ('order_items','order_item_id','<= 0'),('order_payments','payment_sequential','<= 0'),
 ('order_payments','payment_value','< 0'),('order_payments','payment_value','= 0'),
 ('order_payments','payment_installments','<= 0'),('order_reviews','review_score','NOT BETWEEN 1 AND 5'),
 ('products','product_weight_g','<= 0'),('products','product_length_cm','<= 0'),
 ('products','product_height_cm','<= 0'),('products','product_width_cm','<= 0'),
 ('products','product_photos_qty','< 0'),('products','product_name_lenght','< 0'),
 ('products','product_description_lenght','< 0')
 ) v(t,col,predicate) LOOP
  EXECUTE format('SELECT count(*) FROM raw.%I WHERE CASE WHEN pg_input_is_valid(%I,''numeric'') THEN %I::numeric %s ELSE false END',c.t,c.col,c.col,c.predicate) INTO n;
  INSERT INTO audit_results VALUES ('domain',c.t||'.'||c.col||' '||c.predicate,n);
 END LOOP;
 FOR c IN SELECT * FROM (VALUES
 ('orders','order_approved_at','order_purchase_timestamp'),
 ('orders','order_delivered_carrier_date','order_purchase_timestamp'),
 ('orders','order_delivered_carrier_date','order_approved_at'),
 ('orders','order_delivered_customer_date','order_purchase_timestamp'),
 ('orders','order_delivered_customer_date','order_delivered_carrier_date'),
 ('orders','order_estimated_delivery_date','order_purchase_timestamp'),
 ('order_reviews','review_answer_timestamp','review_creation_date')
 ) v(t,later_col,earlier_col) LOOP
  EXECUTE format('SELECT count(*) FROM raw.%I WHERE CASE WHEN pg_input_is_valid(%I,''timestamp'') AND pg_input_is_valid(%I,''timestamp'') THEN %I::timestamp < %I::timestamp ELSE false END',c.t,c.later_col,c.earlier_col,c.later_col,c.earlier_col) INTO n;
  INSERT INTO audit_results VALUES ('chronology',c.t||': '||c.later_col||' < '||c.earlier_col,n);
 END LOOP;
END $$;

INSERT INTO audit_results
SELECT 'consistency','delivered without customer delivery date',count(*) FROM raw.orders WHERE order_status='delivered' AND nullif(btrim(order_delivered_customer_date),'') IS NULL
UNION ALL SELECT 'consistency','delivered without carrier date',count(*) FROM raw.orders WHERE order_status='delivered' AND nullif(btrim(order_delivered_carrier_date),'') IS NULL
UNION ALL SELECT 'consistency','delivered without approval date',count(*) FROM raw.orders WHERE order_status='delivered' AND nullif(btrim(order_approved_at),'') IS NULL
UNION ALL SELECT 'consistency','review_id with different attributes',count(*) FROM (SELECT review_id FROM raw.order_reviews r GROUP BY review_id HAVING count(DISTINCT (to_jsonb(r)-'order_id'-'_source_file'-'_loaded_at'))>1) d
UNION ALL SELECT 'consistency','order/seller with different shipping limits',count(*) FROM (SELECT order_id,seller_id FROM raw.order_items GROUP BY 1,2 HAVING count(DISTINCT shipping_limit_date)>1) d;

\pset format csv
SELECT * FROM audit_results ORDER BY section,check_name;
ROLLBACK;
