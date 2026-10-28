# Ответы на вопросы к модели — блок А

Результаты получены 28 октября 2026 года на текущей выгрузке Olist.
Все пять запросов используют только `core`; исходный `raw` нужен для сборки и проверок.
Задание: [tasks.md](../tasks.md). Сборка: [model/README.md](../../model/README.md).

## Как повторить расчёты

```bash
bash scripts/setup.sh
bash scripts/build_core.sh
```

Подключитесь к PostgreSQL (`localhost:5432`, база `marketplace`, пользователь
и пароль `hack`) и выполните SQL нужного раздела в DBeaver.

## Правила расчёта

- Выручка — сумма `price` доставленных товаров, **без доставки**, в бразильских реалах (R$).
- Средний чек — сумма товаров **с доставкой**, делённая на число доставленных заказов.
- Только вопрос №1 ограничен датами оформления с сентября 2016 по август 2018 включительно.
  Вопросы №2–5 считаются за всю выгрузку.
- В вопросах №1, №2, №3 и №5 учитывается статус `delivered`.
  В №4 учитываются все статусы: повторная покупка здесь означает минимум два оформленных заказа.
- Одна строка `fct_order_item` — одна единица товара; поля количества нет.
- Человек определяется по `customer_key`, равному исходному `customer_unique_id`.
- Позиции не соединяются с платежами и отзывами: это исключает размножение сумм.

## 1. Выручка и число заказов по месяцам

Месяц определяется по дате **оформления**, а не доставки.
`purchase_date` получена из `raw.orders.order_purchase_timestamp::timestamp::date`.
`items_price` — заранее проверенная сумма цен позиций одного заказа.

Формула: выручка месяца = сумма `items_price` доставленных заказов,
оформленных в этом месяце. Число заказов — также только доставленные.

Календарь `generate_series` создаёт 24 месяца; `LEFT JOIN` сохраняет месяц без продаж,
а `COALESCE` заменяет отсутствие результата нулём.


```sql
WITH months AS (
    SELECT generate_series(
        DATE '2016-09-01', DATE '2018-08-01', INTERVAL '1 month'
    )::date AS month
), sales AS (
    SELECT date_trunc('month', purchase_date)::date AS month,
           COUNT(*) AS orders_count,
           SUM(items_price) AS revenue
    FROM core.fct_order
    WHERE status_code = 'delivered'
      AND purchase_date >= DATE '2016-09-01'
      AND purchase_date < DATE '2018-09-01'
    GROUP BY 1
)
SELECT to_char(m.month, 'YYYY-MM') AS month,
       COALESCE(s.orders_count, 0) AS orders_count,
       COALESCE(s.revenue, 0) AS revenue
FROM months m
LEFT JOIN sales s ON s.month = m.month
ORDER BY m.month;
```

| Месяц | Доставленных заказов | Выручка, R$ |
| --- | --- | --- |
| 2016-09 | 1 | 134,97 |
| 2016-10 | 265 | 40 325,11 |
| 2016-11 | 0 | 0 |
| 2016-12 | 1 | 10,90 |
| 2017-01 | 750 | 111 798,36 |
| 2017-02 | 1 653 | 234 223,40 |
| 2017-03 | 2 546 | 359 198,85 |
| 2017-04 | 2 303 | 340 669,68 |
| 2017-05 | 3 546 | 489 338,25 |
| 2017-06 | 3 135 | 421 923,37 |
| 2017-07 | 3 872 | 481 604,52 |
| 2017-08 | 4 193 | 554 699,70 |
| 2017-09 | 4 150 | 607 399,67 |
| 2017-10 | 4 478 | 648 247,65 |
| 2017-11 | 7 289 | 987 765,37 |
| 2017-12 | 5 513 | 726 033,19 |
| 2018-01 | 7 069 | 924 645,00 |
| 2018-02 | 6 555 | 826 437,13 |
| 2018-03 | 7 003 | 953 356,25 |
| 2018-04 | 6 798 | 973 534,09 |
| 2018-05 | 6 749 | 977 544,69 |
| 2018-06 | 6 099 | 856 077,86 |
| 2018-07 | 6 159 | 867 953,46 |
| 2018-08 | 6 351 | 838 576,64 |
| Итого | 96 478 | 13 221 498,11 |

