import base64
import copy
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from datetime import date,datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import ConflictError,DomainError,json_dumps
from backend.deployment_guard import capture,verify,require_empty
from backend.migration import migrate
from backend.research import providers
from backend.research.metrics import drawdown,valuation,financial_totals,years_before,price_changes,moving_averages
from backend.research.service import ResearchService,validate_backup
from backend.research.snapshots import encode,decode,MAX_PAYLOAD
from backend.service import Service
from backend.store import Store
from tests.research_fixtures import ASOF,price_payload,valuation_payload,member_payload,stock_payload,finance_payload
from tests.lixinger_fixtures import transport


class MetricTests(unittest.TestCase):
    def setUp(self):self.p=providers.identity('000905');self.rows=price_payload(self.p,'price')['rows']
    def test_independent_high_dates_latest_equal_and_underwater(self):
        expected={3:('200','2025-06-02','-0.4'),5:('250','2022-06-01','-0.52'),10:('300','2017-06-01','-0.6')}
        for w,(high,d,ratio) in expected.items():
            result=drawdown(self.rows,ASOF,w)
            self.assertEqual((result['high_close'],result['high_date'],result['drawdown_ratio']),(high,d,ratio))
            self.assertLessEqual(float(result['max_drawdown_ratio']),float(ratio))
            self.assertEqual(result['curve'][0]['drawdown_ratio'],'0')
    def test_short_history_is_null_not_zero_or_invented_date(self):
        self.assertEqual(drawdown(self.rows[-100:],ASOF,3),dict(status='insufficient_history',high_date=None,high_close=None,drawdown_ratio=None))
    def test_missing_interval_not_a_complete_window(self):
        rows=[r for r in self.rows if not '2025-01-01'<=r['date']<='2025-08-01']
        self.assertIsNone(drawdown(rows,ASOF,3)['drawdown_ratio'])
    def test_future_prices_ignored_and_duplicate_rejected(self):
        result=drawdown(self.rows+[dict(date='2027-01-01',close='9999')],ASOF,3)
        self.assertEqual(result['high_date'],'2025-06-02')
        with self.assertRaises(ValueError):drawdown(self.rows+[self.rows[-1]],ASOF,3)
    def test_total_return_does_not_fall_back_to_price(self):
        tr=drawdown(price_payload(self.p,'total_return')['rows'],ASOF,3)
        self.assertEqual(tr['high_date'],'2024-07-01');self.assertEqual(tr['drawdown_ratio'],'-0.7')
    def test_leap_day_policy(self):self.assertEqual(years_before('2024-02-29',3),date(2021,2,28))
    def test_monthly_percentile_current_excluded_and_half_ties(self):
        series=valuation_payload(self.p)['series']['pe']['overall'];series=copy.deepcopy(series)
        for r in series['rows']:r['value']='10'
        result=valuation(series,ASOF)
        self.assertEqual([result['windows'][str(w)]['count'] for w in (3,5,10)],[36,60,120])
        self.assertEqual(result['windows']['3']['rank'],'0.5')
    def test_missing_month_and_nonpositive_month_do_not_pass(self):
        s=valuation_payload(self.p)['series']['pe']['overall'];s=copy.deepcopy(s)
        s['rows']=[r for r in s['rows'] if not r['date'].startswith('2025-04')]
        self.assertIsNone(valuation(s,ASOF)['windows']['3']['rank'])
        s=copy.deepcopy(valuation_payload(self.p)['series']['pe']['overall']);next(r for r in s['rows'] if r['date'].startswith('2025-04'))['value']='-1'
        self.assertIsNone(valuation(s,ASOF)['windows']['3']['rank'])
    def test_snapshot_and_negative_current_have_no_rank(self):
        for frequency,value in [('snapshot','10'),('daily','-1'),('monthly','0')]:
            s=dict(rows=[dict(date=ASOF,value=value)],frequency=frequency)
            self.assertTrue(all(v['rank'] is None for v in valuation(s,ASOF)['windows'].values()))
    def test_independent_period_company_sets(self):
        members=[dict(code=str(i),weight='20',industry='工业') for i in range(5)]
        rows=[dict(code=str(i),period='2025-12-31',revenue='100') for i in range(4)]+[dict(code=str(i),period='2024-12-31',revenue='500' if i==4 else '100') for i in range(1,5)]
        r=financial_totals(rows,members,'2025-12-31')['revenue']
        self.assertEqual(r['current']['coverage'],'0.8');self.assertEqual(r['growth'],'-0.5')
    def test_cash_excludes_financial_and_unknown_industry(self):
        members=[dict(code='a',weight='20',industry='工业'),dict(code='b',weight='40',industry='银行'),dict(code='c',weight='40',industry='')]
        rows=[dict(code=c,period=p,operating_cf='10') for c in ('a','b','c') for p in ('2025-12-31','2024-12-31')]
        r=financial_totals(rows,members,'2025-12-31')['operating_cf']
        self.assertEqual(r['current']['amount'],'10');self.assertEqual(r['current']['denominator'],1);self.assertEqual(r['current']['coverage'],'1')
    def test_nonpositive_base_preserves_change_not_growth(self):
        members=[dict(code='a',weight='100',industry='工业')]
        rows=[dict(code='a',period='2025-12-31',profit='10'),dict(code='a',period='2024-12-31',profit='-10')]
        r=financial_totals(rows,members,'2025-12-31')['profit']
        self.assertEqual(r['status'],'nonpositive_base');self.assertIsNone(r['growth']);self.assertEqual(r['change'],'20')

    def test_monthly_change_uses_last_close_before_month_boundary(self):
        rows = [dict(date='2026-01-30', close='100'), dict(date='2026-02-02', close='110'), dict(date='2026-02-03', close='121')]
        result = price_changes(rows, '2026-02-03')
        self.assertEqual(result['monthly']['ratio'], '0.21')
        self.assertEqual(result['monthly']['from_date'], '2026-01-30')
        self.assertIsNone(price_changes(rows[1:], '2026-02-03')['monthly'])
        self.assertIsNone(price_changes(rows, '2026-03-02')['monthly'])

    def test_calendar_returns_and_missing_anchors(self):
        rows=[dict(date='2025-12-31',close='100'),dict(date='2026-10-23',close='110'),dict(date='2026-10-29',close='120'),dict(date='2026-10-30',close='132')]
        c=price_changes(rows,'2026-10-30')
        self.assertEqual(c['daily']['ratio'],'0.1');self.assertEqual(c['weekly']['ratio'],'0.2');self.assertEqual(c['ytd']['ratio'],'0.32')
        self.assertEqual(c['weekly']['from_date'],'2026-10-23')
        self.assertIsNone(price_changes(rows[1:],'2026-10-30')['ytd'])
        self.assertIsNone(price_changes(rows,'2026-11-06')['weekly'])
        self.assertIsNone(price_changes(rows[:1],'2025-12-31')['daily'])
    def test_moving_average_uses_full_history_and_exact_observation_counts(self):
        start=date(2023,1,1);rows=[dict(date=str(start+timedelta(days=i)),close=str(i+1)) for i in range(1000)]
        ma=moving_averages(rows,10)
        self.assertIsNone(ma[49]['ma51']);self.assertEqual(ma[50]['ma51'],'26')
        self.assertIsNone(ma[848]['ma850']);self.assertEqual(ma[849]['ma850'],'425.5')
        self.assertEqual(ma[-1]['ma51'],'975');self.assertEqual(ma[-1]['ma850'],'575.5')
        clipped=moving_averages(rows,1)
        self.assertEqual(clipped[0]['ma51'],str(int(clipped[0]['close'])-25))


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'test';self.store=Store(self.path);self.legacy=Service(self.store);self.r=ResearchService(self.store,self.legacy)
        self.no_start=patch.object(self.r.updates,'start');self.no_start.start()
    def tearDown(self):self.no_start.stop();self.r.shutdown();self.temp.cleanup()
    def add(self,code='000905',request='synthetic-add'):
        from backend.research.lixinger import MODULES
        p=providers.identity(code);iid=p['provider']+':'+p['code'];existing=any(v['id']==iid for v in self.r.profiles())
        if not existing:
            with self.r.transaction(bump=True) as db:
                db.execute('INSERT INTO research_indices VALUES (?,?)',(iid,json_dumps(dict(p,id=iid,subscription=dict(enabled=True,modules=list(MODULES),history_start=None)))))
        body=dict(scopes=['index_prices','memberships','valuations','financials'],targets=[iid],request_id=request)
        job=self.r.run(body)
        return dict(id=iid,basis=p['input_basis'],existing=existing,research_revision=self.r.revision(),job_id=job['job_id']),body
    def test_old_new_year_gap_flags_are_corrected_without_rewriting_snapshot(self):
        from backend.trading_calendar import no_expected_session
        self.assertTrue(no_expected_session('CN', '2025-12-31', '2026-01-04'))
        self.assertFalse(no_expected_session('CN', '2025-12-31', '2026-01-05'))
        self.assertFalse(no_expected_session('CN', '2026-12-31', '2027-01-01'))
        result, _ = self.add()
        iid = result['id']
        value = price_payload(providers.identity('000905'), 'price')
        value['rows'] = [r for r in value['rows'] if r['date'] not in ('2026-01-01', '2026-01-02', '2026-02-03')]
        value['missing_dates'] = ['2026-01-01', '2026-02-03']
        snapshot = self.r.publish(iid, 'price', value)
        self.assertEqual(self.r.publish(iid, 'price', value), snapshot)
        current = self.r.module(iid, 'price')
        self.assertEqual(current['missing_dates'], ['2026-02-03'])
        computed = moving_averages(current['rows'], 0, current['missing_dates'])
        january = next(r for r in computed if r['date']=='2026-01-05')
        self.assertTrue(all(january['ma'+str(n)] is not None for n in (51,120,250,850)))
        self.assertIsNone(next(r for r in computed if r['date']=='2026-02-04')['ma51'])
        with self.store.connection() as db:
            row = db.execute('SELECT data,sha256 FROM research_snapshots WHERE id=?',(snapshot,)).fetchone()
            self.assertEqual(decode(row[0],row[1])['missing_dates'], value['missing_dates'])

    def test_listing_reuses_catalog_summaries_without_loading_company_history(self):
        for p in providers.CATALOG:self.add(p['code'],'synthetic-'+p['code'])
        with patch.object(self.r,'module',wraps=self.r.module) as load:
            first=self.r.listing()
            self.assertEqual(len(first['items']),len(providers.CATALOG))
            self.assertEqual(load.call_count,2*len(providers.CATALOG))
            self.assertTrue(all(call.args[1] in ('price','valuations') for call in load.call_args_list))
            load.reset_mock()
            self.assertEqual(first,self.r.listing())
            self.assertEqual(load.call_count,0)
        first['items'][0]['profile']['name']='caller edit'
        self.assertNotEqual(self.r.listing()['items'][0]['profile']['name'],'caller edit')
        iid=self.r.profiles()[0]['id']
        full=self.r.detail(iid)
        summary=next(x for x in self.r.listing()['items'] if x['id']==iid)
        self.assertEqual(summary,{k:full[k] for k in summary})

    def test_update_history_collapses_active_children_and_limits_total_to_five(self):
        added,_=self.add()
        job=self.r.updates.get(added['job_id'])
        job.update(started_at='2026-01-02T10:00:00+08:00',legacy_job_ids=['synthetic-child'])
        self.r.updates.save(job,'running')
        self.store.save_job(dict(id='synthetic-child',status='running',started_at='2026-01-02T10:00:01+08:00',items=[]))
        for n in range(6):self.store.save_job(dict(id='standalone-'+str(n),status='complete',started_at='2026-01-01T10:00:0'+str(n)+'+08:00',items=[]))
        rows=self.r.updates.history()['jobs']
        self.assertEqual(len(rows),5)
        self.assertEqual(rows[0]['id'],job['id'])
        self.assertEqual(rows[0]['trigger_label'],'手动更新')
        self.assertNotIn('synthetic-child',[x['id'] for x in rows])
        self.assertEqual([r['id'] for r in rows[1:]],['standalone-'+str(n) for n in (5,4,3,2)])
        self.r.updates.save(dict(job,finished_at='2026-01-03T10:00:00+08:00'),'complete')
        self.assertEqual([r['id'] for r in rows],[r['id'] for r in self.r.updates.history()['jobs']])

    def test_update_uses_completed_prices_and_today_announcements(self):
        p=self.populate();self.r.updates.current_job={'market_cutoff':'2026-10-30','information_cutoff':'2026-11-04'}
        with patch.object(self.r.collection,'collect',return_value={}) as collect:
            self.r.updates.execute(dict(scope='index_prices',target=p['id'],module='price'))
            self.r.updates.execute(dict(scope='financials',target=p['id'],module='financials'))
            self.assertEqual([c.args[2] for c in collect.call_args_list],['2026-10-30','2026-11-04'])

    def populate(self):
        added,_=self.add();p=self.r.profile(added['id'])
        for module,v in [('price',price_payload(p,'price')),('total_return',price_payload(p,'total_return')),('valuations',valuation_payload(p)),('memberships',member_payload(p)),('stock_valuations',stock_payload(member_payload(p)['rows'])),('financials',finance_payload(member_payload(p)['rows']))]:self.r.publish(p['id'],module,v)
        return p
    def test_fresh_install_remains_empty_and_get_resolve_no_side_effect(self):
        before=capture(self.path);self.r.resolve('399606');self.assertEqual(capture(self.path),before);self.assertTrue(require_empty(self.path)['empty'])
        self.r.tick('2026-10-30T23:59:00+08:00');self.assertEqual(self.r.jobs(),[])
    def test_add_pairs_both_codes_and_atomic_durable_update_intent(self):
        revision=self.store.state()['revision'];a,body=self.add('H00905');self.assertEqual(a['basis'],'total_return');self.assertEqual(self.r.profile(a['id'])['return_code'],'H00905')
        self.assertEqual(self.store.state()['revision'],revision);self.assertEqual(len(self.r.jobs()[0]['resources']),12)
        self.assertEqual(self.r.run(body)['job_id'],a['job_id'])
        a2,_=self.add('000905','same-family');self.assertTrue(a2['existing']);self.assertEqual(len(self.r.profiles()),1);self.assertEqual(self.r.revision(),1)
    def test_unknown_and_similar_code_never_guessed(self):
        for code in ('000906','399999','中证500','399005'):
            with self.assertRaises(DomainError):self.r.resolve(code)
    def test_added_csi_families_deduplicate_both_bases_and_preserve_backup_identity(self):
        ledger_revision=self.store.state()['revision']
        for code,tr in [('930955','H20955'),('399998','H20596'),('000991','H00991')]:
            with self.subTest(code=code):
                preview=self.r.resolve(tr.lower())
                self.assertEqual(preview['profile']['input_basis'],'total_return')
                saved,_=self.add(tr,'new-'+code)
                duplicate,_=self.add(code,'duplicate-'+code)
                self.assertEqual(saved['id'],'csi:'+code)
                self.assertEqual(duplicate['id'],saved['id']);self.assertTrue(duplicate['existing'])
                profile=self.r.profile(saved['id'])
                for basis in ('price','total_return'):
                    self.r.publish(saved['id'],basis,price_payload(profile,basis))
                self.assertEqual(self.r.detail(saved['id'],'total_return')['series_code'],tr)
        self.assertEqual(len(self.r.profiles()),3)
        self.assertEqual(self.store.state()['revision'],ledger_revision)
        with self.store.connection() as db:validate_backup(db)
    def test_dividend_trial_without_token_is_blocked_without_provider_request(self):
        added,_=self.add('930955')
        with patch('requests.Session.post') as fetch,self.assertRaises(DomainError):
            self.r.updates.execute(dict(scope='valuations',target=added['id'],module='valuations'))
        fetch.assert_not_called()
        self.assertEqual(self.r.detail(added['id'])['module_status']['valuations']['status'],'missing')

    def test_existing_dividend_legacy_snapshot_remains_readable_without_profile_rewrite(self):
        added,_=self.add('000922');old=self.r.profile(added['id']);self.r.publish(added['id'],'price',price_payload(old,'price'))
        self.r.publish(added['id'],'valuations',valuation_payload(old));detail=self.r.detail(added['id'])
        self.assertEqual(detail['valuation']['pe']['source'],'synthetic');self.assertEqual(self.r.profile(added['id']),old)
        with patch('requests.Session.post') as post:self.r.detail(added['id'],method='mcw');post.assert_not_called()

    def test_collection_preview_identity_and_cas(self):
        with patch.object(self.r.access, 'request', side_effect=lambda endpoint, params: transport(endpoint, params)[1]['data']):
            p=self.r.collection.preview('H00905',['price'],None)
        self.assertEqual(p['profile']['code'],'000905');self.assertEqual(self.r.profiles(),[])
        self.add('399006','other')
        with self.assertRaises(ConflictError):self.r.collection.save('000905',['price'],None,True,p['research_revision'])

    def test_request_id_cannot_change_payload(self):
        _,body=self.add()
        with self.assertRaises(ConflictError):self.r.run(dict(body,scopes=['index_prices']))
    def test_publish_is_immutable_idempotent_and_a_b_a_is_new_version(self):
        a,_=self.add();value=price_payload(self.r.profile(a['id']),'price');sid=self.r.publish(a['id'],'price',value);revision=self.r.revision()
        self.assertEqual(self.r.publish(a['id'],'price',dict(value,evidence=[dict(fetched_at='other') ])),sid);self.assertEqual(self.r.revision(),revision)
        changed=copy.deepcopy(value);changed['rows'][-1]['close']='121';sid2=self.r.publish(a['id'],'price',changed);sid3=self.r.publish(a['id'],'price',value)
        self.assertEqual(len({sid,sid2,sid3}),3)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM research_snapshots').fetchone()[0],3)
        with self.assertRaises(DomainError):self.r.publish(a['id'],'price',dict(value,effective_date='2020-01-01'))
    def test_basis_switch_and_finance_summary_sources(self):
        p=self.populate();d=self.r.detail(p['id']);tr=self.r.detail(p['id'],'total_return');self.assertEqual(d['drawdown']['3']['high_date'],'2025-06-02');self.assertEqual(tr['drawdown']['3']['high_date'],'2024-07-01')
        self.assertEqual(d['valuation'],tr['valuation']);self.assertNotEqual(d['analysis_id'],tr['analysis_id']);self.assertEqual(d['financials']['main_period'],'2025-12-31');self.assertEqual(d['companies'][1]['valuation']['pe'],'-10');self.assertEqual(len(d['sources']),6)
        self.assertFalse(tr['mapping_available'])
    def test_missing_total_return_no_substitute(self):
        a,_=self.add();self.r.publish(a['id'],'price',price_payload(self.r.profile(a['id']),'price'))
        self.assertIsNone(self.r.detail(a['id'],'total_return')['latest'])
    def test_coverage_failure_refreshes_without_a_new_data_snapshot(self):
        a,_=self.add('000688');before=self.r.detail(a['id']);revision=self.r.revision()
        self.assertEqual(before['module_status']['price']['status'],'missing')
        job=dict(items=[dict(target=a['id'],module='price',status='error',error='synthetic unavailable source')])
        with patch.object(self.r.updates,'jobs',return_value=[job]):
            after=self.r.detail(a['id'])
        self.assertEqual(self.r.revision(),revision)
        self.assertEqual(after['module_status']['price']['status'],'error')
        self.assertEqual(after['module_status']['price']['reason'],'synthetic unavailable source')
        self.assertIsNone(after['top10_weight'])
    def test_sparse_latest_quarter_does_not_replace_covered_period(self):
        p=self.populate();value=finance_payload(member_payload(p)['rows']);value['periods'].append('2026-09-30');value['rows'].append(dict(code='000201',period='2026-09-30',revenue='1',profit='1'));self.r.publish(p['id'],'financials',value)
        self.assertEqual(self.r.detail(p['id'])['financials']['main_period'],'2025-12-31')
    def test_settings_cas_separate_from_research_and_ledger(self):
        a=self.r.settings();before=self.store.state()['revision'];r=self.r.revision()
        saved=self.r.save_settings(dict(policies=a['policies'],expected_settings_revision=0));self.assertEqual(saved['settings_revision'],1)
        self.assertEqual(self.store.state()['revision'],before);self.assertEqual(self.r.revision(),r)
        with self.assertRaises(ConflictError):self.r.save_settings(dict(policies=a['policies'],expected_settings_revision=0))
        for invalid in ('25:00','x',''):
            rows=copy.deepcopy(a['policies']);rows[0]['time']=invalid
            with self.assertRaises(DomainError):self.r.save_settings(dict(policies=rows,expected_settings_revision=1))
    def test_backup_restore_all_research_hashes_and_broken_reference_rejected(self):
        p=self.populate();backup=self.legacy.backup_download();target=Service(Store(Path(self.temp.name)/'restored'));target.restore(backup)
        r=ResearchService(target.store,target)
        self.assertEqual(self.r.detail(p['id'])['analysis_id'],r.detail(p['id'])['analysis_id'])
        with self.store.connection() as db:
            db.execute("UPDATE research_snapshots SET sha256='bad'");db.commit()
            with self.assertRaises(DomainError):validate_backup(db)
    def test_compressed_payload_tamper_and_size_limit(self):
        digest,content=encode(dict(rows=[dict(date=ASOF,close='100')]*1000));self.assertLess(len(content),1000);self.assertEqual(len(decode(content,digest)['rows']),1000)
        with self.assertRaises(DomainError):decode(content,'bad')
    def test_schema9_additive_migration_preserves_business_and_rollback(self):
        p=self.legacy.save_portfolio(dict(name='合成迁移',planned_amount='1000',start_date='2024-01-01'))
        with self.store.connection() as db:
            for t in ('research_heads','research_snapshots','research_indices','research_meta','research_price_chunks','data_update_tasks','data_update_policies','data_update_receipts'):db.execute('DROP TABLE '+t)
            from tests.waterlevel_fixtures import drop_tables
            drop_tables(db)
            db.execute('DROP TABLE IF EXISTS family_expense_plans'); db.execute('DROP TABLE IF EXISTS family_expense_months')
            db.execute("UPDATE meta SET value='9' WHERE key='schema'");db.execute("UPDATE alembic_version SET version_num='0008_retire_legacy_labels'");db.commit()
        before=capture(self.path)
        from backend import migration
        actual=migration.command.upgrade
        def fail(*a,**kw):actual(*a,**kw);raise RuntimeError('synthetic migration rollback')
        with patch.object(migration.command,'upgrade',side_effect=fail):
            with self.assertRaises(RuntimeError):migrate(self.store.path)
        self.assertEqual(capture(self.path),before);migrate(self.store.path);self.assertTrue(verify(before,capture(self.path,before))['preserved'])
    def test_exact_failed_module_retry_does_not_call_other_sources(self):
        p=self.populate();self.r.updates.recover(False)
        with patch.object(self.r.collection,'collect',return_value={}) as prices:
            job=self.r.run(dict(scopes=['index_prices'],resources=[dict(scope='index_prices',target=p['id'],module='total_return')],request_id='exact'))
            self.r.updates._work();prices.assert_called_once();self.assertEqual(prices.call_args.args[1],'total_return')
        self.assertEqual(next(j for j in self.r.jobs() if j['id']==job['job_id'])['state'],'complete')
    def test_duplicate_manual_requests_coalesce(self):
        self.add();self.r.updates.recover(False)
        b=dict(scopes=['index_prices']);one=self.r.run(dict(b,request_id='manual'));two=self.r.run(dict(b,request_id='second-manual'))
        self.assertEqual(one['job_id'],two['job_id'])
        with patch.object(self.r.collection,'collect',return_value={}) as f:
            self.r.updates._work();again=self.r.run(dict(b,request_id='second-manual'));self.r.updates._work();self.assertEqual(f.call_count,4)
        self.assertEqual(again['job_id'],one['job_id'])
    def test_restart_leaves_research_interrupted_and_retains_successful_resources(self):
        a,_=self.add();job=self.r.jobs()[0]
        resource=job['resources'][0];sid=self.r.publish(a['id'],'price',price_payload(self.r.profile(a['id']),'price'))
        from backend.research.updates import key
        job['completed_resources']=[key(resource)];job['items']=[dict(scope=resource['scope'],resource=resource,status='complete',snapshot_id=sid)]
        self.r.updates.save(job,'running');self.r.recover(True);self.assertEqual(self.r.jobs()[0]['state'],'interrupted')
        self.assertEqual(self.r.jobs()[0]['completed_resources'],[key(resource)])
    def test_no_sync_recovery_does_not_launch_updates_and_guard_blocks_write(self):
        self.add();self.r.recover(False);self.assertEqual(self.r.jobs()[0]['state'],'interrupted')
        guarded=ResearchService(self.store,self.legacy,lambda:False)
        with self.assertRaises(DomainError):guarded.run(dict(scopes=['fx'],request_id='blocked'))
    def test_scheduler_slot_idempotence_and_weekend_policy(self):
        self.add();self.r.updates.recover(False)
        policies=self.r.settings()['policies']
        for p in policies:p['enabled']=p['scope']=='fx';p['cadence']='trade_day'
        self.legacy.save_portfolio(dict(name='测试调度', planned_amount='1000', start_date='2026-01-01'))
        self.r.save_settings(dict(policies=policies,expected_settings_revision=0))
        self.r.tick('2026-10-30T23:59:00+08:00');count=len(self.r.jobs());self.r.tick('2026-10-31T23:59:00+08:00');self.r.tick('2026-10-30T23:59:45+08:00');self.assertEqual(len(self.r.jobs()),count)
    def test_only_requested_fx_currency(self):
        self.r.updates.recover(False)
        with patch('backend.market.exchange_rates',return_value=[dict(currency='HKD',date=ASOF,rate='0.9',source='synthetic')]) as rates:
            self.r.run(dict(scopes=['fx'],resources=[dict(scope='fx',currency='HKD')],request_id='hkd'));self.r.updates._work()
            self.assertEqual({c.args[0] for c in rates.call_args_list},{'HKD'})
        self.assertEqual(self.r.jobs()[0]['state'],'complete')
    def test_partial_source_failure_preserves_last_snapshot(self):
        p=self.populate();old=self.r.module(p['id'],'total_return')['snapshot_id'];self.r.updates.recover(False)
        with patch.object(providers,'prices',side_effect=TimeoutError('synthetic timeout')):
            self.r.run(dict(scopes=['index_prices'],request_id='fail'));self.r.updates._work()
        self.assertEqual(self.r.jobs()[0]['state'],'partial');self.assertEqual(self.r.module(p['id'],'total_return')['snapshot_id'],old)

    def test_same_revision_cache_does_not_keep_last_week_or_last_year_returns(self):
        p=self.populate()
        with patch.object(providers,'cutoff',return_value='2026-10-30'):first=self.r.detail(p['id'])
        self.assertIsNotNone(first['changes']['weekly']);self.assertIsNotNone(first['changes']['ytd'])
        with patch.object(providers,'cutoff',return_value='2026-11-06'):later=self.r.detail(p['id'])
        self.assertIsNone(later['changes']['weekly']);self.assertEqual(first['research_revision'],later['research_revision'])
        with patch.object(providers,'cutoff',return_value='2027-01-05'):newyear=self.r.detail(p['id'])
        self.assertIsNone(newyear['changes']['ytd'])

    def test_company_financials_rank_annual_reference_and_scenario_curve(self):
        p=self.populate();d=self.r.detail(p['id'])
        self.assertEqual([r['weight_rank'] for r in d['companies']],[1,2])
        self.assertEqual(d['top10_weight'],'100')
        self.assertEqual(d['companies'][0]['financials']['2025-12-31']['revenue'],'202500')
        years=d['financials']['current_years'];self.assertEqual(len(years),10)
        self.assertEqual(years[0]['period'],'2016-12-31');self.assertEqual(years[-1]['metrics']['revenue']['amount'],'405000')
        body=dict(analysis_id=d['analysis_id'],metric='pe',target_multiple='10',change_ratio='0',accept_fixed_basket_assumption=True,reference_window=5)
        r=self.r.scenario(p['id'],body);self.assertEqual(r['point'],d['latest']['close']);self.assertEqual(len(r['curve']),21)
        self.assertEqual(r['reference_window'],5);self.assertEqual(set(r['reference_points']),{'p10','p25','p50','p75','p90'})
        self.assertEqual(r['curve'][0]['point'],'60');self.assertEqual(r['curve'][-1]['point'],'180')
        changed=self.r.scenario(p['id'],dict(body,change_ratio='0.25'))
        self.assertEqual(changed['point'],'150');self.assertEqual(changed['reference_points'],r['reference_points'])
        short=valuation_payload(p);short['series']['pe']['overall']['rows']=short['series']['pe']['overall']['rows'][-10:]
        self.r.publish(p['id'],'valuations',short);latest=self.r.detail(p['id'])
        limited=self.r.scenario(p['id'],dict(body,analysis_id=latest['analysis_id']))
        self.assertEqual(limited['reference_points'],{})


