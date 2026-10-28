import json,tempfile,unittest
from pathlib import Path
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch
from backend.common import DomainError,REGIONS,tags
from backend.service import Service
from backend.store import Store

class RegionsPiesAndBasisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name));self.s=Service(self.store)
        self.p=self.s.save_portfolio({'name':'新头部与饼图','planned_amount':'10000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,code='159307',pid=None,**kw):
        return self.s.save_event({'portfolio_id':pid or self.p['id'],'kind':'BUY','date':'2023-01-01','instrument':{'market':'CN','code':code},'quantity':'100','gross_amount':'1000','fx_rate':'1',**kw})
    def price(self,e,value='11'):
        self.s.cache_prices(e['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':'2024-01-01','price':value}])
    def plan(self,e,**kw):
        return self.s.attach_instrument(self.p['id'],e['instrument_id'],{'asset_class':'权益','region':'A股','tags':[],**kw})
    def test_idle_budget_change_does_not_change_investment_xirr_or_flows(self):
        e=self.buy(transaction_costs='10');self.price(e)
        before=self.s.dashboard(self.p['id'],end='2024-01-01')['capital_summary']
        with patch('backend.service.today',return_value=date(2023,6,1)):
            self.s.save_portfolio({**self.p,'planned_amount':'100000'},self.p['id'])
        after=self.s.dashboard(self.p['id'],end='2024-01-01')['capital_summary']
        self.assertEqual(after['planned_amount'],'100000');self.assertNotEqual(before['available_amount'],after['available_amount'])
        self.assertEqual(before['xirr'],after['xirr']);self.assertEqual(before['cash_flows'],after['cash_flows'])
        self.assertAlmostEqual(float(after['xirr']),90/1010,places=10)
        basis=after['xirr_basis'];self.assertIs(basis['includes_idle_cash'],False);self.assertEqual(basis['day_count'],'ACT/365');self.assertIn('不含未投入现金',basis['description'])
    def test_legacy_deposits_do_not_enter_investment_cashflow_xirr(self):
        rates=[]
        for amount in ['10000','100000']:
            p=self.s.save_portfolio({'name':'现金'+amount,'planned_amount':amount,'start_date':'2023-01-01','funding_mode':'cash'})
            self.s.save_event({'portfolio_id':p['id'],'kind':'DEPOSIT','date':'2023-01-01','amount':amount,'fx_rate':'1'})
            e=self.buy(pid=p['id']);self.price(e)
            summary=self.s.dashboard(p['id'],end='2024-01-01')['capital_summary'];rates.append(summary['xirr'])
            self.assertEqual(sorted(D(f['amount']) for f in summary['cash_flows']),[D(-1000),D(1100)])
        self.assertEqual(rates[0],rates[1]);self.assertAlmostEqual(float(rates[0]),.1,places=10)
    def test_all_new_regions_accepted_and_legacy_values_canonicalized(self):
        self.assertEqual(REGIONS,('A股','美股','港股','海外其他','混合','未分类'))
        for region in REGIONS:self.assertEqual(tags({'region':region})['region'],region)
        self.assertEqual(tags({'region':'国内'})['region'],'A股');self.assertEqual(tags({'region':'海外'})['region'],'海外其他')
        with self.assertRaises(DomainError):tags({'region':'不存在地区'})
    def test_legacy_region_read_compatibility_does_not_modify_storage_or_guess_market(self):
        e=self.buy();allocation=self.plan(e,region='港股');allocation['region']='海外'
        with self.store.connection() as db:
            db.execute('UPDATE allocations SET data=? WHERE portfolio_id=? AND instrument_id=?',(json.dumps(allocation),self.p['id'],e['instrument_id']));db.commit()
        before=self.store.state()['allocations'];r=self.s.tag_report(self.p['id'],'region')
        self.assertIn('海外其他',[g['label'] for g in r['groups']]);self.assertNotIn('A股',[g['label'] for g in r['groups']])
        self.assertEqual(self.store.state()['allocations'],before)
    def test_region_alias_csv_roundtrip_preserves_original_historical_event(self):
        e=self.buy();legacy=dict(e,region='国内')
        with self.store.connection() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(legacy),e['id']));db.commit()
        before=self.store.state()['events'];text=self.s.csv_export(self.p['id'])
        preview=self.s.csv_preview(text,portfolio_id=self.p['id']);self.assertEqual(preview['errors'],[])
        self.assertEqual(preview['duplicates'],1)
        imported=self.s.csv_import(text,'events',portfolio_id=self.p['id']);self.assertEqual(imported['imported'],0)
        self.assertEqual(self.store.state()['events'],before)
        changed=self.s.csv_preview(text.replace('国内','港股'),portfolio_id=self.p['id'])
        self.assertTrue(changed['errors']);self.assertEqual(self.store.state()['events'],before)
    def test_overlapping_tags_are_a_single_pie_slice_with_cash_and_untagged(self):
        e=self.buy();f=self.buy('515080');self.price(e,'10');self.price(f,'10')
        self.plan(e,tags=['成长','消费']);self.plan(f,tags=[])
        r=self.s.tag_report(self.p['id'],'tags');slices=r['pie_groups']
        self.assertTrue(r['pie_eligible']);self.assertEqual(sum(D(g['value']) for g in slices),D(r['total_assets']))
        self.assertEqual(sum(D(g['weight']) for g in slices),D(1));self.assertEqual(len(slices),3)
        combo=next(g for g in slices if '成长' in g['label']);self.assertEqual(D(combo['value']),1000);self.assertIn('消费',combo['label'])
        self.assertEqual({g['label']:D(g['weight']) for g in r['groups']},{'成长':D('.1'),'消费':D('.1')})
        self.assertEqual(next(D(g['value']) for g in slices if g['label']=='未打主题标签'),1000)
    def test_category_and_region_pies_partition_total_assets(self):
        e=self.buy();self.price(e);self.plan(e,region='美股')
        for dimension in ('asset_class','region'):
            r=self.s.tag_report(self.p['id'],dimension)
            self.assertTrue(r['pie_eligible']);self.assertEqual(r['groups'],r['pie_groups'])
            self.assertEqual(sum(D(g['value']) for g in r['pie_groups']),D(r['total_assets']))
            self.assertAlmostEqual(float(sum(D(g['weight']) for g in r['pie_groups'])),1,places=12)
    def test_missing_and_negative_values_do_not_draw_false_pies(self):
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2023-01-01','instrument':{'market':'US','code':'AAPL'},'quantity':'1','gross_amount':'100','fx_rate':'7'})
        r=self.s.tag_report(self.p['id']);self.assertTrue(r['incomplete']);self.assertFalse(r['pie_eligible'])
        with patch.object(self.s,'dashboard',return_value={'holdings':[],'total_assets':'-5','cash_total':'-5','receivables':[]}):
            r=self.s.tag_report(self.p['id']);self.assertFalse(r['pie_eligible']);self.assertEqual(r['pie_groups'][0]['value'],'-5')
    def test_all_yearly_metrics_expose_identical_investment_basis(self):
        e=self.buy();self.price(e);d=self.s.dashboard(self.p['id'],end='2024-01-01');basis=d['capital_summary']['xirr_basis']
        self.assertEqual(basis,d['annual_performance']['xirr_basis']);self.assertEqual(basis,d['holdings'][0]['performance']['xirr_basis'])
        self.assertEqual(basis,self.s.transaction_rows(self.p['id'])[0]['actual']['xirr_basis'])
