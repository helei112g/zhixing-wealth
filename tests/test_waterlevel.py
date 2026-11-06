import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import ConflictError, DomainError, json_dumps
from backend.deployment_guard import capture, verify
from backend.migration import SCHEMA
from backend.research.service import ResearchService, validate_backup
from backend.service import Service
from backend.store import Store
from backend.waterlevel import metrics, sources
from backend.waterlevel.export import render
from tests.waterlevel_fixtures import ASOF, history, payload, seed_indices, drop_tables


class MetricTests(unittest.TestCase):
    def test_strict_rank_ties_and_complete_span(self):
        rows = history('10')
        result = metrics.analyze(rows, ASOF, positive=True)
        self.assertEqual(result['windows']['10']['percentile_ratio'], '0')
        short = metrics.analyze(rows[-100:], ASOF)
        self.assertIsNone(short['windows']['5']['percentile_ratio'])
        rows[-1]['value'] = '20'
        result = metrics.analyze(rows, ASOF)
        sample = result['windows']['10']['count']
        self.assertAlmostEqual(float(result['windows']['10']['percentile_ratio']), (sample-1)/sample)

    def test_monthly_mean_excludes_incomplete_month_and_uses_last_observation(self):
        rows = history('10')
        rows[-1]['value'] = '1000'
        result = metrics.analyze(rows, ASOF)
        self.assertEqual(result['means']['5']['value'], '10')
        self.assertEqual(result['means']['5']['through_month'], '2026-09')
        self.assertEqual(result['means']['5']['deviation_ratio'], '99')
        self.assertEqual(result['chart'][-1]['mean5'], '10')
        self.assertEqual(result['chart'][-1]['mean10'], '10')
        for row in rows:
            if row['date'].startswith('2026-09'):
                row['value'] = '30'
        self.assertAlmostEqual(float(metrics.analyze(rows, ASOF)['means']['5']['value']), (59*10+30)/60)

    def test_missing_month_is_not_filled_and_chart_breaks(self):
        rows = [row for row in history() if not row['date'].startswith('2025-04')]
        result = metrics.analyze(rows, ASOF)
        self.assertIsNone(result['means']['5']['value'])
        self.assertEqual(result['means']['5']['count'], 59)
        self.assertIsNone(next(row for row in result['chart'] if row['date'].startswith('2025-04'))['value'])
        self.assertIsNone(result['chart'][-1]['mean5'])
        self.assertIsNone(result['chart'][-1]['mean10'])

    def test_invalid_last_observation_does_not_resurrect_an_older_value(self):
        for value in ('0', '-2', None):
            rows = history()
            rows[-1]['value'] = value
            result = metrics.analyze(rows, ASOF, positive=True)
            self.assertEqual(result['current'], value)
            self.assertEqual(result['status'], 'invalid_current')
            self.assertIsNone(result['windows']['10']['percentile_ratio'])
        rows = history()
        next(row for row in reversed(rows) if row['date'].startswith('2026-09'))['value'] = None
        self.assertIsNone(metrics.analyze(rows, ASOF)['means']['5']['value'])

    def test_cutoff_and_staleness(self):
        rows = history()
        result = metrics.analyze(rows, '2020-10-30')
        self.assertEqual(result['date'], '2020-10-30')
        self.assertIsNone(result['windows']['10']['percentile_ratio'])
        self.assertEqual(metrics.analyze(rows, '2026-12-01')['status'], 'stale')
        self.assertEqual(metrics.years_before('2024-02-29', 5), date(2019,2,28))

    def test_spreads_match_dates_units_and_sign(self):
        left = [dict(date='2026-10-29',value='7'),dict(date=ASOF,value='9')]
        right = [dict(date='2026-10-29',value='4')]
        self.assertEqual(metrics.difference(left,right),[dict(date='2026-10-29',value='3')])
        self.assertEqual(metrics.difference(left,right,factor=100)[0]['value'],'300')
        self.assertEqual(metrics.earnings_spread([dict(date='2026-10-29',value='20')],right)[0]['value'],'1')
        invalid = metrics.earnings_spread([dict(date='2026-10-29',value='-20')],right)
        self.assertEqual(invalid,[dict(date='2026-10-29',value=None)])
        self.assertEqual(metrics.analyze(invalid,ASOF)['status'],'invalid_current')

    def test_inverse_scores_median_and_missing_classes(self):
        stats = metrics.analyze(history(), ASOF)
        value = metrics.score([dict(id='a',label='rate',category='rates',inverse=True,statistics=stats)])
        self.assertEqual(value['overall']['score'],'100')
        self.assertEqual(value['overall']['included'],1)
        self.assertIsNone(value['categories']['equity']['score'])
        stats['status']='stale'
        self.assertIsNone(metrics.score([dict(id='a',label='rate',category='rates',statistics=stats)])['overall']['score'])

    def test_comparisons_align_months_use_common_base_and_preserve_gaps(self):
        valuation = [dict(date='2020-01-31', value='20', mean5='15', mean10=None),
                     dict(date='2020-02-28', value=None), dict(date='2020-03-31', value='25')]
        sh = [dict(date='2020-01-23',value='1000'), dict(date='2020-03-30',value='1200')]
        sz = [dict(date='2020-02-28',value='500'), dict(date='2020-03-31',value='550')]
        bond = [dict(date='2020-01-30',value='50')]
        value = metrics.comparison(valuation, bond, sh, sz, asof='2020-04-01')
        self.assertEqual(value['base_month'], '2020-03')
        self.assertIsNone(value['chart'][0]['sh'])
        self.assertEqual(value['chart'][2]['sh'], '100')
        self.assertEqual(value['chart'][2]['sz'], '100')
        self.assertIsNone(value['chart'][1]['value'])
        self.assertIsNone(value['chart'][1]['bond_inverse'])
        self.assertEqual(value['chart'][0]['bond_inverse_date'], '2020-01-30')
        self.assertEqual(value['chart'][0]['mean5'], '15')
        self.assertEqual([row['historical_mean'] for row in value['chart']], ['20', '20', '22.5'])
        partial = metrics.comparison(valuation, bond, sh, sz, asof='2020-03-31')
        self.assertEqual(partial['chart'][-1]['historical_mean'], '20')


