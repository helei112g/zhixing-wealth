import tempfile,unittest
from pathlib import Path
from datetime import date
from decimal import Decimal as D
from unittest.mock import patch
from backend.store import Store
from backend.environment import claim_environment
from backend.service import Service

class CapitalSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.s=Service(Store(claim_environment(Path(self.tmp.name),'test')));self.p=self.s.save_portfolio({'name':'共享摘要','planned_amount':'100000','start_date':'2023-01-01'})
    def tearDown(self):self.tmp.cleanup()
    def buy(self,market='CN',code='159307',qty='100',gross='1000',**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2023-01-01','instrument':{'market':market,'code':code},'quantity':qty,'gross_amount':gross,'fx_rate':'7' if market=='US' else '1',**kw})
    def price(self,e,value,at='2024-01-01'):
        self.s.cache_prices(e['instrument_id'],[{'date':'2023-01-01','price':'10'},{'date':at,'price':str(value)}])
    def snap(self,end='2024-01-01'):return self.s.dashboard(self.p['id'],end=end)
    def test_running_days_include_start_and_do_not_follow_chart_end(self):
        with patch('backend.service.today',return_value=date(2023,1,1)):
            self.assertEqual(self.s.bootstrap()['portfolios'][0]['running_days'],1)
        with patch('backend.service.today',return_value=date(2024,1,1)):
            self.assertEqual(self.s.bootstrap()['portfolios'][0]['running_days'],366)
            self.assertEqual(self.s.dashboard(self.p['id'],end='2023-06-01')['running_days'],366)
        self.assertNotIn('running_days',self.s.store.state()['portfolios'][0])
    def test_xirr_is_computed_from_combined_flows_not_average_of_instrument_rates(self):
        cn=self.buy();us=self.buy('US','AAPL',qty='10',gross='100');self.s.save_fx({'currency':'USD','date':'2023-01-01','rate':'7','mode':'daily'});self.price(cn,11);self.price(us,12)
        r=self.snap();a=r['capital_summary'];self.assertEqual(D(a['cost']),1700);self.assertEqual(D(a['profit']),240);self.assertEqual(D(a['market_value']),1940)
        self.assertEqual(D(a['invested_amount']),1700);self.assertEqual(D(a['invested_weight']),D('.017'))
        self.assertAlmostEqual(float(a['xirr']),240/1700,places=10)
        self.assertNotAlmostEqual(float(a['xirr']),sum(float(h['performance']['xirr']) for h in r['holdings'])/2,places=6)
    def test_dividends_linked_tax_and_unassigned_expenses_are_not_lost_or_doubled(self):
        e=self.buy(fee='10');self.price(e,11)
        div=self.s.save_event({'portfolio_id':self.p['id'],'kind':'DIVIDEND','instrument_id':e['instrument_id'],'date':'2023-06-01','net_amount':'50','pay_date':'2023-06-03','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'TAX','related_event_id':div['id'],'date':'2023-08-01','amount':'2','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'FEE','date':'2023-08-02','amount':'5','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'INTEREST','date':'2023-08-03','amount':'3','fx_rate':'1'})
        r=self.snap();a=r['capital_summary'];self.assertEqual(D(a['profit']),136);self.assertEqual(a['profit'],r['pnl']);self.assertEqual(D(a['invested_amount']),967)
        self.assertEqual(D(a['planned_amount'])-D(a['available_amount']),D(a['invested_amount']))
    def test_reinvestment_internal_flows_cancel_in_aggregate_xirr(self):
        e=self.buy();self.price(e,10)
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'REINVEST','instrument_id':e['instrument_id'],'date':'2023-06-01','net_amount':'100','pay_date':'2023-06-03','fx_rate':'1','reinvest_date':'2023-06-03','reinvest_quantity':'10','reinvest_gross_amount':'100','reinvest_fx_rate':'1'})
        a=self.snap()['capital_summary'];self.assertEqual(D(a['market_value']),1100);self.assertEqual(D(a['profit']),100);self.assertAlmostEqual(float(a['xirr']),.1,places=10)
    def test_closed_investment_profit_and_yearly_rate_remain_frozen(self):
        e=self.buy();self.s.save_event({'portfolio_id':self.p['id'],'instrument_id':e['instrument_id'],'kind':'SELL','date':'2024-01-01','sell_all':True,'gross_amount':'1100','fx_rate':'1'})
        a=self.snap()['capital_summary'];self.price(e,999,at='2024-06-01');b=self.snap('2024-06-01')['capital_summary']
        self.assertEqual(a['profit'],b['profit']);self.assertEqual(a['xirr'],b['xirr']);self.assertEqual(D(b['market_value']),0);self.assertEqual(D(b['invested_amount']),-100)
    def test_missing_foreign_valuation_is_unknown_instead_of_zero(self):
        self.buy('US','AAPL',qty='10',gross='100');a=self.snap()['capital_summary'];self.assertIsNone(a['market_value']);self.assertIsNone(a['profit']);self.assertIsNone(a['xirr']);self.assertIsNone(a['return_on_cost'])
    def test_historical_snapshot_uses_budget_active_on_that_date(self):
        e=self.buy();self.price(e,10)
        with patch('backend.service.today',return_value=date(2024,2,1)):
            self.s.save_portfolio({**self.p,'planned_amount':'200000'},self.p['id'])
        a=self.snap()['capital_summary'];b=self.snap('2024-03-01')['capital_summary'];self.assertEqual(D(a['planned_amount']),100000);self.assertEqual(D(b['planned_amount']),200000);self.assertEqual(a['invested_amount'],b['invested_amount'])
    def test_empty_portfolio_shows_no_fabricated_annual_return(self):
        a=self.snap()['capital_summary'];self.assertEqual(D(a['profit']),0);self.assertEqual(D(a['invested_amount']),0);self.assertEqual(D(a['market_value']),0);self.assertIsNone(a['xirr']);self.assertIsNone(a['return_on_cost'])

    def test_expenses_and_interest_alone_do_not_invent_investment_principal(self):
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'FEE','date':'2023-01-01','amount':'5','fx_rate':'1'})
        self.s.save_event({'portfolio_id':self.p['id'],'kind':'INTEREST','date':'2024-01-01','amount':'10','fx_rate':'1'})
        a=self.snap()['capital_summary'];self.assertEqual(D(a['profit']),5);self.assertIsNone(a['xirr']);self.assertIsNone(a['return_on_cost']);self.assertIn('买入本金',a['xirr_status'])

    def test_current_holding_profit_separates_sales_distributions_and_fees(self):
        e=self.buy(fee='10');self.price(e,11)
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2023-02-01',sell_lots=[dict(buy_event_id=e['id'],quantity='50')],quantity='50',gross_amount='600',fee='3',tax='2',fx_rate='1'))
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='DIVIDEND',date='2023-06-01',pay_date='2023-06-03',net_amount='50',fx_rate='1'))
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='FEE',date='2023-08-01',amount='5',fx_rate='1'))
        before=self.s.store.state();r=self.snap();h=r['holdings'][0];a=r['capital_summary']
        self.assertEqual(D(a['holding_profit']),45);self.assertEqual(D(a['realized_sale_profit']),90)
        self.assertEqual(D(a['dividends_net']),50);self.assertEqual(D(a['profit']),180)
        self.assertEqual(D(a['invested_amount']),370);self.assertEqual(D(a['available_amount']),99630)
        self.assertEqual(D(h['cost_including_fees_base']),505);self.assertEqual(a['holding_profit'],h['performance']['holding_profit'])
        self.assertEqual(self.s.store.state(),before)

    def test_holding_profit_without_sales_or_distributions_matches_cumulative(self):
        e=self.buy(fee='10',tax='2');self.price(e,11)
        a=self.snap()['capital_summary'];self.assertEqual(a['holding_profit'],a['profit'])
        self.assertEqual(D(a['holding_profit']),88);self.assertEqual(D(a['realized_sale_profit']),0)

    def test_reinvestment_does_not_add_personal_principal_but_increases_remaining_cost(self):
        e=self.buy();self.price(e,10)
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='REINVEST',date='2023-06-01',pay_date='2023-06-03',net_amount='100',fx_rate='1',reinvest_date='2023-06-03',reinvest_quantity='10',reinvest_gross_amount='100',reinvest_fx_rate='1'))
        a=self.snap()['capital_summary'];self.assertEqual(D(a['invested_amount']),1000)
        self.assertEqual(D(a['holding_profit']),0);self.assertEqual(D(a['profit']),100)

    def test_net_invested_ignores_unsettled_sales_and_dividends(self):
        e=self.buy('FUND','000001',fee='10')
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2023-06-01',payment_date='2023-06-03',sell_lots=[dict(buy_event_id=e['id'],quantity='50')],quantity='50',gross_amount='600',fee='5',fx_rate='1'))
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='DIVIDEND',date='2023-06-01',pay_date='2023-06-04',net_amount='20',fx_rate='1'))
        self.assertEqual(D(self.snap('2023-06-02')['capital_summary']['invested_amount']),1010)
        self.assertEqual(D(self.snap('2023-06-03')['capital_summary']['invested_amount']),415)
        self.assertEqual(D(self.snap('2023-06-04')['capital_summary']['invested_amount']),395)

    def test_remaining_cost_uses_purchase_fx_for_fees_and_sale_cost_allocation(self):
        e=self.buy('US','AAPL',qty='10',gross='100',fee='1');self.price(e,12)
        self.s.save_fx(dict(currency='USD',date='2023-01-01',rate='7',mode='daily'))
        self.s.save_fx(dict(currency='USD',date='2024-01-01',rate='8',mode='daily'))
        a=self.snap()['capital_summary'];self.assertEqual(D(a['holding_profit']),253)
        self.assertEqual(D(a['invested_amount']),707)
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2024-01-01',sell_lots=[dict(buy_event_id=e['id'],quantity='5')],quantity='5',gross_amount='60',fee='1',fx_rate='8'))
        r=self.snap();self.assertEqual(D(r['capital_summary']['holding_profit']),D('126.5'))
        self.assertEqual(D(r['capital_summary']['realized_sale_profit']),D('118.5'))
        self.assertEqual(D(r['holdings'][0]['cost_including_fees_base']),D('353.5'))

    def test_cash_mode_budget_remainder_is_distinct_from_actual_account_cash(self):
        p=self.s.save_portfolio(dict(name='合成实盘',planned_amount='20000',start_date='2023-01-01',funding_mode='cash'))
        self.s.save_event(dict(portfolio_id=p['id'],kind='DEPOSIT',date='2023-01-01',currency='CNY',amount='5000',fx_rate='1'))
        self.s.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2023-01-01',instrument=dict(market='CN',code='159307'),quantity='100',gross_amount='1000',fee='10',fx_rate='1'))
        a=self.s.dashboard(p['id'],end='2024-01-01')['capital_summary']
        self.assertEqual(D(a['invested_amount']),1010);self.assertEqual(D(a['available_amount']),18990)
        self.assertEqual(D(a['account_available_cash']),3990)

    def test_unknown_opening_principal_does_not_invent_net_invested_from_budget(self):
        self.s.save_event(dict(portfolio_id=self.p['id'],kind='OPENING_POSITION',date='2023-01-01',instrument=dict(market='CN',code='159307'),quantity='100',gross_amount='1000',fx_rate='1'))
        a=self.snap()['capital_summary'];self.assertIsNone(a['invested_amount']);self.assertIsNone(a['available_amount'])
        self.assertIsNotNone(a['account_available_cash'])

    def test_closed_positions_have_zero_current_profit_and_keep_realized_profit(self):
        e=self.buy(fee='10');self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2024-01-01',sell_all=True,gross_amount='1100',fee='5',fx_rate='1'))
        a=self.snap()['capital_summary'];self.assertEqual(D(a['holding_profit']),0)
        self.assertEqual(D(a['realized_sale_profit']),85);self.assertEqual(D(a['invested_amount']),-85)

    def test_cash_interest_does_not_turn_account_balance_into_personal_principal(self):
        e=self.buy();self.price(e,10)
        self.s.save_event(dict(portfolio_id=self.p['id'],kind='INTEREST',date='2023-08-01',amount='3',fx_rate='1'))
        a=self.snap()['capital_summary']
        self.assertEqual(D(a['invested_amount']),1000);self.assertEqual(D(a['available_amount']),99000)
        self.assertEqual(D(a['account_available_cash']),99003);self.assertEqual(D(a['profit']),3)

    def test_internal_exchange_only_counts_fees_not_exchange_principal_or_gain(self):
        p=self.s.save_portfolio(dict(name='合成换汇实盘',planned_amount='100000',start_date='2023-01-01',funding_mode='cash'))
        self.s.save_event(dict(portfolio_id=p['id'],kind='DEPOSIT',date='2023-01-01',currency='USD',amount='101',fx_rate='7'))
        self.s.save_event(dict(portfolio_id=p['id'],kind='FX',date='2023-01-01',from_currency='USD',to_currency='CNY',from_amount='100',to_amount='800',from_fx_rate='7',to_fx_rate='1',fee='1'))
        a=self.s.dashboard(p['id'],end='2024-01-01')['capital_summary'];self.assertEqual(D(a['invested_amount']),7)
        self.assertEqual(D(a['available_amount']),99993);self.assertEqual(D(a['account_available_cash']),800)

    def test_foreign_reinvest_principal_cancels_in_native_currency_at_both_timings(self):
        for mode in ('cash','budget'):
            for early in (True,False):
                for gross in ('80','99'):
                    with self.subTest(mode=mode,early=early,gross=gross):
                        p=self.s.save_portfolio(dict(name='合成再投'+mode+str(early)+gross,planned_amount='100000',start_date='2023-01-01',funding_mode=mode))
                        if mode=='cash':self.s.save_event(dict(portfolio_id=p['id'],kind='DEPOSIT',date='2023-01-01',currency='USD',amount='1000',fx_rate='7'))
                        e=self.s.save_event(dict(portfolio_id=p['id'],kind='BUY',date='2023-01-01',instrument=dict(market='US',code='AAPL'),quantity='10',gross_amount='100',fx_rate='7'))
                        self.s.save_event(dict(portfolio_id=p['id'],instrument_id=e['instrument_id'],kind='REINVEST',date='2023-06-01',pay_date='2023-06-03',net_amount='100',fee='1',fx_rate='7',pay_fx_rate='7',reinvest_date='2023-06-02' if early else '2023-06-05',reinvest_quantity='10',reinvest_gross_amount=gross,reinvest_fx_rate='8'))
                        final=D(700)-(D(100)-D(gross)-1)*7
                        for end,expected in [('2023-06-02',D(700)),('2023-06-03',final if early else D(0)),('2023-06-05',final)]:
                            r=self.s.dashboard(p['id'],end=end,view='summary')
                            self.assertEqual(D(r['capital_summary']['invested_amount']),expected)
                            self.assertEqual(D(r['holdings'][0]['net_invested_base']),expected)

    def test_completed_sales_remain_known_when_current_valuation_is_missing(self):
        e=self.buy('US','AAPL',qty='10',gross='100')
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2024-01-01',sell_lots=[dict(buy_event_id=e['id'],quantity='5')],gross_amount='50',fx_rate='8'))
        a=self.snap()['capital_summary'];self.assertIsNone(a['holding_profit']);self.assertIsNone(a['profit'])
        self.assertEqual(D(a['realized_sale_profit']),50)

    def test_pending_sale_without_valuation_is_unknown_until_receipt(self):
        e=self.buy('US','AAPL',qty='10',gross='100')
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2024-01-01',payment_date='2024-01-03',sell_lots=[dict(buy_event_id=e['id'],quantity='5')],gross_amount='50',fx_rate='8'))
        self.assertIsNone(self.snap()['capital_summary']['realized_sale_profit'])
        self.assertEqual(D(self.snap('2024-01-03')['capital_summary']['realized_sale_profit']),50)

    def test_liquidation_resets_unknown_remaining_cost_for_a_new_position(self):
        e=self.buy();self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='ADJUSTMENT',date='2023-02-01',new_quantity='100',new_cost='1000',fx_rate='1',reason='合成成本待核对'))
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2023-03-01',sell_all=True,gross_amount='1100',fx_rate='1'))
        new=self.buy(date='2023-04-01');self.price(new,11)
        a=self.snap()['capital_summary'];self.assertEqual(D(a['holding_profit']),100)
        self.assertIsNone(a['realized_sale_profit'])

    def test_unconfirmed_fund_purchase_is_not_a_realized_sale_loss(self):
        self.s.save_event(dict(portfolio_id=self.p['id'],kind='BUY',date='2023-01-02',payment_date='2023-01-01',confirmation_date='2023-01-03',instrument=dict(market='FUND',code='000001'),quantity='100',gross_amount='1000',fx_rate='1'))
        a=self.snap('2023-01-02')['capital_summary'];self.assertEqual(D(a['realized_sale_profit']),0)

    def test_opening_receivable_keeps_associated_principal_unknown(self):
        e=self.buy();self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='OPENING_RECEIVABLE',date='2023-01-01',pay_date='2023-02-01',amount='100',fx_rate='1'))
        r=self.snap();self.assertIsNone(r['capital_summary']['invested_amount'])
        self.assertIsNone(r['holdings'][0]['net_invested_base'])

    def test_current_unknown_cost_does_not_hide_a_prior_closed_sale(self):
        e=self.buy();self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2023-02-01',sell_all=True,gross_amount='1100',fx_rate='1'))
        self.buy(date='2023-04-01')
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='ADJUSTMENT',date='2023-05-01',new_quantity='100',new_cost='1000',fx_rate='1',reason='合成新持仓待核对'))
        a=self.snap()['capital_summary'];self.assertIsNone(a['holding_profit']);self.assertEqual(D(a['realized_sale_profit']),100)

    def test_pending_split_only_invalidates_disposals_after_its_effective_date(self):
        e=self.buy();self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2023-02-01',sell_all=True,gross_amount='1100',fx_rate='1'))
        new=self.buy(date='2023-04-01')
        self.s.cache_actions(e['instrument_id'],[dict(kind='split_notice',date='2023-05-01',factor='2',source='synthetic')])
        a=self.snap()['capital_summary'];self.assertIsNone(a['holding_profit']);self.assertEqual(D(a['realized_sale_profit']),100)
        self.s.save_event(dict(portfolio_id=self.p['id'],instrument_id=e['instrument_id'],kind='SELL',date='2023-06-01',sell_lots=[dict(buy_event_id=new['id'],quantity='10')],gross_amount='110',fx_rate='1'))
        self.assertIsNone(self.snap()['capital_summary']['realized_sale_profit'])
