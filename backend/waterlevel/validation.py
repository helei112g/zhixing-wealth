"""Validate source identities and archived research before publication or restore."""
import json
import re
from urllib.parse import urlsplit

from ..common import DomainError, day, dec
from ..research.snapshots import decode
from .catalog import FULL_MARKET_CODE, FULL_MARKET_FIELDS, GROUPS, SERIES
from .metrics import METHODS, METHOD_VERSION
from .sources import HOSTS

TABLES = {'market_snapshots', 'market_heads', 'market_reports', 'market_settings'}


def source(value, *, archival=False):
    group = value.get('group')
    legacy = archival and group == 'full_market' and value.get('publisher') == '乐咕乐股'
    if group not in GROUPS or (not legacy and value.get('publisher') != GROUPS[group]['publisher']):
        raise DomainError('市场来源身份无效')
    day(value['requested_asof'])
    if not isinstance(value.get('evidence'), list) or not value['evidence']:
        raise DomainError('市场来源缺少证据')
    for item in value['evidence']:
        url = urlsplit(item['url'])
        lixinger = group == 'full_market' and not legacy
        hosts = {'www.lixinger.com'} if lixinger else {'legulegu.com'} if legacy else HOSTS
        if url.scheme != 'https' or url.hostname not in hosts or url.username or url.password or url.port:
            raise DomainError('市场来源证据地址无效')
        if lixinger and item.get('stock_code') != FULL_MARKET_CODE:
            raise DomainError('全市场估值必须来自理杏仁A股全指')
        if not re.fullmatch('[a-f0-9]{64}', item['sha256']):
            raise DomainError('市场来源摘要无效')
    series = value.get('series')
    expected = {key for key, entry in SERIES.items() if entry[0] == group}
    if group == 'full_market' and not legacy:
        expected = set(FULL_MARKET_FIELDS)
    old_fields = {key for key in expected if not key.endswith('_ewpvo')}
    valid_fields = isinstance(series, dict) and (set(series) == expected or
                   (archival and group == 'full_market' and not legacy and set(series) == old_fields))
    if not valid_fields:
        raise DomainError('市场指标集合与来源组不符')
    for key, rows in series.items():
        if not isinstance(rows, list) or not rows or len(rows) > 20000:
            raise DomainError('市场历史规模无效')
        previous = ''
        for row in rows:
            if group == 'full_market' and not legacy and row.get('field') != FULL_MARKET_FIELDS[key]:
                raise DomainError('全市场估值字段与方法不符')
            stamp = str(day(row['date']))
            if stamp <= previous or stamp > value['requested_asof']:
                raise DomainError('市场历史日期重复、乱序或超出截止日')
            previous = stamp
            limit = 1000000 if group == 'composites' else 10000
            if row['value'] is not None:
                number = dec(row['value'])
                if (group != 'full_market' or legacy) and abs(number) > limit:
                    raise DomainError('市场历史数值无效')
            if row.get('published_at') is not None:
                day(row['published_at'])
            if key == 'sf_stock_yoy':
                url = urlsplit(row.get('source_url', ''))
                if url.scheme != 'https' or url.hostname != 'www.pbc.gov.cn':
                    raise DomainError('社融存量同比必须具有官方逐项证据')
    if group == 'calendar':
        calendar = value.get('calendar', {})
        if type(calendar.get('year')) is not int or not isinstance(calendar.get('events'), list):
            raise DomainError('宏观发布日历无效')
        for event in calendar['events']:
            if event.get('group') not in ('pmi', 'prices') or day(event['date']).year != calendar['year']:
                raise DomainError('宏观发布日历身份无效')
            day(event['period'] + '-01')


def settings(value):
    if set(value) != {'automatic', 'groups'} or type(value['automatic']) is not bool:
        raise DomainError('市场更新设置无效')
    if not isinstance(value['groups'], list) or not value['groups'] or len(value['groups']) != len(set(value['groups'])) or any(g not in GROUPS for g in value['groups']):
        raise DomainError('市场更新来源组无效')


def structure(db):
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES.issubset(tables):
        raise DomainError('市场水位数据表缺失')
    if db.execute('''SELECT 1 FROM market_heads h LEFT JOIN market_snapshots s ON s.id=h.snapshot_id
                     WHERE s.id IS NULL OR s.group_id<>h.group_id LIMIT 1''').fetchone():
        raise DomainError('市场来源快照引用不完整')


def backup(db):
    structure(db)
    for row in db.execute('SELECT group_id,sha256,data FROM market_snapshots'):
        value = decode(row[2], row[1])
        source(value, archival=True)
        if value['group'] != row[0]:
            raise DomainError('市场来源快照归属无效')
    for row in db.execute('SELECT id,data FROM market_settings'):
        if row[0] != 'settings':
            raise DomainError('市场设置身份无效')
        settings(json.loads(row[1]))
    for row in db.execute('SELECT id,sha256,data FROM market_reports'):
        value = decode(row[2], row[1])
        if value.get('analysis_id') != row[0] or value.get('method') not in METHODS or value.get('method_version') not in ('waterlevel-v1', 'waterlevel-v2', 'waterlevel-v3', METHOD_VERSION):
            raise DomainError('市场研究快照身份无效')
        for ref in value.get('sources', []):
            table = 'research_snapshots' if ref['kind'] == 'index' else 'market_snapshots'
            if not db.execute('SELECT 1 FROM ' + table + ' WHERE id=?', (ref['snapshot_id'],)).fetchone():
                raise DomainError('市场研究快照证据缺失')
