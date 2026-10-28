"""Generate the sales/buyer dashboard and its shared metric dictionary."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
SOURCE={'type':'grafana-clickhouse-datasource','uid':'capibara-clickhouse'}
BLUE,GOLD,GREEN,PURPLE='#56C2FF','#F5C56A','#69DBB0','#BAA1FF'
DATE='$__dateFilter(day)'
METRICS=[
 ('Оборот товаров','Σ items_price_cents / 100','Заказ','mart.commerce_orders → core.fct_order → raw.order_items.price_cents','Суммируется по непересекающимся позициям/заказам; без доставки.'),
 ('Стоимость доставки','Σ items_freight_cents / 100','Заказ','mart.commerce_orders → core.fct_order → raw.order_items.freight_cents','Суммируется по непересекающимся позициям/заказам.'),
 ('Доставленные заказы','COUNT(*)','Заказ','mart.commerce_orders → core.fct_order.order_id, status_code','Суммируется по непересекающимся периодам; по категориям/продавцам — нет.'),
 ('Средний чек с доставкой','Σ(items_price_cents + items_freight_cents) / COUNT(*) / 100','Заказ','mart.commerce_orders → core.fct_order','Не суммируется; пересчитывается из суммы и числа заказов. При 0 заказов — NULL.'),
 ('Покупатели','uniqExact(customer_key)','Человек','mart.commerce_orders → core.fct_order → raw.customers.customer_unique_id','Не суммируется между месяцами, категориями и продавцами.'),
 ('Доля повторных заказов','100 × COUNT_IF(is_returning=1) / COUNT(*)','Заказ','mart.commerce_orders: purchased_at > первая дата оформления delivered-заказа человека за всю историю','Не суммируется; знаменатель — все delivered-заказы выбранного периода. При 0 заказов — NULL.'),
 ('Первые заказы','COUNT_IF(is_returning=0)','Заказ','mart.commerce_orders.is_returning','Считаются относительно всей истории, а не начала выбранного периода. Одновременные первые заказы относятся сюда.'),
 ('Повторные заказы','COUNT_IF(is_returning=1)','Заказ','mart.commerce_orders.is_returning','У человека есть delivered-заказ со строго более ранним purchased_at; суммируется по периодам.'),
 ('Доля категории в обороте','100 × Σ price_cents категории / Σ price_cents всех категорий','Позиция','mart.commerce_items → core.fct_order_item, dim_product, dim_category','Знаменатель — все категории периода. unknown не исключается; процент пересчитывается.'),
 ('Проданные единицы','COUNT(*) позиций','Единица товара','mart.commerce_items → core.fct_order_item','Одна исходная позиция — одна единица; quantity в выгрузке отсутствует.'),
 ('Заказы категории / продавца','uniqExact(order_id) внутри категории / продавца','Заказ в срезе','mart.commerce_items.order_id, category_key, seller_id','Один заказ может включать несколько категорий/продавцов; числа между срезами не складываются.'),
 ('Оборот продавца','Σ price_cents только позиций продавца / 100','Позиция продавца','mart.commerce_items → core.fct_order_item.seller_id, price_cents; core.dim_seller','Чужие позиции заказа продавцу не приписываются; доставка не включена.'),
]
def describe(names):
 return '\n\n'.join(f'{n}. Формула: {f}. Уровень: {g}. Источник: {s}. {a}' for n,f,g,s,a in METRICS if n in names)+'\nОбщее: только delivered; период по календарной дате оформления, UTC без сдвига исходного времени; BRL.'
def target(sql,kind=1):
 return {'refId':'A','datasource':SOURCE,'rawSql':sql,'queryType':'sql','editorType':'sql','format':kind,'meta':{'builderOptions':{'mode':'sql','queryType':'table' if kind==1 else 'timeSeries'}}}
def stat(i,title,expression,color,x,y,unit='locale',decimals=0):
 return {'id':i,'title':title,'description':describe([title]),'type':'stat','datasource':SOURCE,'gridPos':{'x':x,'y':y,'w':8,'h':5},'targets':[target(f'SELECT {expression} AS value FROM mart.commerce_orders WHERE {DATE}')],
 'fieldConfig':{'defaults':{'unit':unit,'decimals':decimals,'noValue':'—','color':{'mode':'fixed','fixedColor':color}},'overrides':[]},
 'options':{'reduceOptions':{'calcs':['lastNotNull'],'fields':'','values':False},'textMode':'value','colorMode':'value','graphMode':'none','justifyMode':'center','text':{'valueSize':40}}}
MONTHLY=f'''WITH months AS (SELECT toStartOfMonth(day) AS month FROM mart.sales_daily WHERE {DATE} GROUP BY month),
 totals AS (SELECT toStartOfMonth(day) AS month, sum(items_price_cents) AS revenue,
 sum(items_freight_cents) AS freight,count() AS orders,uniqExact(customer_key) AS buyers,
 countIf(is_returning=1) AS returning,countIf(is_returning=0) AS first_orders
 FROM mart.commerce_orders WHERE {DATE} GROUP BY month)
 SELECT toDateTime(m.month,'UTC') AS time, {{fields}}
 FROM months AS m LEFT JOIN totals AS t ON m.month=t.month ORDER BY time'''
def chart(i,title,fields,names,x,y,unit='short',color=BLUE,decimals=0):
 return {'id':i,'title':title,'description':describe(names)+'\nПолный календарь: месяцы без продаж сохраняются. Средний чек/доля при 0 заказов — NULL.','type':'timeseries','datasource':SOURCE,'gridPos':{'x':x,'y':y,'w':12,'h':9},'targets':[target(MONTHLY.format(fields=fields),0)],
 'fieldConfig':{'defaults':{'unit':unit,'decimals':decimals,'noValue':'—','min':0,'color':{'mode':'palette-classic'} if ',' in fields else {'mode':'fixed','fixedColor':color},'custom':{'drawStyle':'line','lineInterpolation':'linear','lineWidth':3,'fillOpacity':15,'gradientMode':'opacity','showPoints':'always','pointSize':4,'spanNulls':False,'hideFrom':{'tooltip':False,'viz':False,'legend':False}}},'overrides':[]},
 'options':{'legend':{'showLegend':True,'placement':'bottom'},'tooltip':{'mode':'multi','sort':'desc'}}}
def table(i,title,sql,names,x,y,w=12,h=11,money=(),percent=()):
 overrides=[{'matcher':{'id':'byName','options':name},'properties':[{'id':'unit','value':'currency:financial:R$'},{'id':'decimals','value':2}]} for name in money]
 overrides += [{'matcher':{'id':'byName','options':name},'properties':[{'id':'unit','value':'percent'},{'id':'decimals','value':2}]} for name in percent]
 return {'id':i,'title':title,'description':describe(names),'type':'table','datasource':SOURCE,'gridPos':{'x':x,'y':y,'w':w,'h':h},'targets':[target(sql)],'fieldConfig':{'defaults':{'decimals':0,'noValue':'—','custom':{'align':'auto','cellOptions':{'type':'auto'}}},'overrides':overrides},'options':{'showHeader':True,'cellHeight':'sm','footer':{'show':False}}}
header='''<div style="padding:12px 20px;border-left:4px solid #56C2FF;"><p style="color:#56C2FF;letter-spacing:3px;font-size:12px;">CAPIBARA / COMMERCE INTELLIGENCE</p><h1 style="font-size:32px;margin:6px 0 12px;">Продажи и покупатели.</h1><p style="color:#b7c4d5;font-size:15px;">От оборота к повторным покупкам: масштаб, состав продаж и возвращение аудитории.</p><p style="color:#8895a7;font-size:12px;">DELIVERED · ДАТА ОФОРМЛЕНИЯ · BRL / R$ · ФОРМУЛЫ В ПОДСКАЗКЕ КАЖДОЙ ПАНЕЛИ</p></div>'''
panels=[{'id':1,'title':'','type':'text','transparent':True,'gridPos':{'x':0,'y':0,'w':24,'h':6},'options':{'mode':'html','content':header}},
 stat(2,'Оборот товаров','toFloat64(coalesce(sum(items_price_cents),0))/100',BLUE,0,6,'currency:financial:R$',2),
 stat(3,'Стоимость доставки','toFloat64(coalesce(sum(items_freight_cents),0))/100',GOLD,8,6,'currency:financial:R$',2),
 stat(5,'Средний чек с доставкой','toFloat64(sum(items_price_cents+items_freight_cents))/nullIf(count(),0)/100',GREEN,16,6,'currency:financial:R$',2),
 stat(4,'Доставленные заказы','count()',BLUE,0,11),stat(6,'Покупатели','uniqExact(customer_key)',PURPLE,8,11),
 stat(7,'Доля повторных заказов','100.0*countIf(is_returning=1)/nullIf(count(),0)',GREEN,16,11,'percent',2),
 chart(10,'Оборот товаров · по месяцам','toFloat64(coalesce(t.revenue,0))/100 AS "Оборот товаров"',['Оборот товаров'],0,16,'currencyBRL',BLUE,2),
 chart(11,'Доставка · по месяцам','toFloat64(coalesce(t.freight,0))/100 AS "Стоимость доставки"',['Стоимость доставки'],12,16,'currencyBRL',GOLD,2),
 chart(12,'Заказы · по месяцам','coalesce(t.orders,0) AS "Доставленные заказы"',['Доставленные заказы'],0,25),
 chart(13,'Средний чек · по месяцам','toFloat64(t.revenue+t.freight)/nullIf(t.orders,0)/100 AS "Средний чек"',['Средний чек с доставкой'],12,25,'currencyBRL',GREEN,2),
 chart(14,'Первые и повторные заказы','coalesce(t.first_orders,0) AS "Первые заказы", coalesce(t.returning,0) AS "Повторные заказы"',['Первые заказы','Повторные заказы'],0,34),
 chart(15,'Покупатели · по месяцам','coalesce(t.buyers,0) AS "Покупатели"',['Покупатели'],12,34,'short',PURPLE),
 table(20,'Категории · весь оборот, включая неизвестную',f'''SELECT category_name AS "Категория",toFloat64(sum(price_cents))/100 AS "Оборот товаров",
 100.0*sum(price_cents)/nullIf(sum(sum(price_cents)) OVER (),0) AS "Доля оборота",
 uniqExact(order_id) AS "Заказов",count() AS "Единиц"
 FROM mart.commerce_items WHERE {DATE} GROUP BY category_key,category_name ORDER BY "Оборот товаров" DESC''',
 ['Оборот товаров','Доля категории в обороте','Заказы категории / продавца','Проданные единицы'],0,43,money=['Оборот товаров'],percent=['Доля оборота']),
 table(21,'Топ-10 продавцов · только их товары',f'''SELECT seller_id AS "Продавец",seller_city AS "Город",seller_state AS "Штат",
 uniqExact(order_id) AS "Заказов",count() AS "Единиц",toFloat64(sum(price_cents))/100 AS "Оборот продавца"
 FROM mart.commerce_items WHERE {DATE} GROUP BY seller_id,seller_city,seller_state ORDER BY "Оборот продавца" DESC,seller_id LIMIT 10''',
 ['Оборот продавца','Заказы категории / продавца','Проданные единицы'],12,43,money=['Оборот продавца']),
 table(22,'Месячная детализация · средние и доли пересчитаны',MONTHLY.format(fields='''toFloat64(coalesce(t.revenue,0))/100 AS "Оборот товаров",
 toFloat64(coalesce(t.freight,0))/100 AS "Доставка",coalesce(t.orders,0) AS "Заказов",
 toFloat64(t.revenue+t.freight)/nullIf(t.orders,0)/100 AS "Средний чек",
 coalesce(t.buyers,0) AS "Покупателей",100.0*t.returning/nullIf(t.orders,0) AS "Повторные заказы, %"'''),
 ['Оборот товаров','Стоимость доставки','Доставленные заказы','Средний чек с доставкой','Покупатели','Доля повторных заказов'],0,54,24,11,money=['Оборот товаров','Доставка','Средний чек'],percent=['Повторные заказы, %']),
]
rows=['| Метрика | Формула | Уровень | Источник | Агрегация и ограничения |','|---|---|---|---|---|']
rows += ['| '+' | '.join(values)+' |' for values in METRICS]
metric_table='\n'.join(rows)
panels.append({'id':30,'title':'Паспорт метрик / формулы, источники, ограничения','type':'text','gridPos':{'x':0,'y':65,'w':24,'h':22},'options':{'mode':'markdown','content':'Все показатели относятся к **delivered** и выбранным календарным датам оформления. Деньги — BRL.\n\n'+metric_table+'\n\nПовторные заказы определяются по всей истории доставленных покупок. Это отличается от вопроса 4 в docs/answers: там покупатели всех статусов с ≥2 заказами. Средний чек, проценты и уникальных покупателей не складывают. Нулевая выручка показывается как 0; средний чек и доля при пустой выборке — как «—». Источники — семантические View над core; опубликованный календарь берётся из mart.sales_daily.'}})
dashboard={'id':None,'uid':'capibara-commerce','title':'Capibara · Продажи и покупатели','description':'Коммерческий дашборд: оборот, доставка, заказы, средний чек, покупатели и повторные покупки. Формулы и происхождение каждого показателя доступны в подсказках и паспорте метрик.','tags':['RWB TECH HACK','ClickHouse','Kafka','Продажи','Покупатели'],'timezone':'utc','schemaVersion':39,'version':3,'editable':False,'graphTooltip':1,'refresh':'1m','time':{'from':'2016-09-01T00:00:00.000Z','to':'2018-08-31T23:59:59.999Z'},'timepicker':{'refresh_intervals':['1m','5m','15m']},'templating':{'list':[]},'annotations':{'list':[]},'links':[{'title':'Модель и архитектура · draw.io','url':'https://app.diagrams.net/?page-id=rwb-architecture-current#G15KSdc2VXaQKBaW5urVHvc2VvqZbFUYNo','type':'link','targetBlank':True},{'title':'Ответы аналитикам и SQL','url':'https://github.com/TaySon-7/RWB-TECH-HACK/tree/main/docs/answers','type':'link','targetBlank':True}],'panels':panels}
if __name__=='__main__':
 (ROOT/'grafana/dashboards/commerce.json').write_text(json.dumps(dashboard,ensure_ascii=False,indent=2)+'\n')
 (ROOT/'docs/dashboard-metrics.md').write_text('# Паспорт дашборда «Продажи и покупатели»\n\nГенерируется вместе с Grafana из `scripts/generate_dashboard.py`.\n\nПериод — календарная дата оформления; только `delivered`; валюта BRL. История возвращения покупателя считается до фильтрации периода.\n\n'+metric_table+'\n\nВ `raw` поля `_cents` получены из `price` / `freight_value` исходного CSV умножением на 100. Покупатель определяется по `customer_unique_id`, а не по одноразовому `customer_id`.\n\nДоли, средние и уникальные покупатели пересчитываются на выбранной выборке. По продавцам/категориям один заказ может встретиться несколько раз; деньги распределяются только по собственным позициям.\n\nНет просмотров и корзин, комиссий, возвратов и себестоимости: конверсию, прибыль и полный LTV этот экран не рассчитывает.\n')