**Контроль совпал:** 24 строки, ноябрь 2016 года — нулевой месяц,
итоговая выручка **13 221 498,11**, число заказов **96 478**.

## 2. Топ-10 продавцов по выручке

Формула: сумма `price` только позиций конкретного продавца в доставленных заказах.
Стоимость остальных продавцов из того же заказа ему не приписывается.
`COUNT(DISTINCT order_id)` считает заказы, `COUNT(*)` — единицы товара.


```sql
SELECT s.seller_id, s.city, s.state,
       COUNT(DISTINCT i.order_id) AS orders_count,
       COUNT(*) AS units_count,
       SUM(i.price) AS revenue
FROM core.fct_order_item i
JOIN core.fct_order o ON o.order_id = i.order_id
JOIN core.dim_seller s ON s.seller_id = i.seller_id
WHERE o.status_code = 'delivered'
GROUP BY s.seller_id, s.city, s.state
ORDER BY revenue DESC, s.seller_id
LIMIT 10;
```

| Продавец | Город | Штат | Заказов | Единиц | Выручка, R$ |
| --- | --- | --- | --- | --- | --- |
| 4869f7a5dfa277a7dca6462dcf3b52b2 | guariba | SP | 1 124 | 1 148 | 226 987,93 |
| 53243585a1d6dc2643021fd1853d8905 | lauro de freitas | BA | 348 | 400 | 217 940,44 |
| 4a3ca9315b744ce9f8e9374361493884 | ibitinga | SP | 1 772 | 1 949 | 196 882,12 |
| fa1c13f2614d7b5c4749cbc52fecda94 | sumare | SP | 578 | 579 | 190 917,14 |
| 7c67e1448b00f6e969d365cea6b010ab | itaquaquecetuba | SP | 973 | 1 355 | 186 570,05 |
| 7e93a43ef30c4f03f38b393420bc753a | barueri | SP | 319 | 322 | 165 981,49 |
| da8622b14eb17ae2831f4ac5b9dab84a | piracicaba | SP | 1 311 | 1 548 | 159 816,87 |
| 7a67c85e85bb2ce8582c35f2203ad736 | sao paulo | SP | 1 145 | 1 155 | 139 658,69 |
| 1025f0e2d44d7041d6cf58b6550e0bfa | sao paulo | SP | 910 | 1 420 | 138 208,56 |
| 955fee9216a65b617aa5c0531780ce60 | sao paulo | SP | 1 261 | 1 472 | 131 836,71 |

**Контроль совпал:** первый продавец — `4869f7a5dfa277a7dca6462dcf3b52b2`,
его выручка **226 987,93**. Число заказов разных продавцов нельзя складывать
для получения уникальных заказов площадки: один заказ может включать нескольких продавцов.

## 3. Средний чек заказа

Сначала формируем одну строку на заказ, затем считаем средние.

```text
Чек заказа = сумма (price + freight) всех его позиций
Средний чек = 15 424 463,65 / 96 478 = 159,88 после округления
Среднее число строк = 110 197 / 96 478 = 1,1422 после округления
Среднее число единиц = 110 197 / 96 478 = 1,1422 после округления
```

Число строк и единиц совпадает, поскольку две одинаковые кружки представлены
двумя строками. В системе с `quantity` метрики могли бы различаться.


```sql
WITH per_order AS (
    SELECT o.order_id,
           SUM(i.price + i.freight) AS order_total,
           COUNT(i.order_item_id) AS lines_count,
           COUNT(i.order_item_id) AS units_count
    FROM core.fct_order o
    LEFT JOIN core.fct_order_item i ON i.order_id = o.order_id
    WHERE o.status_code = 'delivered'
    GROUP BY o.order_id
)
SELECT ROUND(AVG(order_total), 2) AS average_check,
       ROUND(AVG(lines_count), 4) AS average_lines,
       ROUND(AVG(units_count), 4) AS average_units,
       COUNT(*) AS orders_count,
       SUM(order_total) AS total_with_shipping,
       COUNT(*) FILTER (WHERE lines_count = 0) AS orders_without_items
FROM per_order;
```

| Средний чек, R$ | Среднее строк | Среднее единиц | Заказов | Сумма с доставкой, R$ | Без позиций |
| --- | --- | --- | --- | --- | --- |
| 159,88 | 1,1422 | 1,1422 | 96 478 | 15 424 463,65 | 0 |

