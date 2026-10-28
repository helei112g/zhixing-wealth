import tempfile
import unittest
import io
import zipfile
from unittest.mock import patch
from decimal import Decimal as D
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError
from backend.service import Service
from backend.store import Store
from backend.store import MAX_BACKUP_DATABASE_BYTES


class EventEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.s=Service(Store(Path(self.temp.name)))
        self.p=self.s.save_portfolio({'name':'合成录入验收','start_date':'2023-01-01','planned_amount':'100000'})
        self.buy=self.s.save_event({'portfolio_id':self.p['id'],'kind':'BUY','date':'2023-01-03',
            'instrument':{'market':'US','code':'VTI'},'quantity':'3','gross_amount':'675','transaction_costs':'1.02','fx_rate':'7'})

    def tearDown(self):self.temp.cleanup()

    def dividend(self,**kw):
        return self.s.save_event({'portfolio_id':self.p['id'],'kind':'DIVIDEND','instrument_id':self.buy['instrument_id'],
            'date':'2023-02-01','pay_date':'2023-02-03','fx_rate':'6.8','pay_fx_rate':'6.9',
            'gross_amount':'10.8023','tax':'1.2345',**kw})

    def test_gross_minus_tax_uses_decimal_and_preserves_legacy_net_input(self):
        e=self.dividend();self.assertEqual(e['net_amount'],'9.5678')
        old=self.dividend(date='2023-03-01',pay_date='2023-03-03',gross_amount=None,net_amount='9.5678')
        self.assertEqual(old['gross_amount'],'10.8023')
        self.assertEqual(old['net_amount'],e['net_amount'])
        with self.assertRaises(DomainError):self.dividend(net_amount='10')
        with self.assertRaises(DomainError):self.dividend(tax='11')

    def test_unknown_deduction_state_and_zero_net_are_not_lost(self):
        e=self.dividend(tax_known=False);self.assertFalse(e['tax_known']);self.assertEqual(e['tax'],'1.2345')
        zero=self.dividend(date='2023-03-01',pay_date='2023-03-03',gross_amount='10',tax='10')
        self.assertEqual(zero['net_amount'],'0')

    def test_cost_price_and_sale_net_price_do_not_repeat_dividend_tax(self):
        d=self.dividend(kind='REINVEST',reinvest_quantity='1',reinvest_gross_amount='8',fee='.5',reinvest_date='2023-02-06',reinvest_fx_rate='7.1')
        sale=self.s.save_event({'portfolio_id':self.p['id'],'kind':'SELL','instrument_id':self.buy['instrument_id'],
            'date':'2023-04-03','sell_lots':[{'buy_event_id':self.buy['id'],'quantity':'1'}],
            'gross_amount':'250','transaction_costs':'1.5','fx_rate':'7'})
        facts={r['event']['id']:r['facts'] for r in self.s.transaction_rows(self.p['id'])}
        self.assertEqual(D(facts[self.buy['id']]['unit_price']),D('225'))
        self.assertEqual(D(facts[self.buy['id']]['unit_after_costs']),D('225.34'))
        self.assertEqual(D(facts[d['id']]['unit_after_costs']),D('8.5'))
        self.assertEqual(D(facts[sale['id']]['unit_after_costs']),D('248.5'))
        self.assertEqual(facts[sale['id']]['unit_after_costs_label'],'扣费税净回款')

    def test_pay_and_reinvest_rate_provenance_validated_and_roundtrips(self):
        e=self.dividend(pay_fx_source_date='2023-02-02',pay_fx_type='reference')
        self.assertEqual(e['pay_fx_source_date'],'2023-02-02')
        self.assertIn('pay_fx_source_date',self.s.csv_export(self.p['id']))
        with self.assertRaises(DomainError):self.dividend(pay_fx_source_date='2023-02-04')
        with self.assertRaises(DomainError):self.dividend(kind='REINVEST',reinvest_quantity='1',reinvest_price='8',reinvest_date='2023-02-06',reinvest_fx_source_date='2023-02-07')

    def test_reference_uses_date_currency_and_never_future_or_interval_override(self):
        self.s.save_fx({'currency':'USD','date':'2023-01-03','rate':'6.8','mode':'daily'})
        self.s.save_fx({'currency':'USD','date':'2023-02-03','rate':'6.9','mode':'daily'})
        self.s.save_fx({'currency':'USD','date':'2023-01-01','rate':'99','mode':'manual'})
        with TestClient(create_app(self.temp.name,testing=True,auto_sync=False)) as c:
            early=c.get('/api/reference-rate?currency=USD&date=2023-01-01').json();self.assertIsNone(early['reference'])
            weekend=c.get('/api/reference-rate?currency=USD&date=2023-02-04').json()['reference']
            self.assertEqual(weekend['rate'],'6.9');self.assertEqual(weekend['date'],'2023-02-03')
            first=c.get('/api/reference-rate?currency=USD&date=2023-01-03').json()['reference'];self.assertEqual(first['rate'],'6.8')
            self.assertEqual(c.get('/api/reference-rate?currency=CNY&date=2023-01-03').json()['reference']['rate'],'1')
            self.assertEqual(c.get('/api/reference-rate?currency=USD&date=2999-01-01').status_code,422)

    def test_backup_capacity_guards_reject_before_replacing_current_data(self):
        self.assertEqual(MAX_BACKUP_DATABASE_BYTES,512*1024*1024)
        archive=self.s.backup_download()
        raw=zipfile.ZipFile(io.BytesIO(archive)).read('portfolio.sqlite')
        before=self.s.store.state()
        for boundary in ('backend.service.MAX_BACKUP_DATABASE_BYTES','backend.store.MAX_BACKUP_DATABASE_BYTES'):
            with patch(boundary,len(raw)-1):
                with self.assertRaises(DomainError):self.s.restore(archive)
            self.assertEqual(self.s.store.state(),before)
        result=self.s.restore(archive)
        self.assertTrue(result['restored'])
        self.assertEqual(self.s.store.state()['events'],before['events'])
        self.assertTrue((self.s.store.backups/result['preserved_backup']).exists())


if __name__=='__main__':unittest.main()
