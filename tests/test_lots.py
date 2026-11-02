import tempfile
import unittest
from pathlib import Path
from decimal import Decimal as D
from backend.common import DomainError, dec
from backend.service import Service
from backend.store import Store
from backend.trade_analytics import lot_projection,xirr

class LotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)));self.store=self.s.store
        self.p=self.s.save_portfolio({'name':'批次测试','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,quantity='100',gross='1000',date='2024-01-01',code='159307',fee='0',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':date,'instrument':{'market':'CN','code':code},'quantity':quantity,'gross_amount':gross,'fee':fee,'fx_rate':'1',**kw})
    def sell(self,rows,date='2024-03-01',gross='1200',fee='0',instrument_id=None,**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':instrument_id or rows[0][0]['instrument_id'],'kind':'SELL','date':date,'sell_lots':[{'buy_event_id':e['id'],'quantity':str(q)} for e,q in rows],'gross_amount':gross,'fee':fee,'fx_rate':'1',**kw})
    def dividend(self,e,net='100',date='2024-02-01',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':e['instrument_id'],'kind':'DIVIDEND','date':date,'net_amount':net,'pay_date':date,'fx_rate':'1',**kw})
    def result(self,at='2024-04-01'):
        return lot_projection(self.store.state(),self.p,at)
    def price(self,e,p,date='2024-03-02'):
        self.s.cache_prices(e['instrument_id'],[{'date':date,'price':str(p)}])
    def test_mandatory_linkage_and_quantity_is_authoritative_sum(self):
        a=self.buy();b=self.buy(date='2024-01-02',quantity='50',gross='600')
        with self.assertRaises(DomainError):self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SELL','date':'2024-03-01','quantity':'10','gross_amount':'120','fx_rate':'1'})
        e=self.sell([(a,20),(b,30)],gross='600',quantity='999');self.assertEqual(e['quantity'],'50');self.assertEqual(D(e['price']),12)
        rows=self.s.sale_lots(self.p['id'],a['instrument_id'],'2024-03-02');self.assertEqual({r['buy_event_id']:r['available_quantity'] for r in rows},{a['id']:'80',b['id']:'20'})
    def test_all_sell_materializes_current_explicit_batches(self):
        a=self.buy();b=self.buy(date='2024-01-02',quantity='50',gross='600')
        e=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SELL','date':'2024-03-01','sell_all':True,'gross_amount':'1800','fx_rate':'1'})
        self.assertEqual(e['quantity'],'150');self.assertEqual(len(e['sell_lots']),2);self.assertEqual(self.s.sale_lots(self.p['id'],a['instrument_id'],'2024-03-02'),[])
    def test_links_cannot_cross_symbol_portfolio_duplicate_or_future(self):
        a=self.buy();other=self.buy(code='515080',date='2024-01-02');future=self.buy(date='2024-03-02')
        q=self.s.save_portfolio({'name':'别的','planned_amount':'10000','start_date':'2023-01-01'})
        elsewhere=self.buy(portfolio_id=q['id'],date='2024-01-03')
        for rows in [[(other,10)],[(future,10)],[(elsewhere,10)],[(a,60),(a,40)],[(a,101)]]:
            with self.assertRaises(DomainError):self.sell(rows,instrument_id=a['instrument_id'])
    def test_backdated_edit_cancel_and_repeated_selling_are_rejected(self):
        a=self.buy();sale=self.sell([(a,80)])
        with self.assertRaises(DomainError):self.sell([(a,30)],date='2024-03-02',gross='360')
        with self.assertRaises(DomainError):self.s.save_event({**a,'quantity':'50','gross_amount':'500','price':'10','change_reason':'修改历史'},a['id'])
        with self.assertRaises(DomainError):self.s.delete_event(a['id'],'删历史')
        self.assertEqual(self.store.state()['events'][0]['quantity'],'100')
        choices=self.s.sale_lots(self.p['id'],a['instrument_id'],'2024-03-01',sale['id']);self.assertEqual(choices[0]['available_quantity'],'100')
    def test_unconfirmed_fund_and_same_day_later_batch_unavailable(self):
        a=self.buy(instrument={'market':'FUND','code':'001257'},confirmation_date='2024-01-03')
        with self.assertRaises(DomainError):self.sell([(a,10)],date='2024-01-02')
        self.assertEqual(self.s.sale_lots(self.p['id'],a['instrument_id'],'2024-01-02'),[])
        b=self.buy(date='2024-03-01')
        with self.assertRaises(DomainError):self.sell([(b,10)],sequence='0')
    def test_multi_buy_multi_sell_dividend_cost_and_freeze(self):
        a=self.buy(fee='10');b=self.buy(date='2024-01-02',gross='2000',fee='20');self.dividend(a,net='180')
        sale=self.sell([(a,40),(b,60)],gross='3000',fee='30');self.price(a,25)
        r=self.result();self.assertEqual(r[sale['id']]['cost'],D('1616'));self.assertEqual(r[sale['id']]['profit'],D('1444'))
        self.assertEqual(r[a['id']]['profit'],D('1768'));self.assertEqual(r[b['id']]['profit'],D('852'))
        second=self.sell([(a,60),(b,40)],date='2024-03-03',gross='2400',fee='24');self.price(a,999,date='2024-03-04')
        r=self.result();self.assertEqual(r[second['id']]['profit'],D('1052'));self.assertEqual(r[a['id']]['profit']+r[b['id']]['profit'],D('2496'))
        self.assertTrue(r[a['id']]['valuation_frozen']);self.assertEqual(r[a['id']]['market_value'],0)
        self.assertIsNotNone(r[sale['id']]['xirr']);self.assertTrue(all(p['profit'] is not None for p in r[sale['id']]['parts']))
    def test_reinvest_is_economic_cost_not_zero(self):
        a=self.buy();e=self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'REINVEST','date':'2024-02-01','gross_amount':'100','net_amount':'100','pay_date':'2024-02-01','reinvest_date':'2024-02-01','reinvest_quantity':'10','reinvest_gross_amount':'100','fx_rate':'1'})
        self.price(a,12);r=self.result();self.assertEqual(r[a['id']]['profit'],D('300'));self.assertEqual(r[e['id']]['profit'],D('20'))
    def test_split_and_reverse_split_preserve_lot_cost(self):
        a=self.buy();self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SPLIT','date':'2024-01-10','factor':'2','fx_rate':'1'})
        sale=self.sell([(a,50)],gross='300');self.price(a,6);r=self.result();self.assertEqual(r[sale['id']]['cost'],D('250'));self.assertEqual(r[sale['id']]['profit'],D('50'));self.assertEqual(r[a['id']]['profit'],D('200'))
        self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':a['instrument_id'],'kind':'SPLIT','date':'2024-03-03','factor':'.01','fx_rate':'1'})
        rows=self.s.sale_lots(self.p['id'],a['instrument_id'],'2024-03-04');self.assertEqual(D(rows[0]['available_quantity']),D('1.5'))
    def test_foreign_locked_flows_and_current_remainder_fx(self):
        a=self.buy(instrument={'market':'HK','code':'700','currency':'HKD'},fee='10',fx_rate='.90')
        self.dividend(a,fx_rate='.92',pay_fx_rate='.93');sale=self.sell([(a,40)],gross='480',fee='4',fx_rate='.92')
        self.price(a,13);self.s.save_fx({'currency':'HKD','date':'2024-03-02','rate':'.95','mode':'daily'})
        r=self.result();self.assertEqual(r[a['id']]['profit'],D('362.92'));self.assertEqual(r[sale['id']]['profit'],D('111.52'))
        self.s.save_fx({'currency':'HKD','date':'2024-03-02','rate':'.5','mode':'manual'});r=self.result();self.assertEqual(r[sale['id']]['profit'],D('111.52'))
    def test_sold_after_registry_before_ex_still_gets_dividend(self):
        a=self.buy();self.sell([(a,100)],date='2024-02-02',gross='1100')
        self.dividend(a,date='2024-02-03',record_date='2024-02-01',pay_date='2024-02-07');r=self.result();self.assertEqual(r[a['id']]['profit'],D('200'))
        self.price(a,999);self.assertEqual(self.result()[a['id']]['profit'],D('200'))
    def test_later_tax_follows_original_dividend_even_after_new_purchase(self):
        a=self.buy();div=self.dividend(a);sale=self.sell([(a,100)],gross='1100');new=self.buy(date='2024-03-02')
        tax=self.s.save_event({'portfolio_id':self.p['id'],'kind':'TAX','date':'2024-03-03','amount':'10','currency':'CNY','fx_rate':'1','related_event_id':div['id']})
        r=self.result();self.assertEqual(r[a['id']]['profit'],D('190'));self.assertEqual(r[sale['id']]['profit'],D('190'));self.assertEqual(r[new['id']]['profit'],D('0'))
        with self.assertRaises(DomainError):self.s.delete_event(div['id'],'删除关联分红')
        self.assertEqual(tax['instrument_id'],a['instrument_id'])
    def test_unallocated_fees_are_explicit(self):
        a=self.buy();self.s.save_event({'portfolio_id':self.p['id'],'kind':'FEE','date':'2024-03-03','amount':'10','currency':'CNY','fx_rate':'1'})
        self.assertTrue(self.result()[a['id']]['unallocated_notes'])
    def test_foreign_pending_sale_and_dividend_value_at_current_fx(self):
        a=self.buy(instrument={'market':'HK','code':'700','currency':'HKD'},fx_rate='.9')
        self.dividend(a,net='100',date='2024-01-31',pay_date='2024-02-10',pay_fx_rate='.8',fx_rate='.9')
        sale=self.sell([(a,100)],date='2024-02-01',gross='1000',payment_date='2024-02-10',fx_rate='.9')
        self.s.save_fx({'currency':'HKD','date':'2024-02-02','rate':'.95','mode':'daily'})
        r=self.result('2024-02-03');self.assertEqual(r[a['id']]['profit'],D('145'));self.assertEqual(r[sale['id']]['profit'],D('145'));self.assertIsNotNone(r[sale['id']]['xirr']);self.assertIn('应收估值',r[sale['id']]['xirr_status']);self.assertTrue(r[a['id']]['pending_settlement'])
        r=self.result('2024-02-11');self.assertEqual(r[a['id']]['profit'],D('80'));self.assertFalse(r[a['id']]['pending_settlement'])
    def test_csv_roundtrip_keeps_explicit_source_ids_and_profits(self):
        a=self.buy();e=self.sell([(a,50)],gross='600');before=self.result()[e['id']]['profit'];text=self.s.csv_export(self.p['id']);self.assertIn('sell_lots',text)
        self.assertEqual(self.s.csv_import(text,'events',self.p['id'])['imported'],0);self.assertEqual(self.result()[e['id']]['profit'],before)
    def test_history_buy_does_not_reassign_a_selected_sale(self):
        a=self.buy(date='2024-01-03');sale=self.sell([(a,100)],gross='1200');b=self.buy(date='2024-01-02',gross='500')
        r=self.result();self.assertEqual(r[sale['id']]['parts'][0]['buy_event_id'],a['id']);self.assertEqual(r[sale['id']]['profit'],D('200'));self.assertEqual(r[b['id']]['remaining_quantity'],100)
    def test_central_classification_are_not_overwritten_by_later_buys(self):
        a=self.buy(asset_class='其他');self.s.attach_instrument(self.p['id'],a['instrument_id'],{'target_pct':'15','asset_class':'权益','region':'国内'})
        self.buy(date='2024-01-02',asset_class='债券')
        allocation=self.store.state()['allocations'][0];self.assertEqual(allocation['region'],'A股');self.assertEqual(allocation['asset_class'],'权益')

