import copy
import io
import json
import os
import stat
import tempfile
import unittest
import zipfile
from datetime import date,timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import ConflictError,DomainError
from backend.research.lixinger_access import LocalAccess
from backend.research.collection import merge
from backend.research import lixinger
from backend.research.analytics import ranks,flow_months,valuation_curve
from backend.research.metrics import moving_averages,full_window,years_before
from backend.research.service import ResearchService,validate_backup
from backend.service import Service
from backend.store import Store
from tests.lixinger_fixtures import transport

SYNTHETIC='synthetic-local-only-credential'

class AccessTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.a=LocalAccess(Path(self.temp.name))
    def tearDown(self):self.temp.cleanup()
    def enable(self,limit=3):
        s=self.a.save_token(SYNTHETIC,0);return self.a.configure(s['revision'],True,limit)
    def test_local_file_permissions_cas_no_echo_and_no_counter_reset(self):
        self.assertFalse(self.a.status()['configured']);s=self.enable()
        path=Path(s['path']);self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode),0o700)
        self.assertNotIn(SYNTHETIC,json.dumps(s))
        with self.assertRaises(ConflictError):self.a.save_token(SYNTHETIC,0)
        self.a.request('cn/index',{'stockCodes':['000905']},transport=transport)
        s=self.a.status();self.a.save_token(SYNTHETIC,s['revision'])
        self.assertEqual(self.a.status()['counts']['cn/index'],1)
        self.assertFalse(self.a.status()['enabled'])
    def test_failed_attempts_count_and_auth_blocks_no_secret_exception(self):
        self.enable()
        def fail(*args):raise RuntimeError('private='+SYNTHETIC)
        with self.assertRaises(DomainError) as caught:self.a.request('cn/index',{},transport=fail)
        self.assertNotIn(SYNTHETIC,str(caught.exception));self.assertIsNone(caught.exception.__context__)
        with self.assertRaises(DomainError):self.a.request('cn/index',{},transport=lambda *args:(403,{'message':SYNTHETIC}))
        self.assertEqual(self.a.status()['counts']['cn/index'],2)
    def test_saved_token_allows_only_explicit_manual_collection(self):
        self.a.save_token(SYNTHETIC, 0)
        state = self.a.status()
        self.assertFalse(state['enabled'])
        self.assertFalse(state['automatic'])
        self.a.request('cn/index', {'stockCodes': ['000905']}, transport=transport)
        self.assertEqual(self.a.status()['counts']['cn/index'], 1)
        with self.assertRaises(DomainError):
            self.a.request('cn/index', {}, automatic=True, transport=transport)

    def test_limit_and_automatic_gate_prevent_transport(self):
        self.enable(1)
        with self.assertRaises(DomainError):self.a.request('cn/index',{},automatic=True,transport=transport)
        self.a.request('cn/index',{'stockCodes':['000905']},transport=transport)
        with self.assertRaises(DomainError):self.a.request('cn/index',{},transport=lambda *args:self.fail('request beyond budget'))
    def test_symlinks_hardlinks_and_corrupt_counters_fail_closed(self):
        self.enable();path=Path(self.a.status()['path']);path.unlink();path.symlink_to(Path(self.temp.name)/'elsewhere')
        with self.assertRaises(DomainError):self.a.save_token(SYNTHETIC,self.a.status()['revision'])
        path.unlink();path.write_text(SYNTHETIC);path.chmod(0o600);os.link(path,path.with_name('hardlink'))
        with self.assertRaises(DomainError):self.a.request('cn/index',{},transport=transport)
        path.with_name('hardlink').unlink();path.with_name('budget.json').write_text('{bad')
        with self.assertRaises(DomainError):self.a.status()
    def test_lost_budget_with_existing_credential_cannot_reset_usage(self):
        self.enable();(self.a.directory/'budget.json').unlink()
        with self.assertRaises(DomainError):self.a.status()
        with self.assertRaises(DomainError):self.a.save_token(SYNTHETIC,0)

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'test');self.r=ResearchService(self.store,Service(self.store));self.no_start=patch.object(self.r.updates,'start');self.no_start.start()
        s=self.r.access.save_token(SYNTHETIC,0);self.r.access.configure(s['revision'],True,20)
        self.calls=[]
        def request(e,p,**kw):
            self.calls.append((e,p));return LocalAccess.request(self.r.access,e,p,**dict(kw,transport=transport))
        self.request=patch.object(self.r.access,'request',side_effect=request);self.request.start()
    def tearDown(self):self.request.stop();self.no_start.stop();self.r.shutdown();self.temp.cleanup()
    def add(self,modules=None):
        return self.r.collection.save('000905',modules or list(lixinger.DEFAULT_MODULES),'2026-10-01',True,self.r.revision())['id']
    def test_read_preview_add_are_offline_and_pause_prevents_queue(self):
        before=self.store.state()['revision'];p=self.r.collection.preview('000905',['price'],None)
        self.assertEqual(self.r.profiles(),[]);self.assertFalse(self.calls)
        iid=self.add();self.r.detail(iid,method='mcw');self.r.listing();self.assertFalse(self.calls)
        self.r.collection.save('000905',['price'],'2026-10-01',False,self.r.revision())
        with self.assertRaises(DomainError):self.r.run(dict(scopes=['index_prices'],targets=[iid],request_id='paused'))
        self.assertFalse(self.calls);self.assertEqual(self.store.state()['revision'],before)
    def test_subscription_cas_and_duplicate_family_no_new_data(self):
        iid=self.add();n=len(self.r.profiles())
        with self.assertRaises(ConflictError):self.r.collection.save('000905',['price'],None,True,0)
        self.assertEqual(iid,self.add());self.assertEqual(len(self.r.profiles()),n);self.assertFalse(self.calls)
    def test_collection_and_independent_valuation_date_and_version_restore(self):
        iid=self.add()
        for m in lixinger.DEFAULT_MODULES:self.r.collection.collect(iid,m,'2026-11-04')
        self.r.collection.collect(iid,'valuations','2026-11-05')
        d=self.r.detail(iid,method='mcw',window=0)
        self.assertEqual(d['latest']['date'],'2026-11-04');self.assertEqual(d['valuation']['pe']['date'],'2026-11-05')
        self.assertEqual(d['top10_weight'],'100');self.assertFalse(d['mapping_available'])
        with self.store.connection() as db:validate_backup(db)
        self.assertNotIn(SYNTHETIC,json.dumps(d));self.assertTrue(d['curve']);self.assertTrue(d['drawdown']['3']['partial'])
    def test_full_valuation_rechecks_old_dates_and_preserves_snapshot_on_truncation(self):
        self.r.collection.save('000905', ['valuations'], '2026-09-01', True, self.r.revision())
        iid = 'csi:000905'
        self.r.collection.collect(iid, 'index_info', '2026-11-04')
        self.r.collection.collect(iid, 'valuations', '2026-11-04')
        self.r.collection.collect(iid, 'valuations', '2026-11-05')
        self.assertEqual(self.calls[-1][1]['startDate'], '2026-10-20')
        old = self.r.module(iid, 'valuations')['snapshot_id']

        def truncated(endpoint, params, **kwargs):
            self.assertEqual(params['startDate'], '2026-09-01')
            return transport(endpoint, dict(params, startDate='2026-10-20'))[1]['data']

        with patch.object(self.r.access, 'request', side_effect=truncated), self.assertRaisesRegex(DomainError, '历史缩水'):
            self.r.collection.collect(iid, 'valuations', '2026-11-05', full=True)
        self.assertEqual(self.r.module(iid, 'valuations')['snapshot_id'], old)
        result = self.r.collection.collect(iid, 'valuations', '2026-11-05', full=True)
        self.assertEqual(self.calls[-1][1]['startDate'], '2026-09-01')
        self.assertIn('revised', result['update_difference'])

    def test_condition_scenario_requires_explicit_uniform_change_and_same_day(self):
        iid=self.add()
        for module in lixinger.DEFAULT_MODULES:self.r.collection.collect(iid,module,'2026-11-04')
        d=self.r.detail(iid,method='mcw')
        self.assertFalse(d['mapping_available'])
        self.assertTrue(d['scenario_metrics']['pe'])
        body=dict(analysis_id=d['analysis_id'],method='mcw',metric='pe',target_multiple=d['valuation']['pe']['current'],change_ratio='0.1',accept_fixed_basket_assumption=True)
        result=self.r.scenario(iid,body)
        from decimal import Decimal
        self.assertEqual(Decimal(result['point']),Decimal(d['latest']['close'])*Decimal('1.1'))
        self.assertIn('同比例',result['assumption'])
        with self.assertRaises(DomainError):self.r.scenario(iid,dict(body,accept_fixed_basket_assumption=False))
        self.r.collection.collect(iid,'valuations','2026-11-05')
        d=self.r.detail(iid,method='mcw');self.assertFalse(d['scenario_metrics']['pe'])
        with self.assertRaises(DomainError):self.r.scenario(iid,dict(body,analysis_id=d['analysis_id']))
    def test_observation_dates_replace_legacy_query_cutoff_without_losing_newer_data(self):
        modules=['index_info','index_financials','market_activity','historical_memberships']
        iid=self.r.collection.save('000905',modules,'2026-07-01',True,self.r.revision())['id']
        for module in modules:self.r.collection.collect(iid,module,'2026-11-04')
        for module in modules[1:]:
            original=self.r.module(iid,module)
            self.r.publish(iid,module,dict(original,effective_date='2026-11-05'))
            self.r.publish(iid,module,original)
            self.assertEqual(self.r.module(iid,module)['effective_date'],original['effective_date'])
        original=self.r.module(iid,'index_financials');older=copy.deepcopy(original)
        older['rows']=[dict(older['rows'][0],date='2020-03-31')];older['effective_date']='2020-03-31'
        with self.assertRaises(DomainError):self.r.publish(iid,'index_financials',older)
    def test_empty_legacy_membership_placeholder_cannot_block_real_quarters(self):
        iid=self.r.collection.save('000905',['index_info','historical_memberships'],'2026-07-01',True,self.r.revision())['id']
        self.r.publish(iid,'historical_memberships',dict(effective_date='2026-11-05',baskets=[dict(period='2025-12-31',members=[],status='unavailable')]))
        self.r.collection.collect(iid,'index_info','2026-11-04')
        self.r.collection.collect(iid,'historical_memberships','2026-11-04')
        self.assertTrue(self.r.module(iid,'historical_memberships')['baskets'][0]['members'])

    def test_full_financial_and_activity_ranges_are_not_silently_capped_at_ten_years(self):
        iid=self.r.collection.save('399006',['index_info','index_financials','market_activity'],None,True,self.r.revision())['id']
        for module in ('index_info','index_financials','market_activity'):self.r.collection.collect(iid,module,'2026-11-04')
        for module in ('index_financials','market_activity'):
            self.assertEqual(self.r.module(iid,'stage.'+module)['range_start'],'2010-06-01')

    def test_checkpoint_resume_after_budget_failure_does_not_repeat_successful_shard(self):
        iid=self.add(['market_activity']);self.r.collection.collect(iid,'index_info','2026-11-04');s=self.r.access.status();self.r.access.configure(s['revision'],True,1)
        self.r.access.request('cn/index/mutual-market',{'stockCode':'000905','startDate':'2026-10-01','endDate':'2026-11-04'})
        with self.assertRaises(DomainError):self.r.collection.collect(iid,'market_activity','2026-11-04')
        self.assertIsNone(self.r.module(iid,'market_activity'));stage=self.r.module(iid,'stage.market_activity');self.assertEqual(len(stage['parts']),1)
        s=self.r.access.status();self.r.access.configure(s['revision'],True,2)
        self.r.collection.collect(iid,'market_activity','2026-11-05')
        self.assertEqual(self.r.access.status()['counts']['cn/index/margin-trading-and-securities-lending'],1)
        self.assertEqual(self.r.module(iid,'stage.market_activity')['range_end'],'2026-11-04')
    def test_pause_during_response_does_not_publish(self):
        iid=self.add(['price']);self.r.collection.collect(iid,'index_info','2026-11-04')
        def pause(e,p,**kw):
            self.r.collection.save('000905',['price'],'2026-10-01',False,self.r.revision())
            return transport(e,dict(p,token=SYNTHETIC))[1]['data']
        with patch.object(self.r.access,'request',side_effect=pause),self.assertRaises(DomainError):self.r.collection.collect(iid,'price','2026-11-04')
        self.assertIsNone(self.r.module(iid,'price'))
    def test_merge_revisions_and_shrink_preserve_old_source(self):
        a=dict(provider_id='lixinger',rows=[dict(date='2026-01-01',close='10'),dict(date='2026-01-02',close='11')])
        b=dict(provider_id='lixinger',rows=[dict(date='2026-01-02',close='12')])
        merged,stats=merge(a,b,False);self.assertEqual(len(merged['rows']),2);self.assertEqual(stats['revised'],1)
        with self.assertRaises(DomainError):merge(a,b,True)
        self.assertEqual(a['rows'][1]['close'],'11')
        fresh,stats=merge(dict(a,provider_id='legacy'),b,True);self.assertEqual(len(fresh['rows']),1)
        narrowed,_=merge(a,b,True,('2026-01-02','2026-01-02'));self.assertEqual(len(narrowed['rows']),2)
        with self.assertRaises(DomainError):merge(a,dict(provider_id='lixinger',rows=[]),True)
        old=dict(provider_id='lixinger',baskets=[dict(period='2025-12-31',members=[dict(code='600001')])])
        with self.assertRaises(DomainError):merge(old,dict(provider_id='lixinger',baskets=[dict(period='2025-12-31',members=[])]),True)
    def test_identity_is_required_before_any_paid_series_request(self):
        iid=self.add(['price'])
        with self.assertRaises(DomainError):self.r.collection.collect(iid,'price','2026-11-04')
        self.assertEqual(self.calls,[])
    def test_company_cache_shared_only_at_same_query_cutoff(self):
        from tests.research_fixtures import stock_payload
        modules=['index_info','memberships','stock_valuations'];iid=self.add(modules)
        other=self.r.collection.save('000300',modules,'2026-10-01',True,self.r.revision())['id']
        for target in (iid,other):
            for m in ('index_info','memberships'):self.r.collection.collect(target,m,'2026-11-04')
        with patch('backend.research.providers.stock_valuations',side_effect=stock_payload) as fetch:
            self.r.collection.collect(iid,'stock_valuations','2026-11-04')
            self.r.collection.collect(other,'stock_valuations','2026-11-04')
            self.assertEqual(fetch.call_count,1)
            self.r.collection.collect(other,'stock_valuations','2026-11-05');self.assertEqual(fetch.call_count,2)
        with self.store.connection() as db:validate_backup(db)
    def test_validation_failure_can_be_explicitly_retried_without_automatic_loop(self):
        iid=self.add(['price']);self.r.collection.collect(iid,'index_info','2026-11-04')
        with patch('backend.research.lixinger.assemble',side_effect=DomainError('合成校验失败')):
            with self.assertRaises(DomainError):self.r.collection.collect(iid,'price','2026-11-04')
        self.assertEqual(self.r.module(iid,'stage.price')['phase'],'validation_failed')
        used=len(self.calls)
        with self.assertRaises(DomainError):self.r.collection.collect(iid,'price','2026-11-04',automatic=True)
        self.assertEqual(len(self.calls),used)
        self.r.collection.collect(iid,'price','2026-11-04');self.assertIsNotNone(self.r.module(iid,'price'))

    def test_historical_industry_cache_uses_period_and_cutoff_not_current_membership(self):
        from tests.research_fixtures import finance_payload,industry_payload,member_payload
        modules=['memberships','historical_memberships','historical_financials'];iid=self.add(modules)
        other=self.r.collection.save('000300',modules,'2026-10-01',True,self.r.revision())['id']
        members=member_payload({})['rows'];members[0]['industry']='银行'
        baskets=[dict(period='2025-12-31',status='checked',members=members)]
        for target in (iid,other):
            self.r.publish(target,'memberships',dict(rows=members,effective_date='2026-09-30'))
            self.r.publish(target,'historical_memberships',dict(baskets=baskets,effective_date='2025-12-31'))
        with patch('backend.research.providers.financials',side_effect=finance_payload),patch('backend.research.providers.historical_industries',side_effect=industry_payload) as fetch:
            first=self.r.collection.company_data(iid,'historical_financials','2026-11-04')
            self.r.collection.company_data(other,'historical_financials','2026-11-04');self.assertEqual(fetch.call_count,1)
            self.assertEqual(first['years'][0]['metrics']['operating_cf']['count'],1)
            self.assertEqual(self.r.module(iid,'historical_memberships')['baskets'][0]['members'][0]['industry'],'银行')
            self.r.collection.company_data(other,'historical_financials','2026-11-05');self.assertEqual(fetch.call_count,2)
    def test_official_check_is_versioned_and_does_not_modify_valuation(self):
        iid=self.add()
        for m in ('index_info','price','valuations'):self.r.collection.collect(iid,m,'2026-11-04')
        before=self.r.module(iid,'valuations');counts=self.r.access.status()['counts']
        official=dict(rows=[dict(date='2026-11-04',close='1',reference_pe='9')],source='合成官方',evidence=[])
        with patch('backend.research.providers.prices',return_value=official):
            r=self.r.check_source(iid,self.r.detail(iid,method='mcw')['analysis_id'])
        self.assertFalse(r['rows'][0]['close_matches']);self.assertEqual(self.r.module(iid,'valuations'),before)
        self.assertEqual(self.r.access.status()['counts'],counts)