class CombinedPlanTests(unittest.TestCase):
    def test_atomic_labels_region_preservation_scope_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(tmp,testing=True,auto_sync=False)
            with TestClient(app) as c:
                b=c.get('/api/bootstrap').json();h={'X-CSRF-Token':b['csrf_token']};s=app.state.service
                p=s.save_portfolio(dict(name='合成计划',planned_amount='1000',start_date='2024-01-01'))
                e=s.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2024-01-02',instrument=dict(market='CN',code='600201'),quantity='1',gross_amount='10',fx_rate='1'))
                iid=e['instrument_id'];base=f'/api/portfolios/{p["id"]}/instruments/{iid}'
                s.attach_instrument(p['id'],iid,dict(target_pct='10',asset_class='权益',region='国内'))
                # Preserve the exact hidden historical alias, including unknown fields.
                with s.store.connection() as db:
                    a=json.loads(db.execute('SELECT data FROM allocations').fetchone()[0]);a['region']='国内';a['legacy_note']='keep';db.execute('UPDATE allocations SET data=?',(json_dumps(a),));db.commit()
                context=c.get(base+'/labels').json();before=s.store.state();body=dict(target_pct='20',asset_class='债券',holding_labels=dict(scope='A股',direction='红利',detail='合成'),expected_revision=context['revision'])
                r=c.put(base,headers=h,json=body);self.assertEqual(r.status_code,200,r.text)
                fresh=c.get(base+'/labels').json();self.assertEqual(fresh['allocation']['region'],'国内');self.assertEqual(fresh['allocation']['legacy_note'],'keep');self.assertEqual(fresh['labels']['detail'],'合成')
                self.assertEqual(s.store.state()['events'],before['events']);self.assertEqual(fresh['revision'],context['revision']+1)
                state=s.store.state();self.assertEqual(c.put(base,headers=h,json=body).status_code,409);self.assertEqual(s.store.state(),state)
                body.update(expected_revision=fresh['revision'],holding_labels=dict(scope='A股',direction='invalid',detail='x'))
                self.assertEqual(c.put(base,headers=h,json=body).status_code,422);self.assertEqual(s.store.state(),state)
                self.assertEqual(c.put(base,headers=h,json=dict(target_pct='30')).status_code,200)
                self.assertEqual(c.get(base+'/labels').json()['labels']['detail'],'合成')


