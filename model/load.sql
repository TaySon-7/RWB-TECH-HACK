-- Validated temporary projections; original records and decisions live in audit.
INSERT INTO core.dim_order_status VALUES
 ('created','Создан',1,false,false,false),
 ('approved','Подтверждён',2,false,false,false),
 ('invoiced','Выставлен счёт',3,false,false,false),
 ('processing','В обработке',4,false,false,false),
 ('shipped','Отправлен',5,false,false,false),
 ('delivered','Доставлен',6,true,false,true),
 ('canceled','Отменён',7,false,true,true),
 ('unavailable','Недоступен',8,false,false,true);
INSERT INTO core.dim_order_status
SELECT DISTINCT coalesce(nullif(btrim(order_status),''),'unknown'),
 coalesce(nullif(btrim(order_status),''),'Не указан'),99,false,false,false
FROM pg_temp.orders ON CONFLICT DO NOTHING;
INSERT INTO core.dim_payment_type VALUES
 ('credit_card','Кредитная карта',true),('boleto','Платёжный документ',true),
 ('voucher','Ваучер',true),('debit_card','Дебетовая карта',true),
 ('not_defined','Не определён',false);
INSERT INTO core.dim_payment_type
SELECT DISTINCT coalesce(nullif(btrim(payment_type),''),'not_defined'),
 coalesce(nullif(btrim(payment_type),''),'Не определён'),false
FROM pg_temp.order_payments ON CONFLICT DO NOTHING;

-- Cover every source timestamp and full calendar years, including empty months.
WITH dates AS (
 SELECT value::timestamp::date d
 FROM pg_temp.orders o CROSS JOIN LATERAL jsonb_each_text(to_jsonb(o))
 WHERE key IN ('order_purchase_timestamp','order_approved_at','order_delivered_carrier_date',
 'order_delivered_customer_date','order_estimated_delivery_date') AND nullif(value,'') IS NOT NULL
 UNION ALL SELECT nullif(shipping_limit_date,'')::timestamp::date FROM pg_temp.order_items
 UNION ALL SELECT nullif(review_creation_date,'')::timestamp::date FROM pg_temp.order_reviews
 UNION ALL SELECT nullif(review_answer_timestamp,'')::timestamp::date FROM pg_temp.order_reviews
), bounds AS (
 SELECT date_trunc('year',min(d)) first_day,
 date_trunc('year',max(d))+interval '1 year - 1 day' last_day FROM dates
)
INSERT INTO core.dim_date
SELECT d::date,date_trunc('month',d)::date,extract(year FROM d),
 extract(quarter FROM d),extract(isodow FROM d)
FROM bounds CROSS JOIN LATERAL generate_series(first_day,last_day,interval '1 day') d;

-- Prefix real names so the unknown key cannot collide with a source category.
INSERT INTO core.dim_category VALUES ('unknown',NULL,NULL,false,true);
INSERT INTO core.dim_category
SELECT 'pt:'||n.name_pt,n.name_pt,t.product_category_name_english,
 t.product_category_name_english IS NOT NULL,false
FROM (
 SELECT DISTINCT nullif(btrim(product_category_name),'') name_pt FROM pg_temp.products
 UNION SELECT nullif(btrim(product_category_name),'') FROM pg_temp.product_category_translation
) n LEFT JOIN pg_temp.product_category_translation t ON t.product_category_name=n.name_pt
WHERE n.name_pt IS NOT NULL;

INSERT INTO core.dim_seller
SELECT seller_id,CASE WHEN seller_zip_code_prefix ~ '^[0-9]{1,5}$' THEN lpad(seller_zip_code_prefix,5,'0') END,seller_city,
 lower(regexp_replace(btrim(seller_city),'\s+',' ','g')),CASE WHEN seller_state ~ '^[A-Z]{2}$' THEN seller_state END
FROM pg_temp.sellers;
INSERT INTO core.dim_product
SELECT product_id,coalesce('pt:'||nullif(btrim(product_category_name),''),'unknown'),
 nullif(product_name_lenght,'')::integer,nullif(product_description_lenght,'')::integer,
 nullif(product_photos_qty,'')::integer,nullif(product_weight_g,'')::integer,
 nullif(product_length_cm,'')::integer,nullif(product_height_cm,'')::integer,
 nullif(product_width_cm,'')::integer FROM pg_temp.products;

