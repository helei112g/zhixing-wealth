from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import threading
import uuid
import zipfile
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from .common import CURRENCIES, DomainError, ONE, ZERO, currency, day, dec, ds, json_dumps, now, ratio, tags, canonical_region, today, wire
from .ledger import Ledger, Market, normalize_event, periods, split_notice_records, split_notice_confirmed
from .store import SCHEMA, Store, MAX_BACKUP_DATABASE_BYTES
from .version import VERSION, NAME
from .notes import MAX_NOTE_BYTES, MAX_NOTES_BYTES, validate_path
from .investment_notes import InvestmentNotes
from .event_deletion import deletion_plan
from . import market
from . import dividends
from .sync_meta import TRIGGERS, trigger_metadata
from .instruments import BENCHMARKS, describe, identity
from .transactions import transaction_facts, split_effects
from .planning import plan_funding, plan_percentage
from .trade_windows import forward_performance
from .trade_analytics import lot_projection, instrument_projection, annual_summary, dividend_segments, target_path, investment_summary


def uid(): return uuid.uuid4().hex


def running_days(portfolio):
    return max(0, (today() - day(portfolio['start_date'])).days + 1)


def event_fingerprint(record):
    canonical = {k: v for k, v in record.items() if k not in ('id', 'created_at', 'updated_at', 'sequence', 'change_reason', 'allow_duplicate', 'locked_base_amount', 'fingerprint')}
    if 'region' in canonical:canonical['region']=canonical_region(canonical['region'])
    if canonical.get('record_date') is None:canonical.pop('record_date',None)
    if record['kind'] not in ('DIVIDEND', 'REINVEST'):
        canonical.pop('tax_known', None); canonical.pop('ex_date_known', None)
    return hashlib.sha256(json_dumps(canonical).encode()).hexdigest()