class HistoryTests(unittest.TestCase):
    def test_history_different_baskets_count_coverage_and_conflict_gap(self):
        from backend.research.history import totals
        baskets=[dict(period='2023-12-31',status='official',members=[dict(code='a',industry='工业')]),dict(period='2024-12-31',status='official',members=[dict(code='b',industry='工业')]),dict(period='2025-12-31',status='conflict',members=[])]
        rows=[dict(code='a',period='2023-12-31',profit='100'),dict(code='b',period='2024-12-31',profit='200')]
        r=totals(rows,baskets);self.assertEqual(r[1]['metrics']['profit']['growth'],'1');self.assertIsNone(r[1]['metrics']['profit']['weight_coverage']);self.assertIsNone(r[2]['metrics']['profit']['amount'])


class ResearchApiTests(unittest.TestCase):
    def test_authenticated_api_revision_projection_scenario_and_readonly_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=create_app(tmp,testing=True,auto_sync=False)
            with (
                patch.object(app.state.research.updates, 'start'),
                patch.object(app.state.research.access, 'request', side_effect=lambda endpoint, params: transport(endpoint, params)[1]['data']),
                TestClient(app) as c,
            ):
                b=c.get('/api/bootstrap').json();h={'X-CSRF-Token':b['csrf_token']}
                self.assertEqual(c.post('/api/research/indices/resolve',json={'code':'000905'}).status_code,403)
                preview=c.post('/api/research/indices/resolve',headers=h,json={'code':'000905'}).json()
                a=c.put('/api/research/collection',headers=h,json=dict(code='000905',modules=['price','valuations','memberships'],history_start=None,enabled=True,expected_research_revision=0));self.assertEqual(a.status_code,200,a.text)
                iid=a.json()['id'];r=app.state.research;p=r.profile(iid)
                r.publish(iid,'price',price_payload(p,'price'));r.publish(iid,'valuations',valuation_payload(p));r.publish(iid,'memberships',member_payload(p))
                d=c.get('/api/research/indices/'+iid).json();self.assertEqual(c.get('/api/research/indices/'+iid+'/constituents?page_size=1&analysis_id='+d['analysis_id']).json()['total'],2)
                self.assertEqual(c.get('/api/research/indices/'+iid+'/series?analysis_id=old').status_code,409)
                body=dict(analysis_id=d['analysis_id'],basis='price',metric='pe',target_multiple=d['valuation']['pe']['current'],change_ratio='0',accept_fixed_basket_assumption=True)
                calc=c.post('/api/research/indices/'+iid+'/scenarios',headers=h,json=body);self.assertEqual(calc.status_code,200,calc.text);self.assertEqual(calc.json()['point'],d['latest']['close'])
                self.assertEqual(c.post('/api/research/indices/'+iid+'/scenarios',headers=h,json=dict(body,analysis_id='old')).status_code,409)
                self.assertEqual(c.get('/api/research/indices/'+iid+'?asof=2020-01-01').status_code,422)
                self.assertEqual(c.get('/api/bootstrap').json()['revision'],b['revision'])
            guarded=create_app(Path(tmp)/'readonly',testing=True,auto_sync=False,read_only=True)
            with TestClient(guarded) as c:self.assertEqual(c.post('/api/data-updates/run',json={}).status_code,503)

