"""Single-process durable planner; process ownership is enforced by run.py's ledger lock."""
import hashlib
import json
import secrets
import threading
from datetime import datetime,timedelta
from ..common import ConflictError,DomainError,day,json_dumps,now
from . import providers,history
from .snapshots import encode,decode

SCOPES=('quotes','fx','index_prices','memberships','valuations','financials')
MODULES={'index_prices':('price','total_return'),'memberships':('memberships','historical_memberships'),
         'valuations':('valuations','stock_valuations'),'financials':('financials','historical_financials')}


def key(resource):return hashlib.sha256(json_dumps(resource).encode()).hexdigest()


def load_job(raw):
    value=json.loads(raw)
    if 'archive' in value:return decode(json_dumps(value['archive']),value['archive_sha256'])
    return value


def store_job(job,state):
    if state in ('complete','partial','error'):
        sha,content=encode(job)
        return json_dumps(dict(archive=json.loads(content),archive_sha256=sha))
    return json_dumps(job)


class UpdateJobs:
    def __init__(self,research):
        self.research=research;self.store=research.store;self.legacy=research.legacy
        self.worker_lock=threading.Lock();self.worker=None;self.stopping=threading.Event();self.owner=secrets.token_hex(16);self.current_trigger='manual';self.current_slot=None

    def active(self):
        with self.store.connection() as db:return bool(db.execute("SELECT 1 FROM data_update_tasks WHERE state IN ('pending','running') LIMIT 1").fetchone())

    def jobs(self):
        with self.store.connection() as db:return [dict(load_job(r[1]),state=r[0]) for r in db.execute('SELECT state,data FROM data_update_tasks ORDER BY rowid DESC LIMIT 20')]

    def get(self,jid):
        with self.store.connection() as db:r=db.execute('SELECT state,data FROM data_update_tasks WHERE id=?',(jid,)).fetchone()
        if not r:raise DomainError('更新任务不存在')
        return dict(load_job(r[1]),state=r[0])

    def plan(self,body):
        scopes=body.get('scopes',list(SCOPES));targets=body.get('targets');resources=body.get('resources')
        if not isinstance(scopes,list) or not scopes or any(s not in SCOPES for s in scopes):raise DomainError('更新范围无效')
        if targets is not None and (not isinstance(targets,list) or not targets or any(not isinstance(t,str) for t in targets)):raise DomainError('更新对象无效')
        if targets:
            for iid in targets:self.research.profile(iid)
        targets=targets or [p['id'] for p in self.research.profiles()]
        if resources is None:
            resources=[]
            for scope in SCOPES:
                if scope not in scopes:continue
                if scope=='fx':resources += [dict(scope=scope,currency=c) for c in ('HKD','USD')]
                elif scope=='quotes':resources.append(dict(scope=scope,instrument_id=None))
                else:resources += [dict(scope=scope,target=iid,module=m) for iid in targets for m in MODULES[scope]]
        if not isinstance(resources,list) or not resources or len(resources)>200:raise DomainError('没有可更新的对象，或任务超过200项')
        for r in resources:
            if not isinstance(r,dict) or r.get('scope') not in scopes:raise DomainError('资源与更新范围不一致')
            scope=r['scope']
            if 'full' in r and (scope not in ('quotes','fx') or type(r['full']) is not bool):raise DomainError('全量补齐参数无效')
            if scope=='fx':
                if set(r)-{'full'}!={'scope','currency'} or r['currency'] not in ('HKD','USD'):raise DomainError('汇率资源无效')
            elif scope=='quotes':
                if set(r)-{'full'}!={'scope','instrument_id'}:raise DomainError('行情资源无效')
                if r['instrument_id'] is not None and not any(i['id']==r['instrument_id'] for i in self.store.state()['instruments']):raise DomainError('行情标的不存在')
            else:
                if set(r)!={'scope','target','module'} or r['module'] not in MODULES[scope]:raise DomainError('指数模块资源无效')
                self.research.profile(r['target'])
        return sorted({key(r):r for r in resources}.values(),key=lambda r:(SCOPES.index(r['scope']),r.get('target',''),MODULES.get(r['scope'],('',)).index(r.get('module','')) if r['scope'] in MODULES else r.get('currency','')))

    def run(self,body,trigger='manual'):
        resources=self.plan(body);request_id=body.get('request_id')
        if not isinstance(request_id,str) or not request_id or len(request_id)>160:raise DomainError('更新必须携带1至160字符的request_id')
        receipt=hashlib.sha256(request_id.encode()).hexdigest()
        request_digest=key(dict(scopes=sorted(set(body.get('scopes',SCOPES))),targets=sorted(body['targets']) if body.get('targets') else None,resources=sorted(body['resources'],key=key) if body.get('resources') else None))
        generation=providers.cutoff();digest=key(dict(resources=resources,generation=generation))
        with self.worker_lock,self.research.transaction() as db:
            old=db.execute('SELECT digest,job_id FROM data_update_receipts WHERE id=?',(receipt,)).fetchone()
            if old:
                if old[0]!=request_digest:raise ConflictError('request_id已用于另一更新范围')
                jid=old[1]
            else:
                pending=db.execute("SELECT id,data FROM data_update_tasks WHERE state IN ('pending','running')").fetchall()
                match=next((r for r in pending if json.loads(r[1])['digest']==digest),None)
                jid=match[0] if match else secrets.token_hex(16)
                if not match:
                    job=dict(id=jid,digest=digest,generation=generation,resources=resources,scopes=list(dict.fromkeys(r['scope'] for r in resources)),trigger=trigger,slot_at=body.get('slot_at'),started_at=now(),items=[],completed_resources=[])
                    db.execute('INSERT INTO data_update_tasks VALUES (?,?,?)',(jid,'pending',json_dumps(job)))
                db.execute('INSERT INTO data_update_receipts VALUES (?,?,?,?)',(receipt,request_digest,jid,json_dumps(dict(job_id=jid,status='accepted'))))
        self.start()
        return dict(job_id=jid,status='accepted')

    def start(self):
        with self.worker_lock:
            if self.stopping.is_set() or self.worker and self.worker.is_alive():return
            self.worker=threading.Thread(target=self._work,name='public-data-updates',daemon=True);self.worker.start()

    def save(self,job,state):
        job['lease_owner']=self.owner if state=='running' else None
        job['lease_expires']=(datetime.fromisoformat(now())+timedelta(seconds=90)).isoformat() if state=='running' else None
        with self.research.transaction() as db:
            db.execute('UPDATE data_update_tasks SET state=?,data=? WHERE id=?',(state,store_job(job,state),job['id']))
            # The API limits presentation to20; immutable compressed outcomes remain addressable by ID.


    def _legacy(self,r):
        while self.legacy.syncing:
            if self.stopping.wait(.1):raise DomainError('更新已中断')
        args=dict(scopes=[r['scope']],full=r.get('full',False))
        if r['scope']=='fx':args['fx_currencies']=[r['currency']]
        from ..sync_meta import TRIGGERS
        result=self.legacy.start_sync(r.get('instrument_id'),trigger=self.current_trigger if self.current_trigger in TRIGGERS else 'manual_update',**args)
        while self.legacy.syncing:
            if self.stopping.wait(.1):raise DomainError('更新已中断')
        j=next((j for j in self.store.jobs() if j['id']==result.get('job_id')),None)
        if not j:raise DomainError('行情任务已合并或结果未取得；请核对旧行情更新记录后重试')
        items=[]
        for item in j['items']:
            if r['scope']=='fx' and item.get('source_kind')!='fx':continue
            if r['scope']=='quotes' and item.get('source_kind')=='fx':continue
            resource=dict(r,currency=item['currency']) if r['scope']=='fx' else dict(r,instrument_id=item.get('instrument_id'))
            items.append(dict(scope=r['scope'],resource=resource,status='complete' if item['status'] in ('ok','manual') else 'error',date=item.get('date'),name=item.get('name'),error=item.get('message') if item['status']=='error' else None,legacy_job_id=j['id']))
        return items or [dict(scope=r['scope'],resource=r,status='complete',name='没有待更新行情标的')]

    def execute(self,r):
        if r['scope'] in ('quotes','fx'):return self._legacy(r)
        iid,module=r['target'],r['module']
        p=self.research.profile(iid);asof=providers.cutoff()
        if module in ('price','total_return'):value=providers.prices(p,module,asof)
        elif module=='memberships':value=providers.constituents(p,asof)
        elif module=='historical_memberships':
            value=history.memberships(p,asof)
            old=self.research.module(iid,module)
            if value.get('status')=='unavailable' and old and any(b['members'] for b in old.get('baskets',[])):raise DomainError('官方历史名单暂不可用，保留已核验版本')
        elif module=='historical_financials':
            baskets=self.research.module(iid,'historical_memberships')
            if not baskets:raise DomainError('缺少核验的历史成分版本，请先更新成分名单')
            members={m['code']:m for b in baskets['baskets'] if b['status']=='official' for m in b['members']}
            finance=self.research.module(iid,'financials') or {}
            cached={r['code'] for r in finance.get('rows',[])}
            extra=[m for c,m in members.items() if c not in cached]
            fetched=providers.financials(extra,asof) if extra else dict(rows=[],evidence=[])
            rows=finance.get('rows',[])+fetched['rows']
            value=dict(years=history.totals(rows,baskets['baskets']),rows=rows,evidence=fetched.get('evidence',[]),membership_snapshot_id=baskets['snapshot_id'],source_snapshot_ids=[finance['snapshot_id']] if finance.get('snapshot_id') else [],effective_date=asof,version_basis='current_restated')
        elif module=='valuations':value=providers.valuations(p,asof)
        else:
            members=self.research.module(iid,'memberships')
            if not members:raise DomainError('缺少有效成分，请先更新成分名单')
            value=(providers.stock_valuations if module=='stock_valuations' else providers.financials)(members['rows'],asof)
        sid=self.research.publish(iid,module,value)
        return [dict(scope=r['scope'],target=iid,module=module,resource=r,status='complete',snapshot_id=sid,date=value.get('effective_date'))]

    def _work(self):
        try:
            while not self.stopping.is_set() and self.research.writes_allowed():
                with self.worker_lock,self.store.connection() as db:
                    r=db.execute("SELECT data FROM data_update_tasks WHERE state='pending' ORDER BY rowid LIMIT 1").fetchone()
                    if not r:
                        self.worker=None
                        return
                job=json.loads(r[0]);self.current_trigger=job['trigger'];self.current_slot=job.get('slot_at');self.save(job,'running')
                for resource in job['resources']:
                    if key(resource) in job['completed_resources']:continue
                    if self.stopping.is_set() or not self.research.writes_allowed():break
                    try:items=self.execute(resource)
                    except Exception as exc:
                        if self.stopping.is_set():break
                        items=[dict(scope=resource['scope'],resource=resource,target=resource.get('target'),module=resource.get('module'),status='error',error=str(exc)[:300])]
                    job['items'].extend(items);job['completed_resources'].append(key(resource));self.save(job,'running')
                interrupted=self.stopping.is_set() or len(job['completed_resources'])!=len(job['resources'])
                job['finished_at']=now();job['resume_on_start']=interrupted;self.save(job,'interrupted' if interrupted else 'partial' if any(i['status']=='error' for i in job['items']) else 'complete')
        except DomainError:
            # Stop guard changed in flight. The durable lease is recovered on next writable startup.
            return

    def after_write(self,instrument_id=None,trigger='event_create'):
        # Fill newly required history through the same durable planner, respecting global opt-outs.
        allowed={p['scope'] for p in self.research.settings()['policies'] if p['enabled'] and p['cadence']!='manual'}
        resources=[]
        if 'quotes' in allowed:resources.append(dict(scope='quotes',instrument_id=instrument_id,full=True))
        if 'fx' in allowed:resources.extend(dict(scope='fx',currency=c,full=True) for c in ('HKD','USD'))
        if not resources:return dict(skipped=True,reason='automatic_updates_disabled')
        return self.run(dict(scopes=list(dict.fromkeys(r['scope'] for r in resources)),resources=resources,request_id='write:'+secrets.token_hex(16)),trigger=trigger)

    def tick(self,stamp=None,trigger='schedule'):
        stamp=stamp or now();d=day(stamp[:10]);clock=stamp[11:16]
        from ..family_domain import TABLES
        with self.store.connection() as db:populated=any(db.execute('SELECT 1 FROM '+t+' LIMIT 1').fetchone() for t in ('portfolios','instruments',*TABLES))
        for p in self.research.settings()['policies']:
            if not p['enabled'] or p['cadence']=='manual':continue
            slot=d if clock>=p['time'] else d-timedelta(days=1)
            if p['cadence']=='trade_day':
                while slot.weekday()>4:slot-=timedelta(days=1)
            elif p['cadence']=='weekly':slot-=timedelta(days=slot.weekday())
            if p['scope'] in ('quotes','fx') and not populated:continue
            if p['scope'] not in ('quotes','fx') and not self.research.profiles():continue
            request_id='schedule:'+slot.isoformat()+':'+p['scope']+':'+str(self.research.revision('settings_revision'))
            with self.store.connection() as db:
                if db.execute('SELECT 1 FROM data_update_receipts WHERE id=?',(hashlib.sha256(request_id.encode()).hexdigest(),)).fetchone():continue
            try:self.run(dict(scopes=[p['scope']],request_id=request_id,slot_at=slot.isoformat()+'T'+p['time']+':00+08:00'),trigger=trigger)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning('自动更新检查失败，下一轮重试 scope=%s error=%s',p['scope'],type(exc).__name__)

    def recover(self,resume=True):
        query="SELECT id,data FROM data_update_tasks WHERE state IN ('pending','running') OR (state='interrupted' AND json_extract(data,'$.resume_on_start')=1)"
        with self.store.connection() as db:rows=db.execute(query).fetchall()
        if not rows:return
        with self.research.transaction() as db:
            for r in rows:
                job=json.loads(r[1]);job['lease_owner']=None;job['lease_expires']=None;job['resume_on_start']=True
                db.execute('UPDATE data_update_tasks SET state=?,data=? WHERE id=?',('pending' if resume else 'interrupted',json_dumps(job),r[0]))
        if resume:self.start()

    def shutdown(self):
        self.stopping.set()
        if self.worker:self.worker.join(timeout=25)
