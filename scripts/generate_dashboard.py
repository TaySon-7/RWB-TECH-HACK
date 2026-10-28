"""Generate the reproducible Capibara Grafana dashboard (two business metrics)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = {'type': 'grafana-clickhouse-datasource', 'uid': 'capibara-clickhouse'}
BLUE, GOLD = '#56C2FF', '#F5C56A'


def target(sql, kind=1):
    return {'refId': 'A', 'datasource': SOURCE, 'rawSql': sql,
            'queryType': 'sql', 'editorType': 'sql', 'format': kind,
            'meta': {'builderOptions': {'mode': 'sql', 'queryType': 'table' if kind == 1 else 'timeSeries'}}}


def stat(number, title, column, color, x):
    return {
        'id': number, 'title': title, 'type': 'stat', 'datasource': SOURCE,
        'description': ('Сумма цен товаров без доставки.' if column == 'revenue' else
                        'Стоимость доставки тех же доставленных заказов.') +
                       ' Только delivered. Период по дате оформления. Валюта: BRL (R$).',
        'gridPos': {'x': x, 'y': 6, 'w': 12, 'h': 5},
        'fieldConfig': {'defaults': {'unit': 'currency:financial:R$', 'decimals': 2,
                                     'color': {'mode': 'fixed', 'fixedColor': color}}, 'overrides': []},
        'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                    'orientation': 'horizontal', 'textMode': 'value', 'colorMode': 'value',
                    'graphMode': 'none', 'justifyMode': 'center', 'wideLayout': True,
                    'text': {'valueSize': 48}},
        'targets': [target(f'SELECT toFloat64(coalesce(sum({column}),0)) AS value FROM mart.sales_metrics WHERE $__timeFilter(day)')],
    }


def chart(number, title, column, color, x):
    return {
        'id': number, 'title': title, 'type': 'timeseries', 'datasource': SOURCE,
        'description': 'Полный календарь сохраняет месяцы без продаж. Ноябрь 2016 года — ноль, а не пропуск.',
        'gridPos': {'x': x, 'y': 11, 'w': 12, 'h': 9},
        'fieldConfig': {'defaults': {
            'unit': 'currencyBRL', 'decimals': 0,
            'color': {'mode': 'fixed', 'fixedColor': color},
            'custom': {'drawStyle': 'line', 'lineInterpolation': 'linear', 'lineWidth': 3,
                       'fillOpacity': 18, 'gradientMode': 'opacity', 'showPoints': 'always',
                       'pointSize': 5, 'spanNulls': False, 'axisBorderShow': False,
                       'axisColorMode': 'text', 'axisLabel': 'R$ · в месяц',
                       'scaleDistribution': {'type': 'linear'}, 'axisCenteredZero': False,
                       'hideFrom': {'tooltip': False, 'viz': False, 'legend': False}},
            'min': 0}, 'overrides': []},
        'options': {'legend': {'showLegend': False}, 'tooltip': {'mode': 'single', 'sort': 'none'}},
        'targets': [target(f'''SELECT toDateTime(toStartOfMonth(day), 'UTC') AS time,
            toFloat64(sum({column})) AS "{title}"
            FROM mart.sales_metrics WHERE $__timeFilter(day) GROUP BY time ORDER BY time''', 0)],
    }


dashboard = {
    'id': None, 'uid': 'capibara-commerce', 'title': 'Capibara · Пульс маркетплейса',
    'description': 'Две проверяемые метрики. ClickHouse ← Kafka ← Protobuf. Только состоявшиеся продажи.',
    'tags': ['RWB TECH HACK', 'ClickHouse', 'Kafka', 'Protobuf'],
    'timezone': 'utc', 'schemaVersion': 39, 'version': 2, 'editable': False,
    'graphTooltip': 1, 'refresh': '1m',
    'time': {'from': '2016-09-01T00:00:00.000Z', 'to': '2018-08-31T23:59:59.999Z'},
    'timepicker': {'refresh_intervals': ['1m', '5m', '15m'], 'hidden': False},
    'templating': {'list': []}, 'annotations': {'list': []},
    'links': [],
    'panels': [
        {'id': 1, 'title': '', 'type': 'text', 'transparent': True,
         'gridPos': {'x': 0, 'y': 0, 'w': 24, 'h': 6},
         'options': {'mode': 'html', 'content': '''
<div style="padding:12px 20px;border-left:4px solid #56C2FF;">
<p style="color:#56C2FF;letter-spacing:3px;font-size:12px;">CAPIBARA / COMMERCE INTELLIGENCE</p>
<h1 style="font-size:34px;margin:4px 0 12px;">Продажи, которые состоялись.</h1>
<p style="color:#b7c4d5;font-size:15px;">Оборот товаров и стоимость доставки — отдельно, прозрачно, до последнего сентаво.</p>
<p style="color:#8895a7;font-size:12px;">ДОСТАВЛЕННЫЕ ЗАКАЗЫ · ПЕРИОД ПО ДАТЕ ОФОРМЛЕНИЯ · БРАЗИЛЬСКИЙ РЕАЛ R$</p>
</div>'''}},
        stat(2, '01 / Оборот товаров · без доставки', 'revenue', BLUE, 0),
        stat(3, '02 / Стоимость доставки', 'freight', GOLD, 12),
        chart(4, 'Оборот товаров · динамика по месяцам', 'revenue', BLUE, 0),
        chart(5, 'Стоимость доставки · динамика по месяцам', 'freight', GOLD, 12),
        {'id': 6, 'title': 'Проверяемая детализация / каждый месяц на месте',
         'type': 'table', 'datasource': SOURCE,
         'gridPos': {'x': 0, 'y': 20, 'w': 24, 'h': 9},
         'fieldConfig': {'defaults': {'decimals': 2, 'custom': {'align': 'auto', 'cellOptions': {'type': 'auto'}}},
                         'overrides': [
                             {'matcher': {'id': 'byName', 'options': 'Оборот товаров'},
                              'properties': [{'id': 'unit', 'value': 'currency:financial:R$'},
                                             {'id': 'color', 'value': {'mode': 'fixed', 'fixedColor': BLUE}},
                                             {'id': 'custom.cellOptions', 'value': {'type': 'color-text'}}]},
                             {'matcher': {'id': 'byName', 'options': 'Стоимость доставки'},
                              'properties': [{'id': 'unit', 'value': 'currency:financial:R$'},
                                             {'id': 'color', 'value': {'mode': 'fixed', 'fixedColor': GOLD}},
                                             {'id': 'custom.cellOptions', 'value': {'type': 'color-text'}}]},
                         ]},
         'options': {'showHeader': True, 'cellHeight': 'sm',
                     'footer': {'show': True, 'reducer': ['sum'], 'countRows': False,
                                'fields': ['Оборот товаров', 'Стоимость доставки']}},
         'targets': [target('''SELECT formatDateTime(toStartOfMonth(day), '%Y-%m') AS "Месяц",
             toFloat64(sum(revenue)) AS "Оборот товаров",
             toFloat64(sum(freight)) AS "Стоимость доставки"
             FROM mart.sales_metrics WHERE $__timeFilter(day) GROUP BY "Месяц" ORDER BY "Месяц"''')]},
        {'id': 7, 'title': 'За цифрами / архитектура и определения', 'type': 'text',
         'transparent': True, 'gridPos': {'x': 0, 'y': 29, 'w': 24, 'h': 5},
         'options': {'mode': 'markdown', 'content': '''### PROTOBUF → KAFKA → CLICKHOUSE → GRAFANA
**8 типов событий · 3 партиции на топик · пакетная публикация проверенных витрин**

Оборот = Σ цен позиций доставленных заказов. Доставка = Σ стоимости доставки тех же позиций.
Оборот не включает комиссии площадки и не равен её доходу. Поздние статусы относятся к месяцу оформления.
Повторная доставка сообщений не увеличивает суммы: канонические представления выбирают актуальную версию.
Исходные даты без часового пояса сохранены как календарные значения; дашборд отображает UTC без сдвига дат.'''}},
    ],
}

if __name__ == '__main__':
    (ROOT / 'grafana/dashboards/commerce.json').write_text(
        json.dumps(dashboard, ensure_ascii=False, indent=2) + '\n')