class ReviewRegressions(unittest.TestCase):
    setUp=ResearchTests.setUp
    tearDown=ResearchTests.tearDown
    add=ResearchTests.add
    populate=ResearchTests.populate
    def test_tail_gap_is_not_a_complete_window(self):
        rows=price_payload(providers.identity('000905'),'price')['rows'];rows=[r for r in rows if r['date']<='2025-12-31' or r['date']==ASOF]
        self.assertIsNone(drawdown(rows,ASOF,3)['drawdown_ratio'])
        series=dict(rows=[dict(date=r['date'],value=r['close']) for r in rows],frequency='daily')
        self.assertIsNone(valuation(series,ASOF)['windows']['3']['rank'])
    def test_nonpositive_base_does_not_poison_next_year(self):
        from backend.research.history import totals
        baskets=[dict(period=f'{y}-12-31',status='official',members=[dict(code='a',industry='工业')]) for y in (2023,2024,2025)]
        rows=[dict(code='a',period=f'{y}-12-31',profit=v) for y,v in ((2023,'-10'),(2024,'10'),(2025,'20'))]
        r=totals(rows,baskets);self.assertIsNone(r[1]['metrics']['profit']['growth']);self.assertEqual(r[2]['metrics']['profit']['growth'],'1')
    def test_financial_page_uses_immutable_captured_source(self):
        p=self.populate();old=self.r.detail(p['id']);original=self.r.detail
        def swap(*a,**kw):
            d=original(*a,**kw);value=finance_payload(member_payload(p)['rows']);value['rows'][0]['profit']='999';self.r.publish(p['id'],'financials',value);return d
        with patch.object(self.r,'detail',side_effect=swap):page=self.r.projection(p['id'],'financials',analysis_id=old['analysis_id'])
        self.assertEqual(page['analysis_id'],old['analysis_id']);self.assertNotEqual(page['items'][0]['profit'],'999')
    def test_legacy_research_policies_and_nonmanual_triggers_are_ignored(self):
        saved, _ = self.add()
        self.r.recover(False)
        self.legacy.save_portfolio(dict(name='测试调度', planned_amount='1000', start_date='2026-01-01'))
        with self.store.connection() as db:
            for policy in self.r.settings()['policies']:
                enabled = policy['scope'] not in ('quotes', 'fx')
                value = dict(policy, enabled=enabled, cadence='daily')
                db.execute('INSERT INTO data_update_policies VALUES (?,?)', (value['scope'], json_dumps(value)))
            db.commit()
        for trigger in ('schedule', 'startup', 'index_create'):
            with self.assertRaisesRegex(DomainError, '仅支持手动更新'):
                self.r.run(dict(scopes=['valuations'], targets=[saved['id']], request_id=trigger), trigger=trigger)
        self.r.tick('2026-10-30T23:59:00+08:00')
        self.assertFalse(any(job['trigger'] == 'schedule' for job in self.r.jobs()))
        for policy in self.r.settings()['policies']:
            if policy['scope'] not in ('quotes', 'fx'):
                self.assertEqual((policy['enabled'], policy['cadence']), (False, 'manual'))

    def test_replayed_global_body_keeps_original_targets_and_new_day_is_distinct(self):
        self.add();body=dict(scopes=['index_prices'],request_id='stable-global')
        with patch.object(providers,'cutoff',return_value='2026-10-29'):one=self.r.run(body)
        self.add('399006','another-profile')
        with patch.object(providers,'cutoff',return_value='2026-10-30'):
            self.assertEqual(self.r.run(body)['job_id'],one['job_id'])
            two=self.r.run(dict(body,request_id='new-global'))
        self.assertNotEqual(two['job_id'],one['job_id'])
    def test_resource_interrupted_in_flight_is_not_marked_completed(self):
        self.r.run(dict(scopes=['fx'],resources=[dict(scope='fx',currency='HKD')],request_id='inflight-stop'))
        def interrupted(resource):
            self.r.updates.stopping.set();raise DomainError('更新已中断')
        with patch.object(self.r.updates,'execute',side_effect=interrupted):self.r.updates._work()
        job=self.r.jobs()[0];self.assertEqual(job['state'],'interrupted');self.assertEqual(job['completed_resources'],[])
        new=ResearchService(self.store,self.legacy)
        with patch.object(new.updates,'start'):new.recover(True)
        calls=[]
        with patch.object(new.updates,'execute',side_effect=lambda r:calls.append(r) or [dict(scope='fx',resource=r,status='complete')]):new.updates._work()
        self.assertEqual(calls,[dict(scope='fx',currency='HKD')]);self.assertEqual(new.jobs()[0]['state'],'complete')
    def test_restore_invalidates_independent_revisions(self):
        policies=self.r.settings()['policies'];self.r.save_settings(dict(policies=policies,expected_settings_revision=0));self.add();self.r.recover(False)
        with tempfile.TemporaryDirectory() as tmp:
            other=Service(Store(Path(tmp)));r=ResearchService(other.store,other);new_policies=r.settings()['policies'];new_policies[0]['enabled']=False;r.save_settings(dict(policies=new_policies,expected_settings_revision=0));backup=other.backup_download()
        old=self.r.revision();self.legacy.restore(backup);self.r.cache.clear();self.assertGreater(self.r.revision(),old);self.assertGreater(self.r.revision('settings_revision'),1)
        with self.assertRaises(ConflictError):self.r.save_settings(dict(policies=policies,expected_settings_revision=1))

