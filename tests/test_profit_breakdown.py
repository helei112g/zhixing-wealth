import tempfile
import unittest
from decimal import Decimal as D
from pathlib import Path
from backend.service import Service
from backend.store import Store
from backend.api_models import ProfitBreakdown, TransactionFacts


class ProfitBreakdownTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(Path(self.tmp.name)))
        self.p=self.s.save_portfolio({'name':'合成收益分解','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,market='CN',code='510300',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2023-01-02','instrument':{'market':market,'code':code},'quantity':'100','gross_amount':'1000','fx_rate':'1',**kw})
    def event(self,e,kind,**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':e['instrument_id'],'kind':kind,'date':'2023-06-01','fx_rate':'1',**kw})
    def snap(self,e):
        self.s.cache_prices(e['instrument_id'],[{'date':'2023-01-02','price':'10'},{'date':'2024-01-01','price':'12'}])
        return self.s.dashboard(self.p['id'],end='2024-01-01')
    def check_equation(self,b):
        ProfitBreakdown.model_validate(b)
        self.assertEqual(D(b['total']),sum(D(b[k]) for k in ('price_net','dividends_net','other_income')))
        self.assertEqual(D(b['price_net']),D(b['market_value'])+D(b['sale_proceeds'])-D(b['buy_cost'])-D(b['extra_costs']))
    def test_cost_and_net_investment_diverge_only_after_cash_recovery(self):
        e=self.buy(fee='10');d=self.snap(e);h=d['holdings'][0]
        self.assertEqual(h['net_invested_base'],h['performance']['cost'])
        self.event(e,'DIVIDEND',net_amount='90',tax='10',pay_date='2023-06-03')
        self.event(e,'SELL',date='2023-08-01',sell_lots=[{'buy_event_id':e['id'],'quantity':'50'}],gross_amount='550',fee='5')
        d=self.snap(e);h=d['holdings'][0];self.assertEqual(D(h['net_invested_base']),D(1010)-90-545)
        self.assertEqual(h['performance']['cost'],'1010');self.check_equation(h['performance']['profit_breakdown'])
    def test_fx_tax_fee_and_interest_reconcile_without_double_counting(self):
        e=self.buy('HK','03110',quantity='10',gross_amount='100',fx_rate='.9')
        self.s.save_fx({'currency':'HKD','date':'2023-01-01','rate':'1','mode':'daily'})
        div=self.event(e,'DIVIDEND',net_amount='8',tax='2',pay_date='2023-06-03',pay_fx_rate='.95')
        self.event(e,'TAX',date='2023-07-01',related_event_id=div['id'],amount='1')
        self.event(e,'FEE',date='2023-07-02',amount='1')
        self.event(e,'INTEREST',date='2023-07-03',amount='3')
        d=self.snap(e);b=d['holdings'][0]['performance']['profit_breakdown']
        self.check_equation(b);self.assertEqual(D(b['price_net']),29);self.assertEqual(D(b['dividends_net']),D('6.6'));self.assertEqual(D(b['other_income']),3)
        self.check_equation(d['capital_summary']['profit_breakdown'])
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'FEE','date':'2023-09-01','currency':'CNY','amount':'2','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'INTEREST','date':'2023-09-02','currency':'CNY','amount':'4','fx_rate':'1'})
        b=self.snap(e)['capital_summary']['profit_breakdown'];self.check_equation(b);self.assertEqual(D(b['price_net']),27);self.assertEqual(D(b['other_income']),7)
    def test_reinvestment_is_income_and_cost_once(self):
        e=self.buy();self.event(e,'REINVEST',net_amount='100',tax='5',pay_date='2023-06-05',reinvest_date='2023-06-03',reinvest_quantity='10',reinvest_gross_amount='100')
        d=self.snap(e);b=d['holdings'][0]['performance']['profit_breakdown'];self.check_equation(b)
        self.assertEqual(D(b['buy_cost']),1100);self.assertEqual(D(b['dividends_net']),100);self.assertEqual(D(b['total']),320)
    def test_dividend_native_tax_equation_is_market_independent(self):
        for market,code in [('CN','510300'),('HK','03110'),('US','AAPL')]:
            with self.subTest(market=market):
                e=self.buy(market,code)
                for net,tax in [('7.1234','1.5'),('5','0')]:
                    div=self.event(e,'DIVIDEND',net_amount=net,tax=tax,pay_date='2023-06-03')
                    row=next(r for r in self.s.transaction_rows(self.p['id']) if r['event']['id']==div['id']);f=row['facts'];TransactionFacts.model_validate(f)
                    self.assertEqual(D(f['dividend_gross_native'])-D(f['dividend_tax_native']),D(net))
                    self.assertEqual(f['dividend_tax_differs'],D(tax)!=0)
    def test_missing_valuation_preserves_unknown_profit(self):
        self.buy('US','AAPL');d=self.s.dashboard(self.p['id'],end='2024-01-01')
        b=d['capital_summary']['profit_breakdown'];self.assertFalse(b['complete']);self.assertIsNone(b['total']);self.assertIsNone(b['price_net'])

    def test_unpaid_dividend_counts_as_receivable_not_cash_recovery(self):
        e=self.buy('HK','03110',fx_rate='.9')
        self.s.save_fx({'currency':'HKD','date':'2023-01-01','rate':'1','mode':'daily'})
        self.event(e,'DIVIDEND',date='2023-12-29',net_amount='8',tax='2',pay_date='2024-01-03',pay_fx_rate='.95')
        h=self.snap(e)['holdings'][0];b=h['performance']['profit_breakdown'];self.check_equation(b)
        self.assertEqual(D(h['net_invested_base']),900)
        self.assertEqual(D(b['dividends_net']),8)
        self.assertTrue(h['performance']['pending_settlement'])

    def test_subcent_returns_disclose_display_rounding_without_changing_values(self):
        e=self.buy(quantity='1',gross_amount='10')
        self.event(e,'DIVIDEND',net_amount='.004',tax='0',pay_date='2023-06-03')
        self.s.cache_prices(e['instrument_id'],[{'date':'2024-01-01','price':'10.004'}])
        b=self.s.dashboard(self.p['id'],end='2024-01-01')['capital_summary']['profit_breakdown']
        self.check_equation(b);self.assertEqual(D(b['total']),D('.008'));self.assertEqual(D(b['rounding_adjustment']),D('.01'))


if __name__=='__main__':unittest.main()