class Service:
    def __init__(self, store: Store):
        self.store = store
        self.notes = InvestmentNotes(store)
        self.job_lock = threading.Lock()
        self.syncing = False
        self.pending_sync = {}
        self.upgrade_budget_model()

    def upgrade_budget_model(self):
        """One reversible upgrade; original transaction JSON/IDs remain intact."""
        with self.store.connection() as db:
            marker=db.execute("SELECT value FROM meta WHERE key='experience_version'").fetchone()
        if marker and marker[0]=='2': return
        with self.store.transaction(protected='before-budget-upgrade-'+uid()) as db:
            state=self.store.state(db)
            for old in state['portfolios']:
                if old.get('funding_mode'): continue
                updated=dict(old,funding_mode='budget',budget_history=[{'date':old['start_date'],'amount':old['planned_amount']}])
                Ledger(state,updated).validate()
                db.execute('UPDATE portfolios SET data=? WHERE id=?',(json_dumps(updated),old['id']))
                self.store.audit(db,'portfolio',old['id'],old,updated,'切换预算抵扣；原入金仅保留历史，不重复计入')
            for old in state['instruments']:
                if old.get('builtin'): continue
                new=describe(old)
                if old['market']!=new['market']:
                    updated=dict(old,market=new['market'])
                    db.execute('UPDATE instruments SET data=? WHERE id=?',(json_dumps(updated),old['id']))
                    self.store.audit(db,'instrument',old['id'],old,updated,'按代码自动纠正交易所；保持原流水和标的ID')
            db.execute("INSERT OR REPLACE INTO meta VALUES ('experience_version','2')")

    def ensure_benchmarks(self):
        state=self.store.state()
        missing=[i for i in BENCHMARKS if not any(j['id']==i['id'] for j in state['instruments'])]
        if not missing:return
        with self.store.transaction() as db:
            for item in missing:
                item=dict(item,created_at=now(),updated_at=now())
                db.execute('INSERT OR IGNORE INTO instruments VALUES (?,?,?)',(item['id'],'BENCHMARK:'+item['code'],json_dumps(item)))

    def portfolio(self, state, portfolio_id):
        result = next((p for p in state['portfolios'] if p['id'] == portfolio_id), None)
        if not result: raise DomainError('组合不存在')
        return dict(result, target_annual_pct=result.get('target_annual_pct', '5'))

    def bootstrap(self):
        state = self.store.state(); m = Market(state)
        return wire({k: state[k] for k in ('portfolios', 'instruments', 'allocations', 'revision')} | {
            'portfolios': [dict(p, target_annual_pct=p.get('target_annual_pct', '5'), running_days=running_days(p)) for p in state['portfolios']],
            'today': today(), 'version': VERSION, 'data_directory': str(self.store.directory),
            'benchmarks':BENCHMARKS,
            'event_refs':[{k:e.get(k) for k in ('id','portfolio_id','instrument_id','kind','date','quantity')} for e in state['events'] if e['kind'] in ('BUY','SELL','DIVIDEND','REINVEST','OPENING_POSITION')],
            'latest_fx': {c: m.fx(c, today()) for c in CURRENCIES},
            'fx_entries': [r for r in state['fx'] if r.get('manual') and r.get('mode') != 'daily'][-60:],
            'jobs': self.store.jobs(), 'syncing': self.syncing,
            'price_status': [{'instrument_id': i['id'], 'latest': m.price(i['id'], today(),i.get('series_kind','raw')),
                              'count': len(m.price_series[(i['id'], i.get('series_kind','raw'))].rows) if (i['id'], i.get('series_kind','raw')) in m.price_series else 0}
                             for i in state['instruments']],
            'last_backup': max((p.name for p in self.store.backups.iterdir() if p.suffix in ('.sqlite', '.zip') and not p.name.endswith('.notes.zip')), default=None),
        })

    def save_portfolio(self, data, portfolio_id=None):
        name = str(data.get('name') or '').strip()
        if not name or len(name) > 80: raise DomainError('组合名称须为1到80字')
        planned = dec(data.get('planned_amount'), '总计划投资金额', positive=True)
        start = day(data.get('start_date') or today())
        if start > today() or start.year < 1970: raise DomainError('起始日须为1970年至今天')
        with self.store.transaction(expected_revision=data.get('expected_revision')) as db:
            state = self.store.state(db)
            before = self.portfolio(state, portfolio_id) if portfolio_id else None
            description = data.get('description')
            if description is None: description = (before or {}).get('description', '')
            if not isinstance(description, str) or len(description) > 10000:
                raise DomainError('组合介绍须为不超过10000字的纯文字')
            target = dec(data.get('target_annual_pct', (before or {}).get('target_annual_pct', '5')), '目标年化收益率', nonnegative=True)
            if target > 100: raise DomainError('目标年化收益率须为0到100%')
            if before and any(e['portfolio_id'] == portfolio_id and e['date'] < start.isoformat() for e in state['events']):
                raise DomainError('已有更早流水，起始日不能移到这些流水之后')
            if before and any(e['portfolio_id'] == portfolio_id and e['kind'].startswith('OPENING_') and e['date'] != start.isoformat() for e in state['events']):
                raise DomainError('存在期初资产时，起始日须与期初记录日期一致')
            benchmark = data.get('benchmark_id') or None
            builtin=next((i for i in BENCHMARKS if i['id']==benchmark),None)
            if builtin and not any(i['id']==benchmark for i in state['instruments']):
                item=dict(builtin,created_at=now(),updated_at=now())
                db.execute('INSERT INTO instruments VALUES (?,?,?)',(item['id'],'BENCHMARK:'+item['code'],json_dumps(item)))
                state['instruments'].append(item)
            if benchmark and not any(i['id'] == benchmark for i in state['instruments']):
                raise DomainError('基准标的不存在，请先添加标的或导入序列')
            result = {'id': portfolio_id or uid(), 'name': name, 'planned_amount': ds(planned),
                      'description': description,
                      'start_date': start.isoformat(), 'base_currency': 'CNY', 'target_annual_pct': ds(target),
                      'benchmark_id': benchmark, 'benchmark_basis': 'total_return' if builtin else data.get('benchmark_basis', 'raw'),
                      'funding_mode':data.get('funding_mode') or (before or {}).get('funding_mode') or 'budget',
                      'archived': bool(data.get('archived', False)),
                      'created_at': before['created_at'] if before else now(), 'updated_at': now()}
            if result['benchmark_basis'] not in ('raw', 'total_return'): raise DomainError('基准口径无效')
            if result['funding_mode'] not in ('budget','cash'):raise DomainError('资金口径无效')
            if result['funding_mode']=='budget':
                history=[dict(r) for r in (before or {}).get('budget_history',[])]
                if not history: history=[{'date':str(start),'amount':ds(planned)}]
                else:
                    history[0]['date']=str(start)
                    delta=planned-dec(before['planned_amount'])
                    if delta:history.append({'date':str(today()),'amount':ds(delta)})
                result['budget_history']=history
                Ledger(state,result).validate()
            db.execute('INSERT OR REPLACE INTO portfolios VALUES (?,?)', (result['id'], json_dumps(result)))
            self.store.audit(db, 'portfolio', result['id'], before, result, data.get('change_reason', '编辑组合' if before else '创建组合'))
        return result

    def inline_instrument(self, state, payload):
        """Preview and commit use the same pure code resolution; preview never saves a stub."""
        if not payload.get('instrument'): return state,payload,None
        if payload.get('kind') not in ('BUY','OPENING_POSITION','SUBSCRIBE_PENDING','REINVEST'):
            raise DomainError('新标的请在买入时填写')
        portfolio=self.portfolio(state,payload.get('portfolio_id'))
        description=describe(payload['instrument']); key=description.pop('identity')
        existing=[i for i in state['instruments'] if not i.get('builtin') and identity(i)==key]
        if len(existing)>1: raise DomainError('历史存在多份同代码档案，请先核对后合并，原数据未修改')
        old=existing[0] if existing else None
        if old and old['currency']!=description['currency']:raise DomainError('同代码的交易币种不能改写，请沿用既有币种')
        name=str(payload['instrument'].get('name') or (old or {}).get('name') or description['code']).strip()[:100]
        inst=dict(old or {},**description,id=old['id'] if old else uuid.uuid5(uuid.UUID(self.store.get_id()),key).hex,
                  name=name,custom_name=bool(payload['instrument'].get('name') or (old or {}).get('custom_name')),
                  created_at=(old or {}).get('created_at') or now(),updated_at=now())
        old_alloc=next((a for a in state['allocations'] if a['portfolio_id']==portfolio['id'] and a['instrument_id']==inst['id']),None)
        allocation=dict(old_alloc or {},portfolio_id=portfolio['id'],instrument_id=inst['id'],
                        target_pct=(old_alloc or {}).get('target_pct','0'),**tags(old_alloc or {}))
        differences=[]
        if old and old['name']!=name:differences.append({'field':'名称','before':old['name'],'after':name})
        info={'instrument':inst,'allocation':allocation,'before_instrument':old,'before_allocation':old_alloc,
              'identity':key,'differences':differences,'is_new':old is None}
        candidate=dict(state,instruments=[i for i in state['instruments'] if i['id']!=inst['id']]+[inst],
                       allocations=[a for a in state['allocations'] if (a['portfolio_id'],a['instrument_id'])!=(portfolio['id'],inst['id'])]+[allocation])
        return candidate,dict(payload,instrument_id=inst['id']),info

    def persist_inline(self,db,info):
        if not info:return
        inst,allocation=info['instrument'],info['allocation']
        db.execute('INSERT OR REPLACE INTO instruments VALUES (?,?,?)',(inst['id'],info['identity'],json_dumps(inst)))
        db.execute('INSERT OR REPLACE INTO allocations VALUES (?,?,?)',(allocation['portfolio_id'],inst['id'],json_dumps(allocation)))
        self.store.audit(db,'instrument',inst['id'],info['before_instrument'],inst,'买入时按代码合并标的；名称以最新填写为准')
        self.store.audit(db,'allocation',allocation['portfolio_id']+':'+inst['id'],info['before_allocation'],allocation,'建立本组合持仓关联；标签统一在计划中管理')

    def save_instrument(self, data):
        scope = data.get('portfolio_id')
        market_code = str(data.get('market') or 'SH').upper()
        kind = data.get('type') or 'stock'
        c = currency(data.get('currency') or ('USD' if market_code=='US' else 'HKD' if market_code == 'HK' else 'CNY'))
        code = str(data.get('code') or '').strip().upper()
        if market_code not in ('SH', 'SZ', 'HK', 'US', 'FUND', 'CUSTOM'): raise DomainError('市场无效')
        if kind not in ('stock', 'etf', 'lof', 'fund', 'custom', 'index'): raise DomainError('标的类型无效')
        if market_code=='US':
            info=describe({'market':'US','code':code,'currency':c,'type':kind});code=info['code']
        if market_code == 'HK' and code.isdigit(): code = code.zfill(5)
        if market_code in ('SH', 'SZ', 'FUND') and not re.fullmatch(r'\d{6}', code): raise DomainError('内地证券/基金代码须为6位数字')
        if market_code == 'HK' and not re.fullmatch(r'\d{5}', code): raise DomainError('港股代码须为5位数字')
        if not re.fullmatch(r'[A-Z0-9_.-]{1,40}', code): raise DomainError('代码只接受字母、数字、点、横线和下划线')
        share_class = str(data.get('share_class') or '')[:100]
        identity = '|'.join((market_code, code, kind, c, share_class))
        with self.store.transaction() as db:
            state = self.store.state(db)
            if scope: self.portfolio(state, scope)
            old = db.execute('SELECT data FROM instruments WHERE identity=?', (identity,)).fetchone()
            item = json.loads(old[0]) if old else None
            result = item or {'id': uid(), 'market': market_code, 'code': code, 'type': kind,
                             'currency': c, 'share_class': share_class, 'created_at': now()}
            result = dict(result, name=str(data.get('name') or result.get('name') or code + '.' + market_code).strip()[:100],
                          custom_name=bool(data.get('name') or result.get('custom_name')), updated_at=now())
            db.execute('INSERT OR REPLACE INTO instruments VALUES (?,?,?)', (result['id'], identity, json_dumps(result)))
            self.store.audit(db, 'instrument', result['id'], item, result, '标的建档')
            if scope:
                existing = next((a for a in state['allocations'] if a['portfolio_id'] == scope and a['instrument_id'] == result['id']), None)
                if not existing:
                    allocation = {'portfolio_id': scope, 'instrument_id': result['id'],
                                  'target_pct': '0', **tags(data)}
                    db.execute('INSERT INTO allocations VALUES (?,?,?)', (scope, result['id'], json_dumps(allocation)))
        return result

    def attach_instrument(self, portfolio_id, instrument_id, data):
        with self.store.transaction(expected_revision=data.get('expected_revision')) as db:
            state = self.store.state(db); portfolio = self.portfolio(state, portfolio_id)
            if not any(i['id'] == instrument_id for i in state['instruments']): raise DomainError('标的不存在')
            old = next((a for a in state['allocations'] if a['portfolio_id'] == portfolio_id and a['instrument_id'] == instrument_id), None)
            pct = plan_percentage(data.get('target_pct'))
            funding = plan_funding(self.plan_snapshot(state, portfolio), instrument_id=instrument_id, target_pct=pct)
            item = {'portfolio_id': portfolio_id, 'instrument_id': instrument_id, 'target_pct': ds(pct), **tags(data)}
            db.execute('INSERT OR REPLACE INTO allocations VALUES (?,?,?)', (portfolio_id, instrument_id, json_dumps(item)))
            self.store.audit(db, 'allocation', portfolio_id + ':' + instrument_id, old, item, '更新持仓计划/当前标签；保留原始流水录入标签')
        return wire(dict(item, plan_funding=funding))

    def plan_snapshot(self, state, portfolio):
        end = str(today()); key = ('plan-snapshot', state['revision'], portfolio['id'], end)
        snap = self.store.cache.get(key)
        if snap is None:
            ledger = Ledger(state, portfolio)
            for op in ledger.ops:
                if op[0] > end: break
                ledger.apply(op)
            snap = ledger.snapshot(end)
            self.store.cache[key] = snap
        return snap

    def check_plan(self, portfolio_id, instrument_id, target_pct):
        state = self.store.state(); portfolio = self.portfolio(state, portfolio_id)
        if not any(a['portfolio_id'] == portfolio_id and a['instrument_id'] == instrument_id for a in state['allocations']):
            raise DomainError('标的不属于当前组合')
        pct = plan_percentage(target_pct)
        return wire({'portfolio_id': portfolio_id, 'instrument_id': instrument_id, 'revision': state['revision'],
                     'target_pct': pct, 'target_amount': dec(portfolio['planned_amount']) * pct / 100,
                     'plan_funding': plan_funding(self.plan_snapshot(state, portfolio), instrument_id=instrument_id, target_pct=pct)})

    def sale_lots(self,portfolio_id,instrument_id,at,exclude_event_id=None,sequence=None,*,state=None):
        state=state or self.store.state();p=self.portfolio(state,portfolio_id);date=str(day(at))
        if date<p['start_date'] or date>str(today()):raise DomainError('卖出日期须在记账区间内')
        if not any(a['portfolio_id']==portfolio_id and a['instrument_id']==instrument_id for a in state['allocations']):raise DomainError('标的不属于当前组合')
        old=next((e for e in state['events'] if e['id']==exclude_event_id and e['portfolio_id']==portfolio_id),None)
        try:boundary=int(sequence) if sequence not in (None,'') else old['sequence'] if old else max((e['sequence'] for e in state['events'] if e['portfolio_id']==portfolio_id and e['date']==date),default=0)+1
        except (TypeError,ValueError):raise DomainError('同日顺序必须是整数') from None
        candidate=dict(state,events=[e for e in state['events'] if e['id']!=exclude_event_id])
        ledger=Ledger(candidate,p)
        for op in ledger.ops:
            if op[0]>date:break
            if op[0]==date and op[1]>=boundary and op[4] not in ('SPLIT','BUDGET_OPEN','BUDGET_CHANGE','DIV_ACCRUE','DIV_RECEIPT','INTEREST','SELL_RECEIPT','OPENING_RECEIPT'):continue
            ledger.apply(op)
        events={e['id']:e for e in state['events']};rows=[]
        for lot in ledger.position(instrument_id)['lots']:
            if lot['quantity']<=0 or lot['available_on']>date:continue
            e=events[lot['event_id']]
            rows.append({'buy_event_id':e['id'],'date':lot['date'],'kind':e['kind'],'available_quantity':lot['quantity'],
                         'original_quantity':e.get('reinvest_quantity') if e['kind']=='REINVEST' else e.get('quantity'),
                         'price':e.get('reinvest_price') if e['kind']=='REINVEST' else e.get('price'),'currency':e['currency']})
        return wire(rows)

    def prepare_sale_selection(self,state,payload,event_id=None):
        if payload.get('kind') not in ('SELL','REDEEM_PENDING'):return payload
        payload=dict(payload)
        if str(payload.get('sell_all',False)).lower() in ('true','1'):
            rows=self.sale_lots(payload.get('portfolio_id'),payload.get('instrument_id'),payload.get('date'),event_id,payload.get('sequence'),state=state)
            payload['sell_lots']=[{'buy_event_id':r['buy_event_id'],'quantity':r['available_quantity']} for r in rows]
        elif isinstance(payload.get('sell_lots'),str):
            try:payload['sell_lots']=json.loads(payload['sell_lots'])
            except json.JSONDecodeError:raise DomainError('卖出关联批次JSON无效') from None
        if not isinstance(payload.get('sell_lots'),list) or not payload['sell_lots']:raise DomainError('卖出必须选择一笔或多笔买入批次，或选择全部卖出；总数量自动合计')
        return payload

    def prepared_event(self, state, payload, event_id=None):
        payload=self.prepare_sale_selection(state,payload,event_id)
        if payload.get('kind') in ('FEE','TAX') and payload.get('related_event_id'):
            related=next((e for e in state['events'] if e['id']==payload['related_event_id'] and e['portfolio_id']==payload.get('portfolio_id') and e['kind'] in ('BUY','SELL','DIVIDEND','REINVEST','OPENING_POSITION')),None)
            if not related or related['date']>payload.get('date',''):raise DomainError('费税须关联本组合已发生的买卖或分红')
            payload=dict(payload,instrument_id=related['instrument_id'])
        portfolio = self.portfolio(state, payload.get('portfolio_id'))
        if portfolio.get('funding_mode')=='budget' and payload.get('kind') in ('DEPOSIT','OPENING_CASH','WITHDRAW','FX'):
            raise DomainError('本组合按预算直接抵扣，无需入金、出金或手工换汇；调整资金规模请编辑组合预算')
        if portfolio.get('archived'): raise DomainError('组合已归档，请先恢复后再记账')
        before = next((e for e in state['events'] if e['id'] == event_id), None) if event_id else None
        if event_id and not before: raise DomainError('流水不存在或已取消')
        if before and before['portfolio_id'] != portfolio['id']: raise DomainError('不能把流水移动到其他组合')
        if before and before.get('transfer_id'): raise DomainError('组合转移不能单边修改；请取消整笔转移后重新记录')
        if before and before.get('source_split_id') and payload.get('kind')=='SPLIT':
            payload=dict(payload,source_split_id=payload.get('source_split_id') or before['source_split_id'])
        if payload.get('source_dividend_id') and any(e.get('source_dividend_id')==payload['source_dividend_id'] and e['portfolio_id']==portfolio['id'] and e['id']!=event_id for e in state['events']):
            raise DomainError('这条分红公告已确认入账，请更正原分红，不能重复确认')
        instruments = {i['id']: i for i in state['instruments']}
        allocation = next((a for a in state['allocations'] if a['portfolio_id'] == portfolio['id'] and a['instrument_id'] == payload.get('instrument_id')), None)
        if payload.get('instrument_id') and allocation is None: raise DomainError('该标的尚未添加到当前组合')
        event = normalize_event(payload, portfolio, instruments, allocation)
        unchanged_split_key = before and before['kind'] == 'SPLIT' and before.get('instrument_id') == event.get('instrument_id') and before['date'] == event['date']
        if event.get('source_split_id'):
            notice=next((a for a in state['corporate_actions'] if a['id']==event['source_split_id'] and a.get('kind')=='split_notice'),None)
            if event['kind']!='SPLIT' or not notice or notice['instrument_id']!=event['instrument_id']:
                raise DomainError('送转公告关联无效，请从本标的待核对事项重新读取')
        if event['kind']=='SPLIT':
            owned=[e for e in state['events'] if e['portfolio_id']==portfolio['id']]
            for notice in state['corporate_actions']:
                if notice.get('kind')!='split_notice' or notice['instrument_id']!=event['instrument_id']:continue
                if notice['id']!=event.get('source_split_id') and notice['date']!=event['date']:continue
                if not unchanged_split_key and any(e['id']!=event_id for e in split_notice_records(owned,notice)):
                    raise DomainError('该公告已有送转/拆股记录或登记日记录，请核对并更正原记录，不能再次确认')
                if any(e['id']!=event_id and e.get('source_split_id')==notice['id'] for e in owned):
                    raise DomainError('该公告已有送转/拆股记录，请更正原记录，不能重复关联')
        if event['kind'] == 'SPLIT' and not unchanged_split_key and any(e['portfolio_id'] == portfolio['id'] and e['kind'] == 'SPLIT'
                and e.get('instrument_id') == event['instrument_id'] and e['date'] == event['date']
                and e['id'] != event_id for e in state['events']):
            raise DomainError('本组合同标的同日已有送转/拆股记录，请更正原记录；历史补录会自动重算参与批次，无需再次确认')
        event.update(id=event_id or uid(), created_at=before['created_at'] if before else now(), updated_at=now())
        if payload.get('sequence') in (None, ''):
            event['sequence'] = before['sequence'] if before else 0 if event['kind'].startswith('OPENING_') else max((e['sequence'] for e in state['events'] if e['portfolio_id'] == portfolio['id'] and e['date'] == event['date']), default=0) + 1
        fingerprint = event_fingerprint(event)
        duplicate = next((e['id'] for e in state['events'] if event_fingerprint(e) == fingerprint and e['id'] != event_id), None)
        event['fingerprint'] = fingerprint
        candidate = dict(state, events=[e for e in state['events'] if e['id'] != event_id] + [event])
        self.validate_fee_links(candidate)
        Ledger(candidate, portfolio).validate()
        return event, before, duplicate, candidate

    def validate_fee_links(self,state):
        events={e['id']:e for e in state['events']}
        for e in state['events']:
            if e['kind'] not in ('FEE','TAX') or not e.get('related_event_id'):continue
            ref=events.get(e['related_event_id'])
            if not ref or ref['portfolio_id']!=e['portfolio_id'] or ref['instrument_id']!=e['instrument_id'] or ref['date']>e['date'] or ref['kind'] not in ('BUY','SELL','DIVIDEND','REINVEST','OPENING_POSITION'):
                raise DomainError('后补费税的关联记录将失效，请先更正或取消关联费税')

    def preview_event(self, payload, event_id=None):
        original = self.store.state();state,payload,inline=self.inline_instrument(original,payload)
        event, before, duplicate, candidate = self.prepared_event(state, payload, event_id)
        portfolio = self.portfolio(state, event['portfolio_id'])
        old_ledger = Ledger(original, portfolio); new_ledger = Ledger(candidate, portfolio)
        old = old_ledger.run(include_history=False)
        new = new_ledger.run(include_history=False)
        old_splits, new_splits = old_ledger.split_effects, new_ledger.split_effects
        changes = [{'event_id': key, 'before': old_splits.get(key), 'after': new_splits.get(key)}
                   for key in sorted(old_splits.keys() | new_splits.keys())
                   if old_splits.get(key) != new_splits.get(key)]
        changes.sort(key=lambda r: ((r['after'] or r['before'])['date'], r['event_id']))
        return wire({'revision': original['revision'], 'event': event, 'duplicate_of': duplicate,'instrument_merge':inline,
            'split_changes': changes,
            'cash_changes': [{'currency': c, 'before': old['cash'][n]['balance'], 'after': new['cash'][n]['balance'],
                              'change': new['cash'][n]['balance'] - old['cash'][n]['balance']} for n, c in enumerate(CURRENCIES)],
            'before': {k: old[k] for k in ('total_assets', 'capital', 'available_cash', 'pnl')},
            'after': {k: new[k] for k in ('total_assets', 'capital', 'available_cash', 'pnl')},
            'warnings': new['warnings'], 'recalculate_from': min(event['date'], before['date'] if before else event['date'])})

    def save_event(self, payload, event_id=None):
        with self.store.transaction(expected_revision=payload.get('expected_revision')) as db:
            state,payload,inline = self.inline_instrument(self.store.state(db),payload)
            event, before, duplicate, candidate = self.prepared_event(state, payload, event_id)
            if duplicate and not payload.get('allow_duplicate'):
                raise DomainError('发现相同流水；如确为另一笔相同成交，请在确认预览中允许重复')
            if before and not str(payload.get('change_reason') or '').strip(): raise DomainError('更正历史流水请填写更正原因')
            self.persist_inline(db,inline)
            db.execute('INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,0)', (event['id'], event['portfolio_id'], event['date'], event['sequence'], event['fingerprint'], json_dumps(event)))
            self.store.audit(db, 'event', event['id'], before, event, payload.get('change_reason', '记账'))
        return wire(event)

    def preview_deletion(self, event_id, state=None):
        state = self.store.state() if state is None else state
        plan = deletion_plan(state, event_id)
        candidate = dict(state, events=[e for e in state['events'] if e['id'] not in plan['event_ids']])
        try:
            self.validate_fee_links(candidate)
            for pid in {item['event']['portfolio_id'] for item in plan['items']}:
                Ledger(candidate, self.portfolio(state, pid)).validate()
            plan.update(can_delete=True, validation_error=None)
        except DomainError as exc:
            plan.update(can_delete=False, validation_error=str(exc))
        return wire(plan)

    def delete_event(self, event_id, reason, expected_revision=None, *, cascade=False, event_ids=None):
        if not str(reason or '').strip(): raise DomainError('删除流水必须填写原因')
        if cascade and expected_revision is None: raise DomainError('关联删除必须提供预览的 expected_revision')
        with self.store.transaction(expected_revision=expected_revision) as db:
            state = self.store.state(db); event = next((e for e in state['events'] if e['id'] == event_id), None)
            if not event: raise DomainError('流水不存在')
            if cascade:
                plan = self.preview_deletion(event_id, state)
                if not isinstance(event_ids, list) or sorted(event_ids) != plan['event_ids']:
                    raise DomainError('删除清单与预览不一致，请重新预览并核对全部关联流水')
                if not plan['can_delete']: raise DomainError(plan['validation_error'])
                related = [e for e in state['events'] if e['id'] in plan['event_ids']]
            else:
                related = [e for e in state['events'] if e.get('transfer_id') == event.get('transfer_id')] if event.get('transfer_id') else [event]
            ids = {e['id'] for e in related}
            candidate = dict(state, events=[e for e in state['events'] if e['id'] not in ids])
            self.validate_fee_links(candidate)
            for pid in {e['portfolio_id'] for e in related}:
                Ledger(candidate, self.portfolio(state, pid)).validate()
            for item in related:
                db.execute('UPDATE events SET deleted=1 WHERE id=?', (item['id'],))
                self.store.audit(db, 'event', item['id'], item, None, reason)
        return {'cancelled': event_id, 'deleted_event_ids': sorted(ids)}

    def save_reservation(self, payload, reservation_id=None):
        raise DomainError('预留资金功能已移除，可投入预算直接等于预算余额')

    def dashboard(self, portfolio_id, *, start=None, end=None, grain='month'):
        state = self.store.state(); portfolio = self.portfolio(state, portfolio_id)
        as_of = min(day(end or today()), today()).isoformat()
        if start and day(start) > day(as_of): raise DomainError('区间开始日期不能晚于结束日期')
        if grain not in ('day', 'week', 'month', 'year'): raise DomainError('收益周期无效')
        key = ('snapshot', state['revision'], portfolio_id, as_of)
        snap = self.store.cache.get(key)
        if snap is None:
            snap = Ledger(state, portfolio).run(as_of)
            self.store.cache[key] = snap
        selected_history = [r for r in snap['history'] if not start or r['date'] >= str(start)]
        selected_profit = ZERO; invalid = False; curve = []
        for r in selected_history:
            if r['day_profit'] is None or r['incomplete']: invalid = True
            else: selected_profit += r['day_profit']
            curve.append({'date': r['date'], 'assets': r['assets'], 'pnl': r['pnl'],
                          'portfolio_return': ratio(selected_profit,r['investment_cost']) if not invalid else None,
                          'segment': 1, 'estimated': r['estimated'] or r['incomplete']})
        result = {k: v for k, v in snap.items() if k != 'history'}
        result['curve'] = curve
        missing_points = [p for p, r in zip(curve, selected_history)
                          if p['portfolio_return'] is None and (r['investment_cost'] > ZERO or r['incomplete'])]
        reasons = {}
        for r in selected_history:
            if not r['incomplete'] and r['day_profit'] is not None: continue
            for message in r.get('quality_reasons', []) or ['缺少连续的历史估值，无法计算当日收益']:
                detail = reasons.setdefault(message, {'message': message, 'from': r['date'], 'to': r['date']})
                detail['to'] = r['date']
        result['curve_quality'] = {'missing_days': len(missing_points),
            'from': missing_points[0]['date'] if missing_points else None,
            'to': missing_points[-1]['date'] if missing_points else None,
            'reasons': list(reasons.values()) if missing_points else [],
            'note': '缺口后的累计收益无法连续衔接；请核对待办或补齐历史行情、汇率后重算。基准和目标线仍独立展示。'}
        result['target'], target_points = target_path(selected_history, portfolio)
        for point, target in zip(curve, target_points): point['target_return'] = target
        result['periods'] = periods(snap['history'], grain, start, as_of, investment_basis=True)
        result['selected_return'] = curve[-1]['portfolio_return'] if curve and not invalid else None
        result['selected_segment_count'] = 1 if not invalid else 0
        result['selected_profit'] = sum((r['day_profit'] for r in selected_history if r['day_profit'] is not None), ZERO) if not invalid else None
        result['selected_components'] = {k: sum((r['components'][k] for r in selected_history), ZERO)
                                         if not invalid and all(r['components'][k] is not None for r in selected_history) else None
                                         for k in snap['components']}
        result['benchmark'] = self.benchmark(state, portfolio, selected_history, result['selected_return'])
        perf_key=('instrument-performance',state['revision'],portfolio_id,as_of)
        performance = self.store.cache.get(perf_key)
        if performance is None:
            performance = instrument_projection(state,portfolio,snap['holdings'],as_of)
            self.store.cache[perf_key] = performance
        result['holdings']=[dict(h,performance=performance.get(h['instrument_id'])) for h in snap['holdings']]
        result['capital_summary']=investment_summary(state,portfolio,snap,performance,as_of)
        result['plan_funding']=plan_funding(snap)
        result['running_days']=running_days(portfolio)
        result['todos']=self.todo_items(state,portfolio_id,as_of)
        result['annual_performance']=annual_summary(portfolio,result['capital_summary'],as_of)
        result['cumulative_return']=result['capital_summary']['return_on_cost']
        result['calendar']={key:periods(snap['history'],key,investment_basis=True) for key in ('day','week','month','year')}
        proposals=self.dividend_candidates(portfolio_id,state=state)
        pending=[r for r in proposals if r['status'] in ('pending','needs_review')]
        result['dividend_summary']={'pending_count':len(pending),'review_count':sum(r['status']=='needs_review' for r in pending),
            'estimated_gross':sum((dec(r['estimated_gross']) for r in pending if r['estimated_gross'] is not None and r['notice']['currency']=='CNY'),ZERO),
            'note':'待核对金额未计入收益；含未知金额或外币时人民币合计仅供参考'}
        return wire(result)

    def todo_items(self,state,portfolio_id,as_of=None):
        end=str(as_of or today());instruments={i['id']:i for i in state['instruments']};rows=[]
        for e in state['events']:
            if e['portfolio_id']!=portfolio_id or e['date']>end:continue
            if e['kind'] in ('DIVIDEND','REINVEST') and (not e.get('tax_known',True) or not e.get('ex_date_known',True)):
                fields=[]
                if not e.get('tax_known',True):fields.append('分红税额')
                if not e.get('ex_date_known',True):fields.append('除息日')
                rows.append({'id':'verify:'+e['id'],'type':'verify_dividend','title':instruments[e['instrument_id']]['name']+' · '+e['date']+' · '+'、'.join(fields)+'待核对','event':e})
        for action in state['corporate_actions']:
            if action.get('kind')!='split_notice' or action['date']>end:continue
            iid=action['instrument_id']
            if not any(a['portfolio_id']==portfolio_id and a['instrument_id']==iid for a in state['allocations']):continue
            owned_events=[e for e in state['events'] if e['portfolio_id']==portfolio_id]
            records=split_notice_records(owned_events,action)
            if split_notice_confirmed(records,action):continue
            ledger=Ledger(state,self.portfolio(state,portfolio_id))
            for op in ledger.ops:
                if op[0]>=action['date']:break
                ledger.apply(op)
            if ledger.position(iid)['quantity']<=0:continue
            existing_split=next(iter(records),None)
            if existing_split:
                rows.append({'id':'split:'+action['id'],'type':'verify_split','label':'核对送转/拆股记录','title':instruments[iid]['name']+' · 公告除权日'+action['date']+'、倍数'+action['factor']+'；已录日期'+existing_split['date']+'、倍数'+existing_split['factor']+'，请核对原记录',
                             'notice':action,'event':dict(existing_split,source_split_id=action['id'])});continue
            rows.append({'id':'split:'+action['id'],'type':'confirm_split','title':instruments[iid]['name']+' · '+action['date']+' · 送转/拆股倍数'+action['factor']+'待确认','instrument_id':iid,'notice':action,'prefill':{'kind':'SPLIT','date':action['date'],'factor':action['factor'],'instrument_id':iid,'source_split_id':action['id'],'reason':'核对公开送转/拆股事件：'+action['source']}})
        return rows

    def benchmark(self, state, portfolio, history, portfolio_return):
        inst_id = portfolio.get('benchmark_id')
        if not inst_id or not history: return None
        instrument = next((i for i in state['instruments'] if i['id'] == inst_id), None)
        if not instrument: return None
        market_data = Market(state); kind = portfolio.get('benchmark_basis', 'raw')
        before_day = day(history[0]['date']) - timedelta(days=1)
        base_price = market_data.price(inst_id, before_day, kind)
        base_fx = market_data.fx(instrument['currency'], before_day)
        if not base_price or not base_fx:
            base_price = market_data.price(inst_id, history[0]['date'], kind)
            base_fx = market_data.fx(instrument['currency'], history[0]['date'])
        base = dec(base_price['price']) * dec(base_fx['rate']) if base_price and base_fx else None
        curve = []; complete = base is not None
        for row in history:
            p = market_data.price(inst_id, row['date'], kind); fx = market_data.fx(instrument['currency'], row['date'])
            val = (dec(p['price']) * dec(fx['rate']) / base - ONE) if p and fx and base else None
            if val is None: complete = False
            curve.append({'date': row['date'], 'return': val})
        comparable = kind == 'total_return' and complete and not any(r['estimated'] or r['incomplete'] for r in history)
        end_return = curve[-1]['return'] if curve else None
        return {'instrument': instrument, 'kind': kind, 'label': ('全收益基准' if instrument.get('builtin') else '含息基准（用户提供）') if kind == 'total_return' else '价格参考（不含分红）',
                'curve': curve, 'return': end_return, 'comparable': False,
                'excess': None,
                'note': '组合收益率按累计买入成本计算；基准和目标线仅供走势参考，资金投入时点不同，不直接计算超额。'}

    def instrument_detail(self, portfolio_id, instrument_id):
        state = self.store.state(); snap = self.dashboard(portfolio_id)
        holding = next((h for h in snap['holdings'] if h['instrument_id'] == instrument_id), None)
        if not holding: raise DomainError('当前组合未添加此标的')
        events = [e for e in state['events'] if e['portfolio_id'] == portfolio_id and e.get('instrument_id') == instrument_id]
        trades = []
        for e in events:
            if e['kind'] in ('BUY', 'SELL'): trades.append(e)
            elif e['kind'] == 'REINVEST':
                trades.append(dict(e, trade_kind='BUY', trade_date=e['reinvest_date'],
                                   quantity=e['reinvest_quantity'], price=e['reinvest_price'], fx_rate=e['reinvest_fx_rate']))
        market_data = Market(state)
        dividend_values = {}
        for e in events:
            if e['kind'] not in ('DIVIDEND', 'REINVEST') or e['date'] > snap['date']: continue
            parts = dividend_segments(e, market_data, snap['date'])
            dividend_values[e['id']] = {
                'net_base': transaction_facts(e,market_data,events,snap['date'])['distribution_base'],
                'pending': any(p['pending'] for p in parts),
                'provisional': not e.get('tax_known', True) or not e.get('ex_date_known', True)}
        curves = {}
        for kind in ('raw', 'adjusted', 'total_return'):
            series = market_data.price_series.get((instrument_id, kind))
            curves[kind] = series.rows if series else []
        profit_rows=self.trade_results(portfolio_id,instrument_id,state=state)
        return wire({'transactions':self.transaction_rows(portfolio_id,instrument_id,state=state,profit_rows=profit_rows),'portfolio': self.portfolio(state, portfolio_id), 'holding': holding, 'events': events,
                     'trades': trades, 'curves': curves, 'price_basis': 'adjusted' if curves['adjusted'] and holding['instrument']['market'] in ('SH','SZ','HK','US') else 'raw',
                     'todos': [t for t in snap['todos'] if t.get('instrument_id',t.get('event',{}).get('instrument_id'))==instrument_id], 'dividend_values': dividend_values,
                     'dividends': [e for e in events if e['kind'] in ('DIVIDEND', 'REINVEST')],
                     'dividend_candidates':self.dividend_candidates(portfolio_id,instrument_id,state=state),
                     'trade_results': profit_rows, 'note_count': self.notes.count(portfolio_id, instrument_id)})

    def cache_dividends(self,instrument_id,rows):
        with self.store.transaction() as db:
            state=self.store.state(db)
            inst=next((i for i in state['instruments'] if i['id']==instrument_id),None)
            if not inst:raise DomainError('标的不存在')
            for source in rows:
                if source['code']!=inst['code']:raise DomainError('分红源返回代码不匹配')
                notice_id=uuid.uuid5(uuid.UUID(self.store.get_id()),'dividend|'+instrument_id+'|'+source['source_ref']).hex
                before=next((r for r in state['corporate_actions'] if r['id']==notice_id),None)
                if not before and inst['market'] in ('HK','US'):
                    matches=[r for r in state['corporate_actions'] if r.get('kind')=='dividend_notice' and r.get('instrument_id')==instrument_id and r.get('ex_date')==source['ex_date'] and r.get('currency')==source['currency']]
                    if len(matches)>1:raise DomainError('同日多份分派存在歧义，请先人工核对')
                    if matches:before=matches[0];notice_id=before['id']
                item=dict(source,id=notice_id,kind='dividend_notice',instrument_id=instrument_id,
                          ex_date=str(day(source['ex_date'])),per_share=ds(dec(source['per_share'],'每份分红',positive=True)),
                          currency=currency(source['currency']),fetched_at=now())
                for key in ('record_date','pay_date'):
                    item[key]=str(day(source[key])) if source.get(key) else None
                db.execute('INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?)',(notice_id,instrument_id,json_dumps(item)))
                old={k:v for k,v in (before or {}).items() if k!='fetched_at'}
                current={k:v for k,v in item.items() if k!='fetched_at'}
                if old!=current:self.store.audit(db,'dividend_notice',notice_id,before,item,'更新公开分红公告，待本人核对，不自动入账')

    def cache_actions(self,instrument_id,actions):
        if not actions:return
        with self.store.transaction() as db:
            for action in actions:
                if action.get('kind')!='split_notice':continue
                key=uuid.uuid5(uuid.UUID(self.store.get_id()),'split|'+instrument_id+'|'+action['date']).hex
                item=dict(action,id=key,instrument_id=instrument_id,date=str(day(action['date'])),factor=ds(dec(action['factor'],positive=True)))
                db.execute('INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?)',(key,instrument_id,json_dumps(item)))

    def reinvestment_estimate(self, portfolio_id, instrument_id, date, gross_amount):
        """Read-only estimate from the exact day's automatic fund unit NAV."""
        state = self.store.state()
        self.portfolio(state, portfolio_id)
        if not any(a['portfolio_id'] == portfolio_id and a['instrument_id'] == instrument_id
                   for a in state['allocations']):
            raise DomainError('标的不属于当前组合')
        inst = next(i for i in state['instruments'] if i['id'] == instrument_id)
        selected_date = day(date)
        if selected_date > today():
            raise DomainError('再投净值归属日不能在未来；请核对日期或手填实际确认份额')
        gross = dec(gross_amount, '再投总价', positive=True)
        result = {'portfolio_id': portfolio_id, 'instrument_id': instrument_id,
                  'date': str(selected_date), 'gross_amount': ds(gross), 'currency': inst['currency'],
                  'revision': state['revision'], 'reference': None, 'quantity': None,
                  'quantity_decimals': 2, 'rounding': 'ROUND_HALF_UP',
                  'status': 'unsupported_market', 'note': '仅场外基金支持按单位净值估算；请填写实际确认份额。'}
        if inst['market'] != 'FUND':
            return result
        quote = Market(state).price(instrument_id, selected_date, 'raw')
        if not quote or quote['date'] != str(selected_date):
            return dict(result, status='missing_nav', note='缺少所选日单位净值，请在全局设置同步该基金历史行情后重新读取，或填写实际确认份额。')
        nav = dec(quote['price'], '单位净值', positive=True)
        quantity = dec(gross / nav, '估算份额').quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        result['reference'] = {'date': quote['date'], 'nav': ds(nav), 'source': quote['source']}
        if quantity <= 0:
            return dict(result, status='rounded_zero', note='估算份额四舍五入至两位小数后为0，请按确认单填写实际份额。')
        return dict(result, status='available', quantity=ds(quantity),
                    note='再投总价（不含手续费）÷当日单位净值，四舍五入至两位小数；仅供估算，请按确认单核对。')

    def dividend_candidates(self,portfolio_id,instrument_id=None,*,state=None):
        state=state or self.store.state();p=self.portfolio(state,portfolio_id)
        attached={a['instrument_id'] for a in state['allocations'] if a['portfolio_id']==portfolio_id}
        instruments={i['id']:i for i in state['instruments']};result=[]
        for notice in state['corporate_actions']:
            if notice.get('kind')!='dividend_notice' or notice['instrument_id'] not in attached:continue
            if instrument_id and notice['instrument_id']!=instrument_id:continue
            if notice['ex_date']<p['start_date']:continue
            inst=instruments[notice['instrument_id']]
            existing=next((e for e in state['events'] if e['portfolio_id']==portfolio_id and e.get('source_dividend_id')==notice['id']),None)
            manual=next((e for e in state['events'] if e['portfolio_id']==portfolio_id and e.get('instrument_id')==inst['id'] and e['kind'] in ('DIVIDEND','REINVEST') and e['date']==notice['ex_date'] and not e.get('source_dividend_id')),None)
            cutoff=(day(notice['ex_date'])-timedelta(days=1)).isoformat() if inst['market'] in ('HK','US') else notice.get('record_date')
            qty=None
            if cutoff and p['start_date']<=cutoff<=str(today()):
                ledger=Ledger(state,p)
                for op in ledger.ops:
                    if op[0]>cutoff:break
                    ledger.apply(op)
                pos=ledger.position(inst['id'])
                qty=sum((lot['quantity'] for lot in pos['lots'] if lot['available_on']<=cutoff),ZERO) if inst['market']=='FUND' else pos['quantity']
                if qty<=0 and not (existing or manual):continue
            relevant=any(e['portfolio_id']==portfolio_id and e.get('instrument_id')==inst['id'] and e['date']<=notice['ex_date'] for e in state['events'])
            if qty is None and not relevant:continue
            decision=next((r for r in state['corporate_actions'] if r.get('kind')=='dividend_decision' and r.get('portfolio_id')==portfolio_id and r.get('notice_id')==notice['id']),None)
            gross=(qty*dec(notice['per_share'])).quantize(Decimal('.01'),rounding=ROUND_HALF_UP) if qty is not None and notice['currency']==inst['currency'] else None
            pending_date=notice['ex_date']>str(today())
            recorded=existing or manual
            basis={'eligible_quantity':ds(qty) if qty is not None else None,'per_share':notice['per_share'],'estimated_gross':ds(gross) if gross is not None else None,'record_date':cutoff}
            baseline=(recorded or {}).get('dividend_basis')
            changed=bool(recorded and ((baseline and baseline!=basis) or (existing and not baseline) or (manual and gross is not None and dec(manual['gross_amount'])!=gross)))
            status='needs_review' if changed else 'confirmed' if existing else 'manual_recorded' if manual else 'ignored' if decision and decision.get('ignored') else 'future' if pending_date else 'pending'
            rate=Market(state).fx_series[inst['currency']].at(notice['ex_date']) if inst['currency']!='CNY' else {'rate':'1','date':notice['ex_date']}
            suggested={'kind':'DIVIDEND','portfolio_id':portfolio_id,'instrument_id':inst['id'],'date':notice['ex_date'],
                'gross_amount':ds(gross) if gross is not None else '', 'net_amount':ds(gross) if gross is not None else '',
                'tax':'0','fee':'0','tax_known':False,'ex_date_known':True,'pay_date':notice.get('pay_date') or '',
                'fx_rate':rate['rate'] if rate else '', 'fx_source_date':rate['date'] if rate else notice['ex_date'],
                'reason':'核对自动抓取分红：'+notice['source'],'source_dividend_id':notice['id'],'record_date':notice.get('record_date'),'dividend_basis':basis}
            if changed:
                suggested={**recorded,**{'gross_amount':ds(gross) if gross is not None and gross>0 else '',
                    'net_amount':ds(max(ZERO,gross-dec(recorded['tax']))) if gross is not None and gross>0 else '',
                    'tax_known':False,'source_dividend_id':notice['id'],'dividend_basis':basis,
                    'change_reason':'补录历史交易或公告变化后重新核对分红'}}
            result.append({'notice':notice,'instrument':inst,'portfolio_id':portfolio_id,'eligible_quantity':qty,'eligibility_date':cutoff,
                'estimated_gross':gross,'status':status,'event_id':(existing or manual or {}).get('id'),'suggested_event':suggested,
                'recorded_event':recorded,'previous_basis':baseline,'gross_difference':gross-dec(recorded['gross_amount']) if recorded and gross is not None else None,
                'note':(('按登记日账本份额估算，税费、实际到账金额和再投确认份额须本人核对。' if notice['currency']==inst['currency'] else '派息币种与标的币种不一致，请先按实际到账核对并折算，未自动估算金额。')+notice.get('coverage_note',''))})
        return wire(sorted(result,key=lambda r:r['notice']['ex_date'],reverse=True))

    def confirm_dividend(self,notice_id,payload):
        with self.store.transaction(expected_revision=payload.get('expected_revision')) as db:
            state=self.store.state(db);pid=payload.get('portfolio_id')
            candidate=next((r for r in self.dividend_candidates(pid,state=state) if r['notice']['id']==notice_id),None)
            if not candidate:raise DomainError('未找到本组合有权益的分红公告，请先更新数据并核对持仓日期')
            if candidate['status'] in ('confirmed','manual_recorded'):raise DomainError('该分红已记账，请修改原流水，不能重复确认')
            if candidate['notice']['ex_date']>str(today()):raise DomainError('除息日未到，不能提前作为本期分红入账')
            if candidate['notice'].get('pay_date') and candidate['notice']['pay_date']<candidate['notice']['ex_date']:raise DomainError('该分派为先派付后除息的特殊安排，请按实际交割单手工核对，不直接确认公告')
            if candidate['notice']['currency']!=candidate['instrument']['currency']:raise DomainError('派息币种与标的不同，请先手工核对折算，不直接确认来源金额')
            if payload.get('kind') not in ('DIVIDEND','REINVEST'):raise DomainError('请选择现金分红或红利再投')
            if payload.get('dividend_basis')!=candidate['suggested_event']['dividend_basis']:raise DomainError('持仓或公告已变化，请刷新分红金额后重新核对')
            before=next((e for e in state['events'] if e['id']==candidate.get('event_id')),None) if candidate['status']=='needs_review' else None
            info=None;data=dict(payload,instrument_id=candidate['instrument']['id'],source_dividend_id=notice_id,dividend_basis=candidate['suggested_event']['dividend_basis'])
            data.pop('instrument',None)
            if data['kind']=='REINVEST':
                data['instrument']={k:candidate['instrument'][k] for k in ('code','market','name','currency','type')}
                state,data,info=self.inline_instrument(state,data)
            event,_,duplicate,_=self.prepared_event(state,data,before['id'] if before else None)
            if duplicate:raise DomainError('已存在相同分红流水，请核对原记录')
            self.persist_inline(db,info)
            db.execute('INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,0)',(event['id'],pid,event['date'],event['sequence'],event['fingerprint'],json_dumps(event)))
            self.store.audit(db,'event',event['id'],before,event,'历史持仓变化后重新核对分红' if before else '本人核对公开分红后确认入账')
            decision_id='decision:'+pid+':'+notice_id
            decision={'id':decision_id,'kind':'dividend_decision','instrument_id':event['instrument_id'],'portfolio_id':pid,'notice_id':notice_id,'event_id':event['id'],'ignored':False,'updated_at':now()}
            db.execute('INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?)',(decision_id,event['instrument_id'],json_dumps(decision)))
        return wire(event)

    def ignore_dividend(self,notice_id,payload):
        with self.store.transaction() as db:
            state=self.store.state(db);pid=payload.get('portfolio_id');self.portfolio(state,pid)
            notice=next((r for r in state['corporate_actions'] if r['id']==notice_id and r.get('kind')=='dividend_notice'),None)
            if not notice or not any(a['portfolio_id']==pid and a['instrument_id']==notice['instrument_id'] for a in state['allocations']):raise DomainError('分红公告不属于本组合标的')
            key='decision:'+pid+':'+notice_id;before=next((r for r in state['corporate_actions'] if r['id']==key),None)
            item=dict(before or {},id=key,kind='dividend_decision',instrument_id=notice['instrument_id'],portfolio_id=pid,notice_id=notice_id,ignored=bool(payload.get('ignored',True)),updated_at=now())
            db.execute('INSERT OR REPLACE INTO corporate_actions VALUES (?,?,?)',(key,notice['instrument_id'],json_dumps(item)))
            self.store.audit(db,'dividend_decision',key,before,item,'调整分红待核对提示，不影响资产收益')
        return item

    def transaction_rows(self, portfolio_id, instrument_id=None, *, state=None, profit_rows=None):
        state=state or self.store.state();portfolio=self.portfolio(state,portfolio_id)
        profit_rows=self.trade_results(portfolio_id,instrument_id,state=state) if profit_rows is None else profit_rows
        results={r['event']['id']:r for r in profit_rows};instruments={i['id']:i for i in state['instruments']};market_data=Market(state)
        owned=[e for e in state['events'] if e['portfolio_id']==portfolio_id and (not instrument_id or e.get('instrument_id')==instrument_id)]
        splits=split_effects(state,portfolio)
        rows=[]
        for event in owned:
            result=results.get(event['id']);legacy=portfolio.get('funding_mode')=='budget' and event['kind'] in ('DEPOSIT','OPENING_CASH','WITHDRAW','FX')
            rows.append({'event':event,'instrument':instruments.get(event.get('instrument_id')),
                'facts':transaction_facts(event,market_data,owned,split=splits.get(event['id'])),'actual':result['actual'] if result else None,
                'can_view_profit':result is not None,
                'can_edit':not legacy and not event.get('transfer_id'),'can_cancel':not legacy})
        return wire(sorted(rows,key=lambda r:(r['event']['date'],r['event']['sequence'],r['event']['id']),reverse=True))

    def transaction_page(self, portfolio_id, instrument_id=None, **filters):
        from .transaction_query import paginate
        state = self.store.state()
        if instrument_id and not any(a['portfolio_id'] == portfolio_id and a['instrument_id'] == instrument_id for a in state['allocations']):
            raise DomainError('标的不属于当前组合')
        key = ('transaction-rows', state['revision'], portfolio_id, instrument_id)
        rows = self.store.cache.get(key)
        if rows is None:
            rows = self.transaction_rows(portfolio_id, instrument_id, state=state)
            self.store.cache[key] = rows
        allocations = [a for a in state['allocations'] if a['portfolio_id'] == portfolio_id]
        result = paginate(rows, allocations, **filters)
        result.update(portfolio_id=portfolio_id, instrument_id=instrument_id, revision=state['revision'])
        return result

    def trade_results(self, portfolio_id, instrument_id=None, *, state=None):
        state = state or self.store.state()
        portfolio = self.portfolio(state, portfolio_id)
        actual = lot_projection(state, portfolio)
        instruments = {i['id']: i for i in state['instruments']}
        todos = self.todo_items(state, portfolio_id)
        out = []
        for event in state['events']:
            if event['portfolio_id'] != portfolio_id or event['kind'] not in ('BUY', 'SELL', 'REINVEST'):
                continue
            if instrument_id and event['instrument_id'] != instrument_id:
                continue
            trade_date = event['reinvest_date'] if event['kind'] == 'REINVEST' else event['date']
            out.append({'event': event, 'instrument': instruments[event['instrument_id']], 'trade_date': trade_date,
                        'actual': actual.get(event['id']),
                        'todos': [t for t in todos if (t.get('instrument_id') or t.get('event', {}).get('instrument_id')) == event['instrument_id']]})
        return wire(sorted(out, key=lambda r: (r['trade_date'], r['event']['sequence'], r['event']['id']), reverse=True))

    def transaction_detail(self, portfolio_id, event_id):
        # Hold the store lock so facts, neighbors and revision history describe one revision.
        with self.store.connection() as db:
            state = self.store.state(db)
            self.portfolio(state, portfolio_id)
            raw = db.execute('SELECT data,deleted FROM events WHERE id=? AND portfolio_id=?', (event_id, portfolio_id)).fetchone()
            if not raw:
                raise DomainError('当前组合找不到这笔流水')
            event = json.loads(raw['data'])
            instrument_id = event.get('instrument_id')
            results = self.trade_results(portfolio_id, instrument_id, state=state) if instrument_id else []
            current = next((r for r in results if r['event']['id'] == event_id), None)
            owned = [e for e in state['events'] if e['portfolio_id'] == portfolio_id]
            history = [dict(r) for r in db.execute("SELECT * FROM revisions WHERE entity='event' AND entity_id=? ORDER BY id DESC", (event_id,))]
            for revision in history:
                for key in ('before_data', 'after_data'):
                    revision[key] = json.loads(revision[key]) if revision[key] is not None else None
            return wire({'portfolio_id': portfolio_id, 'revision': state['revision'], 'event': event,
                         'instrument': next((i for i in state['instruments'] if i['id'] == instrument_id), None),
                         'facts': transaction_facts(event, Market(state), owned,
                             split=split_effects(state,self.portfolio(state,portfolio_id)).get(event_id) if event['kind']=='SPLIT' and not raw['deleted'] else None),
                         'actual': current['actual'] if current and not raw['deleted'] else None,
                         'forward_performance': forward_performance(state, event) if current and not raw['deleted'] else None,
                         'todos': current['todos'] if current else [], 'cancelled': bool(raw['deleted']),
                         'neighbors': [{'id': r['event']['id'], 'date': r['trade_date'], 'kind': r['event']['kind']} for r in results] if current else [],
                         'revisions': history})

    def tag_report(self, portfolio_id=None, dimension='asset_class'):
        if dimension not in ('asset_class', 'region', 'tags'): raise DomainError('标签统计维度无效')
        state = self.store.state()
        selected = [p for p in state['portfolios'] if p['id'] == portfolio_id] if portfolio_id else [p for p in state['portfolios'] if not p.get('archived')]
        if portfolio_id and not selected: raise DomainError('组合不存在')
        groups = {}; pie_groups = {}; total = ZERO; incomplete = False; untagged = ZERO
        def add(bucket, key, label, value, detail):
            if key not in bucket: bucket[key] = {'key': json_dumps(key), 'label': label, 'value': ZERO, 'details': []}
            if value is not None: bucket[key]['value'] += value
            bucket[key]['details'].append(detail)
        for p in selected:
            snap = self.dashboard(p['id'])
            if snap['total_assets'] is None: incomplete = True
            else: total += dec(snap['total_assets'])
            for h in snap['holdings']:
                for lot in h['lots']:
                    value = dec(lot['value_base']) if lot['value_base'] is not None else None
                    labels = lot['tags'] if dimension == 'tags' else [lot.get(dimension) or '未分类']
                    detail = dict(lot, portfolio_id=p['id'], portfolio_name=p['name'], instrument=h['instrument'])
                    for label in labels: add(groups, label, label, value, detail)
                    if not lot['tags'] and value is not None: untagged += value
                    if dimension == 'tags':
                        combination = tuple(sorted(set(labels)))
                        label = ' + '.join('「'+v+'」' for v in combination) if combination else '未打主题标签'
                        add(pie_groups, ('tags',)+combination, label, value, detail)
            cash = dec(snap['cash_total']) if snap['cash_total'] is not None else None
            cash_detail = {'portfolio_id': p['id'], 'portfolio_name': p['name'], 'value_base': cash, 'instrument': None}
            add(pie_groups, ('cash',), '剩余可投金额', cash, cash_detail)
            if dimension != 'tags': add(groups, '现金', '现金', cash, cash_detail)
            for r in snap['receivables']:
                value = dec(r['value_base']) if r['value_base'] is not None else None
                detail = dict(r, portfolio_id=p['id'], portfolio_name=p['name'], instrument=None)
                add(pie_groups, ('receivables',), '应收及在途', value, detail)
                if dimension != 'tags': add(groups, '应收及在途', '应收及在途', value, detail)
        if dimension != 'tags': pie_groups = groups
        for bucket in (groups, pie_groups):
            for item in bucket.values(): item['weight'] = ratio(item['value'], total) if not incomplete else None
        slices = sorted(pie_groups.values(), key=lambda g: (-g['value'], g['label']))
        pie_eligible = not incomplete and total > ZERO and all(g['value'] >= ZERO for g in slices)
        return wire({'dimension': dimension, 'scope': portfolio_id or 'all', 'total_assets': None if incomplete else total,
                     'groups': sorted(groups.values(), key=lambda g: (-g['value'], g['label'])), 'incomplete': incomplete,
                     'pie_groups': slices, 'pie_eligible': pie_eligible,
                     'pie_basis': '按互斥标签组合划分，各笔持仓只计一次；现金、未打标签及应收一并计入总资产' if dimension == 'tags' else '按总资产构成划分，包含现金及应收',
                     'overlapping': dimension == 'tags', 'untagged_value': untagged, 'portfolio_count': len(selected)})

    def cache_prices(self, instrument_id, rows):
        counts = {'saved': 0, 'unchanged': 0}
        with self.store.transaction() as db:
            state = self.store.state(db)
            if not any(i['id'] == instrument_id for i in state['instruments']): raise DomainError('标的不存在')
            for row in rows:
                d = day(row.get('date'))
                if d > today(): raise DomainError('不能录入未来价格')
                kind = row.get('kind', 'raw')
                if kind not in ('raw', 'adjusted', 'total_return'): raise DomainError('价格序列口径无效')
                item = dict(row, instrument_id=instrument_id, date=d.isoformat(), kind=kind,
                            price=ds(dec(row.get('price'), '价格/净值', positive=True)),
                            manual=False, source=str(row.get('source') or '数据源同步')[:120], fetched_at=now())
                previous = db.execute('''SELECT data FROM prices WHERE instrument_id=? AND date=?
                    AND kind=? AND manual=0 ORDER BY id DESC LIMIT 1''', (instrument_id, item['date'], kind)).fetchone()
                meaningful = lambda value: {k: v for k, v in value.items() if k != 'fetched_at'}
                if previous and meaningful(json.loads(previous['data'])) == meaningful(item):
                    counts['unchanged'] += 1
                    continue
                db.execute('INSERT INTO prices(instrument_id,date,kind,manual,data) VALUES (?,?,?,?,?)',
                           (instrument_id, item['date'], kind, 0, json_dumps(item)))
                counts['saved'] += 1
            if counts['saved']:
                self.store.audit(db, 'price', instrument_id, None, {'count': counts['saved'], 'manual': False}, '同步价格序列，保留变化版本')
        return counts

    def save_fx(self, payload):
        c = currency(payload.get('currency'))
        if c == 'CNY': raise DomainError('CNY本位汇率固定为1')
        d = day(payload.get('date')); end = day(payload['end_date']).isoformat() if payload.get('end_date') else None
        if end and end <= d.isoformat(): raise DomainError('恢复自动的日期必须晚于生效日')
        mode = payload.get('mode', 'manual')
        if mode not in ('manual', 'daily', 'auto'): raise DomainError('汇率设置模式无效')
        item = {'currency': c, 'date': d.isoformat(), 'end_date': end, 'mode': mode,
                'rate': ds(dec(payload.get('rate'), '估值汇率', positive=True)) if mode != 'auto' else None,
                'manual': True, 'source': str(payload.get('source') or '手工估值设置')[:120], 'fetched_at': now()}
        with self.store.transaction() as db:
            action, before = self.store.upsert_fx(db, item)
            if action != 'unchanged':
                self.store.audit(db, 'fx', c + ':' + item['date'], before, item, '修改估值汇率；交易锁定率不变')
        return item

    def cache_fx(self, rows):
        counts = {'inserted': 0, 'updated': 0, 'unchanged': 0}
        with self.store.transaction() as db:
            for row in rows:
                c = currency(row.get('currency'))
                d = day(row.get('date'))
                if c == 'CNY' or d > today():
                    raise DomainError('只缓存不晚于今天的外币兑CNY日频汇率')
                item = {'currency': c, 'date': d.isoformat(),
                        'rate': ds(dec(row.get('rate'), '汇率', positive=True)),
                        'source': str(row.get('source') or '数据源同步')[:120],
                        'manual': False, 'fetched_at': now()}
                action, _ = self.store.upsert_fx(db, item)
                counts[action] += 1
        return counts

    def revisions(self, entity, entity_id):
        if entity == 'investment_note':
            raise DomainError('投资笔记请通过指定组合和标的的笔记接口读取')
        with self.store.connection() as db:
            rows = db.execute('SELECT * FROM revisions WHERE entity=? AND entity_id=? ORDER BY id DESC', (entity, entity_id)).fetchall()
            return [dict(r, before_data=json.loads(r['before_data']) if r['before_data'] else None,
                         after_data=json.loads(r['after_data']) if r['after_data'] else None) for r in rows]

    def transfer(self, payload):
        source_id, target_id = payload.get('source_id'), payload.get('target_id')
        if source_id == target_id: raise DomainError('转入和转出组合不能相同')
        state = self.store.state(); self.portfolio(state, source_id); self.portfolio(state, target_id)
        link = uid(); base = {'date': payload['date'], 'currency': payload.get('currency', 'CNY'),
                             'amount': payload['amount'], 'fx_rate': payload.get('fx_rate', '1'),
                             'reason': '组合间现金转移：' + str(payload.get('reason') or ''), 'transfer_id': link}
        with self.store.transaction() as db:
            state = self.store.state(db); created = []
            for pid, kind in ((source_id, 'WITHDRAW'), (target_id, 'DEPOSIT')):
                e, _, _, candidate = self.prepared_event(state, dict(base, portfolio_id=pid, kind=kind))
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,0)', (e['id'], pid, e['date'], e['sequence'], e['fingerprint'], json_dumps(e)))
                self.store.audit(db, 'event', e['id'], None, e, '组合现金转移（双边原子记账）')
                state = candidate; created.append(e)
        return wire(created)

    def csv_preview(self, text, category='events', portfolio_id=None, instrument_id=None):
        if category not in ('events','fx'): raise DomainError('价格仅通过数据源同步，不支持人工价格导入')
        if len(text.encode()) > 10 * 1024 * 1024: raise DomainError('CSV超过10MB')
        try: rows = list(csv.DictReader(io.StringIO(text.lstrip('\ufeff'))))
        except csv.Error as exc: raise DomainError('CSV格式无法读取') from exc
        if len(rows) > 30000: raise DomainError('一次最多导入30,000行')
        if not rows: raise DomainError('CSV为空，或缺少标题行')
        state = self.store.state(); preview = []; errors = []; seen = set()
        for n, raw in enumerate(rows, 2):
            raw = {str(k).strip(): v for k, v in raw.items() if k is not None}
            try:
                if category == 'events':
                    payload = dict(raw, portfolio_id=portfolio_id)
                    if (raw.get('tags') or '').startswith('['):payload['tags']=json.loads(raw['tags'])
                    instrument_input=None
                    if payload.get('code') and str(payload.get('kind','')).upper() in ('BUY','OPENING_POSITION','SUBSCRIBE_PENDING','REINVEST') and not any(i['id']==payload.get('instrument_id') for i in state['instruments']):
                        instrument_input={'market':payload.get('market') or ('HK' if len(payload['code'])==5 else 'CN'),
                                          'code':payload['code'],'name':payload.get('name') or '',
                                          'currency':payload.get('currency') or 'CNY'}
                        state_for_row,payload,_=self.inline_instrument(state,dict(payload,instrument=instrument_input))
                    else:state_for_row=state
                    if not payload.get('instrument_id') and payload.get('code'):
                        matching = [i for i in state['instruments'] if i['code'] == payload['code'].zfill(5 if raw.get('market') == 'HK' else 6) and (not raw.get('market') or i['market'] == raw['market'])]
                        if len(matching) != 1: raise DomainError('请用instrument_id或准确市场+代码指定标的')
                        payload['instrument_id'] = matching[0]['id']
                    if raw.get('tags', '').startswith('['): payload['tags'] = json.loads(raw['tags'])
                    source_id = raw.get('event_id') or None
                    e, duplicate, candidate = self.prepared_csv_event(state_for_row, payload, source_id)
                    if not source_id: duplicate = duplicate or (e['fingerprint'] if e['fingerprint'] in seen else None)
                    seen.add(e['fingerprint'])
                    if not duplicate: state = candidate
                    preview.append({'line': n, 'data': e, 'source_event_id': source_id,'instrument_input':instrument_input, 'duplicate': bool(duplicate)})
                elif category == 'fx':
                    d = day(raw.get('date')); c = currency(raw.get('currency'))
                    if d > today() or c == 'CNY': raise DomainError('请导入不晚于今天的HKD/USD汇率')
                    preview.append({'line': n, 'data': {'date': str(d), 'currency': c, 'rate': ds(dec(raw.get('rate'), '汇率', positive=True)), 'manual': True, 'mode': 'daily', 'source': raw.get('source') or '手工CSV', 'fetched_at': now()}, 'duplicate': False})
                else: raise DomainError('CSV类型无效')
            except (DomainError, ValueError, KeyError, json.JSONDecodeError) as exc:
                errors.append({'line': n, 'message': str(exc)})
        return wire({'rows': preview, 'errors': errors, 'count': len(rows), 'duplicates': sum(r['duplicate'] for r in preview),
                     'category': category, 'portfolio_id': portfolio_id, 'instrument_id': instrument_id})

    def prepared_csv_event(self, state, payload, source_id=None):
        if source_id and not re.fullmatch(r'[a-f0-9]{32}', source_id): raise DomainError('event_id须保留本应用导出的32位ID，手工模板可留空')
        existing = next((e for e in state['events'] if e['id'] == source_id), None) if source_id else None
        if isinstance(payload.get('sell_lots'),str) and payload['sell_lots']:
            try:payload=dict(payload,sell_lots=json.loads(payload['sell_lots']))
            except json.JSONDecodeError:raise DomainError('卖出关联批次JSON无效') from None
        if isinstance(payload.get('dividend_basis'),str) and payload['dividend_basis']:
            try:payload=dict(payload,dividend_basis=json.loads(payload['dividend_basis']))
            except json.JSONDecodeError:raise DomainError('分红核对基准JSON无效') from None
        if existing:
            if existing['portfolio_id'] != payload.get('portfolio_id'): raise DomainError('原event_id属于另一组合，不能移动流水')
            portfolio = self.portfolio(state, existing['portfolio_id'])
            instruments = {i['id']: i for i in state['instruments']}
            allocation = next((a for a in state['allocations'] if a['portfolio_id'] == portfolio['id'] and a['instrument_id'] == payload.get('instrument_id')), None)
            event = normalize_event(payload, portfolio, instruments, allocation)
            if event_fingerprint(existing) != event_fingerprint(event):
                raise DomainError('同一event_id内容已变化，请通过流水更正并留痕，不能用CSV覆盖')
            return existing, True, state
        event, _, duplicate, candidate = self.prepared_event(state, payload)
        if source_id:
            with self.store.connection() as db:
                cancelled = db.execute('SELECT deleted FROM events WHERE id=?', (source_id,)).fetchone()
            if cancelled: raise DomainError('此event_id已取消，不能通过CSV恢复；请新建正确记录')
            generated_id = event['id']; event['id'] = source_id
            candidate = dict(candidate, events=[e for e in candidate['events'] if e['id'] != generated_id] + [event])
            Ledger(candidate, self.portfolio(state, event['portfolio_id'])).validate()
            duplicate = False  # Distinct original IDs preserve intentionally identical fills.
        return event, duplicate, candidate

    def csv_import(self, text, category, portfolio_id=None, instrument_id=None):
        preview = self.csv_preview(text, category, portfolio_id, instrument_id)
        if preview['errors']: raise DomainError('CSV有错误，请修正后整批导入；没有写入任何行')
        records = [r for r in preview['rows'] if not r['duplicate']]
        rows = [r['data'] for r in records]; imported = 0
        with self.store.transaction(protected='before-import-' + uid()) as db:
            state = self.store.state(db)
            if category == 'events':
                for row in records:
                    info=None;current=state;payload=row['data']
                    if row.get('instrument_input'):current,payload,info=self.inline_instrument(state,dict(payload,instrument=row['instrument_input']))
                    event, duplicate, candidate = self.prepared_csv_event(current, payload, row.get('source_event_id'))
                    if duplicate: continue
                    self.persist_inline(db,info)
                    db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,0)', (event['id'], event['portfolio_id'], event['date'], event['sequence'], event['fingerprint'], json_dumps(event)))
                    self.store.audit(db, 'event', event['id'], None, event, 'CSV批量导入')
                    state = candidate
                    imported += 1
            else:
                for e in rows:
                    self.store.upsert_fx(db, e)
            self.store.audit(db, 'import', uid(), None, {'category': category, 'count': len(rows)}, '整批CSV原子导入')
        return {'imported': imported if category == 'events' else len(rows), 'skipped_duplicates': preview['duplicates']}

    def csv_export(self, portfolio_id):
        state = self.store.state(); self.portfolio(state, portfolio_id)
        fields = ['event_id', 'kind', 'date', 'sequence', 'instrument_id', 'market','code','name','currency', 'amount', 'quantity', 'price', 'gross_amount', 'fee', 'tax',
                  'fx_rate', 'fx_type', 'fx_source_date', 'payment_date', 'confirmation_date', 'net_amount', 'pay_date', 'pay_fx_rate',
                  'reinvest_quantity', 'reinvest_price', 'reinvest_gross_amount', 'reinvest_date', 'reinvest_fx_rate', 'pay_fx_source_date', 'pay_fx_type', 'reinvest_fx_source_date', 'reinvest_fx_type', 'tax_known', 'ex_date_known',
                  'from_currency', 'to_currency', 'from_amount', 'to_amount', 'from_fx_rate', 'to_fx_rate', 'factor',
                  'new_quantity', 'new_cost', 'historical_cost', 'initial_net_invested', 'asset_class', 'region', 'tags', 'reason', 'external_ref', 'reservation_id', 'transfer_id','source_dividend_id','source_split_id','sell_lots','dividend_basis','record_date','related_event_id','costs_combined']
        buf = io.StringIO(); writer = csv.DictWriter(buf, fieldnames=fields, extrasaction='ignore'); writer.writeheader()
        for e in state['events']:
            if e['portfolio_id'] == portfolio_id:
                inst=next((i for i in state['instruments'] if i['id']==e.get('instrument_id')),{})
                writer.writerow(dict(e, event_id=e['id'], market=inst.get('market',''),code=inst.get('code',''),name=inst.get('name',''),tags=json_dumps(e.get('tags', [])),sell_lots=json_dumps(e.get('sell_lots',[])) if e.get('sell_lots') else '',dividend_basis=json_dumps(e['dividend_basis']) if e.get('dividend_basis') else ''))
        return '\ufeff' + buf.getvalue()

    def backup_download(self):
        with self.store.lock:
            path = self.store.backup(); data = path.read_bytes()
            manifest = {'format': 2, 'schema': SCHEMA, 'created_at': now(), 'sha256': hashlib.sha256(data).hexdigest(),
                        'application': NAME, 'note_files': {}}
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
                z.writestr('portfolio.sqlite', data)
                with zipfile.ZipFile(path.with_suffix('.notes.zip')) as notes:
                    for name in notes.namelist():
                        content = notes.read(name)
                        manifest['note_files'][name] = hashlib.sha256(content).hexdigest()
                        z.writestr(name, content)
                z.writestr('manifest.json', json_dumps(manifest))
            content = buf.getvalue()
            with zipfile.ZipFile(io.BytesIO(content)) as check:
                if check.testzip() is not None:
                    raise DomainError('完整备份压缩校验失败，原始备份已保留')
            target = path.with_suffix('.zip')
            pending = target.with_name(target.name + '.pending-' + uid())
            try:
                with pending.open('xb') as handle:
                    handle.write(content); handle.flush(); os.fsync(handle.fileno())
                os.link(pending, target)
                directory_fd = os.open(self.store.backups, os.O_RDONLY)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
                # A durable full ZIP now includes the same DB and every note version.
                path.unlink()
                path.with_suffix('.notes.zip').unlink()
            finally:
                pending.unlink(missing_ok=True)
            return content

    def restore(self, content):
        if len(content) > 100 * 1024 * 1024: raise DomainError('备份超过100MB')
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                entries = z.infolist()
                if len(entries) > 10000 or len({i.filename for i in entries}) != len(entries):
                    raise DomainError('备份包含重复文件名或文件数超过10000')
                if any((i.external_attr >> 16) & 0o170000 == 0o120000 or i.flag_bits & 1 for i in entries):
                    raise DomainError('备份不能包含符号链接或加密文件')
                info = z.getinfo('portfolio.sqlite')
                if info.file_size > MAX_BACKUP_DATABASE_BYTES: raise DomainError('备份解压后超过512 MiB')
                if z.getinfo('manifest.json').file_size > 1024 * 1024: raise DomainError('备份清单过大')
                data = z.read(info); manifest = json.loads(z.read('manifest.json'))
                if not isinstance(manifest, dict) or manifest.get('format', 1) not in (1, 2):
                    raise DomainError('备份格式不受支持')
                if hashlib.sha256(data).hexdigest() != manifest['sha256']: raise DomainError('备份校验码不匹配')
                expected = manifest.get('note_files', {})
                if not isinstance(expected, dict) or set(z.namelist()) != {'portfolio.sqlite', 'manifest.json', *expected}:
                    raise DomainError('备份文件与清单不一致')
                files = {}; total = 0
                for name, checksum in expected.items():
                    validate_path(name)
                    size = z.getinfo(name).file_size
                    total += size
                    if size > MAX_NOTE_BYTES or total > MAX_NOTES_BYTES:
                        raise DomainError('笔记文件超过单篇1 MiB或总计128 MiB限制')
                    files[name] = z.read(name)
                    if hashlib.sha256(files[name]).hexdigest() != checksum:
                        raise DomainError('笔记文件校验码不匹配，现有账本未替换')
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
            raise DomainError('请选择本应用导出的完整ZIP备份') from exc
        with self.job_lock:
            if self.syncing: raise DomainError('行情仍在更新，请更新结束后恢复备份，避免写入相互覆盖')
            preserved=self.store.restore(data, files);self.upgrade_budget_model()
            return {'restored': True, 'preserved_backup': preserved}

    def start_sync(self, instrument_id=None, *, full=False, trigger='manual_update', _triggers=None, _queued=False):
        causes=set(_triggers or [trigger])
        if not causes or not causes.issubset(TRIGGERS):raise DomainError('无效的更新触发原因')
        if causes.issubset({'startup', 'schedule'}):
            # A fresh installation stays empty, including jobs and FX caches.
            # Existing investment or family records retain automatic syncing.
            from .family_domain import TABLES
            with self.store.connection() as db:
                populated = any(db.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone()
                                for table in ('portfolios', 'instruments', *TABLES))
            if not populated:
                return {'skipped': True, 'reason': 'empty_ledger'}
        with self.job_lock:
            if self.syncing:
                pending=self.pending_sync.setdefault(instrument_id or '*',{'full':False,'triggers':set()})
                pending['full'] |= full
                pending['triggers'].update(causes)
                return {'already_running': True,'queued':True}
            self.syncing = True
        job = {'id': uid(), 'status': 'running', 'started_at': now(), 'items': [], 'message': '正在更新本地缓存',
               'instrument_id':instrument_id,'full':full,**trigger_metadata(causes,_queued)}
        self.store.save_job(job)
        def work():
            try:
                state = self.store.state(); instruments = state['instruments']
                if instrument_id: instruments = [i for i in instruments if i['id'] == instrument_id]
                start = min((p['start_date'] for p in state['portfolios']), default=(today() - timedelta(days=90)).isoformat())
                start = (day(start) - timedelta(days=14)).isoformat()
                # Only public symbol/date queries leave the device. No portfolio data is transmitted.
                for c in ('HKD', 'USD'):
                    try:
                        existing = [r['date'] for r in state['fx'] if r['currency'] == c and not r.get('manual')]
                        beginning = start if full or not existing else max(start, (day(max(existing)) - timedelta(days=7)).isoformat())
                        rows = market.exchange_rates(c, beginning, today().isoformat())
                        # The latest feed may contain a newer date than a market's last close.
                        latest = market.exchange_rates(c)
                        by_date = {r['date']: r for r in rows + latest}
                        counts = self.cache_fx(by_date.values())
                        job['items'].append({'source_kind':'fx','currency':c,'name': c + '/CNY', 'status': 'ok', 'count': len(by_date), **counts, 'date': max(by_date), 'source': latest[-1]['source']})
                    except Exception as exc:
                        job['items'].append({'source_kind':'fx','currency':c,'name': c + '/CNY', 'status': 'error', 'message': str(exc)[:500]})
                    self.store.save_job(job)
                for inst in instruments:
                    if inst['market'] == 'CUSTOM':
                        job['items'].append({'source_kind':'prices','instrument_id':inst['id'],'name':inst['name'],'status':'error','message':'该标的暂无可用同步数据源，请检查代码或稍后重试'})
                        self.store.save_job(job)
                        continue
                    if inst['market'] in ('SH','SZ') and inst['type']=='stock':
                        try:
                            review_notes=[]
                            actions=dividends.fetch_stock_splits(inst,notes=review_notes)
                            self.cache_actions(inst['id'],actions)
                            job['items'].append({'source_kind':'corporate_actions','instrument_id':inst['id'],'name':inst['code']+' 企业行动数据','status':'ok','count':len(actions),'source':'东方财富·已实施权益分派','notes':['仅生成待核对事项；确认后才调整份额，总成本不变',*review_notes]})
                        except Exception as exc:
                            job['items'].append({'source_kind':'corporate_actions','instrument_id':inst['id'],'name':inst['code']+' 企业行动数据','status':'error','message':str(exc)[:500]})
                    try:
                        existing = [r['date'] for r in state['prices'] if r['instrument_id'] == inst['id'] and r['kind'] == inst.get('series_kind','raw') and not r.get('manual')]
                        beginning = start if full or not existing else max(start, (day(max(existing)) - timedelta(days=7)).isoformat())
                        result = market.quotes(inst, beginning, today().isoformat(), adjusted_start=start)
                        price_counts = self.cache_prices(inst['id'], result['rows'])
                        self.cache_actions(inst['id'],result.get('actions',[]))
                        if result.get('name') and not inst.get('custom_name') and not inst.get('builtin'):
                            with self.store.transaction() as db:
                                fresh = db.execute('SELECT identity,data FROM instruments WHERE id=?', (inst['id'],)).fetchone()
                                if fresh:
                                    obj = json.loads(fresh['data'])
                                    if not obj.get('custom_name'):
                                        obj['name'] = result['name']
                                        db.execute('UPDATE instruments SET data=? WHERE id=?', (json_dumps(obj), inst['id']))
                        raw = [r for r in result['rows'] if r['kind'] == inst.get('series_kind','raw')]
                        job['items'].append({'source_kind':'prices','instrument_id':inst['id'],'name': inst['code'] + ' ' + (result.get('name') or inst['name']), 'status': 'ok', 'count': len(raw), 'saved':price_counts['saved'], 'unchanged':price_counts['unchanged'], 'date': max(r['date'] for r in raw), 'source': result['source'], 'notes': result['notes']})
                    except Exception as exc:
                        job['items'].append({'source_kind':'prices','instrument_id':inst['id'],'name': inst['code'], 'status': 'error', 'message': str(exc)[:500]})
                    if not inst.get('builtin') and inst['type']!='index':
                        try:
                            notices=dividends.fetch_notices(inst)
                            if notices is None:
                                job['items'].append({'source_kind':'dividends','instrument_id':inst['id'],'name':inst['code']+' 分红','status':'manual','message':'暂未提供适用的分红源，可手工核对录入'})
                            else:
                                self.cache_dividends(inst['id'],notices)
                                job['items'].append({'source_kind':'dividends','instrument_id':inst['id'],'name':inst['code']+' 分红','status':'ok','count':len(notices),'date':max((r['ex_date'] for r in notices),default='—'),'source':notices[0]['source'] if notices else '公开分红表','notes':['公告只进入待核对列表，不自动记收益']})
                        except Exception as exc:
                            job['items'].append({'source_kind':'dividends','instrument_id':inst['id'],'name':inst['code']+' 分红','status':'error','message':str(exc)[:500]})
                    self.store.save_job(job)
                job['status'] = 'partial' if any(r['status'] == 'error' for r in job['items']) else 'complete'
                job['message'] = '部分数据未更新；旧缓存保留，可重试' if job['status'] == 'partial' else '更新完成'
            except Exception as exc:
                job.update(status='error', message=str(exc)[:500])
            finally:
                job['finished_at'] = now(); self.store.save_job(job)
                with self.job_lock:
                    self.syncing = False
                    pending=self.pending_sync;self.pending_sync={}
                if pending:
                    if '*' in pending:
                        self.start_sync(full=any(r['full'] for r in pending.values()),
                            _triggers=set().union(*(r['triggers'] for r in pending.values())),_queued=True)
                    else:
                        for target,request in sorted(pending.items()):
                            self.start_sync(target,full=request['full'],_triggers=request['triggers'],_queued=True)
        threading.Thread(target=work, daemon=True, name='portfolio-market-update').start()
        return {'job_id': job['id'], 'status': 'running'}