class ProviderContractTests(unittest.TestCase):
    def test_historical_industry_uses_latest_valid_observation_and_rejects_bad_scope(self):
        member=dict(code='600201',market='SH');period='2025-12-31'
        older=dict(SECURITY_CODE='600201',TRADE_DATE='2025-12-26',BOARD_NAME='旧分类')
        latest=dict(older,TRADE_DATE='2025-12-30',BOARD_NAME='信息技术')
        with patch.object(providers,'request',return_value=(dict(result=dict(data=[latest,older],pages=1)),{})):
            self.assertEqual(providers.historical_industries([member],period)['rows'],[dict(code='600201',date='2025-12-30',industry='信息技术')])
        with patch.object(providers,'request',return_value=(dict(result=None,code=9201,message='返回数据为空'),{})):
            self.assertEqual(providers.historical_industries([member],period)['rows'],[])
        with patch.object(providers,'request',return_value=(dict(result=None,code=9501,message='参数错误'),{})):
            with self.assertRaises(providers.market.ProviderError):providers.historical_industries([member],period)
        for data,pages in [([dict(latest,TRADE_DATE='2026-01-01')],1),([dict(latest,SECURITY_CODE='600202')],1),([latest,latest],1),([latest],2)]:
            with self.subTest(data=data,pages=pages),patch.object(providers,'request',return_value=(dict(result=dict(data=data,pages=pages)),{})):
                with self.assertRaises(providers.market.ProviderError):providers.historical_industries([member],period)

    def test_historical_cash_flow_unknown_industry_is_not_complete(self):
        from backend.research.history import totals
        members=[dict(code='a',industry='工业'),dict(code='b',industry='银行'),dict(code='c',industry='')]
        baskets=[dict(period=p,members=members,status='checked') for p in ('2024-12-31','2025-12-31')]
        rows=[dict(code=m['code'],period=b['period'],operating_cf='10') for b in baskets for m in members]
        for year in totals(rows,baskets):
            v=year['metrics']['operating_cf']
            self.assertEqual((v['amount'],v['count'],v['denominator'],v['excluded_count'],v['excluded_unknown_industry']),('10',1,1,2,1))
            self.assertEqual(v['status'],'insufficient_coverage');self.assertIsNone(v['growth'])

    def test_financial_batches_are_bounded_and_merge_both_reports_per_company(self):
        import re,threading,time
        from urllib.parse import urlparse,parse_qs
        members=[dict(code=str(600000+n),market='SH') for n in range(101)]
        lock=threading.Lock();active=0;maximum=0
        def fetch(url):
            nonlocal active,maximum
            with lock:active+=1;maximum=max(maximum,active)
            try:
                time.sleep(.02)
                params=parse_qs(urlparse(url).query);codes=re.findall(r'"(\d{6})"',params['filter'][0]);cash=params['reportName'][0]=='RPT_DMSK_FN_CASHFLOW'
                rows=[dict(SECURITY_CODE=c,REPORT_DATE='2025-12-31',NOTICE_DATE='2026-04-01',NETCASH_OPERATE='8') if cash else dict(SECURITY_CODE=c,REPORTDATE='2025-12-31',NOTICE_DATE='2026-04-01',TOTAL_OPERATE_INCOME='100',PARENT_NETPROFIT='10') for c in codes]
                return dict(result=dict(data=rows,pages=1)),dict(url=url)
            finally:
                with lock:active-=1
        with patch.object(providers,'request',side_effect=fetch):value=providers.financials(members,'2026-11-04')
        self.assertGreater(maximum,1);self.assertLessEqual(maximum,3)
        self.assertEqual([r['code'] for r in value['rows']],[m['code'] for m in members])
        self.assertTrue(all((r['revenue'],r['profit'],r['operating_cf'])==('100','10','8') for r in value['rows']))
        with patch.object(providers,'request',side_effect=TimeoutError('synthetic failure')):
            with self.assertRaises(TimeoutError):providers.financials(members,'2026-11-04')

    def test_xlsx_wrong_declared_dimension_does_not_drop_official_rows(self):
        import openpyxl,re
        from backend.research.providers import excel_rows
        book=openpyxl.Workbook();sheet=book.active;sheet.append(['日期','代码']);sheet.append(['2026-09-30','600201']);sheet.append(['2026-09-30','000201']);raw=io.BytesIO();book.save(raw)
        fixed=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as src,zipfile.ZipFile(fixed,'w') as dst:
            for info in src.infolist():
                value=src.read(info.filename)
                if info.filename=='xl/worksheets/sheet1.xml':value=re.sub(rb'<dimension ref="[^"]+"',b'<dimension ref="A1"',value)
                dst.writestr(info,value)
        self.assertEqual(len(excel_rows(fixed.getvalue())),3)
    def test_stock_history_preserves_loss_zero_and_requested_date(self):
        from datetime import datetime
        from backend.common import ZONE
        stamp=int(datetime(2026,10,30,15,tzinfo=ZONE).timestamp())
        response=dict(result=dict(data=[dict(SECURITY_CODE='600201',PE_TTM=-3,PB_MRQ=0,TRADE_DATE=ASOF,BOARD_NAME='银行')]))
        with patch.object(providers,'request',return_value=(response,{})):
            r=providers.stock_valuations([dict(code='600201',market='SH')],ASOF)['rows'][0]
        self.assertEqual((r['pe'],r['pe_field'],r['pe_status']),('-3','PE_TTM','loss'));self.assertEqual(r['pb_status'],'zero_or_missing');self.assertEqual(r['date'],ASOF)
    def test_intraday_snapshot_is_not_relabelled_as_prior_close(self):
        from datetime import datetime
        from backend.common import ZONE
        stamp=int(datetime(2026,10,31,10,tzinfo=ZONE).timestamp());response=dict(result=dict(data=[dict(SECURITY_CODE='600201',PE_TTM=3,PB_MRQ=1,TRADE_DATE='2026-10-31')]))
        with patch.object(providers,'request',return_value=(response,{})):
            with self.assertRaises(providers.market.ProviderError):providers.stock_valuations([dict(code='600201',market='SH')],ASOF)

