CREATE TABLE IF NOT EXISTS core.dim_date (
 date_key date PRIMARY KEY, month_start date NOT NULL,
 year smallint NOT NULL, quarter smallint NOT NULL, iso_dow smallint NOT NULL
);
CREATE TABLE IF NOT EXISTS core.dim_order_status (
 status_code text PRIMARY KEY, status_name_ru text NOT NULL,
 stage_order smallint NOT NULL, is_revenue boolean NOT NULL,
 is_cancelled boolean NOT NULL, is_final boolean NOT NULL
);
COMMENT ON COLUMN core.dim_order_status.stage_order IS 'Display sorting only, not a transition sequence.';
COMMENT ON COLUMN core.dim_order_status.is_revenue IS 'Eligibility for delivered-sales reports; does not define the monetary formula.';
CREATE TABLE IF NOT EXISTS core.dim_payment_type (
 payment_type text PRIMARY KEY, name_ru text NOT NULL, is_known boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS core.dim_category (
 category_key text PRIMARY KEY, name_pt text UNIQUE, name_en text,
 is_translated boolean NOT NULL, is_unknown boolean NOT NULL
);
CREATE TABLE IF NOT EXISTS core.dim_customer (
 customer_key text PRIMARY KEY, first_order_at timestamp,
 orders_count integer NOT NULL CHECK (orders_count > 0)
);
CREATE TABLE IF NOT EXISTS core.dim_seller (
 seller_id text PRIMARY KEY, zip text CHECK(zip ~ '^[0-9]{5}$'),
 city_raw text, city text,
 state char(2) CHECK(state ~ '^[A-Z]{2}$')
);
CREATE TABLE IF NOT EXISTS core.dim_product (
 product_id text PRIMARY KEY,
 category_key text NOT NULL REFERENCES core.dim_category,
 name_length integer CHECK(name_length >= 0),
 description_length integer CHECK(description_length >= 0),
 photos_qty integer CHECK(photos_qty >= 0),
 weight_g integer CHECK(weight_g >= 0),
 length_cm integer CHECK(length_cm >= 0),
 height_cm integer CHECK(height_cm >= 0),
 width_cm integer CHECK(width_cm >= 0)
);
CREATE TABLE IF NOT EXISTS core.fct_order (
 order_id text PRIMARY KEY,
 customer_key text REFERENCES core.dim_customer,
 status_code text NOT NULL REFERENCES core.dim_order_status,
 purchase_date date REFERENCES core.dim_date,
 source_customer_id text,
 purchased_at timestamp, approved_at timestamp,
 carrier_at timestamp, delivered_at timestamp, estimated_date date,
 ship_zip text CHECK(ship_zip ~ '^[0-9]{5}$'),
 ship_city text, ship_state char(2),
 items_count integer NOT NULL DEFAULT 0,
 sellers_count integer NOT NULL DEFAULT 0,
 items_price numeric(12,2) DEFAULT 0,
 items_freight numeric(12,2) DEFAULT 0,
 paid_amount numeric(12,2)
);
COMMENT ON COLUMN core.fct_order.paid_amount IS 'NULL means no payment record; zero means a recorded zero total.';
CREATE TABLE IF NOT EXISTS core.fct_order_seller (
 order_id text NOT NULL REFERENCES core.fct_order,
 seller_id text NOT NULL REFERENCES core.dim_seller,
 shipping_limit_at timestamp,
 items_count integer NOT NULL,
 items_price numeric(12,2), items_freight numeric(12,2),
 PRIMARY KEY(order_id,seller_id)
);
COMMENT ON TABLE core.fct_order_seller IS 'Seller portion of an order, not a physical parcel. shipping_limit_at is the earliest item deadline.';
CREATE TABLE IF NOT EXISTS core.fct_order_item (
 order_id text NOT NULL REFERENCES core.fct_order,
 order_item_id smallint NOT NULL CHECK(order_item_id > 0),
 seller_id text REFERENCES core.dim_seller,
 product_id text REFERENCES core.dim_product,
 shipping_limit_at timestamp,
 price numeric(12,2) CHECK(price >= 0),
 freight numeric(12,2) CHECK(freight >= 0),
 PRIMARY KEY(order_id,order_item_id),
 FOREIGN KEY(order_id,seller_id) REFERENCES core.fct_order_seller
);
CREATE TABLE IF NOT EXISTS core.fct_payment (
 order_id text NOT NULL REFERENCES core.fct_order,
 payment_seq smallint NOT NULL CHECK(payment_seq > 0),
 payment_type text NOT NULL REFERENCES core.dim_payment_type,
 installments smallint CHECK(installments >= 0),
 amount numeric(12,2) CHECK(amount >= 0),
 PRIMARY KEY(order_id,payment_seq)
);
CREATE TABLE IF NOT EXISTS core.fct_review (
 review_id text PRIMARY KEY, score smallint CHECK(score BETWEEN 1 AND 5),
 title text, message text, created_date timestamp, answered_at timestamp
);
CREATE TABLE IF NOT EXISTS core.fct_review_order (
 review_id text NOT NULL REFERENCES core.fct_review,
 order_id text NOT NULL REFERENCES core.fct_order,
 is_order_score boolean NOT NULL,
 PRIMARY KEY(review_id,order_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS review_one_selected_per_order
 ON core.fct_review_order(order_id) WHERE is_order_score;
CREATE TABLE IF NOT EXISTS core.dq_issue (
 issue_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 check_id text NOT NULL, entity text NOT NULL, entity_key text NOT NULL,
 details jsonb NOT NULL, detected_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_customer_idx ON core.fct_order(customer_key);
CREATE INDEX IF NOT EXISTS order_purchase_idx ON core.fct_order(purchase_date);
CREATE INDEX IF NOT EXISTS item_product_idx ON core.fct_order_item(product_id);
CREATE INDEX IF NOT EXISTS item_seller_idx ON core.fct_order_item(seller_id);
CREATE INDEX IF NOT EXISTS product_category_idx ON core.dim_product(category_key);
CREATE INDEX IF NOT EXISTS payment_type_idx ON core.fct_payment(payment_type);
CREATE INDEX IF NOT EXISTS review_order_idx ON core.fct_review_order(order_id);

-- Compatible migration: relax attributes, never the source fact keys.
ALTER TABLE core.dim_customer ALTER COLUMN first_order_at DROP NOT NULL;
ALTER TABLE core.dim_seller ALTER COLUMN zip DROP NOT NULL, ALTER COLUMN city_raw DROP NOT NULL,
 ALTER COLUMN city DROP NOT NULL, ALTER COLUMN state DROP NOT NULL;
ALTER TABLE core.fct_order DROP CONSTRAINT IF EXISTS fct_order_source_customer_id_key;
ALTER TABLE core.fct_order ALTER COLUMN customer_key DROP NOT NULL,
 ALTER COLUMN purchase_date DROP NOT NULL, ALTER COLUMN source_customer_id DROP NOT NULL,
 ALTER COLUMN purchased_at DROP NOT NULL, ALTER COLUMN ship_zip DROP NOT NULL,
 ALTER COLUMN ship_city DROP NOT NULL, ALTER COLUMN ship_state DROP NOT NULL,
 ALTER COLUMN items_price DROP NOT NULL, ALTER COLUMN items_freight DROP NOT NULL;
ALTER TABLE core.fct_order_seller ALTER COLUMN shipping_limit_at DROP NOT NULL,
 ALTER COLUMN items_price DROP NOT NULL, ALTER COLUMN items_freight DROP NOT NULL;
ALTER TABLE core.fct_order_item ALTER COLUMN seller_id DROP NOT NULL,
 ALTER COLUMN product_id DROP NOT NULL, ALTER COLUMN shipping_limit_at DROP NOT NULL,
 ALTER COLUMN price DROP NOT NULL, ALTER COLUMN freight DROP NOT NULL;
ALTER TABLE core.fct_payment ALTER COLUMN installments DROP NOT NULL, ALTER COLUMN amount DROP NOT NULL;
ALTER TABLE core.fct_review ALTER COLUMN score DROP NOT NULL,
 ALTER COLUMN created_date TYPE timestamp USING created_date::timestamp,
 ALTER COLUMN created_date DROP NOT NULL, ALTER COLUMN answered_at DROP NOT NULL;