**Контроль совпал: 159,88.** У доставленных заказов в текущей выгрузке нет
заказов без позиций, поэтому `AVG` не исключает заказы с отсутствующей суммой.
При изменении данных это нужно проверить до интерпретации среднего.

Средний чек и среднюю цену позиции нельзя путать: каждый заказ должен иметь
одинаковый вес в среднем, независимо от числа позиций.

## 4. Покупатели и повторные покупки

Группируем заказы по постоянному ключу человека `customer_key`, а не по
`source_customer_id`. Человек с тремя и более заказами считается одним повторным покупателем.

```text
Всего покупателей = число людей хотя бы с одним заказом
Вернулись = число людей с двумя и более заказами
Доля = 2 997 / 96 096 × 100% = 3,12% после округления
```


```sql
WITH customer_orders AS (
    SELECT customer_key, COUNT(*) AS orders_count
    FROM core.fct_order
    GROUP BY customer_key
)
SELECT COUNT(*) AS total_customers,
       COUNT(*) FILTER (WHERE orders_count >= 2) AS returning_customers,
       ROUND(
           100.0 * COUNT(*) FILTER (WHERE orders_count >= 2)
           / NULLIF(COUNT(*), 0), 2
       ) AS returning_percent
FROM customer_orders;
```

| Всего покупателей | Вернулись | Доля, % |
| --- | --- | --- |
| 96 096 | 2 997 | 3,12 |

**Контроль совпал: 96 096 и 3,12%.** Все статусы включены. Показатель
«повторно получил доставленный заказ» потребовал бы другой выборки и дал бы другой результат.

## 5. Выручка по категориям товаров

Позиция связана с товаром, товар — с категорией. Все категории представлены в `core`:
73 известных названия и отдельная строка `unknown` для отсутствующей категории.
В результате товары без категории не теряются при соединении.

Для категории без английского перевода показываем португальское название;
`pc_gamer` и `portateis_cozinha_e_preparadores_de_alimentos` не объединяются с неизвестной категорией.

```text
Выручка категории = сумма price её товаров в доставленных заказах
Доля категории = выручка категории / общая выручка × 100%
```

`SUM(revenue) OVER ()` вычисляет общую сумму по всем категориям, сохраняя отдельные строки.


```sql
WITH category_sales AS (
    SELECT c.category_key,
           CASE WHEN c.is_unknown THEN 'Не указана'
                ELSE COALESCE(c.name_en, c.name_pt)
           END AS category,
           SUM(i.price) AS revenue
    FROM core.fct_order_item i
    JOIN core.fct_order o ON o.order_id = i.order_id
    JOIN core.dim_product p ON p.product_id = i.product_id
    JOIN core.dim_category c ON c.category_key = p.category_key
    WHERE o.status_code = 'delivered'
    GROUP BY c.category_key, c.is_unknown, c.name_en, c.name_pt
)
SELECT category, revenue,
       ROUND(100.0 * revenue / SUM(revenue) OVER (), 2) AS share_percent
FROM category_sales
ORDER BY revenue DESC, category_key;
```

