"""Independent research revisions and durable single-worker update planning."""
import hashlib
import json
import secrets
import threading
from collections import OrderedDict
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime,timedelta
from ..common import ConflictError, DomainError, day, dec, ds, json_dumps, now, today, wire
from . import providers,price_chunks
from .snapshots import encode,decode
from .validation import validate as validate_snapshot,public_url
from .updates import UpdateJobs,SCOPES,MODULES
from .metrics import WINDOWS, METHOD_VERSION, clean_prices, drawdown, valuation, financial_totals,price_changes,moving_averages
from .lixinger_access import LocalAccess
from .collection import Collection, subscription

TABLES = {'research_meta','research_price_chunks','research_indices','research_snapshots','research_heads','data_update_policies','data_update_tasks','data_update_receipts'}


class ResearchService:
    def __init__(self, store, legacy_service, writes_allowed=lambda: True):
        self.store, self.legacy = store, legacy_service
        self.writes_allowed = writes_allowed
        self.access = LocalAccess(store.directory)
        self.collection = Collection(self)
        self.updates = UpdateJobs(self)
        self.worker_lock = self.updates.worker_lock
        self.cache = OrderedDict()

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
        return dict(profile=providers.identity(code), research_revision=self.revision())

    def profiles(self):
        with self.store.connection() as db: return [json.loads(r[0]) for r in db.execute('SELECT data FROM research_indices ORDER BY id')]

    def profile(self, iid):
        p=next((p for p in self.profiles() if p['id']==iid),None)
        if not p: raise DomainError('指数档案不存在')
        return p

    def versions(self,iid):
        self.profile(iid)
        with self.store.connection() as db:
            rows=db.execute("SELECT id,module,created_at,sha256 FROM research_snapshots WHERE index_id=? AND module NOT LIKE 'stage.%' ORDER BY rowid DESC LIMIT 100",(iid,)).fetchall()
        return dict(items=[dict(id=r[0],module=r[1],created_at=r[2],sha256=r[3]) for r in rows],limit=100)

    def frozen(self,iid,sid):
        self.profile(iid)
        with self.store.connection() as db:
            row=db.execute("SELECT module,created_at,sha256,data FROM research_snapshots WHERE index_id=? AND id=? AND module NOT LIKE 'stage.%'",(iid,sid)).fetchone()
            if not row:raise DomainError('该指数的来源版本不存在')
            value=decode(row[3],row[2])
            if row[0] in ('price','total_return'):value=price_chunks.expand(db,value)
        return dict(snapshot_id=sid,module=row[0],created_at=row[1],sha256=row[2],data=value)

    def check_source(self,iid,expected):
        """One explicit official-source check; never merges reference values into history."""
        d=self.detail(iid,method='mcw')
        if d['analysis_id']!=expected:raise ConflictError('分析版本已变化，请重新读取后核对')
        if not d['latest']:raise DomainError('请先采集价格日线')
        official=providers.prices(d['profile'],'price',d['asof'],str(day(d['asof'])-timedelta(days=45)))
        local={r['date']:r for r in (self.module(iid,'price') or {}).get('rows',[])}
        vals=((self.module(iid,'valuations') or {}).get('series',{}).get('pe',{}).get('mcw',{}).get('rows',[]))
        pe={r['date']:r['value'] for r in vals};rows=[]
        for r in official['rows']:
            if r['date'] not in local:continue
            difference=abs(dec(r['close'])-dec(local[r['date']]['close']))
            rows.append(dict(date=r['date'],official_close=r['close'],local_close=local[r['date']]['close'],difference=ds(difference),
                             close_matches=difference<=dec('.01'),official_pe=r.get('reference_pe'),lixinger_pe=pe.get(r['date'])))
        if not rows:raise DomainError('官方与本地日线没有同日观测，未生成校验结果')
        value=dict(rows=rows,effective_date=rows[-1]['date'],source=official['source'],evidence=official['evidence'],
                   methodology_note='收盘仅核对同日价格身份，容差0.01点。官方PE与理杏仁mcw口径尚未证实可比，仅并列，不据此开启条件试算；PB参考缺项。')
        with self.store.lock:
            if self.detail(iid,method='mcw')['analysis_id']!=expected:raise ConflictError('校验期间输入版本变化，未发布')
            self.publish(iid,'official_reference',value)
        return value

    def module(self, iid, module, *, raw=False):
        with self.store.connection() as db:
            row=db.execute('SELECT s.id,s.created_at,s.sha256,s.data FROM research_heads h JOIN research_snapshots s ON s.id=h.snapshot_id WHERE h.index_id=? AND h.module=?',(iid,module)).fetchone()
            value=decode(row[3],row[2]) if row else None
            if value is not None and module in ('price','total_return'):value=price_chunks.expand(db,value)
        if not raw and value is not None and module in ('price', 'total_return') and 'missing_dates' in value:
            from ..trading_calendar import no_expected_session
            # Reinterpret derived gap flags after calendar corrections; immutable source versions stay intact.
            value['missing_dates'] = [
                d for d in value.get('missing_dates', [])
                if not no_expected_session('CN', str(day(d) - timedelta(days=1)), d)
            ]
        return dict(snapshot_id=row[0],fetched_at=row[1],sha256=row[2],**value) if row else None

    def publish(self, iid, module, value):
        # Provenance stays inside the same SQLite snapshot/backup. No external file references.
        value={k:v for k,v in value.items() if k not in ('snapshot_id','fetched_at','sha256')}
        validate_snapshot(module,value)
        digest,content,blocks=price_chunks.prepare(wire(value)) if module in ('price','total_return') else (*encode(wire(value)),{})
        current=self.module(iid,module,raw=True)
        # Fetched timestamps are provenance, not financial values; unchanged source versions remain idempotent.
        def meaningful(v):
            if isinstance(v,dict): return {k:meaningful(x) for k,x in v.items() if k not in ('fetched_at','snapshot_id','sha256','evidence')}
            if isinstance(v,list): return [meaningful(x) for x in v]
            return v
        if current and meaningful(current)==meaningful(value): return current['snapshot_id']
        sid=secrets.token_hex(16)
        with self.transaction(bump=True) as db:
            if db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]>400*1024*1024: raise DomainError('数据库接近备份容量限制，请先导出并检查存储；旧快照保留')
            # Older snapshots used the query cutoff as effective_date for these
            # modules. Compare their actual observations, retaining the originals.
            def observed(v):
                if module=='index_financials': dates=[r['date'] for r in v.get('rows',[])]
                elif module=='market_activity': dates=[r['date'] for rows in v.get('series',{}).values() for r in rows]
                elif module=='historical_memberships': dates=[b['period'] for b in v.get('baskets',[]) if b.get('members')]
                else:return v.get('effective_date')
                return max(dates,default=None)
            if current and observed(current) and (not observed(value) or observed(value)<observed(current)): raise DomainError('来源返回旧日期或空观测，保留当前有效快照')
            for sha,block in blocks.items():db.execute('INSERT OR IGNORE INTO research_price_chunks VALUES (?,?)',(sha,block))
            db.execute('INSERT INTO research_snapshots VALUES (?,?,?,?,?,?)',(sid,iid,module,now(),digest,content))
            db.execute('INSERT INTO research_heads VALUES (?,?,?) ON CONFLICT(index_id,module) DO UPDATE SET snapshot_id=excluded.snapshot_id',(iid,module,sid))
        return sid

    def detail(self, iid, basis='price', method='overall', window=3, *, summary=False):
        if basis not in ('price','total_return') or method not in ('overall','median','arithmetic','mcw','ew') or window not in (*WINDOWS,0): raise DomainError('指数口径、估值方法或窗口无效')
        with self.store.lock:
            p=self.profile(iid); revision=self.revision();calculation_date=providers.cutoff()
            recent=[] if summary else self.updates.jobs()
            coverage_key=json_dumps([item for job in recent for item in job.get('items',[]) if item.get('target')==iid])
            key=(iid,basis,method,window,revision,calculation_date,coverage_key,summary)
            if key in self.cache:
                self.cache.move_to_end(key)
                return deepcopy(self.cache[key])
            names=('price','valuations') if summary else ('price','total_return','valuations','memberships','stock_valuations','financials','historical_memberships','historical_financials','index_info','index_financials','market_activity','tracking_funds','official_reference')
            resources={name:self.module(iid,name) for name in names}
        price=resources[basis];v=resources['valuations'];members=resources.get('memberships');stocks=resources.get('stock_valuations');finance=resources.get('financials')
        asof=price['effective_date'] if price else providers.cutoff()
        prices=clean_prices(price['rows'],asof) if price else []
        valuations={}
        for metric in ('pe','pb','ps','dividend_yield'):
            series=(v or {}).get('series',{}).get(metric,{}).get(method)
            if series:
                valuations[metric]=valuation(series,str(today()),allow_zero=metric=='dividend_yield')
        dd={str(w):drawdown(prices,asof,w,allow_partial=(price or {}).get('provider_id')=='lixinger') for w in (*WINDOWS,0)}
        sample=dd[str(window)]
        curve=sample.get('curve',[])
        # Keep complete data for math; only the display curve is downsampled.
        step=max(1,len(curve)//900)
        chart=curve[::step]
        if curve and (not chart or chart[-1]!=curve[-1]):chart.append(curve[-1])
        for entry in dd.values():entry.pop('curve',None)
        if summary:
            result=wire(dict(id=iid,profile=p,latest=prices[-1] if prices else None,asof=asof,valuation=valuations,drawdown=dd,
                             changes=price_changes(prices,calculation_date),status='available' if prices else 'missing'))
            return self._remember(key,result,revision)
        company_vals={r['code']:r for r in (stocks or {}).get('rows',[])}
        companies=[dict(r,industry=r.get('industry') or (company_vals.get(r['code']) or {}).get('industry') or '',valuation=company_vals.get(r['code'])) for r in (members or {}).get('rows',[])]
        periods=(finance or {}).get('periods',[]);totals={period:financial_totals((finance or {}).get('rows',[]),companies,period) for period in periods} if finance else {}
        qualified=[period for period,val in totals.items() if all(val[m]['current']['coverage'] is not None and dec(val[m]['current']['coverage'])>=dec('.8') for m in ('revenue','profit'))]
        main=max(qualified,default=max(periods,default=None)); summary=totals.get(main)
        company_rows={}
        for row in (finance or {}).get('rows',[]):company_rows.setdefault(row['code'],{})[row['period']]=row
        companies=sorted([dict(r,financials=company_rows.get(r['code'],{})) for r in companies],key=lambda r:(r.get('weight') is None,-dec(r.get('weight') or '0'),r['code']))
        for rank,r in enumerate(companies,1):r['weight_rank']=rank
        annual=[]
        for year in range(int(asof[:4])-10,int(asof[:4])):
            period=f'{year}-12-31';metrics=totals.get(period,{})
            annual.append(dict(period=period,membership_status='current_basket',metrics={m:dict(amount=v['current']['amount'],growth=v['growth'],count=v['current']['count'],denominator=v['current']['denominator'],count_coverage=v['current']['count_coverage'],weight_coverage=v['current']['coverage'],previous_amount=v['previous']['amount'],previous_coverage=v['previous']['coverage'],change=v['change'],status=v['status']) for m,v in metrics.items()}))
        sources=[dict(module=name,snapshot_id=m['snapshot_id'],fetched_at=m['fetched_at'],sha256=m['sha256'],effective_date=m.get('effective_date'),evidence=m.get('evidence',[]),limitation=m.get('limitation') or m.get('methodology_note') or m.get('theoretical_history_note')) for name,m in resources.items() if m]
        module_status={}
        for module in (n for n in names if n != 'total_return' or not summary):
            capability=providers.module_capabilities(p).get(module,dict(supported=True,reason=None))
            snapshot=resources.get(module)
            last=next((i for job in recent for i in reversed(job.get('items',[])) if i.get('target')==iid and i.get('module')==module),None)
            module_status[module]=dict(supported=capability['supported'],status='error' if last and last.get('status')=='error' else 'available' if snapshot else 'unsupported' if not capability['supported'] else 'missing',
                                       reason=capability['reason'] or (last.get('error') if last else None), date=snapshot.get('effective_date') if snapshot else None)
        result=wire(dict(module_status=module_status,id=iid,profile=p,basis=basis,series_code=p['return_code'] if basis=='total_return' else p['code'],asof=asof,latest=prices[-1] if prices else None,
            research_revision=revision,method=method,window=window,method_version=METHOD_VERSION,analysis_id=hashlib.sha256(json_dumps([m['snapshot_id'] if m else None for m in resources.values()]+[basis,method,METHOD_VERSION]).encode()).hexdigest(),
            valuation=valuations,valuation_methods=list((v or {}).get('series',{}).get('pe',{})),drawdown=dd,curve=chart,daily_series=moving_averages(prices,window,(price or {}).get('missing_dates',[])),changes=price_changes(prices,calculation_date),top10_weight=ds(sum((dec(r['weight']) for r in companies[:10]),dec('0'))) if companies and all(r.get('weight') is not None for r in companies) else None,companies=companies,membership_date=(members or {}).get('effective_date'),stock_valuation_limitation=(stocks or {}).get('limitation'),
            financials=dict(main_period=main,summary=summary,periods=totals,current_years=annual,history_status=(finance or {}).get('history_status','unavailable'),history_reason=(finance or {}).get('history_reason','尚无已核验历史成分财务'),historical_years=(resources['historical_financials'] or {}).get('years',[])),
            sources=sources,status='available' if prices else 'missing',mapping_available=bool(prices and basis=='price' and (method=='overall' or method=='mcw' and (v or {}).get('mapping_verified')) and all(valuations.get(m,{}).get('date')==asof for m in ('pe','pb'))),
            scenario_metrics={m:bool(prices and basis=='price' and (method=='overall' or method=='mcw' and (v or {}).get('provider_id')=='lixinger') and valuations.get(m,{}).get('date')==asof and dec(valuations.get(m,{}).get('current') or '0')>0) for m in ('pe','pb')},
            scenario_assumption='固定成分、股数、权重和除数；假定所有成分价格同比例变化，所有成分盈利或净资产同比例变化。仅作条件敏感性推算，不代表估值与指数点位的实际映射或预测。',
            collection=self.collection.status(iid),official_reference=resources.get('official_reference'),source_coverage=(price or {}).get('coverage'),memberships_complete=(members or {}).get('weights_complete',True),
            historical_memberships=(resources.get('historical_memberships') or {}).get('baskets',[]),membership_comparison_coverage=(resources.get('historical_memberships') or {}).get('comparison_coverage',[]),index_info=(resources.get('index_info') or {}).get('info'),
            index_financials=(resources.get('index_financials') or {}).get('rows',[]),fundamental_activity=(v or {}).get('activity_rows',[]),
            market_activity=(resources.get('market_activity') or {}).get('series',{}),tracking_funds=(resources.get('tracking_funds') or {}).get('rows',[])))
        return self._remember(key,result,revision)

    def _remember(self,key,result,revision):
        with self.store.lock:
            if self.revision()==revision:
                self.cache[key]=deepcopy(result)
                self.cache.move_to_end(key)
                while len(self.cache)>64:self.cache.popitem(last=False)
        return result

    def scenario(self,iid,body):
        basis=body.get('basis','price');metric=body.get('metric','pe')
        if metric not in ('pe','pb'):raise DomainError('试算指标无效')
        d=self.detail(iid,basis,body.get('method','overall'))
        if body.get('analysis_id')!=d['analysis_id']:raise ConflictError('分析版本已变化，请重新读取并核对假设')
        if not d['scenario_metrics'][metric]:raise DomainError('条件试算须使用价格指数与同日正数整体法/总市值加权估值；全收益、异日或非正估值不能试算')
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
            formula='P0*(1+change_ratio)*target_multiple/current_multiple',assumption=d['scenario_assumption'])

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
            d=self.detail(p['id'],summary=True);rows.append({k:d[k] for k in ('id','profile','latest','asof','valuation','drawdown','changes','status')})
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