INSERT INTO core.dim_customer
SELECT c.customer_unique_id,min(o.order_purchase_timestamp::timestamp),count(*)
FROM pg_temp.orders o JOIN pg_temp.customers c USING(customer_id)
WHERE c.customer_unique_id IS NOT NULL GROUP BY c.customer_unique_id;
INSERT INTO core.fct_order (
 order_id,customer_key,status_code,purchase_date,source_customer_id,
 purchased_at,approved_at,carrier_at,delivered_at,estimated_date,
 ship_zip,ship_city,ship_state
)
SELECT o.order_id,c.customer_unique_id,coalesce(nullif(btrim(o.order_status),''),'unknown'),
 o.order_purchase_timestamp::timestamp::date,o.customer_id,
 o.order_purchase_timestamp::timestamp,nullif(o.order_approved_at,'')::timestamp,
 nullif(o.order_delivered_carrier_date,'')::timestamp,
 nullif(o.order_delivered_customer_date,'')::timestamp,
 nullif(o.order_estimated_delivery_date,'')::timestamp::date,
 CASE WHEN c.customer_zip_code_prefix ~ '^[0-9]{1,5}$' THEN lpad(c.customer_zip_code_prefix,5,'0') END,
 lower(regexp_replace(btrim(c.customer_city),'\s+',' ','g')),c.customer_state
FROM pg_temp.orders o LEFT JOIN pg_temp.customers c USING(customer_id);

INSERT INTO core.fct_order_seller
SELECT order_id,seller_id,min(shipping_limit_date::timestamp),count(*),
 sum(price::numeric(12,2)),sum(freight_value::numeric(12,2))
FROM pg_temp.order_items WHERE seller_id IS NOT NULL GROUP BY order_id,seller_id;
INSERT INTO core.fct_order_item
SELECT order_id,order_item_id::smallint,seller_id,product_id,
 shipping_limit_date::timestamp,price::numeric(12,2),freight_value::numeric(12,2)
FROM pg_temp.order_items;
INSERT INTO core.fct_payment
SELECT order_id,payment_sequential::smallint,coalesce(nullif(btrim(payment_type),''),'not_defined'),
 payment_installments::smallint,payment_value::numeric(12,2)
FROM pg_temp.order_payments;

-- A shared review_id may only be collapsed if all source review attributes agree.
DO $$ BEGIN
 IF EXISTS(SELECT review_id FROM pg_temp.order_reviews r GROUP BY review_id
 HAVING count(DISTINCT(to_jsonb(r)-'order_id'-'_source_file'-'_loaded_at'))>1)
 THEN RAISE EXCEPTION 'Conflicting attributes for review_id; do not deduplicate'; END IF;
END $$;
INSERT INTO core.fct_review
SELECT DISTINCT review_id,review_score::smallint,
 CASE WHEN nullif(btrim(review_comment_title),'') IS NOT NULL THEN review_comment_title END,
 CASE WHEN nullif(btrim(review_comment_message),'') IS NOT NULL THEN review_comment_message END,
 review_creation_date::timestamp,review_answer_timestamp::timestamp
FROM pg_temp.order_reviews;
-- Latest answer wins for the order score; ties: creation time, then review_id.
INSERT INTO core.fct_review_order
SELECT review_id,order_id,row_number() OVER (
 PARTITION BY order_id ORDER BY review_answer_timestamp::timestamp DESC,
 review_creation_date::timestamp DESC,review_id DESC)=1
FROM pg_temp.order_reviews;

WITH s AS (
 SELECT order_id,count(*) n,count(DISTINCT seller_id) sellers,
 sum(price) price,sum(freight) freight FROM core.fct_order_item GROUP BY order_id
)
UPDATE core.fct_order o SET items_count=s.n,sellers_count=s.sellers,
 items_price=s.price,items_freight=s.freight FROM s WHERE s.order_id=o.order_id;
WITH p AS (SELECT order_id,sum(amount) amount FROM core.fct_payment GROUP BY order_id)
UPDATE core.fct_order o SET paid_amount=p.amount FROM p WHERE p.order_id=o.order_id;
