import base64
import csv
import io
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.common import DomainError, month_after
from backend.ledger import Ledger, Market, periods
from backend.service import Service
from backend.store import Store


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.s = Service(self.store)
        self.p = self.s.save_portfolio({'funding_mode':'cash','name':'测试组合','planned_amount':'100000','start_date':'2024-01-01'})
        self.i = self.s.save_instrument({'portfolio_id':self.p['id'],'market':'CUSTOM','type':'custom',
            'code':'TEST-A','currency':'CNY','name':'测试标的','asset_class':'权益','region':'国内'})

    def tearDown(self): self.temp.cleanup()
    def event(self, kind, d='2024-01-01', **kw):
        p=kw.pop('portfolio_id',self.p['id'])
        if kind in ('SELL','REDEEM_PENDING') and 'sell_lots' not in kw:
            left=D(kw['quantity']);chosen=[]
            for row in self.s.sale_lots(p,kw.get('instrument_id',self.i['id']),d):
                used=min(left,D(row['available_quantity']));chosen.append({'buy_event_id':row['buy_event_id'],'quantity':str(used)});left-=used
                if left<=0:break
            if left>0 and chosen:chosen[-1]['quantity']=str(D(chosen[-1]['quantity'])+left)
            kw['sell_lots']=chosen
        return self.s.save_event({'kind':kind,'date':d,'portfolio_id':p,'fx_rate':'1',**kw})
    def buy(self, qty=100, price=100, d='2024-01-01', **kw):
        kw.setdefault('instrument_id',self.i['id'])
        return self.event('BUY',d,quantity=str(qty),price=str(price),**kw)
    def price(self, value, d='2024-01-01', instrument_id=None):
        self.s.cache_prices(instrument_id or self.i['id'],[{'date':d,'price':str(value),'source':'测试确认'}])
    def snap(self, d='2024-01-03', portfolio_id=None):
        return self.s.dashboard(portfolio_id or self.p['id'],end=d)
    def cash(self, value=10000, d='2024-01-01', **kw): return self.event('DEPOSIT',d,amount=str(value),currency='CNY',**kw)
    def holding(self, snap=None): return (snap or self.snap())['holdings'][0]
    def hk(self):
        return self.s.save_instrument({'portfolio_id':self.p['id'],'market':'HK','code':'02800','type':'etf',
            'currency':'HKD','name':'港币标的','asset_class':'权益','region':'海外'})

    def test_deposit_not_profit(self):
        self.cash();self.cash(10000,'2024-01-02');r=self.snap()
        self.assertEqual(D(r['total_assets']),D(20000));self.assertEqual(D(r['pnl']),0);self.assertEqual(D(r['twr']),0)

    def test_fees_and_partial_sell(self):
        self.cash(10010);self.buy(fee='10');self.price(100)
        r=self.snap('2024-01-01');self.assertEqual(D(r['pnl']),-10);self.assertEqual(D(r['components']['price']),0)
        self.event('SELL','2024-01-02',instrument_id=self.i['id'],quantity='40',price='110',fee='4')
        self.price(110,'2024-01-02');r=self.snap()
        self.assertEqual(D(r['cash'][0]['balance']),4396);self.assertEqual(D(r['total_assets']),10996)
        self.assertEqual(D(r['pnl']),986);self.assertEqual(D(r['components']['price']),1000)
        self.assertEqual(D(r['components']['fees']),14)

    def test_sell_oversized_is_atomic(self):
        self.cash();self.buy();count=len(self.store.state()['events'])
        with self.assertRaises(DomainError):self.event('SELL',instrument_id=self.i['id'],quantity='101',price='100')
        self.assertEqual(len(self.store.state()['events']),count)

    def test_insufficient_cash_is_atomic(self):
        self.cash(99)
        with self.assertRaises(DomainError):self.buy(1,100)
        self.assertEqual(len(self.store.state()['events']),1)

    def test_dividend_receivable_then_cash(self):
        self.cash();self.buy(1000,10);self.price(10)
        self.event('DIVIDEND','2024-01-02',instrument_id=self.i['id'],gross_amount='200',net_amount='200',pay_date='2024-01-03')
        self.price('9.8','2024-01-02')
        a=self.snap('2024-01-02');b=self.snap('2024-01-03')
        self.assertEqual(D(a['total_assets']),10000);self.assertEqual(D(a['pnl']),0)
        self.assertEqual(D(a['cash'][0]['balance']),0);self.assertEqual(D(a['receivables'][0]['amount']),200)
        self.assertEqual(D(b['cash'][0]['balance']),200);self.assertEqual(D(b['pnl']),0)
        self.assertEqual(D(b['components']['dividends']),200)

    def test_dividend_tax(self):
        self.cash();self.buy(1000,10);self.price(10)
        self.event('DIVIDEND','2024-01-02',instrument_id=self.i['id'],gross_amount='200',net_amount='180',tax='20')
        self.price('9.8','2024-01-02');r=self.snap()
        self.assertEqual(D(r['pnl']),-20);self.assertEqual(D(r['components']['taxes']),20)

    def test_reinvestment_no_external_principal(self):
        self.cash();self.buy(1000,10);self.price(10)
        self.event('REINVEST','2024-01-02',instrument_id=self.i['id'],gross_amount='200',net_amount='200',
            pay_date='2024-01-02',reinvest_quantity='20',reinvest_price='10',tags=['红利'],asset_class='权益',region='国内')
        self.price('9.8','2024-01-02');r=self.snap()
        self.assertEqual(D(r['capital']),10000);self.assertEqual(D(self.holding(r)['quantity']),1020)
        self.assertEqual(D(self.holding(r)['net_invested_base']),10000)
        self.assertEqual(D(r['pnl']),D('-4'))  # actual reinvest NAV differs from the 9.8 closing mark

    def test_early_reinvestment_consumes_receivable_once(self):
        self.cash();self.buy(1000,10);self.price(10)
        self.event('REINVEST','2024-01-02',instrument_id=self.i['id'],gross_amount='200',net_amount='200',
            pay_date='2024-01-04',reinvest_date='2024-01-02',reinvest_quantity='20',reinvest_price='10')
        self.price('9.8','2024-01-02');r=self.snap('2024-01-04')
        self.assertEqual(D(r['cash'][0]['balance']),0);self.assertEqual(r['receivables'],[])
        self.assertEqual(D(self.holding(r)['net_invested_base']),10000)

    def test_missing_fx_never_assumes_one(self):
        self.event('DEPOSIT',currency='HKD',amount='10000',fx_rate='.9')
        r=self.snap();self.assertIsNone(r['total_assets']);self.assertTrue(r['incomplete'])
        self.assertEqual(D(r['cash'][1]['balance']),10000)

    def test_foreign_cash_fx_pnl(self):
        self.event('DEPOSIT',currency='HKD',amount='10000',fx_rate='.9')
        self.s.save_fx({'currency':'HKD','date':'2024-01-01','rate':'.9','mode':'daily'})
        self.s.save_fx({'currency':'HKD','date':'2024-01-02','rate':'.92','mode':'daily'})
        r=self.snap();self.assertEqual(D(r['total_assets']),9200);self.assertEqual(D(r['components']['fx']),200)

    def test_locked_trade_rate_survives_global_change(self):
        i=self.hk();self.event('DEPOSIT',currency='HKD',amount='10000',fx_rate='.9')
        e=self.buy(1000,10,instrument_id=i['id'],fx_rate='.9')
        self.price(10,instrument_id=i['id']);self.s.save_fx({'currency':'HKD','date':'2024-01-01','rate':'.92'})
        r=self.snap();self.assertEqual(D(r['total_assets']),9200)
        self.assertEqual(D(next(x for x in self.store.state()['events'] if x['id']==e['id'])['fx_rate']),D('.9'))
        self.assertEqual(D(next(h for h in r['holdings'] if h['instrument_id']==i['id'])['net_invested_base']),9000)

    def test_fx_manual_effective_date_and_return_to_auto(self):
        self.s.save_fx({'currency':'HKD','date':'2024-01-01','rate':'.9','mode':'daily'})
        self.s.save_fx({'currency':'HKD','date':'2024-01-02','rate':'.92','mode':'manual'})
        self.s.save_fx({'currency':'HKD','date':'2024-01-03','mode':'auto'})
        m=Market(self.store.state());self.assertEqual(dec_rate(m,'2024-01-01'),D('.9'))
        self.assertEqual(dec_rate(m,'2024-01-02'),D('.92'));self.assertEqual(dec_rate(m,'2024-01-03'),D('.9'))

    def test_fifo_tag_quantity_independent_of_average_cost(self):
        self.cash(40000);self.buy(100,100,tags=['红利']);self.buy(100,200,tags=['成长','消费'])
        self.event('SELL','2024-01-02',instrument_id=self.i['id'],quantity='50',price='100');self.price(100,'2024-01-02')
        r=self.snap();h=self.holding(r)
        self.assertEqual(D(h['cost_native']),22500)  # moving average cost
        self.assertEqual([D(l['quantity']) for l in h['lots']],[D(50),D(100)])
        tag_report=self.s.tag_report(self.p['id'],'tags')
        values={g['label']:D(g['value']) for g in tag_report['groups']}
        self.assertEqual(values,{'红利':D(5000),'成长':D(10000),'消费':D(10000)})

    def test_same_security_separate_portfolio_records(self):
        q=self.s.save_portfolio({'funding_mode':'cash','name':'另一个','planned_amount':'10000','start_date':'2024-01-01'})
        self.s.attach_instrument(q['id'],self.i['id'],{'target_pct':'100'})
        self.cash();self.buy(100,100)
        self.cash(1000,portfolio_id=q['id']);self.buy(10,100,portfolio_id=q['id'])
        self.price(100)
        a=self.s.instrument_detail(self.p['id'],self.i['id']);b=self.s.instrument_detail(q['id'],self.i['id'])
        self.assertEqual(len(a['trades']),1);self.assertEqual(len(b['trades']),1)
        self.assertNotEqual(a['trades'][0]['id'],b['trades'][0]['id'])
        self.assertEqual(a['holding']['quantity'],'100');self.assertEqual(b['holding']['quantity'],'10')

    def test_retired_reservation_cannot_be_created(self):
        self.cash(60000)
        with self.assertRaises(DomainError):self.s.save_reservation({'portfolio_id':self.p['id'],'amount':'10000','currency':'CNY','date':'2024-01-02'})
        r=self.snap();self.assertEqual(D(r['available_cash']),60000);self.assertEqual(D(r['pnl']),0)

    def test_old_reservations_no_longer_block_spending(self):
        self.cash(60000)
        with self.store.transaction() as db:
            record={'id':'old-reserve','portfolio_id':self.p['id'],'amount':'55000','currency':'CNY','date':'2024-01-02'}
            db.execute('INSERT INTO reservations VALUES (?,?,?)',(record['id'],self.p['id'],json.dumps(record)))
        self.buy(100,100,'2024-01-03',reservation_id='old-reserve');self.price(100,'2024-01-03')
        r=self.snap();self.assertEqual(D(r['cash'][0]['reserved']),0);self.assertEqual(D(r['available_cash']),50000)

    def test_fund_in_transit_then_confirmation(self):
        self.cash();self.buy(1000,10,'2024-01-02',payment_date='2024-01-01',confirmation_date='2024-01-03')
        a=self.snap('2024-01-01');self.assertEqual(D(a['total_assets']),10000);self.assertEqual(D(a['cash'][0]['balance']),0)
        self.assertEqual(len(a['receivables']),1)
        self.assertEqual(D(self.holding(self.snap('2024-01-02'))['available_quantity']),0)
        with self.assertRaises(DomainError):self.event('SELL','2024-01-02',instrument_id=self.i['id'],quantity='1',price='10')
        self.assertEqual(D(self.holding(self.snap())['available_quantity']),1000)

    def test_redemption_receivable_not_cash(self):
        self.cash();self.buy();self.price(100)
        self.event('SELL','2024-01-02',instrument_id=self.i['id'],quantity='100',price='100',payment_date='2024-01-04')
        r=self.snap();self.assertEqual(D(r['total_assets']),10000);self.assertEqual(D(r['available_cash']),0)
        self.assertEqual(D(self.snap('2024-01-04')['available_cash']),10000)

    def test_opening_value_is_period_basis_not_historic_cost(self):
        self.event('OPENING_POSITION',instrument_id=self.i['id'],quantity='100',price='100',historical_cost='8000')
        self.price(110,'2024-01-02');r=self.snap();self.assertEqual(D(r['pnl']),1000)
        self.assertIsNone(self.holding(r)['net_invested_base'])

    def test_split_preserves_assets_cost_and_tags(self):
        self.cash();self.buy(tags=['红利']);self.price(100)
        self.event('SPLIT','2024-01-02',instrument_id=self.i['id'],factor='2');self.price(50,'2024-01-02')
        r=self.snap();self.assertEqual(D(r['pnl']),0);self.assertEqual(D(self.holding(r)['quantity']),200)
        self.assertEqual(D(self.holding(r)['cost_native']),10000)

    def test_legacy_manual_price_is_preserved_but_not_used_for_valuation(self):
        self.cash();self.buy(100,1,'2024-01-01')
        with self.store.connection() as db:
            row={'instrument_id':self.i['id'],'date':'2024-01-01','price':'100','kind':'raw','manual':True,'source':'legacy'}
            db.execute('INSERT INTO prices(instrument_id,date,kind,manual,data) VALUES (?,?,?,?,?)',(self.i['id'],'2024-01-01','raw',1,json.dumps(row)));db.commit()
        self.s.cache_prices(self.i['id'],[{'date':'2024-01-01','price':'90','source':'自动源'}])
        self.assertEqual(self.snap('2024-01-01')['holdings'][0]['price'],'90')
        self.assertTrue(any(r['manual'] for r in self.store.state()['prices']))

    def test_history_group_returns_compound(self):
        self.cash();self.buy();self.price(100);self.price(110,'2024-01-02');self.price(99,'2024-01-03')
        r=self.snap();self.assertEqual(D(r['pnl']),-100);self.assertAlmostEqual(D(r['twr']),D('-.01'))
        monthly=r['periods'][0];self.assertEqual(D(monthly['profit']),-100);self.assertAlmostEqual(D(monthly['return']),D('-.01'))

    def test_empty_portfolio_has_no_fabricated_return(self):
        r=self.snap();self.assertIsNone(r['twr']);self.assertIsNone(r['periods'][0]['return'])

    def test_return_restarts_after_zero_assets(self):
        self.cash(10000);self.event('WITHDRAW','2024-01-02',amount='10000',currency='CNY')
        self.cash(5000,'2024-01-03');r=self.snap()
        self.assertEqual(D(r['twr']),0);self.assertEqual(r['return_segment'],2)
        self.assertIsNone(r['selected_return']);self.assertFalse(r['periods'][0]['segment_split'])
        self.assertIsNone(r['periods'][0]['return'])
        self.assertEqual(r['selected_segment_count'],1)

    def test_return_segments_do_not_compound_separate_investments(self):
        self.cash();self.buy();self.price(110,'2024-01-02')
        self.event('SELL','2024-01-02',instrument_id=self.i['id'],quantity='100',price='110')
        self.event('WITHDRAW','2024-01-02',amount='11000',currency='CNY')
        self.cash(5000,'2024-01-03');self.buy(50,100,'2024-01-03');self.price(100,'2024-01-03');self.price(110,'2024-01-04')
        r=self.snap('2024-01-04')
        self.assertEqual(D(r['twr']),D('.1'));self.assertEqual(D(r['selected_return']),D('.1'))
        self.assertEqual(D(r['pnl']),1500);self.assertEqual(D(r['curve'][-1]['portfolio_return']),D('.1'))
        recent=self.s.dashboard(self.p['id'],start='2024-01-03',end='2024-01-04')
        self.assertEqual(D(recent['selected_return']),D(500)/D(15000))

    def test_invalid_performance_range_is_rejected(self):
        with self.assertRaises(DomainError): self.s.dashboard(self.p['id'],start='2024-01-04',end='2024-01-03')

    def test_restore_waits_for_market_write_to_finish(self):
        content=self.s.backup_download();self.s.syncing=True
        with self.assertRaises(DomainError): self.s.restore(content)
        self.s.syncing=False
        self.assertTrue(self.s.restore(content)['restored'])

    def test_new_buy_after_unknown_opening_cost_is_known(self):
        self.event('OPENING_POSITION',instrument_id=self.i['id'],quantity='100',price='100')
        self.event('SELL','2024-01-02',instrument_id=self.i['id'],quantity='100',price='110')
        self.buy(10,100,'2024-01-03');self.price(110,'2024-01-03')
        self.assertEqual(D(self.holding()['reference_gain_native']),100)

    def test_duplicate_and_csv_roundtrip(self):
        self.cash()
        with self.assertRaises(DomainError):self.cash()
        exported=self.s.csv_export(self.p['id']);r=self.s.csv_preview(exported,'events',self.p['id'])
        self.assertEqual(r['errors'],[]);self.assertEqual(r['duplicates'],1)
        self.assertEqual(self.s.csv_import(exported,'events',self.p['id'])['imported'],0)

    def test_csv_roundtrip_with_browser_boolean_fields_and_linked_reservation(self):
        self.cash(tax_known=True,ex_date_known=True,pay_date='2024-01-01',reinvest_date='2024-01-01')
        reserve={'id':'legacy-reservation'}
        self.buy(10,100,tax_known=True,ex_date_known=True,reservation_id=reserve['id'])
        exported=self.s.csv_export(self.p['id']);r=self.s.csv_preview(exported,'events',self.p['id'])
        self.assertEqual(r['errors'],[]);self.assertEqual(r['duplicates'],2)
        self.assertEqual(self.s.csv_import(exported,'events',self.p['id'])['imported'],0)

    def test_csv_distinct_original_ids_preserve_identical_fills(self):
        text='event_id,kind,date,currency,amount,fx_rate\n'+'a'*32+',DEPOSIT,2024-01-01,CNY,100,1\n'+'b'*32+',DEPOSIT,2024-01-01,CNY,100,1\n'
        self.assertEqual(self.s.csv_import(text,'events',self.p['id'])['imported'],2)
        self.assertEqual(D(self.snap()['cash_total']),200)
        r=self.s.csv_import(self.s.csv_export(self.p['id']),'events',self.p['id'])
        self.assertEqual(r['imported'],0);self.assertEqual(r['skipped_duplicates'],2)

    def test_csv_original_id_cannot_overwrite_changed_history(self):
        self.cash();text=self.s.csv_export(self.p['id']).replace(',10000,',',20000,')
        with self.assertRaises(DomainError): self.s.csv_import(text,'events',self.p['id'])
        self.assertEqual(D(self.snap()['cash_total']),10000)

    def test_daily_price_attribution_does_not_reprice_old_price_profit_when_fx_moves(self):
        i=self.hk();self.event('DEPOSIT',currency='HKD',amount='10000',fx_rate='.9')
        self.buy(1000,10,instrument_id=i['id'],fx_rate='.9')
        for d,p,fx in [('2024-01-01','10','.9'),('2024-01-02','11','.92'),('2024-01-03','11','.94')]:
            self.price(p,d,instrument_id=i['id']);self.s.save_fx({'currency':'HKD','date':d,'rate':fx,'mode':'daily'})
        r=self.snap();self.assertEqual(D(r['components']['price']),920);self.assertEqual(D(r['components']['fx']),420)
        self.assertEqual(D(r['pnl']),1340)
        self.assertEqual(D(r['selected_components']['price']),920)

    def test_csv_failure_rolls_back_entire_batch(self):
        text='kind,date,currency,amount,fx_rate\nDEPOSIT,2024-01-01,CNY,100,1\nWITHDRAW,2024-01-02,CNY,200,1\n'
        with self.assertRaises(DomainError):self.s.csv_import(text,'events',self.p['id'])
        self.assertEqual(self.store.state()['events'],[])

    def test_event_edit_recalculates_and_retains_audit(self):
        self.cash();e=self.buy(100,100);self.price(110)
        edited=dict(e,price='90',gross_amount='9000',change_reason='更正成交价格')
        self.s.save_event(edited,e['id']);r=self.snap();self.assertEqual(D(r['pnl']),2000)
        self.assertEqual(len(self.s.revisions('event',e['id'])),2)

    def test_cancelling_necessary_deposit_rejected(self):
        e=self.cash();self.buy()
        with self.assertRaises(DomainError):self.s.delete_event(e['id'],'测试取消')
        self.assertEqual(len(self.store.state()['events']),2)

    def test_default_tag_changes_do_not_reclassify_history(self):
        self.cash();self.buy(tags=['红利']);self.s.attach_instrument(self.p['id'],self.i['id'],{'target_pct':'50','tags':['成长']})
        self.assertEqual(self.store.state()['events'][-1]['tags'],['红利'])

    def test_calendar_month_end(self):
        self.assertEqual(month_after(date(2024,1,31),1),date(2024,2,29))

    def test_price_only_benchmark_does_not_claim_excess(self):
        p=self.s.save_portfolio(dict(self.p,benchmark_id=self.i['id'],benchmark_basis='raw'),self.p['id'])
        self.cash();self.buy();self.price(100);self.price(110,'2024-01-02')
        b=self.snap()['benchmark'];self.assertFalse(b['comparable']);self.assertIsNone(b['excess'])

    def test_transfer_is_atomic_and_aggregate_external_flow_zero(self):
        q=self.s.save_portfolio({'funding_mode':'cash','name':'另一组合','planned_amount':'10000','start_date':'2024-01-01'})
        self.cash();self.s.transfer({'source_id':self.p['id'],'target_id':q['id'],'date':'2024-01-02','amount':'1000'})
        a=self.snap();b=self.snap(portfolio_id=q['id'])
        self.assertEqual(D(a['capital'])+D(b['capital']),10000)
        result=self.s.csv_import(self.s.csv_export(self.p['id']),'events',self.p['id'])
        self.assertEqual(result['imported'],0);self.assertEqual(result['skipped_duplicates'],2)
        self.assertEqual(D(a['pnl'])+D(b['pnl']),0)

    def test_backup_restore_preserves_current_copy_and_all_records(self):
        self.cash();self.buy();self.price(100);backup=self.s.backup_download()
        self.cash(1000,'2024-01-02');self.assertEqual(D(self.snap()['cash'][0]['balance']),1000)
        result=self.s.restore(backup);self.assertTrue((self.store.backups/result['preserved_backup']).exists())
        self.assertEqual(D(self.snap()['cash'][0]['balance']),0);self.assertEqual(len(self.store.state()['events']),2)

    def test_unknown_dividend_tax_remains_visible(self):
        self.cash();self.buy();self.price(100)
        self.event('DIVIDEND','2024-01-02',instrument_id=self.i['id'],net_amount='100',tax_known=False)
        self.assertTrue(any('分红税额' in s for s in self.snap()['warnings']))


