"""System reminder registry. Business providers own their facts and completion rules."""
import hashlib
from copy import deepcopy
from .common import now, today, json_dumps, wire
from .family_domain import read_state, schedule_projection
from .service import Service


def dividend_reminders(store, state):
    rows = []
    service = Service(store)
    for p in state['portfolios']:
        if p.get('archived'): continue
        for c in service.dividend_candidates(p['id'], state=state):
            if c['status'] not in ('pending', 'needs_review'): continue
            facts = {k:c[k] for k in ('notice','status','eligible_quantity','estimated_gross','previous_basis','recorded_event')}
            facts['notice'] = {k:v for k,v in facts['notice'].items() if k != 'fetched_at'}
            fingerprint = hashlib.sha256((p['id']+c['notice']['id']+json_dumps(facts)).encode()).hexdigest()
            rows.append(dict(id='dividend:'+p['id']+':'+c['notice']['id'],source='dividend',kind='dividend',
                             title=c['instrument']['name']+' · 分红待核对',detail=p['name'],date=c['notice']['ex_date'],
                             amount=None,currency=c['notice']['currency'],days=None,severity='attention',
                             portfolio_id=p['id'],event_id=c['event_id'],fingerprint=fingerprint,
                             action=dict(type='dividend',portfolio_id=p['id'],label='核对分红')))
    return rows


def family_provider(store, state, family):
    names = dict(debt='还款',premium='缴保费',renewal='保险到期 / 续保')
    return [dict(id='family:'+r['id'],source='family',kind=r['kind'],title=r['name']+' · '+names[r['kind']],
                 detail=('第 '+str(r['period'])+' / '+str(r['periods'])+' 期') if r.get('period') else '到期提醒',
                 date=r['due_date'],days=r['days'],amount=None if r['kind']=='renewal' else r['household_amount'],
                 currency=r['currency'],estimated=r.get('estimated',False),severity='today' if r['days']==0 else 'upcoming',action=None)
            for r in schedule_projection(family, str(today()))]


def portfolio_provider(store, state, family):
    dividends = dividend_reminders(store, state)
    existing = {r['event_id'] for r in dividends if r.get('event_id')}
    rows = list(dividends); service = Service(store)
    for p in state['portfolios']:
        if p.get('archived'): continue
        for t in service.todo_items(state,p['id']):
            if t.get('event',{}).get('id') in existing: continue
            rows.append(dict(id='portfolio:'+p['id']+':'+t['id'],source='portfolio',kind=t['type'],title=t['title'],detail=p['name'],
                             date=(t.get('event') or t.get('prefill') or {}).get('date'),days=None,amount=None,currency=None,severity='attention',
                             action=dict(type='portfolio_todo',portfolio_id=p['id'],todo=t,label=t.get('label','前往核对'))))
    return rows


PROVIDERS = (family_provider, portfolio_provider)


def sync_reminders(jobs):
    seen, rows = set(), []
    for job in jobs:
        for item in job.get('items',[]):
            key = item['source_kind']+':'+(item.get('instrument_id') or item.get('currency') or item['name']) if item.get('source_kind') else item['name']
            if key in seen: continue
            seen.add(key)
            if item['status'] != 'error': continue
            rows.append(dict(id='sync:'+hashlib.sha256(key.encode()).hexdigest(),source='sync',kind='sync_failure',title=item['name']+' · 同步未完成',
                             detail=item.get('message','请重试同步'),date=job.get('started_at','')[:10],days=None,amount=None,currency=None,severity='attention',
                             action=dict(type='sync',instrument_id=item.get('instrument_id'),label='重试同步')))
    if jobs and jobs[0].get('status')=='error' and not rows:
        rows.append(dict(id='sync:job',source='sync',kind='sync_failure',title='数据同步未完成',detail=jobs[0].get('message','请重试同步'),date=jobs[0].get('started_at','')[:10],
                         days=None,amount=None,currency=None,severity='attention',action=dict(type='sync',label='重试同步')))
    return rows


class ReminderService:
    def __init__(self, store, notifications): self.store, self.notifications = store, notifications

    def read(self):
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
            key = ('reminders',state['revision'],str(today()))
            items = self.store.cache.get(key)
            if items is None:
                items = [r for provider in PROVIDERS for r in provider(self.store,state,family)]
                self.store.cache[key] = deepcopy(items)
            else: items = deepcopy(items)
        items += sync_reminders(self.store.jobs())
        notification = self.notifications.status()
        if notification.get('enabled') and notification.get('error'):
            items.append(dict(id='system:notifications',source='system',kind='notification_failure',title='系统通知需要检查',detail=notification['error'],
                              date=None,days=None,amount=None,currency=None,severity='attention',action=dict(type='settings',label='前往设置')))
        items = sorted({r['id']:r for r in items}.values(),key=lambda r:(r['severity']=='upcoming',r.get('date') or '',r['id']))
        return wire(dict(items=items,count=len(items),today=sum(r.get('days')==0 for r in items),revision=state['revision'],as_of=str(today()),notification=notification))
