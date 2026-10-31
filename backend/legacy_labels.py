"""Frozen schema9 cleanup: only retired tag arrays and the retired region value."""
import json
from .common import json_dumps

TABLES = frozenset(('instruments', 'allocations', 'events', 'reservations'))


def clean_data(raw):
    value = json.loads(raw)
    changed = 'tags' in value or value.get('region') == '混合'
    if not changed:
        return raw
    value.pop('tags', None)
    if value.get('region') == '混合':
        value['region'] = '未分类'
    return json_dumps(value)
