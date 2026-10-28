-- Candidate views are used only by deterministic audit code, never by LLM SQL.
-- They include rejected rows, so the audit can explain what core did not admit.
CREATE OR REPLACE FUNCTION audit.safe_timestamp(v text) RETURNS timestamp
LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN audit.valid_value(v,'timestamp') THEN v::timestamp END
$$;
CREATE OR REPLACE FUNCTION audit.safe_money(v text) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN audit.valid_value(v,'money') THEN v::numeric(12,2) END
$$;
CREATE OR REPLACE FUNCTION audit.safe_item_number(v text) RETURNS smallint
LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN audit.valid_value(v,'positive_smallint') THEN v::smallint END
$$;
CREATE INDEX IF NOT EXISTS source_record_order ON audit.source_record
 (version_id,source_table,(payload->>'order_id'));
CREATE INDEX IF NOT EXISTS source_record_customer ON audit.source_record
 (version_id,source_table,(payload->>'customer_id'));

CREATE OR REPLACE VIEW audit.order_candidates AS
SELECT r.record_id AS _record_id,r.disposition AS _disposition,r.source_file AS _source_file,
 nullif(btrim(r.payload->>'order_id'),'') AS order_id,
 coalesce(o.customer_key,nullif(btrim(c.payload->>'customer_unique_id'),'')) AS customer_key,
 coalesce(nullif(btrim(r.payload->>'order_status'),''),'unknown') AS status_code,
 audit.safe_timestamp(r.payload->>'order_purchase_timestamp')::date AS purchase_date,
 nullif(btrim(r.payload->>'customer_id'),'') AS source_customer_id,
 audit.safe_timestamp(r.payload->>'order_purchase_timestamp') AS purchased_at,
 audit.safe_timestamp(r.payload->>'order_approved_at') AS approved_at,
 audit.safe_timestamp(r.payload->>'order_delivered_carrier_date') AS carrier_at,
 audit.safe_timestamp(r.payload->>'order_delivered_customer_date') AS delivered_at,
 audit.safe_timestamp(r.payload->>'order_estimated_delivery_date')::date AS estimated_date,
 o.ship_zip,o.ship_city,o.ship_state,o.items_count,o.sellers_count,
 o.items_price,o.items_freight,o.paid_amount
FROM audit.active_dataset a JOIN audit.source_record r USING(version_id)
LEFT JOIN core.fct_order o ON o.order_id=r.payload->>'order_id'
LEFT JOIN audit.source_record c ON c.version_id=r.version_id AND c.source_table='customers'
 AND c.disposition='accepted' AND c.payload->>'customer_id'=r.payload->>'customer_id'
WHERE r.source_table='orders';

CREATE OR REPLACE VIEW audit.item_candidates AS
SELECT r.record_id AS _record_id,r.disposition AS _disposition,r.source_file AS _source_file,
 nullif(btrim(r.payload->>'order_id'),'') AS order_id,
 audit.safe_item_number(r.payload->>'order_item_id') AS order_item_id,
 nullif(btrim(r.payload->>'seller_id'),'') AS seller_id,
 nullif(btrim(r.payload->>'product_id'),'') AS product_id,
 audit.safe_timestamp(r.payload->>'shipping_limit_date') AS shipping_limit_at,
 audit.safe_money(r.payload->>'price') AS price,
 audit.safe_money(r.payload->>'freight_value') AS freight
FROM audit.active_dataset a JOIN audit.source_record r USING(version_id)
WHERE r.source_table='order_items';

CREATE OR REPLACE VIEW audit.quality_summary AS
SELECT r.version_id,r.source_table,r.disposition,count(*) AS records
FROM audit.source_record r JOIN audit.active_dataset a USING(version_id)
GROUP BY r.version_id,r.source_table,r.disposition;