class DurableOutcomeTests(unittest.TestCase):
    def test_results_survive_recent_twenty_presentation_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            s=Service(Store(Path(tmp)));r=ResearchService(s.store,s)
            with patch.object(r.updates,'start'):
                ids=[]
                for n in range(25):
                    queued=r.run(dict(scopes=['fx'],request_id='retained-'+str(n),resources=[dict(scope='fx',currency='HKD')]))
                    job=r.updates.get(queued['job_id']);job['items']=[dict(scope='fx',resource=dict(scope='fx',currency='HKD'),status='complete')];r.updates.save(job,'complete');ids.append(job['id'])
                self.assertEqual(len(r.jobs()),20);self.assertEqual(r.updates.get(ids[0])['state'],'complete')
                again=r.run(dict(scopes=['fx'],request_id='retained-0',resources=[dict(scope='fx',currency='HKD')]))
                self.assertEqual(again['job_id'],ids[0]);self.assertEqual(len(r.jobs()),20)
                with s.store.connection() as db:validate_backup(db)

class PriceStorageTests(unittest.TestCase):
    setUp=ResearchTests.setUp
    tearDown=ResearchTests.tearDown
    add=ResearchTests.add
    populate=ResearchTests.populate
    def test_next_day_reuses_old_years_and_restore_rejects_missing_block(self):
        p=self.populate();before=self.r.detail(p['id']);old=self.r.module(p['id'],'price')
        with self.store.connection() as db:n=db.execute('SELECT COUNT(*) FROM research_price_chunks').fetchone()[0]
        value=price_payload(p,'price');value['rows'].append(dict(date='2026-11-02',close='121'));value['effective_date']='2026-11-02';self.r.publish(p['id'],'price',value)
        with self.store.connection() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM research_price_chunks').fetchone()[0],n+1)
        self.assertEqual(self.r.module(p['id'],'price')['rows'],value['rows'])
        archive=self.legacy.backup_download();target=Service(Store(Path(self.temp.name)/'chunk-restored'));target.restore(archive);r=ResearchService(target.store,target)
        self.assertEqual(r.module(p['id'],'price')['rows'],value['rows'])
        with self.store.connection() as db:
            db.execute('DELETE FROM research_price_chunks WHERE sha256=(SELECT sha256 FROM research_price_chunks LIMIT 1)');db.commit()
            with self.assertRaises(DomainError):validate_backup(db)