class XirrTests(unittest.TestCase):
    def test_official_example_and_actual_days(self):
        r=xirr([('2008-01-01',-10000),('2008-03-01',2750),('2008-10-30',4250),('2009-02-15',3250),('2009-04-01',2750)])
        self.assertAlmostEqual(float(r['rate']),.373362535,places=8)
        r=xirr([('2024-01-01',-100),('2025-01-01',110)]);self.assertAlmostEqual(float(r['rate']),1.1**(365/366)-1,places=10)
    def test_multiple_tangent_and_no_solution(self):
        def calc(vals):return xirr(list(zip(['2021-01-01','2022-01-01','2023-01-01'],vals)))
        self.assertIsNone(calc([-100,230,-132])['rate']);self.assertIn('多个',calc([-100,230,-132])['status'])
        self.assertAlmostEqual(float(calc([-100,220,-121])['rate']),.1,places=7);self.assertIsNone(calc([-100,50,-100])['rate'])
    def test_same_day_sign_and_zero_return(self):
        self.assertIsNone(xirr([('2024-01-01',-100),('2024-01-01',110)])['rate'])
        self.assertIsNone(xirr([('2024-01-01',10),('2025-01-01',110)])['rate'])
        self.assertAlmostEqual(float(xirr([('2024-01-01',-100),('2025-01-01',100)])['rate']),0,places=10)