def dec_rate(m,d):return D(m.fx('HKD',d)['rate'])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,testing=True,auto_sync=False)
        self.client=TestClient(self.app);self.bootstrap=self.client.get('/api/bootstrap').json()
        self.headers={'x-csrf-token':self.bootstrap['csrf_token']}
    def tearDown(self):self.client.close();self.tmp.cleanup()
    def test_starts_empty(self):self.assertEqual(self.bootstrap['portfolios'],[])
    def test_write_requires_csrf(self):self.assertEqual(self.client.post('/api/portfolios',json={}).status_code,403)
    def test_foreign_origin_and_host_rejected(self):
        self.assertEqual(self.client.get('/api/bootstrap',headers={'origin':'https://example.org'}).status_code,403)
        self.assertEqual(self.client.get('/api/bootstrap',headers={'host':'evil.example'}).status_code,400)
    def test_create_real_portfolio_and_invalid_write(self):
        r=self.client.post('/api/portfolios',headers=self.headers,json={'name':'真实账本','planned_amount':'10000','start_date':'2024-01-01'})
        self.assertEqual(r.status_code,200,r.text)
        bad=self.client.post('/api/portfolios',headers=self.headers,json={'name':'','planned_amount':'-1'})
        self.assertEqual(bad.status_code,422)
    def test_backup_restore_confirmation(self):
        data=self.client.get('/api/backup').content
        r=self.client.post('/api/restore',headers=self.headers,json={'content':base64.b64encode(data).decode()})
        self.assertEqual(r.status_code,422)
        r=self.client.post('/api/restore',headers=self.headers,json={'content':base64.b64encode(data).decode(),'confirm':'RESTORE'})
        self.assertEqual(r.status_code,200,r.text)


if __name__=='__main__':unittest.main()
