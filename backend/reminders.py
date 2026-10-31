"""System reminder registry. Business providers own their facts and completion rules."""
import hashlib
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
            # Micro reference-rate refreshes update the inbox, not task identity.
            # cache_dividends retains a fixed source anchor until material change.
            # A completed review advances review_revision, so a later substantive
            # correction gets a new dispatch even if its entitlement is unchanged.
            facts = {k:c[k] for k in ('status','event_id','review_revision')}
            facts['per_share'] = (c['notice'].get('notification_basis') or c['notice'])['per_share']
            facts['entitlement'] = {k:c['review_basis'].get(k) for k in
                                    ('eligible_quantity','record_date','notice_record_date','ex_date','pay_date','currency','split_restore_factor')}
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
            share_action=t['type'] in ('confirm_split','verify_split')
            detail=p['name']+(' · 送转/拆股未核对，相关收益暂不计算；确认后调整份额并重算，原买入与总成本保留。' if share_action else '')
            label=t.get('label') or ('核对送转/拆股份额' if share_action else '前往核对')
            if t.get('event',{}).get('id') in existing: continue
            rows.append(dict(id='portfolio:'+p['id']+':'+t['id'],source='portfolio',kind=t['type'],title=t['title'],detail=detail,
                             date=(t.get('event') or t.get('prefill') or {}).get('date'),days=None,amount=None,currency=None,severity='attention',
                             action=dict(type='portfolio_todo',portfolio_id=p['id'],todo=t,label=label)))
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
            name=item['name']
            if item.get('source_kind')=='corporate_actions' and name.endswith(' 送转股'):
                name=name.removesuffix(' 送转股')+' 企业行动数据'
            rows.append(dict(id='sync:'+hashlib.sha256(key.encode()).hexdigest(),source='sync',kind='sync_failure',title=name+' · 同步未完成',
                             detail=item.get('message','请重试同步'),date=job.get('started_at','')[:10],days=None,amount=None,currency=None,severity='attention',
                             action=dict(type='sync',instrument_id=item.get('instrument_id'),label='重试同步')))
    if jobs and jobs[0].get('status')=='error' and not rows:
        rows.append(dict(id='sync:job',source='sync',kind='sync_failure',title='数据同步未完成',detail=jobs[0].get('message','请重试同步'),date=jobs[0].get('started_at','')[:10],
                         days=None,amount=None,currency=None,severity='attention',action=dict(type='sync',label='重试同步')))
    return rows


class ReminderService:
    def __init__(self, store, notifications): self.store, self.notifications = store, notifications

    def read(self):
        items, revision = self.store.cached_read(('reminder-items',), self._items, clock=today)
        items += sync_reminders(self.store.jobs())
        notification = self.notifications.status()
        if notification.get('enabled') and notification.get('error'):
            items.append(dict(id='system:notifications',source='system',kind='notification_failure',title='系统通知需要检查',detail=notification['error'],
                              date=None,days=None,amount=None,currency=None,severity='attention',action=dict(type='settings',label='前往设置')))
        items = sorted({r['id']:r for r in items}.values(),key=lambda r:(r['severity']=='upcoming',r.get('date') or '',r['id']))
        return wire(dict(items=items,count=len(items),today=sum(r.get('days')==0 for r in items),revision=revision,as_of=str(today()),notification=notification))

    def _items(self):
        with self.store.connection() as db:
            state, family = self.store.state(db), read_state(db)
        return [r for provider in PROVIDERS for r in provider(self.store,state,family)], state['revision']
