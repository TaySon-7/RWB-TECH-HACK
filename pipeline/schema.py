"""Versioned wire contracts. Money is integer centavos, dates are wall-clock ms.

Source timestamps have no timezone. Encoding them as UTC preserves their original
calendar values; it does not assert that the Brazilian events happened in UTC.
Missing timestamps use 0, missing numeric product attributes use -1.
"""

# name, protobuf message, CSV filename, natural key, (source, wire name, kind)
TABLES = [
    ("orders", "Order", "olist_orders_dataset.csv", ("order_id",), [
        ("order_id", "order_id", "id"), ("customer_id", "customer_id", "id"),
        ("order_status", "order_status", "str"),
        *[(name, name + "_ms", "time") for name in (
            "order_purchase_timestamp", "order_approved_at",
            "order_delivered_carrier_date", "order_delivered_customer_date",
            "order_estimated_delivery_date")],
    ]),
    ("order_items", "OrderItem", "olist_order_items_dataset.csv",
     ("order_id", "order_item_id"), [
        ("order_id", "order_id", "id"), ("order_item_id", "order_item_id", "int"),
        ("product_id", "product_id", "id"), ("seller_id", "seller_id", "id"),
        ("shipping_limit_date", "shipping_limit_date_ms", "time"),
        ("price", "price_cents", "money"),
        ("freight_value", "freight_cents", "money"),
    ]),
    ("order_payments", "Payment", "olist_order_payments_dataset.csv",
     ("order_id", "payment_sequential"), [
        ("order_id", "order_id", "id"),
        ("payment_sequential", "payment_sequential", "int"),
        ("payment_type", "payment_type", "str"),
        ("payment_installments", "payment_installments", "int"),
        ("payment_value", "amount_cents", "money"),
    ]),
    ("order_reviews", "Review", "olist_order_reviews_dataset.csv",
     ("review_id", "order_id"), [
        ("review_id", "review_id", "id"), ("order_id", "order_id", "id"),
        ("review_score", "review_score", "int"),
        ("review_comment_title", "review_comment_title", "str"),
        ("review_comment_message", "review_comment_message", "str"),
        ("review_creation_date", "review_creation_date_ms", "time"),
        ("review_answer_timestamp", "review_answer_timestamp_ms", "time"),
    ]),
    ("products", "Product", "olist_products_dataset.csv", ("product_id",), [
        ("product_id", "product_id", "id"),
        ("product_category_name", "product_category_name", "str"),
        *[(name, name, "optional_int") for name in (
            "product_name_lenght", "product_description_lenght", "product_photos_qty",
            "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm")],
    ]),
    ("sellers", "Seller", "olist_sellers_dataset.csv", ("seller_id",), [
        ("seller_id", "seller_id", "id"),
        ("seller_zip_code_prefix", "seller_zip_code_prefix", "str"),
        ("seller_city", "seller_city", "str"), ("seller_state", "seller_state", "str"),
    ]),
    ("customers", "Customer", "olist_customers_dataset.csv", ("customer_id",), [
        ("customer_id", "customer_id", "id"),
        ("customer_unique_id", "customer_unique_id", "id"),
        ("customer_zip_code_prefix", "customer_zip_code_prefix", "str"),
        ("customer_city", "customer_city", "str"),
        ("customer_state", "customer_state", "str"),
    ]),
    ("product_category_translation", "Category", "product_category_name_translation.csv",
     ("product_category_name",), [
        ("product_category_name", "product_category_name", "str"),
        ("product_category_name_english", "product_category_name_english", "str"),
    ]),
]

PROTO_TYPES = {"id": "string", "str": "string", "int": "int32",
               "optional_int": "sint32", "time": "int64", "money": "int64"}
CH_TYPES = {"id": "String", "str": "String", "int": "Int32",
            "optional_int": "Int32", "time": "Int64", "money": "Int64"}