class AnalyticsTests(unittest.TestCase):
    def rows(self):
        d=date(2010,1,1);rows=[]
        while d<=date(2026,11,4):
            if d.weekday()<5:rows.append(dict(date=str(d),value='10',close=str(100+len(rows)/10)))
            d+=timedelta(days=1)
        return rows
    def test_ties_expanding_history_and_zero_yield(self):
        rows = self.rows()
        self.assertEqual(ranks(dict(rows=rows, frequency='daily'))['0']['rank'], '0.5')
        zero = dict(rows=[dict(r, value='0') for r in rows], frequency='daily')
        self.assertIsNone(ranks(zero)['0']['rank'])
        self.assertEqual(ranks(zero, allow_zero=True)['0']['rank'], '0.5')

    def test_valuation_means_span_long_closures_without_inventing_observations(self):
        rows = [dict(r, value=str(i % 30 + 1)) for i, r in enumerate(self.rows())]
        rows = [r for r in rows if not any(first <= r['date'] <= last for first, last in (
            ('2014-10-01', '2014-10-07'), ('2019-10-01', '2019-10-07'), ('2021-10-01', '2021-10-07'),
        ))]
        series = dict(rows=rows, frequency='daily')
        curve = {r['date']: r for r in valuation_curve(series, rows, 3)}
        at = '2024-09-30'
        for years in (3, 5, 10):
            boundary = str(years_before(at, years))
            values = [Decimal(r['value']) for r in rows if boundary < r['date'] <= at]
            self.assertEqual(Decimal(curve[at]['mean' + str(years)]), sum(values) / len(values))
        self.assertTrue(full_window(rows, '2024-10-01', 3))

    def test_valuation_means_keep_real_missing_sessions_and_short_history_empty(self):
        prices = self.rows()
        rows = [r for r in prices if r['date'] != '2021-10-01']
        curve = {r['date']: r for r in valuation_curve(dict(rows=rows, frequency='daily'), prices, 3)}
        self.assertIsNone(curve['2024-09-30']['mean3'])
        rows = [r for r in prices if not '2023-01-01' <= r['date'] <= '2023-03-31']
        curve = {r['date']: r for r in valuation_curve(dict(rows=rows, frequency='daily'), prices, 3)}
        self.assertTrue(all(curve['2024-09-30']['mean' + str(y)] is None for y in (3, 5, 10)))
        short = dict(rows=prices[-500:], frequency='daily')
        self.assertTrue(all(r['mean3'] is None for r in valuation_curve(short, prices, 3)))
        self.assertFalse(full_window(prices[-500:], prices[-1]['date'], 3))

    def test_missing_known_session_invalidates_ma(self):
        rows=self.rows()[-100:];missing=rows[20]['date'];rows.pop(20)
        out=moving_averages(rows,0,[missing]);self.assertIsNone(out[60]['ma51']);self.assertIsNotNone(out[-1]['ma51'])
    def test_flow_missing_day_and_incomplete_month_not_zero(self):
        prices=[dict(date=d) for d in ('2026-01-02','2026-01-05','2026-02-02')]
        rows=[dict(date='2026-01-02',stock='100',flow='3'),dict(date='2026-02-02',stock='110',flow='5')]
        r=flow_months(rows,prices,'stock','flow');self.assertIsNone(r[0]['flow']);self.assertIsNone(r[1]['flow']);self.assertEqual(r[1]['change'],'10')
    def test_quarter_plan_limit_and_weight_unit(self):
        self.assertEqual(len(lixinger.periods('2026-11-04','2005-01-01')),40)
        p={'code':'000905','launch':'2007-01-15','provider':'csi'}
        c=lixinger.request_plan(p,'memberships','2026-10-01','2026-11-04')[0]
        self.assertEqual(c['period'],'2026-10-31')
        normalized=lixinger.normalize(c,transport(c['endpoint'],c['params'])[1]['data'],p)
        self.assertEqual(normalized[0]['weight'],'40');self.assertEqual(sum(float(x['weight']) for x in lixinger.full_weights(normalized,'2026-10-31')),100)
    def test_latest_full_weights_do_not_normalize_current_top_ten(self):
        rows=[dict(date='2026-09-30',code='600001',weight='60'),dict(date='2026-09-30',code='000001',weight='40'),dict(date='2026-10-30',code='600001',weight='18')]
        self.assertFalse(lixinger.full_weights(rows,'2026-10-31'))
        self.assertEqual(lixinger.full_weights(rows,'2026-10-31',True)[0]['date'],'2026-09-30')
    def test_missing_annual_basket_does_not_create_cross_year_yoy(self):
        from backend.research.history import totals
        members=[dict(code='600001',industry='制造')]
        rows=[dict(code='600001',period=p,revenue=v) for p,v in [('2023-12-31','100'),('2025-12-31','200')]]
        result=totals(rows,[dict(period=p,members=members,status='checked') for p in ('2023-12-31','2025-12-31')])
        self.assertIsNone(result[-1]['metrics']['revenue']['growth'])

