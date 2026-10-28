# Паспорт дашборда «Продажи и покупатели»

Генерируется вместе с Grafana из `scripts/generate_dashboard.py`.

Период — календарная дата оформления; только `delivered`; валюта BRL. История возвращения покупателя считается до фильтрации периода.

| Метрика | Формула | Уровень | Источник | Агрегация и ограничения |
|---|---|---|---|---|
| Оборот товаров | Σ items_price_cents / 100 | Заказ | mart.commerce_orders → core.fct_order → raw.order_items.price_cents | Суммируется по непересекающимся позициям/заказам; без доставки. |
| Стоимость доставки | Σ items_freight_cents / 100 | Заказ | mart.commerce_orders → core.fct_order → raw.order_items.freight_cents | Суммируется по непересекающимся позициям/заказам. |
| Доставленные заказы | COUNT(*) | Заказ | mart.commerce_orders → core.fct_order.order_id, status_code | Суммируется по непересекающимся периодам; по категориям/продавцам — нет. |
| Средний чек с доставкой | Σ(items_price_cents + items_freight_cents) / COUNT(*) / 100 | Заказ | mart.commerce_orders → core.fct_order | Не суммируется; пересчитывается из суммы и числа заказов. При 0 заказов — NULL. |
| Покупатели | uniqExact(customer_key) | Человек | mart.commerce_orders → core.fct_order → raw.customers.customer_unique_id | Не суммируется между месяцами, категориями и продавцами. |
| Доля повторных заказов | 100 × COUNT_IF(is_returning=1) / COUNT(*) | Заказ | mart.commerce_orders: purchased_at > первая дата оформления delivered-заказа человека за всю историю | Не суммируется; знаменатель — все delivered-заказы выбранного периода. При 0 заказов — NULL. |
| Первые заказы | COUNT_IF(is_returning=0) | Заказ | mart.commerce_orders.is_returning | Считаются относительно всей истории, а не начала выбранного периода. Одновременные первые заказы относятся сюда. |
| Повторные заказы | COUNT_IF(is_returning=1) | Заказ | mart.commerce_orders.is_returning | У человека есть delivered-заказ со строго более ранним purchased_at; суммируется по периодам. |
| Доля категории в обороте | 100 × Σ price_cents категории / Σ price_cents всех категорий | Позиция | mart.commerce_items → core.fct_order_item, dim_product, dim_category | Знаменатель — все категории периода. unknown не исключается; процент пересчитывается. |
| Проданные единицы | COUNT(*) позиций | Единица товара | mart.commerce_items → core.fct_order_item | Одна исходная позиция — одна единица; quantity в выгрузке отсутствует. |
| Заказы категории / продавца | uniqExact(order_id) внутри категории / продавца | Заказ в срезе | mart.commerce_items.order_id, category_key, seller_id | Один заказ может включать несколько категорий/продавцов; числа между срезами не складываются. |
| Оборот продавца | Σ price_cents только позиций продавца / 100 | Позиция продавца | mart.commerce_items → core.fct_order_item.seller_id, price_cents; core.dim_seller | Чужие позиции заказа продавцу не приписываются; доставка не включена. |

В `raw` поля `_cents` получены из `price` / `freight_value` исходного CSV умножением на 100. Покупатель определяется по `customer_unique_id`, а не по одноразовому `customer_id`.

Доли, средние и уникальные покупатели пересчитываются на выбранной выборке. По продавцам/категориям один заказ может встретиться несколько раз; деньги распределяются только по собственным позициям.

Нет просмотров и корзин, комиссий, возвратов и себестоимости: конверсию, прибыль и полный LTV этот экран не рассчитывает.
