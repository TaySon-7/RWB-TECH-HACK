"""Batch admission tests independent of Kafka/LLM; conversion is an injected contract."""
import csv
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from quality import snapshot
from schema import TABLES


def event(row, table, version):
    values=[]
    for key in table[3]:
        if not row.get(key):
            raise ValueError('missing key '+key)
        values.append(row[key])
    return ':'.join(values).encode(),SimpleNamespace(SerializeToString=lambda:json.dumps(row,sort_keys=True).encode())


@pytest.fixture
def source(tmp_path):
    data=tmp_path/'data';data.mkdir()
    for table in TABLES:
        with (data/table[2]).open('w',newline='') as h:
            csv.DictWriter(h,fieldnames=[x[0] for x in table[4]]).writeheader()
    def append(name, values, file=None):
        table=next(t for t in TABLES if t[0]==name)
        row={f[0]:'' for f in table[4]};row.update(values)
        path=data/(file or table[2]);path.parent.mkdir(parents=True,exist_ok=True)
        new=not path.exists()
        with path.open('a',newline='') as h:
            w=csv.DictWriter(h,fieldnames=row)
            if new:w.writeheader()
            w.writerow(row)
    append('customers',{'customer_id':'c','customer_unique_id':'person'})
    append('sellers',{'seller_id':'s'});append('products',{'product_id':'p'})
    append('orders',{'order_id':'o','customer_id':'c','order_status':'delivered'})
    return tmp_path,append


def test_additional_batch_duplicates_and_missing_keys(source):
    root,append=source
    row={'order_id':'o','order_item_id':'1','seller_id':'s','product_id':'p','price':'10'}
    append('order_items',row)
    append('order_items',row,'extra/batch.csv')
    append('order_items',{**row,'order_item_id':''},'extra/batch.csv')
    append('order_items',{**row,'order_item_id':'2'},'extra/batch.csv')
    result=snapshot(root,event)
    assert result['expected']['order_items']==2
    assert [r['disposition'] for r in result['rejections']]==['duplicate','rejected']
    assert all(r['source_file']=='extra/batch.csv' for r in result['rejections'])
    assert len(result['manifest'])==9


def test_conflicting_parent_has_no_arbitrary_winner(source):
    root,append=source
    append('orders',{'order_id':'o','customer_id':'c','order_status':'canceled'},'extra/orders.csv')
    append('order_items',{'order_id':'o','order_item_id':'1','seller_id':'s','product_id':'p'})
    result=snapshot(root,event)
    assert result['expected']['orders']==result['expected']['order_items']==0
    assert sum(r['reason'].startswith('conflicting_key') for r in result['rejections'])==2
    assert any(r['reason']=='missing_reference: order is absent or rejected' for r in result['rejections'])


def test_unknown_format_requires_contract_registration(source):
    root,_=source;extra=root/'data/extra';extra.mkdir()
    (extra/'different.csv').write_text('some_new_column\nvalue\n')
    with pytest.raises(ValueError,match='contract'):
        snapshot(root,event)
