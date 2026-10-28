#!/usr/bin/env python3
"""
Загрузка сырых CSV в схему raw.

Что делает:
  1. Создаёт схемы и таблицы raw-слоя (init/01_init.sql и init/02_raw_ddl.sql).
  2. Очищает таблицы raw и заливает в них CSV из папки data.

Скрипт идемпотентный: можно запускать сколько угодно раз, каждый запуск
начинается с чистых таблиц raw. Схему core он не трогает, так что ваша
модель от перезагрузки сырых данных не пострадает.

Запуск из корня проекта:
    pip install psycopg2-binary
    python scripts/load_raw.py

Другая база:
    python scripts/load_raw.py --host localhost --port 5433 --db marketplace
"""

import argparse
import csv
import io
import pathlib
import sys
import time

try:
    import psycopg2
except ImportError:
    sys.exit("Нет psycopg2. Установите:  pip install psycopg2-binary")


HERE = pathlib.Path(__file__).resolve().parent   # scripts/
ROOT = HERE.parent                                # корень проекта
DATA_DIR = ROOT / "data"
INIT_DIR = HERE / "init"

# Какой файл в какую таблицу и в каком порядке идут колонки.
# Порядок колонок обязан совпадать с заголовком CSV.
MAPPING = [
    (
        "olist_orders_dataset.csv",
        "raw.orders",
        [
            "order_id", "customer_id", "order_status",
            "order_purchase_timestamp", "order_approved_at",
            "order_delivered_carrier_date", "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ],
    ),
    (
        "olist_order_items_dataset.csv",
        "raw.order_items",
        [
            "order_id", "order_item_id", "product_id", "seller_id",
            "shipping_limit_date", "price", "freight_value",
        ],
    ),
    (
        "olist_order_payments_dataset.csv",
        "raw.order_payments",
        [
            "order_id", "payment_sequential", "payment_type",
            "payment_installments", "payment_value",
        ],
    ),
    (
        "olist_order_reviews_dataset.csv",
        "raw.order_reviews",
        [
            "review_id", "order_id", "review_score", "review_comment_title",
            "review_comment_message", "review_creation_date",
            "review_answer_timestamp",
        ],
    ),
    (
        "olist_products_dataset.csv",
        "raw.products",
        [
            "product_id", "product_category_name", "product_name_lenght",
            "product_description_lenght", "product_photos_qty",
            "product_weight_g", "product_length_cm", "product_height_cm",
            "product_width_cm",
        ],
    ),
    (
        "olist_sellers_dataset.csv",
        "raw.sellers",
        ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
    ),
    (
        "olist_customers_dataset.csv",
        "raw.customers",
        [
            "customer_id", "customer_unique_id", "customer_zip_code_prefix",
            "customer_city", "customer_state",
        ],
    ),
    (
        "product_category_name_translation.csv",
        "raw.product_category_translation",
        ["product_category_name", "product_category_name_english"],
    ),
]


def check_header(path, expected):
    """Сверяем заголовок файла с тем, что ждёт таблица."""
    with path.open(encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f))
    if header != expected:
        raise SystemExit(
            f"\nЗаголовок {path.name} не совпадает с таблицей.\n"
            f"  в файле:  {header}\n"
            f"  ожидаем:  {expected}\n"
            f"Проверьте, что скачали правильный файл."
        )


def load_file(cur, path, table, columns, *, append=False):
    check_header(path, columns)

    if not append:
        cur.execute(f"TRUNCATE {table}")
    cur.execute(f"SELECT count(*) FROM {table}")
    before = cur.fetchone()[0]

    # Читаем в utf-8-sig: у одного из файлов в начале стоит BOM,
    # без этого первая колонка приедет с невидимым мусором в имени.
    with path.open(encoding="utf-8-sig", newline="") as f:
        buf = io.StringIO(f.read())

    collist = ", ".join(columns)
    cur.copy_expert(
        f"COPY {table} ({collist}) FROM STDIN WITH (FORMAT csv, HEADER true)",
        buf,
    )

    cur.execute(f"UPDATE {table} SET _source_file = %s WHERE _source_file IS NULL",
                (str(path.relative_to(DATA_DIR)),))
    cur.execute(f"SELECT count(*) FROM {table}")
    return cur.fetchone()[0] - before


def source_files():
    """Full snapshot: supplied files plus registered-format CSV batches."""
    files = [(DATA_DIR / name, table, columns, False) for name, table, columns in MAPPING]
    for path in sorted((DATA_DIR / 'extra').rglob('*.csv')):
        with path.open(encoding='utf-8-sig', newline='') as handle:
            columns = next(csv.reader(handle))
        matches = [(table, expected) for _, table, expected in MAPPING if set(columns) == set(expected)]
        if len(matches) != 1:
            raise SystemExit(f'Неизвестный контракт {path}: сначала зарегистрируйте поля в MAPPING.')
        files.append((path, matches[0][0], columns, True))
    return files


def run_sql_file(cur, path):
    cur.execute(path.read_text(encoding="utf-8"))


def main():
    p = argparse.ArgumentParser(description="Загрузка сырых CSV в схему raw")
    p.add_argument("--host", default="localhost")
    p.add_argument("--port", default=5432, type=int)
    p.add_argument("--db", default="marketplace")
    p.add_argument("--user", default="hack")
    p.add_argument("--password", default="hack")
    p.add_argument(
        "--skip-ddl",
        action="store_true",
        help="не пересоздавать таблицы raw, только перезалить данные",
    )
    args = p.parse_args()

    if not DATA_DIR.exists():
        sys.exit(f"Нет папки с данными: {DATA_DIR}")

    missing = [name for name, _, _ in MAPPING if not (DATA_DIR / name).exists()]
    if missing:
        sys.exit(
            "В папке data не хватает файлов:\n  "
            + "\n  ".join(missing)
            + "\n\nСкачайте их:  bash scripts/download_data.sh"
        )

    try:
        conn = psycopg2.connect(
            host=args.host, port=args.port, dbname=args.db,
            user=args.user, password=args.password,
        )
    except psycopg2.OperationalError as e:
        sys.exit(
            f"Не удалось подключиться к базе: {e}\n"
            "Проверьте, что контейнер запущен:  docker compose ps"
        )

    conn.autocommit = False
    started = time.time()

    with conn, conn.cursor() as cur:
        if not args.skip_ddl:
            # Схемы создаём здесь, а не полагаемся на init-скрипты Docker:
            # если папка init не смонтировалась, Postgres их не выполнит,
            # и схем в базе не окажется.
            print("  создаю схемы raw / core / mart")
            for schema in ("raw", "core", "mart"):
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

            print("  выполняю init/02_raw_ddl.sql")
            run_sql_file(cur, INIT_DIR / "02_raw_ddl.sql")

        print()
        total = 0
        for path, table, columns, append in source_files():
            n = load_file(cur, path, table, columns, append=append)
            total += n
            print(f"  {table:<38} {n:>8,} строк · {path.relative_to(DATA_DIR)}")

        print(f"\n  итого {total:,} строк за {time.time() - started:.1f} с")

    conn.close()
    print("\nГотово. Данные лежат в схеме raw, схема core пустая и ждёт вашу модель.")


if __name__ == "__main__":
    main()