if __name__=='__main__':unittest.main()


class RegisteredIdentityTests(unittest.TestCase):
    def test_dividend_mapping_is_shared_by_price_and_total_return_inputs(self):
        for code in ('000922','H00922'):
            profile=providers.identity(code)
            self.assertEqual(profile['code'],'000922')
            self.assertEqual(profile['provider'],'csi')
    def test_registered_csi_pairs_resolve_bidirectionally(self):
        for code,tr,name in [('000510','000510CNY010','中证A500'),('000993','H00993','全指信息'),
                             ('930955','H20955','红利低波100'),('399998','H20596','中证煤炭'),('000991','H00991','全指医药')]:
            with self.subTest(code=code):
                price=providers.identity(code);total=providers.identity(tr.lower())
                self.assertEqual((price['code'],price['return_code'],price['name']),(code,tr,name))
                self.assertEqual((total['code'],total['return_code']),(code,tr))
                self.assertEqual(price['input_basis'],'price');self.assertEqual(total['input_basis'],'total_return')
                self.assertIn(code+'factsheet.pdf',price['method_url'])
    def test_unknown_variants_are_not_guessed_from_prefix(self):
        for code in ('H00510','00051','H993','000993FAKE','H30955','H399998','H00998','DJUSDIV'):
            with self.subTest(code=code),self.assertRaises(providers.market.ProviderError):providers.identity(code)
    def test_a500_official_return_code_and_publication_boundary_reach_adapter(self):
        profile=providers.identity('000510CNY010')
        raw=[dict(indexCode=profile['return_code'],tradeDate=d,close='100') for d in ['1999-01-01','2020-01-02','2024-09-23','2026-10-30']]
        with patch.object(providers,'request',return_value=(dict(data=raw),{})) as request:
            result=providers.prices(profile,'total_return',ASOF)
        self.assertIn('indexCode=000510CNY010',request.call_args.args[0])
        self.assertEqual([r['date'] for r in result['rows']],['2024-09-23','2026-10-30'])
        self.assertIsNotNone(result['theoretical_history_note'])
    def test_new_csi_daily_series_use_exact_codes_and_launch_boundaries(self):
        for code in ('930955','399998','000991'):
            profile=providers.identity(code)
            self.assertEqual(profile['provider'],'csi')
            for basis in ('price','total_return'):
                series=profile['return_code'] if basis=='total_return' else code
                raw=[dict(indexCode=series,tradeDate=d,close='100') for d in ('1999-01-01','2010-01-04',profile['launch'],ASOF,'2026-11-03')]
                with self.subTest(code=code,basis=basis),patch.object(providers,'request',return_value=(dict(data=raw),{})) as request:
                    result=providers.prices(profile,basis,ASOF)
                    self.assertIn(providers.CSI+'/csindex-home/perf/index-perf?',request.call_args.args[0])
                    self.assertIn('indexCode='+series,request.call_args.args[0])
                    self.assertEqual(result['series_code'],series)
                    self.assertEqual([r['date'] for r in result['rows']],[profile['launch'],ASOF])
                with patch.object(providers,'request',return_value=(dict(data=[dict(indexCode='WRONG',tradeDate=ASOF,close='100')]),{})):
                    with self.assertRaises(providers.market.ProviderError):providers.prices(profile,basis,ASOF)
    def test_coal_legacy_source_mapping_remains_documented(self):
        p=providers.identity('399998');self.assertEqual(p['provider'],'csi')
    def test_beijing_member_preserves_official_exchange_and_uses_a_share_adapters(self):
        profile=providers.identity('000991')
        row=[ASOF,profile['code'],'合成医药指数',None,'920201','合成北交所公司',None,'北京证券交易所',None,'100']
        with patch('requests.get') as get,patch.object(providers,'excel_rows',return_value=[[],row]):
            get.return_value.content=b'synthetic weights'
            member=providers.constituents(profile,ASOF)['rows'][0]
        self.assertEqual((member['code'],member['market'],member['exchange']),('920201','BJ','北京证券交易所'))
        stamp=int(datetime.fromisoformat(ASOF+'T15:00:00+08:00').timestamp())
        quote=dict(result=dict(data=[dict(SECURITY_CODE='920201',PE_TTM=20,PB_MRQ=2,TRADE_DATE=ASOF,BOARD_NAME='医药')]))
        with patch.object(providers,'request',return_value=(quote,{})) as request:
            value=providers.stock_valuations([member],ASOF)
        self.assertIn('920201',request.call_args.args[0]);self.assertIn('RPT_VALUEANALYSIS_DET',request.call_args.args[0])
        self.assertEqual(value['rows'][0]['code'],'920201')
        with patch.object(providers,'request',return_value=(dict(result=dict(data=[],pages=1)),{})) as request:
            value=providers.financials([member],ASOF)
        self.assertEqual(request.call_count,2);self.assertEqual(value['rows'],[])


class RetiredValuationSelectionTests(unittest.TestCase):
    def test_legacy_write_routes_are_retired_without_network_or_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            app=create_app(root,testing=True,auto_sync=False)
            with TestClient(app) as c,patch('requests.Session.post') as post,patch('requests.get') as get:
                headers={'X-CSRF-Token':c.get('/api/bootstrap').json()['csrf_token']}
                for path in ('/api/research/indices','/api/research/indices/valuation-preview'):
                    self.assertEqual(c.post(path,headers=headers,json={}).status_code,410)
                post.assert_not_called();get.assert_not_called();self.assertEqual(app.state.research.profiles(),[])
