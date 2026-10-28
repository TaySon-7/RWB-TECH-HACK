"""Deterministic CSV contracts, duplicates and reference checks before Kafka.

A snapshot may consist of the original files plus data/extra/**/*.csv with the
same columns. Bad rows are retained as evidence; no data-specific IDs or counts.
"""
import csv
import hashlib
import json
from pathlib import Path

from schema import TABLES


def source_files(root):
    files = [(table, root / 'data' / table[2]) for table in TABLES]
    extra = root / 'data' / 'extra'
    if extra.exists():
        for path in sorted(extra.rglob('*.csv')):
            with path.open(encoding='utf-8-sig', newline='') as handle:
                columns = set(next(csv.reader(handle)))
            matches = [t for t in TABLES if {f[0] for f in t[4]} == columns]
            if len(matches) != 1:
                raise ValueError(f'No unambiguous source contract for {path.name}; register its schema first.')
            files.append((matches[0], path))
    return files


def snapshot(root, event_for):
    files = source_files(root)
    digest = hashlib.sha256()
    candidates, conflicts, bad = {}, {}, {}
    manifests = []
    review_hashes, conflicting_reviews = {}, set()
    for table, path in files:
        name = table[0]
        key_index = candidates.setdefault(name, {})
        conflicts.setdefault(name, set())
        relative = str(path.relative_to(root / 'data'))
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(relative.encode())
        digest.update(bytes.fromhex(content_hash))
        count = 0
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for number, row in enumerate(csv.DictReader(handle), 1):
                count += 1
                locator = (relative, number)
                try:
                    key, event = event_for(row, table, 0)
                    value_hash = hashlib.sha256(event.SerializeToString()).hexdigest()
                    if key in key_index and key_index[key][0] != value_hash:
                        conflicts[name].add(key)
                    key_index.setdefault(key, (value_hash, locator))
                    if name == 'order_reviews':
                        attrs = {k: v for k, v in row.items() if k != 'order_id'}
                        h = hashlib.sha256(json.dumps(attrs, sort_keys=True).encode()).hexdigest()
                        rid = row['review_id']
                        if rid in review_hashes and review_hashes[rid] != h:
                            conflicting_reviews.add(rid)
                        review_hashes[rid] = h
                except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                    bad[locator] = f'contract_error: {exc}'
        manifests.append({'file': relative, 'sha256': content_hash, 'rows': count, 'source_table': name})
    valid = {name: set(index) - conflicts[name] for name, index in candidates.items()}
    # Orders with missing customer mappings cannot satisfy the existing CH model.
    for table, path in files:
        if table[0] != 'orders':
            continue
        relative = str(path.relative_to(root / 'data'))
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for number, row in enumerate(csv.DictReader(handle), 1):
                if (relative, number) not in bad and row['customer_id'].strip().encode() not in valid['customers']:
                    valid['orders'].discard(row['order_id'].strip().encode())
    expected = {t[0]: 0 for t in TABLES}
    rejects = []
    for table, path in files:
        name = table[0]
        relative = str(path.relative_to(root / 'data'))
        with path.open(encoding='utf-8-sig', newline='') as handle:
            for number, row in enumerate(csv.DictReader(handle), 1):
                locator = (relative, number)
                reason = bad.get(locator)
                disposition = 'rejected'
                if not reason:
                    key, event = event_for(row, table, 0)
                    if key in conflicts[name]:
                        reason = 'conflicting_key: same key has different values'
                    elif candidates[name][key][1] != locator:
                        reason, disposition = 'exact_duplicate: same key and values', 'duplicate'
                    elif name == 'orders' and key not in valid['orders']:
                        reason = 'missing_reference: customer is absent or rejected'
                    elif name in {'order_items','order_payments','order_reviews'}:
                        if row['order_id'].strip().encode() not in valid['orders']:
                            reason = 'missing_reference: order is absent or rejected'
                        elif name == 'order_items' and row['seller_id'].strip().encode() not in valid['sellers']:
                            reason = 'missing_reference: seller is absent or rejected'
                        elif name == 'order_items' and row['product_id'].strip().encode() not in valid['products']:
                            reason = 'missing_reference: product is absent or rejected'
                        elif name == 'order_reviews' and row['review_id'] in conflicting_reviews:
                            reason = 'conflicting_review: shared review_id has different attributes'
                if reason:
                    bad[locator] = reason
                    record_id = hashlib.sha256((relative + ':' + str(number) + ':' + json.dumps(row, sort_keys=True)).encode()).hexdigest()
                    rejects.append({'record_id': record_id, 'source_table': name, 'source_file': relative,
                                    'row_number': number, 'reason': reason, 'disposition': disposition,
                                    'payload': json.dumps(row, ensure_ascii=False),
                                    'order_id': (row.get('order_id') or '').strip(),
                                    'seller_id': (row.get('seller_id') or '').strip()})
                else:
                    expected[name] += 1
    return {'hash': digest.hexdigest(), 'expected': expected, 'rejections': rejects,
            'bad_locators': bad, 'files': files, 'manifest': manifests}
