"""Independent research revisions and durable single-worker update planning."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from ..common import ConflictError, DomainError, day, dec, ds, json_dumps, now, today, wire
from . import providers,price_chunks
from .snapshots import encode,decode
from .validation import validate as validate_snapshot,public_url
from .updates import UpdateJobs,SCOPES,MODULES,key as resource_key
from .metrics import WINDOWS, METHOD_VERSION, clean_prices, drawdown, valuation, financial_totals,price_changes,moving_averages

TABLES = {'research_meta','research_price_chunks','research_indices','research_snapshots','research_heads','data_update_policies','data_update_tasks','data_update_receipts'}


class ResearchService:
    def __init__(self, store, legacy_service, writes_allowed=lambda: True):
        self.store, self.legacy = store, legacy_service
        self.writes_allowed = writes_allowed
        self.secret = secrets.token_bytes(32)
        self.updates = UpdateJobs(self)
        self.worker_lock = self.updates.worker_lock
        self.cache = {}

    @contextmanager
    def transaction(self, key='revision', expected=None, bump=False):
        if not self.writes_allowed(): raise DomainError('系统处于只读验收或停写状态')
        with self.store.lock:
            self.store.backup('auto-'+today().isoformat(),once=True)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT value FROM research_meta WHERE key=?', (key,)).fetchone()
                current = int(row[0]) if row else 0
                if expected is not None and (type(expected) is not int or current != expected): raise ConflictError('资料或设置已变化，请重新读取并核对；本次未写入')
                yield db
                if bump: db.execute('INSERT INTO research_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,str(current+1)))
                db.commit()
            except Exception:
                db.rollback(); raise
        if bump: self.cache.clear()

    def revision(self, key='revision'):
        with self.store.connection() as db:
            row=db.execute('SELECT value FROM research_meta WHERE key=?',(key,)).fetchone()
            return int(row[0]) if row else 0

    def resolve(self, code):
        p = providers.identity(code); expires = int(time.time())+600
        digest = hashlib.sha256(json_dumps(p).encode()).hexdigest()
        message = f'{p["code"]}:{p["input_basis"]}:{expires}:{digest}'
        token = message+':'+hmac.new(self.secret,message.encode(),'sha256').hexdigest()
        return dict(profile=p, resolve_token=token, expires_at=expires, research_revision=self.revision())

    def add(self, body):
        request_id=body.get('request_id')
        if not isinstance(request_id,str) or not request_id or len(request_id)>160:raise DomainError('建档须携带request_id')
        receipt=hashlib.sha256(('add:'+request_id).encode()).hexdigest()
        digest=hashlib.sha256(json_dumps({k:v for k,v in body.items() if k!='request_id'}).encode()).hexdigest()
        with self.worker_lock,self.transaction() as db:
            old=db.execute('SELECT digest,result FROM data_update_receipts WHERE id=?',(receipt,)).fetchone()
            if old:
                if old[0]!=digest:raise ConflictError('request_id已用于其他建档请求')
                result=json.loads(old[1])
            else:
                token=body.get('resolve_token','')
                try:
                    code,basis,expires,identity_digest,signature=token.split(':');message=':'.join((code,basis,expires,identity_digest))
                    if int(expires)<time.time() or not hmac.compare_digest(signature,hmac.new(self.secret,message.encode(),'sha256').hexdigest()):raise ValueError()
                    p=providers.identity(code);p['input_basis']=basis
                    if hashlib.sha256(json_dumps(p).encode()).hexdigest()!=identity_digest:raise ValueError()
                except (ValueError,TypeError):raise DomainError('身份预览已失效，请重新识别') from None
                row=db.execute("SELECT value FROM research_meta WHERE key='revision'").fetchone();revision=int(row[0]) if row else 0
                if type(body.get('expected_research_revision')) is not int or body['expected_research_revision']!=revision:raise ConflictError('资料已变化，请重新识别并核对')
                iid=p['provider']+':'+p['code']
                inserted=db.execute('INSERT OR IGNORE INTO research_indices VALUES (?,?)',(iid,json_dumps(dict(p,id=iid,created_at=now())))).rowcount
                if inserted:
                    revision+=1
                    db.execute("INSERT INTO research_meta VALUES ('revision',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(revision),))
                resources=[dict(scope=scope,target=iid,module=m) for scope in SCOPES if scope in MODULES for m in MODULES[scope]]
                generation=providers.cutoff();task_digest=resource_key(dict(resources=resources,generation=generation))
                match=next((r for r in db.execute("SELECT id,data FROM data_update_tasks WHERE state IN ('pending','running')") if json.loads(r[1])['digest']==task_digest),None)
                jid=match[0] if match else secrets.token_hex(16)
                if not match:
                    job=dict(id=jid,digest=task_digest,generation=generation,resources=resources,scopes=list(MODULES),trigger='index_create',started_at=now(),items=[],completed_resources=[])
                    db.execute('INSERT INTO data_update_tasks VALUES (?,?,?)',(jid,'pending',json_dumps(job)))
                result=dict(id=iid,basis=basis,research_revision=revision,job_id=jid,existing=not inserted)
                db.execute('INSERT INTO data_update_receipts VALUES (?,?,?,?)',(receipt,digest,jid,json_dumps(result)))
        self.cache.clear();self.updates.start()
        return result

    def profiles(self):
        with self.store.connection() as db: return [json.loads(r[0]) for r in db.execute('SELECT data FROM research_indices ORDER BY id')]

    def profile(self, iid):
        p=next((p for p in self.profiles() if p['id']==iid),None)
        if not p: raise DomainError('指数档案不存在')
        return p

    def module(self, iid, module):
        with self.store.connection() as db:
            row=db.execute('SELECT s.id,s.created_at,s.sha256,s.data FROM research_heads h JOIN research_snapshots s ON s.id=h.snapshot_id WHERE h.index_id=? AND h.module=?',(iid,module)).fetchone()
            value=decode(row[3],row[2]) if row else None
            if value is not None and module in ('price','total_return'):value=price_chunks.expand(db,value)
        return dict(snapshot_id=row[0],fetched_at=row[1],sha256=row[2],**value) if row else None

    def publish(self, iid, module, value):
        # Provenance stays inside the same SQLite snapshot/backup. No external file references.
        validate_snapshot(module,value)
        digest,content,blocks=price_chunks.prepare(wire(value)) if module in ('price','total_return') else (*encode(wire(value)),{})
        current=self.module(iid,module)
        # Fetched timestamps are provenance, not financial values; unchanged source versions remain idempotent.
        def meaningful(v):
            if isinstance(v,dict): return {k:meaningful(x) for k,x in v.items() if k not in ('fetched_at','snapshot_id','sha256','evidence')}
            if isinstance(v,list): return [meaningful(x) for x in v]
            return v
        if current and meaningful(current)==meaningful(value): return current['snapshot_id']
        sid=secrets.token_hex(16)
        with self.transaction(bump=True) as db:
            if db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]>400*1024*1024: raise DomainError('数据库接近备份容量限制，请先导出并检查存储；旧快照保留')
            # Supplied data must never overwrite a newer effective date.
            if current and value.get('effective_date') and current.get('effective_date') and value['effective_date']<current['effective_date']: raise DomainError('来源返回旧日期，保留当前有效快照')
            for sha,block in blocks.items():db.execute('INSERT OR IGNORE INTO research_price_chunks VALUES (?,?)',(sha,block))
            db.execute('INSERT INTO research_snapshots VALUES (?,?,?,?,?,?)',(sid,iid,module,now(),digest,content))
            db.execute('INSERT INTO research_heads VALUES (?,?,?) ON CONFLICT(index_id,module) DO UPDATE SET snapshot_id=excluded.snapshot_id',(iid,module,sid))
        return sid

    def detail(self, iid, basis='price', method='overall', window=3):
        if basis not in ('price','total_return') or method not in ('overall','median','arithmetic') or window not in WINDOWS: raise DomainError('指数口径、估值方法或窗口无效')
        with self.store.lock:
            p=self.profile(iid); revision=self.revision();calculation_date=providers.cutoff();key=(iid,basis,method,window,revision,calculation_date)
            if key in self.cache: return self.cache[key]
            resources={name:self.module(iid,name) for name in ('price','total_return','valuations','memberships','stock_valuations','financials','historical_memberships','historical_financials')}
        price=resources[basis];v=resources['valuations'];members=resources['memberships'];stocks=resources['stock_valuations'];finance=resources['financials']
        asof=price['effective_date'] if price else providers.cutoff()
        prices=clean_prices(price['rows'],asof) if price else []
        calendar={r['date'] for r in (resources['price'] or {}).get('rows',[])}
        valuations={}
        for metric in ('pe','pb'):
            series=(v or {}).get('series',{}).get(metric,{}).get(method)
            if series:
                series=dict(series,rows=[r for r in series['rows'] if r['date'] in calendar])
                valuations[metric]=valuation(series,asof)
        dd={str(w):drawdown(prices,asof,w) for w in WINDOWS}
        sample=dd[str(window)]
        curve=sample.get('curve',[])
        # Keep complete data for math; only the display curve is downsampled.
        step=max(1,len(curve)//900)
        chart=curve[::step]
        if curve and (not chart or chart[-1]!=curve[-1]):chart.append(curve[-1])
        for entry in dd.values():entry.pop('curve',None)
        company_vals={r['code']:r for r in (stocks or {}).get('rows',[])}
        companies=[dict(r,industry=r.get('industry') or (company_vals.get(r['code']) or {}).get('industry') or '',valuation=company_vals.get(r['code'])) for r in (members or {}).get('rows',[])]
        periods=(finance or {}).get('periods',[]);totals={period:financial_totals((finance or {}).get('rows',[]),companies,period) for period in periods} if finance else {}
        qualified=[period for period,val in totals.items() if all(val[m]['current']['coverage'] is not None and dec(val[m]['current']['coverage'])>=dec('.8') for m in ('revenue','profit'))]
        main=max(qualified,default=max(periods,default=None)); summary=totals.get(main)
        company_rows={}
        for row in (finance or {}).get('rows',[]):company_rows.setdefault(row['code'],{})[row['period']]=row
        companies=sorted([dict(r,financials=company_rows.get(r['code'],{})) for r in companies],key=lambda r:(-dec(r['weight']),r['code']))
        for rank,r in enumerate(companies,1):r['weight_rank']=rank
        annual=[]
        for year in range(int(asof[:4])-10,int(asof[:4])):
            period=f'{year}-12-31';metrics=totals.get(period,{})
            annual.append(dict(period=period,membership_status='current_basket',metrics={m:dict(amount=v['current']['amount'],growth=v['growth'],count=v['current']['count'],denominator=v['current']['denominator'],count_coverage=v['current']['count_coverage'],weight_coverage=v['current']['coverage'],previous_amount=v['previous']['amount'],previous_coverage=v['previous']['coverage'],change=v['change'],status=v['status']) for m,v in metrics.items()}))
        sources=[dict(module=name,snapshot_id=m['snapshot_id'],fetched_at=m['fetched_at'],sha256=m['sha256'],effective_date=m.get('effective_date'),evidence=m.get('evidence',[]),limitation=m.get('limitation') or m.get('methodology_note') or m.get('theoretical_history_note')) for name,m in resources.items() if m]
        result=wire(dict(id=iid,profile=p,basis=basis,series_code=p['return_code'] if basis=='total_return' else p['code'],asof=asof,latest=prices[-1] if prices else None,
            research_revision=revision,method=method,window=window,method_version=METHOD_VERSION,analysis_id=hashlib.sha256(json_dumps([m['snapshot_id'] if m else None for m in resources.values()]+[basis,method,METHOD_VERSION]).encode()).hexdigest(),
            valuation=valuations,drawdown=dd,curve=chart,daily_series=moving_averages(prices,window),changes=price_changes(prices,calculation_date),top10_weight=ds(sum((dec(r['weight']) for r in companies[:10]),dec('0'))),companies=companies,membership_date=(members or {}).get('effective_date'),stock_valuation_limitation=(stocks or {}).get('limitation'),
            financials=dict(main_period=main,summary=summary,periods=totals,current_years=annual,history_status=(finance or {}).get('history_status','unavailable'),history_reason=(finance or {}).get('history_reason','尚无已核验历史成分财务'),historical_years=(resources['historical_financials'] or {}).get('years',[])),
            sources=sources,status='available' if prices else 'missing',mapping_available=bool(prices and basis=='price' and method=='overall' and all(valuations.get(m,{}).get('date')==asof for m in ('pe','pb')))))
        if len(self.cache)>8:self.cache.clear()
        self.cache[key]=result
        return result

    def scenario(self,iid,body):
        basis=body.get('basis','price');metric=body.get('metric','pe')
        if metric not in ('pe','pb'):raise DomainError('试算指标无效')
        d=self.detail(iid,basis)
        if body.get('analysis_id')!=d['analysis_id']:raise ConflictError('分析版本已变化，请重新读取并核对假设')
        if not d['mapping_available']:raise DomainError('仅同日价格序列与整体法估值可试算；全收益缺少未来分红再投资假设')
        if body.get('accept_fixed_basket_assumption') is not True:raise DomainError('请明确接受固定成分、权重及除数的条件假设')
        change=dec(body.get('change_ratio'));target=dec(body.get('target_multiple'),positive=True)
        if change<=-1:raise DomainError('分母变化须大于-100%')
        current=dec(d['valuation'][metric]['current'],positive=True);point=dec(d['latest']['close'],positive=True)
        result=point*(1+change)*target/current
        reference_window=str(body.get('reference_window',5))
        if reference_window not in ('3','5','10'):raise DomainError('参考窗口无效')
        references=d['valuation'][metric].get('windows',{}).get(reference_window,{}).get('quantiles',{})
        reference_points={q:dict(multiple=value,point=ds(point*dec(value)/current)) for q,value in references.items()}
        low=min(dec('-.5'),change);high=max(dec('.5'),change)
        curve=[dict(change_pct=ds((low+(high-low)*i/20)*100),point=ds(point*(1+low+(high-low)*i/20)*target/current)) for i in range(21)]
        return dict(curve=curve,reference_window=int(reference_window),reference_points=reference_points,point=ds(result),current_point=ds(point),metric=metric,target_multiple=ds(target),change_ratio=ds(change),analysis_id=d['analysis_id'],basis=basis,
            formula='P0*(1+change_ratio)*target_multiple/current_multiple',assumption='固定成分、权重及除数；忽略调样与融资变化；仅条件代数结果')

    def projection(self,iid,part,basis='price',window=3,analysis_id=None,page=1,page_size=20,method='overall'):
        d=self.detail(iid,basis,method,window)
        if analysis_id is not None and analysis_id!=d['analysis_id']:raise ConflictError('分析快照已变化，请重新读取，勿合并不同版本分页')
        if part=='series':value=dict(rows=d['curve'],drawdown=d['drawdown'][str(window)],window=window)
        elif part=='sources':value=dict(items=d['sources'])
        elif part=='constituents':value=dict(items=d['companies'][(page-1)*page_size:page*page_size],total=len(d['companies']),page=page,page_size=page_size)
        else:
            source=next((s for s in d['sources'] if s['module']=='financials'),None)
            with self.store.connection() as db:
                frozen=db.execute('SELECT data,sha256 FROM research_snapshots WHERE id=?',(source['snapshot_id'],)).fetchone() if source else None
            rows=decode(frozen[0],frozen[1]).get('rows',[]) if frozen else []
            value=dict(summary=d['financials'],items=rows[(page-1)*page_size:page*page_size],total=len(rows),page=page,page_size=page_size)
        return dict(value,analysis_id=d['analysis_id'],research_revision=d['research_revision'],series_code=d['series_code'],basis=basis)

    def listing(self, kind=None, page=1):
        profiles=[p for p in self.profiles() if not kind or p['kind']==kind]
        if page<1:raise DomainError('页码无效')
        rows=[]
        for p in profiles[(page-1)*20:page*20]:
            d=self.detail(p['id']);rows.append({k:d[k] for k in ('id','profile','latest','asof','valuation','drawdown','changes','status')})
        return dict(items=rows,total=len(profiles),page=page,page_size=20,research_revision=self.revision())

    def settings(self):
        with self.store.connection() as db:
            saved={r[0]:json.loads(r[1]) for r in db.execute('SELECT id,data FROM data_update_policies')}
            row=db.execute("SELECT value FROM research_meta WHERE key='settings_revision'").fetchone();revision=int(row[0]) if row else 0
        defaults=[dict(scope=scope,enabled=True,cadence='daily',time='23:30',timezone='Asia/Shanghai') for scope in SCOPES]
        return dict(policies=[saved.get(p['scope'],p) for p in defaults],settings_revision=revision)

    def save_settings(self, body):
        rows=body.get('policies');revision=body.get('expected_settings_revision')
        if type(revision) is not int or not isinstance(rows,list) or not all(isinstance(r,dict) and isinstance(r.get('scope'),str) for r in rows) or {r.get('scope') for r in rows}!=set(SCOPES) or len(rows)!=len(SCOPES): raise DomainError('须提交完整更新策略与读取时的版本')
        for r in rows:
            if set(r)!={'scope','enabled','cadence','time','timezone'}:raise DomainError('更新策略字段不完整或含未知字段')
            if type(r.get('enabled')) is not bool or r.get('cadence') not in ('daily','trade_day','weekly','manual') or r.get('timezone')!='Asia/Shanghai':raise DomainError('更新频率、时区或开关无效')
            if not isinstance(r.get('time'),str) or len(r['time'])!=5 or r['time'][2]!=':':raise DomainError('更新时刻应为HH:MM')
            try:datetime.strptime(r['time'],'%H:%M')
            except (ValueError,KeyError,TypeError):raise DomainError('更新时刻应为HH:MM') from None
        with self.transaction('settings_revision',revision,bump=True) as db:
            for r in rows:db.execute('INSERT INTO data_update_policies VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data',(r['scope'],json_dumps(r)))
        return self.settings()

    def updating(self):return self.updates.active()
    def jobs(self):return self.updates.jobs()
    def run(self,body,trigger='manual'):return self.updates.run(body,trigger)
    def tick(self,stamp=None,trigger='schedule'):return self.updates.tick(stamp,trigger)
    def recover(self,resume=True):return self.updates.recover(resume)
    def shutdown(self):return self.updates.shutdown()


def validate_structure(db):
    names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES.issubset(names):raise DomainError('研究或调度数据表缺失')
    for key,value in db.execute('SELECT key,value FROM research_meta'):
        if key not in ('revision','settings_revision') or not value.isdigit():raise DomainError('研究版本元数据无效')
    if db.execute('SELECT 1 FROM research_heads h LEFT JOIN research_snapshots s ON s.id=h.snapshot_id WHERE s.id IS NULL OR h.index_id<>s.index_id OR h.module<>s.module LIMIT 1').fetchone():raise DomainError('研究快照引用不完整')


def validate_backup(db):
    validate_structure(db)
    for r in db.execute('SELECT id,data FROM research_indices'):
        p=json.loads(r[1]);known=providers.identity(p['code'])
        if r[0]!=p.get('id') or r[0]!=known['provider']+':'+known['code'] or p['return_code']!=known['return_code']:raise DomainError('指数家族身份或配对无效')
        public_url(p['method_url'])
    summaries=price_chunks.validate_blocks(db)
    for r in db.execute('SELECT id,index_id,module,sha256,data FROM research_snapshots'):
        value=decode(r[4],r[3])
        if r[2] in ('price','total_return'):value=price_chunks.validation_view(value,summaries)
        validate_snapshot(r[2],value)
        refs=value.get('source_snapshot_ids',[])+([value['membership_snapshot_id']] if value.get('membership_snapshot_id') else [])
        for sid in refs:
            if not db.execute('SELECT 1 FROM research_snapshots WHERE id=?',(sid,)).fetchone():raise DomainError('研究证据来源版本缺失')
    for r in db.execute('SELECT id,data FROM data_update_policies'):
        value=json.loads(r[1])
        if r[0] not in SCOPES or value.get('scope')!=r[0]:raise DomainError('更新策略范围无效')

    from .updates import load_job
    for r in db.execute('SELECT id,state,data FROM data_update_tasks'):
        job=load_job(r[2])
        if job.get('id')!=r[0] or not isinstance(job.get('resources'),list) or r[1] not in ('pending','running','interrupted','complete','partial','error'):raise DomainError('更新任务结构或归属无效')