class SourceTests(unittest.TestCase):
    def test_full_market_uses_exact_code_and_six_fields(self):
        access = Mock()
        fields = sources.FULL_MARKET_FIELDS
        values = {field: n+10 for n, field in enumerate(fields.values())}
        access.request.return_value = [dict(stockCode='1000002', date=ASOF, **values)]
        previous = payload('full_market')
        result = sources.collect('full_market', ASOF, previous, access=access)
        endpoint, params = access.request.call_args.args
        self.assertEqual(endpoint, 'cn/index/fundamental')
        self.assertEqual(params['stockCodes'], ['1000002'])
        self.assertEqual(params['endDate'], ASOF)
        self.assertEqual(params['metricsList'], list(fields.values()))
        self.assertEqual({key: rows[-1]['value'] for key, rows in result['series'].items()},
                         {key: str(values[field]) for key, field in fields.items()})
        access.request.return_value[0]['stockCode'] = '000985'
        with self.assertRaises(DomainError):
            sources.collect('full_market', ASOF, previous, access=access)

    def test_full_market_rejects_missing_method_instead_of_substituting(self):
        access = Mock()
        values = dict.fromkeys(sources.FULL_MARKET_FIELDS.values(), 10)
        values.pop('pb.ew')
        access.request.return_value = [dict(stockCode='1000002', date=ASOF, **values)]
        with self.assertRaisesRegex(DomainError, 'pb.ew'):
            sources.collect('full_market', ASOF, payload('full_market'), access=access)

    def test_composites_request_correct_price_index_codes(self):
        reader = sources.Reader()
        with patch.object(reader, 'get', return_value=b'[{"day":"2026-10-30","close":"12345"}]') as get:
            result = sources.composites(reader)
        self.assertIn('symbol=sh000001',get.call_args_list[0].args[0])
        self.assertIn('symbol=sz399106',get.call_args_list[1].args[0])
        self.assertEqual(result['sz_composite'][0]['value'],'12345')

    def test_credit_selects_exact_aaa_note_and_three_year_column(self):
        html = '<table><tr><td>曲线名称</td><td>日期</td><td>1年</td><td>3年</td></tr>'
        for label, value in [('中债国债收益率曲线','2'),('中债中短期票据收益率曲线(AAA)','3'),('中债商业银行普通债收益率曲线(AAA)','9')]:
            html += f'<tr><td>{label}</td><td>{ASOF}</td><td>77</td><td>{value}</td></tr>'
        reader = sources.Reader()
        with patch.object(reader,'html',return_value=sources.Document(html)):
            value=sources.credit(reader,'2026-01-01',ASOF)
        self.assertEqual(value['aaa_3y'][0]['value'],'3')
        self.assertEqual(value['gov_3y'][0]['value'],'2')

    def test_csv_keeps_fixing_identity_and_rejects_changed_columns(self):
        reader=sources.Reader()
        with patch.object(reader,'get',side_effect=[b'2026-10-30,,,,1,2,3',b'2026-10-30,4,5,6']):
            value=sources.repo(reader)
        self.assertEqual(value['fr007'][0]['value'],'2')
        self.assertEqual(value['fdr007'][0]['value'],'5')
        with patch.object(reader,'get',return_value=b'2026-10-30,1,2'):
            with self.assertRaises(DomainError):sources.repo(reader)

    def test_em_rejects_partial_pages_and_skips_empty_calendar_observations(self):
        reader=sources.Reader()
        first=dict(success=True,result=dict(pages=2,data=[dict(REPORT_DATE=ASOF,x='3')]))
        with patch.object(reader,'get',side_effect=[json.dumps(first).encode(),b'{"success":false}']):
            with self.assertRaises(DomainError):sources.em_series(reader,'synthetic',{'a':'x'},'REPORT_DATE','2020-01-01',ASOF)
        first['result']['pages']=1
        first['result']['data'].append(dict(REPORT_DATE='2026-10-31',x=None))
        with patch.object(reader,'get',return_value=json.dumps(first).encode()):
            self.assertEqual(len(sources.em_series(reader,'synthetic',{'a':'x'},'REPORT_DATE','2020-01-01',ASOF)['a']),1)

    def test_pmi_calendar_uses_actual_dates_and_delayed_february(self):
        rows=''
        for label in ('采购经理指数月度报告','居民消费价格指数月度报告'):
            dates=['31/六','……','4/三 31/二']+['9/一']*9 if '采购' in label else ['9/一']*12
            rows+='<tr><td>1</td><td>'+label+'</td>'+''.join('<td>'+cell+'</td>' for cell in dates)+'</tr>'
        document=sources.Document('2026年国家统计局主要统计信息发布日程表<table>'+rows+'</table>')
        reader=sources.Reader()
        with patch.object(reader,'html',return_value=document):value=sources.calendar(reader,2026)
        self.assertIn(dict(group='pmi',date='2026-03-04',period='2026-02'),value['events'])
        self.assertIn(dict(group='pmi',date='2026-03-31',period='2026-03'),value['events'])

    def test_reader_rejects_nonpublic_redirect_targets_before_request(self):
        reader=sources.Reader()
        with patch('requests.get') as request:
            for url in ('http://127.0.0.1/','https://www.pbc.gov.cn.evil.example/','https://u:p@www.pbc.gov.cn/'):
                with self.assertRaises(DomainError):reader.get(url)
        request.assert_not_called()

    def test_social_financing_reads_direct_official_stock_growth_columns(self):
        directory=sources.Document('<a href="/year.html">2026年统计数据</a>')
        section=sources.Document('<a href="/social.html">社会融资规模</a>')
        links=sources.Document('社会融资规模存量统计表<a href="/stock.htm">htm</a>')
        months=''.join(f'<td>2026.{month}</td>' for month in range(1,13))
        header='<td>存量</td><td>增速（%）</td>'*12
        # Deliberately different stock and growth values; never calculate growth from flows.
        values='<td>123</td><td>7</td>'*12
        document=sources.Document(f'<table><tr><td></td>{months}</tr><tr><td>项目 Items</td>{header}</tr>'
                                  f'<tr><td>社会融资规模存量 Aggregate Financing</td>{values}</tr></table>')
        reader=sources.Reader()
        with patch.object(reader,'html',side_effect=[directory,section,links,document]):
            result=sources.social_financing(reader,'2026-01-01',ASOF)
        self.assertEqual(len(result['sf_stock_yoy']),10)
        self.assertEqual(result['sf_stock_yoy'][0]['value'],'7')
        self.assertEqual(result['sf_stock_yoy'][0]['source_url'],'https://www.pbc.gov.cn/stock.htm')

    def test_treasury_explicit_null_and_wrong_xml(self):
        raw=b'''<feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><m:properties><d:NEW_DATE>2026-10-30</d:NEW_DATE><d:BC_2YEAR>2</d:BC_2YEAR><d:BC_10YEAR>3</d:BC_10YEAR><d:BC_30YEAR/></m:properties></feed>'''
        reader=sources.Reader()
        with patch.object(reader,'get',return_value=raw):
            value=sources.treasury(reader,'2026-01-01',ASOF)
        self.assertEqual(value['us_10y'][0]['value'],'3')
        self.assertIsNone(value['us_30y'][0]['value'])
        with patch.object(reader,'get',return_value=b'<!DOCTYPE html><html/>'):
            with self.assertRaises(DomainError):sources.treasury(reader,'2026-01-01',ASOF)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'data')
        self.research=ResearchService(self.store,Service(self.store))
        self.market=self.research.waterlevel
        self.start=patch.object(self.research.updates,'start')
        self.start.start()

    def tearDown(self):
        self.start.stop()
        self.research.shutdown()
        self.temp.cleanup()

    def populate(self):
        seed_indices(self.research)
        with patch.object(sources,'collect',side_effect=payload):
            for group in sources.GROUPS:self.market.collect(group,ASOF)

    def test_empty_read_and_defaults_do_not_fetch_or_write(self):
        before=capture(self.store.directory)
        with patch.object(sources,'collect') as fetch:
            value=self.market.dashboard(asof=ASOF)
            self.research.tick('2026-10-30T23:59:00+08:00')
        self.assertEqual(before,capture(self.store.directory))
        self.assertEqual(value['scores']['overall']['included'],0)
        self.assertFalse(self.market.settings()['automatic'])
        fetch.assert_not_called()

    def test_methods_do_not_change_fixed_scores_and_short_indices_have_no_long_means(self):
        self.populate()
        one=self.market.dashboard('mcw',ASOF)
        two=self.market.dashboard('ew',ASOF)
        positive=self.market.dashboard('ewpvo',ASOF)
        self.assertEqual(one['scores'],two['scores'])
        self.assertEqual(one['scores'],positive['scores'])
        self.assertEqual(positive['indices'][0]['metrics']['pe']['current'],'15')
        self.assertNotEqual(one['comparisons']['pe']['chart'][-1]['value'],positive['comparisons']['pe']['chart'][-1]['value'])
        self.assertEqual(one['scores']['categories']['equity']['included'],7)
        self.assertEqual(two['indices'][0]['metrics']['pe']['status'],'invalid_current')
        self.assertIsNone(next(row for row in one['indices'] if row['id']=='csi:930050')['metrics']['pe']['means']['5']['value'])
        self.assertEqual(next(row for row in one['series'] if row['id']=='money_gap')['statistics']['current'],'3')
        self.assertEqual(next(row for row in one['series'] if row['id']=='credit_spread')['statistics']['current'],'100')

    def test_failed_or_cancelled_collection_preserves_current_snapshot(self):
        with patch.object(sources,'collect',side_effect=payload):self.market.collect('credit',ASOF)
        before=self.market.source('credit')
        with patch.object(sources,'collect',side_effect=DomainError('synthetic source failed')):
            with self.assertRaises(DomainError):self.market.collect('credit',ASOF)
        self.assertEqual(before,self.market.source('credit'))
        self.research.updates.stopping.set()
        with patch.object(sources,'collect',side_effect=payload):
            with self.assertRaises(DomainError):self.market.collect('credit',ASOF)
        self.assertEqual(before,self.market.source('credit'))

    def test_mutated_caller_data_cannot_change_cached_fact_package(self):
        self.populate()
        one=self.market.dashboard(asof=ASOF)
        one['indices'][0]['metrics']['pe']['chart'].clear()
        self.assertTrue(self.market.dashboard(asof=ASOF)['indices'][0]['metrics']['pe']['chart'])

    def test_failures_invalidate_view_without_revision_and_exclude_affected_scores(self):
        self.populate()
        before=self.market.dashboard(asof=ASOF)
        version=self.market.status()
        failures=[dict(items=[dict(resource=dict(scope='valuations',target='csi:000300',module='valuations'),
                                  status='error',error='synthetic valuation failure'),
                             dict(resource=dict(scope='market',group='credit'),status='error',error='synthetic credit failure')])]
        with patch.object(self.research.updates,'jobs',return_value=failures):
            status=self.market.status()
            failed=self.market.dashboard(asof=ASOF)
        self.assertEqual(status['research_revision'],version['research_revision'])
        self.assertNotEqual(status['errors_version'],version['errors_version'])
        self.assertNotEqual(failed['analysis_id'],before['analysis_id'])
        self.assertEqual(failed['scores']['categories']['equity']['included'],5)
        self.assertEqual(failed['scores']['categories']['credit']['included'],0)
        self.assertEqual(failed['indices'][0]['metrics']['pe']['current'],'10')
        self.assertEqual(failed['indices'][0]['metrics']['pe']['status'],'source_error')
        self.assertEqual(next(row for row in failed['series'] if row['id']=='erp')['statistics']['status'],'source_error')
        successes=copy.deepcopy(failures)
        for item in successes[0]['items']:item.update(status='complete',error=None)
        with patch.object(self.research.updates,'jobs',return_value=successes+failures):
            restored=self.market.dashboard(asof=ASOF)
        self.assertEqual(restored['scores'],before['scores'])

    def test_frozen_report_does_not_change_after_later_source_publication(self):
        self.populate()
        value=self.market.dashboard(asof=ASOF)
        body=dict(analysis_id=value['analysis_id'],method='mcw',asof=ASOF,expected_research_revision=value['research_revision'])
        frozen=self.market.freeze(body)
        with patch.object(sources,'collect',side_effect=payload):self.market.collect('credit',ASOF)
        self.assertEqual(frozen,self.market.frozen(frozen['snapshot_id']))
        with self.assertRaises(ConflictError):self.market.freeze(body)
        with patch.object(sources,'collect') as fetch:
            html=render(frozen)
        self.assertIn('<svg',html)
        self.assertNotIn('<script',html)
        fetch.assert_not_called()
        with self.store.connection() as db:validate_backup(db)

    def test_settings_revision_guards_and_shared_durable_receipts(self):
        settings=self.market.settings()
        body={key:settings[key] for key in ('automatic','groups','expected_settings_revision')}
        with self.assertRaises(DomainError):self.market.save_settings(dict(body,automatic=True))
        self.market.save_settings(body)
        with self.assertRaises(ConflictError):self.market.save_settings(body)
        first=self.market.update(['credit'],'synthetic-request')
        self.assertEqual(first,self.market.update(['credit'],'synthetic-request'))
        with self.assertRaises(ConflictError):self.market.update(['repo'],'synthetic-request')

    def test_previous_lixinger_market_snapshot_remains_restorable(self):
        from backend.waterlevel.validation import source
        old = payload('full_market')
        old['series'] = {k:v for k,v in old['series'].items() if not k.endswith('_ewpvo')}
        source(old, archival=True)
        with self.assertRaises(DomainError):
            source(old)

    def test_v1_frozen_reports_remain_readable_exportable_and_backup_valid(self):
        from backend.research.snapshots import encode
        old = self.market.dashboard(asof=ASOF)
        old.update(analysis_id='1'*64, method_version='waterlevel-v1')
        old.pop('comparisons')
        sha, raw = encode(old)
        with self.store.connection() as db:
            db.execute('INSERT INTO market_reports VALUES (?,?,?,?)', ('1'*64, ASOF, sha, raw))
            db.commit()
            validate_backup(db)
        frozen = self.market.frozen('1'*64)
        self.assertEqual(frozen['data'],old)
        self.assertIn('waterlevel-v1',render(frozen))

    def test_legacy_market_schedule_and_startup_recovery_are_disabled(self):
        with self.store.connection() as db:
            db.execute("INSERT INTO market_settings VALUES ('settings',?)",
                       (json_dumps(dict(automatic=True,groups=['china_rates'])),))
            db.commit()
        self.assertFalse(self.market.settings()['automatic'])
        with patch.object(sources, 'collect') as fetch:
            self.research.tick('2026-11-05T19:00:00+08:00', 'schedule')
            self.assertFalse(any(r['scope']=='market' for job in self.research.jobs() for r in job['resources']))
            with patch.object(self.research.updates, 'start'):
                receipt=self.market.update(['china_rates'],'manual-only')
                self.research.recover(resume=True)
            self.assertEqual(self.research.updates.get(receipt['job_id'])['state'],'interrupted')
        fetch.assert_not_called()

    def test_additive_schema11_upgrade_preserves_existing_content_and_revisions(self):
        self.research.legacy.save_portfolio(dict(name='合成迁移验证',planned_amount='1000',start_date='2026-01-01'))
        with sqlite3.connect(self.store.path) as db:
            drop_tables(db)
            db.execute("UPDATE meta SET value='11' WHERE key='schema'")
            db.execute("UPDATE alembic_version SET version_num='0010_monthly_expenses'")
        before=capture(self.store.directory)
        from backend import storage_audit
        audit_before=storage_audit.capture(self.store.directory)
        Store(self.store.directory,migration_backup=False)
        after=capture(self.store.directory,before)
        self.assertEqual(after['schema'],SCHEMA)
        self.assertTrue(verify(before,after)['preserved'])
        self.assertTrue(all(after['counts'][key]==0 for key in ('market_heads','market_snapshots','market_reports','market_settings')))
        audit_after=storage_audit.capture(self.store.directory)
        self.assertTrue(storage_audit.verify(audit_before,audit_after)['equivalent'])
        for table in ('market_heads','market_snapshots','market_reports','market_settings'):
            corrupted=copy.deepcopy(audit_after)
            corrupted['counts'][table]=1
            corrupted['business_table_hashes'][table]=storage_audit.digest(['unexpected'])
            with self.assertRaises(DomainError):storage_audit.verify(audit_before,corrupted)
            corrupted=copy.deepcopy(audit_after)
            del corrupted['business_table_hashes'][table]
            with self.assertRaises(DomainError):storage_audit.verify(audit_before,corrupted)


