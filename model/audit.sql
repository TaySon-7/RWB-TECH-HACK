-- Source ledger lives beside core. It never changes the supplied raw tables.
CREATE SCHEMA IF NOT EXISTS audit;
CREATE TABLE IF NOT EXISTS audit.dataset (
 version_id text PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
 rules_version text NOT NULL, source_counts jsonb NOT NULL DEFAULT '{}',
 source_files jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS audit.active_dataset (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 version_id text NOT NULL REFERENCES audit.dataset
);
CREATE TABLE IF NOT EXISTS audit.source_record (
 version_id text NOT NULL REFERENCES audit.dataset,
 record_id text NOT NULL, source_table text NOT NULL, source_file text,
 source_ref text NOT NULL, entity_key jsonb NOT NULL, payload jsonb NOT NULL,
 disposition text NOT NULL CHECK(disposition IN ('accepted','rejected','duplicate')),
 PRIMARY KEY(version_id,record_id)
);
CREATE INDEX IF NOT EXISTS source_record_lookup ON audit.source_record(version_id,source_table);
CREATE INDEX IF NOT EXISTS source_record_key ON audit.source_record USING gin(entity_key);
CREATE TABLE IF NOT EXISTS audit.issue (
 version_id text NOT NULL, record_id text NOT NULL,
 rule_id text NOT NULL, field_name text NOT NULL DEFAULT '',
 action text NOT NULL CHECK(action IN ('reject_row','nullify_field','keep','deduplicate')),
 message text NOT NULL,
 PRIMARY KEY(version_id,record_id,rule_id,field_name),
 FOREIGN KEY(version_id,record_id) REFERENCES audit.source_record
);

-- One declaration per source field. These are source contracts, not row exceptions.
CREATE TEMP TABLE _contracts (
 source_table text, field_name text, kind text, is_key boolean DEFAULT false,
 PRIMARY KEY(source_table,field_name)
);
INSERT INTO _contracts VALUES
 ('orders','order_id','id',true),('orders','customer_id','id',false),
 ('orders','order_purchase_timestamp','timestamp',false),
 ('orders','order_approved_at','timestamp',false),
 ('orders','order_delivered_carrier_date','timestamp',false),
 ('orders','order_delivered_customer_date','timestamp',false),
 ('orders','order_estimated_delivery_date','timestamp',false),
 ('customers','customer_id','id',true),('customers','customer_unique_id','id',false),
 ('products','product_id','id',true),('sellers','seller_id','id',true),
 ('order_items','order_id','id',true),('order_items','order_item_id','positive_smallint',true),
 ('order_items','seller_id','id',false),('order_items','product_id','id',false),
 ('order_items','price','money',false),('order_items','freight_value','money',false),
 ('order_items','shipping_limit_date','timestamp',false),
 ('order_payments','order_id','id',true),('order_payments','payment_sequential','positive_smallint',true),
 ('order_payments','payment_installments','nonnegative_smallint',false),
 ('order_payments','payment_value','money',false),
 ('order_reviews','order_id','id',true),('order_reviews','review_id','id',true),
 ('order_reviews','review_score','score',false),
 ('order_reviews','review_creation_date','timestamp',false),
 ('order_reviews','review_answer_timestamp','timestamp',false),
 ('product_category_translation','product_category_name','id',true);
INSERT INTO _contracts
SELECT 'products',f,'nonnegative_int',false FROM unnest(ARRAY[
 'product_name_lenght','product_description_lenght','product_photos_qty',
 'product_weight_g','product_length_cm','product_height_cm','product_width_cm']) f;

CREATE OR REPLACE FUNCTION audit.valid_value(value text,kind text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
 IF nullif(btrim(value),'') IS NULL THEN RETURN false; END IF;
 IF kind='id' THEN RETURN true; END IF;
 IF kind='timestamp' THEN
  RETURN pg_input_is_valid(value,'timestamp') AND lower(value) NOT IN ('infinity','-infinity');
 END IF;
 IF kind='money' THEN
  IF NOT pg_input_is_valid(value,'numeric') THEN RETURN false; END IF;
  RETURN value::numeric >= 0 AND value::numeric < 10000000000
   AND value::numeric = round(value::numeric,2);
 END IF;
 IF NOT pg_input_is_valid(value,'integer') THEN RETURN false; END IF;
 IF kind='score' THEN RETURN value::integer BETWEEN 1 AND 5; END IF;
 IF kind='positive_smallint' THEN RETURN value::integer BETWEEN 1 AND 32767; END IF;
 IF kind='nonnegative_smallint' THEN RETURN value::integer BETWEEN 0 AND 32767; END IF;
 RETURN value::integer >= 0;
END $$;

CREATE TEMP TABLE _source (
 record_id text PRIMARY KEY, source_table text, source_file text, source_ref text,
 payload jsonb, cleaned jsonb, entity_key jsonb DEFAULT '{}',
 disposition text NOT NULL DEFAULT 'accepted'
);
DO $$ DECLARE t text; BEGIN
 FOR t IN SELECT DISTINCT source_table FROM _contracts LOOP
  EXECUTE format($q$INSERT INTO _source(record_id,source_table,source_file,source_ref,payload,cleaned)
   SELECT md5(%L||':'||ctid::text||':'||(to_jsonb(r)-'_loaded_at')::text),%L,
    r._source_file,ctid::text,to_jsonb(r)-'_source_file'-'_loaded_at',
    to_jsonb(r)-'_source_file'-'_loaded_at' FROM raw.%I r$q$,t,t,t);
 END LOOP;
END $$;
CREATE INDEX ON _source(source_table);
CREATE TEMP TABLE _issues (
 record_id text, rule_id text, field_name text DEFAULT '', action text, message text
);
INSERT INTO _issues
SELECT r.record_id,
 CASE WHEN nullif(btrim(r.payload->>c.field_name),'') IS NULL
      THEN CASE WHEN c.is_key THEN 'missing_key' ELSE 'missing_value' END ELSE 'invalid_value' END,
 c.field_name,CASE WHEN c.is_key THEN 'reject_row' ELSE 'nullify_field' END,
 CASE WHEN c.is_key THEN 'Обязательный ключ отсутствует или не соответствует типу.'
      ELSE 'Значение поля неизвестно или не соответствует контракту; поле оставлено NULL.' END
FROM _source r JOIN _contracts c USING(source_table)
WHERE NOT audit.valid_value(r.payload->>c.field_name,c.kind)
 AND (c.is_key OR nullif(btrim(r.payload->>c.field_name),'') IS NOT NULL
      OR c.kind IN ('id','money'));

-- Normalize optional bad fields independently, preserving all original payloads.
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT * FROM _contracts LOOP
  UPDATE _source SET cleaned=jsonb_set(cleaned,ARRAY[c.field_name],
   CASE WHEN audit.valid_value(payload->>c.field_name,c.kind)
    THEN to_jsonb(CASE WHEN c.kind IN ('positive_smallint','nonnegative_smallint','nonnegative_int','score')
      THEN ((payload->>c.field_name)::integer)::text ELSE btrim(payload->>c.field_name) END)
    ELSE 'null'::jsonb END)
  WHERE source_table=c.source_table;
 END LOOP;
END $$;
CREATE INDEX ON _source(source_table,(cleaned->>'order_id')) WHERE disposition='accepted';
CREATE INDEX ON _source(source_table,(cleaned->>'seller_id')) WHERE disposition='accepted';
CREATE INDEX ON _source(source_table,(cleaned->>'product_id')) WHERE disposition='accepted';
CREATE INDEX ON _source(source_table,(cleaned->>'customer_id')) WHERE disposition='accepted';
ANALYZE _source;
UPDATE _source r SET entity_key=k.key FROM (
 SELECT r.record_id,jsonb_object_agg(c.field_name,r.cleaned->c.field_name) key
 FROM _source r JOIN _contracts c USING(source_table) WHERE c.is_key GROUP BY r.record_id
) k WHERE k.record_id=r.record_id;
UPDATE _source SET disposition='rejected'
WHERE record_id IN (SELECT record_id FROM _issues WHERE action='reject_row');

-- Conflicting keys have no implicit winner. Identical repeats are accounted for.
INSERT INTO _issues
SELECT r.record_id,'conflicting_key','','reject_row','Один ключ имеет разные исходные значения; выбор версии не задан.'
FROM _source r JOIN (
 SELECT source_table,entity_key FROM _source WHERE disposition='accepted'
 GROUP BY source_table,entity_key HAVING count(DISTINCT payload)>1
) d USING(source_table,entity_key);
UPDATE _source SET disposition='rejected'
WHERE record_id IN (SELECT record_id FROM _issues WHERE action='reject_row');
WITH repeated AS (
 SELECT record_id,row_number() OVER(PARTITION BY source_table,entity_key ORDER BY record_id) n
 FROM _source WHERE disposition='accepted'
)
UPDATE _source r SET disposition='duplicate' FROM repeated d WHERE d.record_id=r.record_id AND n>1;
INSERT INTO _issues
SELECT record_id,'exact_duplicate','','deduplicate','Повтор той же записи с тем же ключом учтён один раз.'
FROM _source WHERE disposition='duplicate';

-- Children need a real order. Missing descriptive dimensions only nullify a FK.
INSERT INTO _issues
SELECT r.record_id,'missing_order','','reject_row','Родительский заказ отсутствует или отклонён.'
FROM _source r WHERE r.disposition='accepted'
 AND r.source_table IN ('order_items','order_payments','order_reviews')
 AND NOT EXISTS(SELECT 1 FROM _source p WHERE p.source_table='orders' AND p.disposition='accepted'
  AND p.cleaned->>'order_id'=r.cleaned->>'order_id');
UPDATE _source SET disposition='rejected'
WHERE record_id IN (SELECT record_id FROM _issues WHERE action='reject_row');
DO $$ DECLARE f record; BEGIN
 FOR f IN SELECT * FROM (VALUES
  ('order_items','seller_id','sellers','seller_id'),
  ('order_items','product_id','products','product_id'),
  ('orders','customer_id','customers','customer_id')
 ) v(child,field,parent,parent_key) LOOP
  INSERT INTO _issues
  SELECT r.record_id,'missing_reference',f.field,'nullify_field',
   'Связанная запись отсутствует или отклонена; остальные факты сохранены.'
  FROM _source r WHERE r.source_table=f.child AND r.disposition='accepted'
   AND r.cleaned->>f.field IS NOT NULL
   AND NOT EXISTS(SELECT 1 FROM _source p WHERE p.source_table=f.parent AND p.disposition='accepted'
    AND p.cleaned->>f.parent_key=r.cleaned->>f.field);
  UPDATE _source SET cleaned=jsonb_set(cleaned,ARRAY[f.field],'null'::jsonb)
  WHERE record_id IN (SELECT record_id FROM _issues WHERE rule_id='missing_reference' AND field_name=f.field);
 END LOOP;
END $$;
INSERT INTO _issues
SELECT r.record_id,'conflicting_review','','reject_row','Один review_id содержит разные атрибуты анкеты.'
FROM _source r JOIN (
 SELECT cleaned->>'review_id' review_id FROM _source
 WHERE source_table='order_reviews' AND disposition='accepted'
 GROUP BY 1 HAVING count(DISTINCT(payload-'order_id'))>1
) bad ON bad.review_id=r.cleaned->>'review_id'
WHERE r.source_table='order_reviews' AND r.disposition='accepted';
UPDATE _source SET disposition='rejected'
WHERE record_id IN (SELECT record_id FROM _issues WHERE action='reject_row');

CREATE TEMP TABLE _version AS
SELECT md5(coalesce(string_agg(record_id,'' ORDER BY record_id),'')||':quality-v1') version_id FROM _source;
INSERT INTO audit.dataset(version_id,rules_version,source_counts,source_files)
SELECT v.version_id,'quality-v1',
 (SELECT jsonb_object_agg(source_table,n) FROM(SELECT source_table,count(*) n FROM _source GROUP BY 1)s),
 (SELECT jsonb_object_agg(source_table,files) FROM(SELECT source_table,jsonb_agg(DISTINCT source_file) files FROM _source GROUP BY 1)s)
FROM _version v ON CONFLICT DO NOTHING;
INSERT INTO audit.source_record
SELECT v.version_id,r.record_id,r.source_table,r.source_file,r.source_ref,r.entity_key,r.payload,r.disposition
FROM _source r CROSS JOIN _version v ON CONFLICT DO NOTHING;
INSERT INTO audit.issue
SELECT DISTINCT v.version_id,i.record_id,i.rule_id,i.field_name,i.action,i.message
FROM _issues i CROSS JOIN _version v ON CONFLICT DO NOTHING;

-- The existing SQL loader reads these validated temporary projections.
DO $$ DECLARE t text; BEGIN
 FOR t IN SELECT DISTINCT source_table FROM _contracts LOOP
  EXECUTE format($q$CREATE TEMP TABLE %I AS SELECT
   (jsonb_populate_record(NULL::raw.%I,cleaned||jsonb_build_object('_source_file',source_file))).*
   FROM _source WHERE source_table=%L AND disposition='accepted'$q$,t,t,t);
 END LOOP;
END $$;