class LocalApiTests(unittest.TestCase):
    def test_token_response_validation_and_backup_exclude_secret(self):
        with tempfile.TemporaryDirectory() as root:
            app=create_app(root,testing=True,auto_sync=False)
            with TestClient(app) as c:
                boot=c.get('/api/bootstrap').json();headers={'X-CSRF-Token':boot['csrf_token']}
                state=c.get('/api/settings/lixinger').json()
                r=c.put('/api/settings/lixinger/token',json={'token':SYNTHETIC,'expected_revision':state['revision']},headers=headers)
                self.assertEqual(r.status_code,200,r.text);self.assertNotIn(SYNTHETIC,r.text)
                bad=c.put('/api/settings/lixinger/token',json={'token':SYNTHETIC,'expected_revision':'bad'},headers=headers)
                self.assertEqual(bad.status_code,422);self.assertNotIn(SYNTHETIC,bad.text)
                self.assertEqual(c.get('/api/settings/lixinger/token').status_code,404)
                self.assertFalse(c.get('/api/research/catalog').json()['subscriptions'])
                archive=c.get('/api/backup');self.assertEqual(archive.status_code,200)
                with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
                    self.assertFalse(any('private-lixinger' in name for name in z.namelist()))
                    for name in z.namelist():self.assertNotIn(SYNTHETIC.encode(),z.read(name))
                r=app.state.research
                iid=r.collection.save('000905',['price'],'2026-10-01',True,r.revision())['id']
                response=c.get('/api/research/indices/'+iid+'/studio?window=0')
                self.assertEqual(response.status_code,200,response.text)
                self.assertNotIn('backtest',response.json())
                self.assertNotIn('overlaps',response.json())
                self.assertEqual(c.get('/api/research/indices/'+iid+'/studio?window=2').status_code,422)
