-- Suspicious but representable facts remain in core. Contract errors are in audit.issue.
INSERT INTO core.dq_issue(check_id,entity,entity_key,details)
SELECT v.check_id,'core.fct_order',o.order_id,to_jsonb(o)
FROM core.fct_order o CROSS JOIN LATERAL (VALUES
 ('carrier_before_purchase',o.carrier_at<o.purchased_at),
 ('carrier_before_approval',o.carrier_at<o.approved_at),
 ('delivery_before_carrier',o.delivered_at<o.carrier_at),
 ('approval_before_purchase',o.approved_at<o.purchased_at),
 ('delivery_before_purchase',o.delivered_at<o.purchased_at),
 ('delivered_missing_delivery_date',o.status_code='delivered' AND o.delivered_at IS NULL),
 ('delivered_missing_carrier_date',o.status_code='delivered' AND o.carrier_at IS NULL),
 ('delivered_missing_approval_date',o.status_code='delivered' AND o.approved_at IS NULL),
 ('order_without_items',o.items_count=0),
 ('order_without_payments',o.paid_amount IS NULL),
 ('payment_items_mismatch',o.paid_amount IS NOT NULL AND o.paid_amount<>o.items_price+o.items_freight)
) v(check_id,failed) WHERE v.failed;

INSERT INTO core.dq_issue(check_id,entity,entity_key,details)
SELECT v.check_id,'core.fct_payment',jsonb_build_array(p.order_id,p.payment_seq)::text,to_jsonb(p)
FROM core.fct_payment p CROSS JOIN LATERAL (VALUES
 ('zero_installments',p.installments=0),('zero_payment',p.amount=0),
 ('undefined_payment_type',p.payment_type='not_defined')
) v(check_id,failed) WHERE v.failed;

INSERT INTO core.dq_issue(check_id,entity,entity_key,details)
SELECT v.check_id,'core.dim_product',p.product_id,to_jsonb(p)
FROM core.dim_product p JOIN core.dim_category c USING(category_key)
CROSS JOIN LATERAL (VALUES
 ('missing_category',c.is_unknown),
 ('category_without_translation',NOT c.is_unknown AND NOT c.is_translated),
 ('zero_weight',p.weight_g=0),
 ('missing_dimensions',p.weight_g IS NULL OR p.length_cm IS NULL OR p.height_cm IS NULL OR p.width_cm IS NULL)
) v(check_id,failed) WHERE v.failed;

INSERT INTO core.dq_issue(check_id,entity,entity_key,details)
SELECT 'multiple_shipping_limits','core.fct_order_seller',
 jsonb_build_array(order_id,seller_id)::text,
 jsonb_build_object('earliest',min(shipping_limit_at),'latest',max(shipping_limit_at))
FROM core.fct_order_item GROUP BY order_id,seller_id
HAVING min(shipping_limit_at)<>max(shipping_limit_at);

INSERT INTO core.dq_issue(check_id,entity,entity_key,details)
SELECT 'multiple_order_reviews','core.fct_order',order_id,
 jsonb_build_object('reviews_count',count(*))
FROM core.fct_review_order GROUP BY order_id HAVING count(*)>1;