| Категория | Выручка, R$ | Доля, % |
| --- | --- | --- |
| health_beauty | 1 233 131,72 | 9,33 |
| watches_gifts | 1 166 176,98 | 8,82 |
| bed_bath_table | 1 023 434,76 | 7,74 |
| sports_leisure | 954 852,55 | 7,22 |
| computers_accessories | 888 724,61 | 6,72 |
| furniture_decor | 711 927,69 | 5,38 |
| housewares | 615 628,69 | 4,66 |
| cool_stuff | 610 204,10 | 4,62 |
| auto | 578 966,65 | 4,38 |
| toys | 471 286,48 | 3,56 |
| garden_tools | 470 495,28 | 3,56 |
| baby | 400 421,84 | 3,03 |
| perfumery | 390 144,65 | 2,95 |
| telephony | 309 860,23 | 2,34 |
| office_furniture | 268 154,31 | 2,03 |
| stationery | 223 788,69 | 1,69 |
| computers | 218 684,14 | 1,65 |
| pet_shop | 211 695,64 | 1,60 |
| musical_instruments | 184 315,74 | 1,39 |
| small_appliances | 182 754,12 | 1,38 |
| Не указана | 170 726,63 | 1,29 |
| electronics | 155 043,93 | 1,17 |
| fashion_bags_accessories | 149 329,39 | 1,13 |
| consoles_games | 148 003,37 | 1,12 |
| construction_tools_construction | 142 108,49 | 1,07 |
| luggage_accessories | 138 701,63 | 1,05 |
| home_appliances_2 | 107 953,95 | 0,82 |
| home_construction | 81 407,57 | 0,62 |
| home_appliances | 78 832,48 | 0,60 |
| agro_industry_and_commerce | 70 566,10 | 0,53 |
| furniture_living_room | 67 350,70 | 0,51 |
| home_confort | 58 008,45 | 0,44 |
| fixed_telephony | 55 315,21 | 0,42 |
| air_conditioning | 53 323,56 | 0,40 |
| audio | 50 570,60 | 0,38 |
| small_appliances_home_oven_and_coffee | 46 589,56 | 0,35 |
| kitchen_dining_laundry_garden_furniture | 45 531,98 | 0,34 |
| books_general_interest | 45 302,15 | 0,34 |
| construction_tools_lights | 39 957,00 | 0,30 |
| construction_tools_safety | 38 773,22 | 0,29 |
| industry_commerce_and_business | 38 498,81 | 0,29 |
| food | 28 731,15 | 0,22 |
| market_place | 27 795,77 | 0,21 |
| costruction_tools_garden | 25 096,28 | 0,19 |
| art | 23 657,30 | 0,18 |
| fashion_shoes | 23 001,46 | 0,17 |
| drinks | 21 529,84 | 0,16 |
| signaling_and_security | 21 315,05 | 0,16 |
| furniture_bedroom | 19 051,80 | 0,14 |
| books_technical | 18 702,23 | 0,14 |
| costruction_tools_tools | 15 903,95 | 0,12 |
| food_drink | 14 942,88 | 0,11 |
| fashion_male_clothing | 10 452,33 | 0,08 |
| fashion_underwear_beach | 9 305,95 | 0,07 |
| christmas_supplies | 8 737,84 | 0,07 |
| tablets_printing_image | 7 528,41 | 0,06 |
| cine_photo | 6 674,47 | 0,05 |
| music | 6 034,35 | 0,05 |
| dvds_blu_ray | 4 542,59 | 0,03 |
| party_supplies | 4 465,19 | 0,03 |
| books_imported | 4 418,86 | 0,03 |
| furniture_mattress_and_upholstery | 4 323,38 | 0,03 |
| portateis_cozinha_e_preparadores_de_alimentos | 3 933,63 | 0,03 |
| fashio_female_clothing | 2 634,94 | 0,02 |
| fashion_sport | 2 094,52 | 0,02 |
| la_cuisine | 2 054,99 | 0,02 |
| arts_and_craftmanship | 1 814,01 | 0,01 |
| diapers_and_hygiene | 1 500,79 | 0,01 |
| pc_gamer | 1 306,95 | 0,01 |
| flowers | 1 110,04 | 0,01 |
| home_comfort_2 | 760,27 | 0,01 |
| cds_dvds_musicals | 730,00 | 0,01 |
| fashion_childrens_clothes | 519,95 | 0,00 |
| security_and_services | 283,29 | 0,00 |

**Контроль совпал:** 74 категории, крупнейшая `health_beauty` —
**1 233 131,72**, сумма по всем категориям — **13 221 498,11**.
«Не указана» даёт **170 726,63 (1,29%)** и включена в общий итог.
Округлённые до двух знаков доли могут в сумме немного отличаться от 100%.

## Проверки и границы интерпретации

- Денежные суммы позиций и платежей проверяются при сборке против `raw`.
- Полнота переноса, ключи и итоги проверяются в [checks.sql](../../model/checks.sql).
- Аномалии сохраняются в `core.dq_issue`; например, неправильный порядок дат
  не исправляется автоматически.
- Сумма платежей и сумма товаров с доставкой могут различаться. В этих отчётах
  источником выручки служат позиции, а не платежи.
- Средние и проценты нельзя складывать по срезам. При объединении групп
  нужно пересчитать числитель и знаменатель.