class ApiTests(unittest.TestCase):
    def test_local_api_security_facts_freeze_and_no_network_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,testing=True,auto_sync=False)
            with TestClient(app) as client, patch.object(app.state.research.updates,'start'), patch.object(sources,'collect') as fetch:
                boot=client.get('/api/bootstrap').json()
                value=client.get('/api/market-waterlevel?asof='+ASOF).json()
                body=dict(analysis_id=value['analysis_id'],method='mcw',asof=ASOF,expected_research_revision=value['research_revision'])
                self.assertEqual(client.post('/api/market-waterlevel/snapshots',json=body).status_code,403)
                headers={'X-CSRF-Token':boot['csrf_token']}
                response=client.post('/api/market-waterlevel/snapshots',json=body,headers=headers)
                self.assertEqual(response.status_code,200,response.text)
                sid=response.json()['snapshot_id']
                html=client.get('/api/market-waterlevel/snapshots/'+sid+'/export')
                self.assertEqual(html.status_code,200)
                self.assertIn('attachment',html.headers['content-disposition'])
                facts=client.get('/api/market-waterlevel/facts?asof='+ASOF).json()
                self.assertIn('errors_version',client.get('/api/market-waterlevel/revision').json())
                self.assertNotIn('chart',facts['series'][0]['statistics'])
                self.assertNotIn('chart',facts['comparisons']['pe'])
                self.assertIn('chart',client.get('/api/market-waterlevel?asof='+ASOF).json()['series'][0]['statistics'])
                self.assertEqual(client.get('/api/market-waterlevel?method=avg').status_code,422)
                self.assertEqual(client.get('/api/market-waterlevel?asof=2999-01-01').status_code,422)
                fetch.assert_not_called()

    def test_read_only_instance_blocks_new_market_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,testing=True,auto_sync=False,read_only=True)
            with TestClient(app) as client:
                result=client.post('/api/market-waterlevel/updates',json=dict(groups=['credit'],request_id='synthetic'))
                self.assertEqual(result.status_code,503)


if __name__ == '__main__':
    unittest.main()
