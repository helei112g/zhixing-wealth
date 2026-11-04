"""Synthetic histories and HTTP shapes; no live data or external network in tests."""
import copy
import unittest
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from backend.research import providers, jiucai
from backend.research.metrics import valuation, years_before
from tests.research_fixtures import ASOF, jiucai_payload


class JiucaiTests(unittest.TestCase):
    def setUp(self):
        self.profile=providers.identity('930955')
        self.value=jiucai_payload(self.profile)
        self.calls=[]
        self.changes={}

    def request(self, url):
        self.calls.append(url)
        params=parse_qs(urlsplit(url).query)
        if '/detail?' in url:
            result=dict(header=dict(code='930955.CSI',name='红利低波100'),na=False)
            kind='detail'
        elif '/history?' in url:
            kind=params['metric'][0]
            result=dict(code='930955.CSI',metric=kind,percentile=99.9,
                        series=copy.deepcopy(self.value['series'][kind]['overall']['rows']))
        else:
            kind='csi'
            result=dict(data=[dict(indexCode='930955',tradeDate=params['startDate'][0],peg='999',tradingVol=0,tradingValue=0),
                              dict(indexCode='930955',tradeDate=ASOF.replace('-',''),peg='10',tradingVol=100,tradingValue=100)])
        if kind in self.changes:self.changes[kind](result)
        return result,dict(url=url,sha256='0'*64,fetched_at='2026-10-30T18:00:00+08:00')

    def fetch(self):
        with patch.object(providers,'request',side_effect=self.request),patch.object(providers,'legu_session',side_effect=AssertionError('must not call Legu')):
            return providers.valuations(self.profile,ASOF)

    def test_source_identity_no_fallback_and_official_provenance(self):
        result=self.fetch()
        self.assertEqual(len(self.calls),4)
        self.assertEqual({urlsplit(u).hostname for u in self.calls},{'ai2.jiucaishuo.com','www.csindex.com.cn'})
        self.assertEqual(result['effective_date'],ASOF)
        self.assertEqual(result['series']['pe']['overall']['field'],'pe_ttm')
        self.assertEqual(result['series']['pb']['overall']['field'],'pb_lf')
        self.assertEqual(result['cross_check']['rows'],[dict(date=ASOF,value='10')])
        self.assertEqual(len(result['evidence']),4)
        self.assertEqual(providers.valuation_options(self.profile),('CSI',))
        with self.assertRaises(ValueError):providers.fetch_valuations(dict(self.profile,valuation_code='930955.SH'),ASOF)

    def test_primary_identity_shape_and_value_failures_reject_complete_refresh(self):
        cases=[('detail',lambda o:o['header'].update(code='OTHER')),
               ('detail',lambda o:o['header'].update(name='相似指数')),
               ('pe',lambda o:o.update(metric='pb')),
               ('pb',lambda o:o.update(code='000922.CSI')),
               ('pe',lambda o:o['series'].append(o['series'][-1])),
               ('pb',lambda o:o['series'].reverse()),
               ('pe',lambda o:o['series'][-1].update(value='NaN')),
               ('pe',lambda o:o['series'][0].update(value=None)),
               ('pb',lambda o:o['series'][-1].update(value=0)),
               ('pb',lambda o:o['series'].pop()),
               ('pe',lambda o:o.update(series=[]))]
        for kind,change in cases:
            with self.subTest(kind=kind,change=change):
                self.changes={kind:change}
                with self.assertRaisesRegex(ValueError,'保留旧数据'):self.fetch()

    def test_future_and_prelaunch_observations_are_not_included(self):
        def extra(o):
            o['series'].insert(0,dict(date='2016-01-01',value='999'))
            o['series'].append(dict(date='2027-01-01',value='999'))
        self.changes={m:extra for m in ('pe','pb')}
        result=self.fetch()
        for m in ('pe','pb'):
            rows=result['series'][m]['overall']['rows']
            self.assertEqual((rows[0]['date'],rows[-1]['date']),('2017-05-26',ASOF))

    def test_preview_rejects_mismatched_primary_dates(self):
        self.changes={'pb':lambda o:o['series'].pop()}
        with patch.object(providers,'request',side_effect=self.request),patch.object(providers,'cutoff',return_value=ASOF):
            result=providers.probe_valuations(self.profile,'CSI')
        self.assertEqual(result['status'],'unavailable')
        self.assertTrue(all(v['status']=='error' for v in result['metrics'].values()))

    def test_official_failure_does_not_block_primary_and_pb_is_unverified(self):
        self.changes={'csi':lambda o:o['data'][-1].update(indexCode='000922')}
        result=self.fetch()
        self.assertEqual(result['effective_date'],ASOF)
        check=jiucai.compare({'pe':dict(date=ASOF,current='12')},result['cross_check'])
        self.assertEqual(check['pe']['status'],'unavailable')
        self.assertEqual(check['pb']['status'],'unavailable')
        self.assertEqual(len(result['evidence']),3)

    def test_official_zero_volume_carry_forward_not_used(self):
        self.changes={'csi':lambda o:o['data'][-1].update(tradingVol=0,tradingValue=0)}
        self.assertEqual(self.fetch()['cross_check']['status'],'unavailable')

    def test_same_date_threshold_both_directions_and_missing_date(self):
        reference=self.value['cross_check']
        for value,status in [('10.99','within_threshold'),('11','warning'),('9','warning'),('9.01','within_threshold')]:
            check=jiucai.compare({'pe':dict(date=ASOF,current=value)},reference)
            self.assertEqual(check['pe']['status'],status)
            self.assertEqual(Decimal(check['pe']['relative_difference']),Decimal(value)/10-1)
            self.assertEqual(check['pb']['status'],'unavailable')
        other=jiucai.compare({'pe':dict(date='2026-10-29',current='12')},reference)
        self.assertEqual(other['pe']['status'],'date_mismatch')
        self.assertNotIn('relative_difference',other['pe'])
        self.assertEqual(jiucai.compare({'pe':dict(date=ASOF,current='12')},reference,'median')['pe']['status'],'unavailable')

    def test_daily_ranks_use_raw_history_half_ties_and_exclude_current(self):
        series=self.value['series']['pe']['overall']
        series['rows'][-1]['value']='10'
        result=valuation(series,ASOF)
        for years in (3,5,10):
            values=[Decimal(r['value']) for r in series['rows'] if str(years_before(ASOF,years))<=r['date']<ASOF]
            expected=(sum(v<10 for v in values)+Decimal('.5')*sum(v==10 for v in values))/len(values)
            w=result['windows'][str(years)]
            self.assertEqual(Decimal(w['rank']),expected)
            self.assertEqual(w['count'],len(values))
            self.assertEqual(w['status'],'partial_history' if years==10 else 'available')
            self.assertEqual(w['to'],'2026-10-29')
        self.assertEqual(result['windows']['10']['from'],'2017-05-26')

    def test_partial_history_does_not_hide_gaps_sparse_samples_or_negative_current(self):
        original=self.value['series']['pe']['overall']
        for change in ('gap','sparse','current','full_gap','too_short','invalid_start'):
            s=copy.deepcopy(original)
            if change=='gap':s['rows']=[r for r in s['rows'] if not '2024-01-01'<=r['date']<'2024-07-01']
            if change=='sparse':s['rows']=s['rows'][:-1:5]+s['rows'][-1:]
            if change=='current':s['rows'][-1]['value']='-1'
            if change=='full_gap':s['rows'].insert(0,dict(date='2015-01-01',value='10'))
            if change=='too_short':s['rows']=s['rows'][-100:]
            if change=='invalid_start':
                for r in s['rows'][:500]:r['value']='-1'
            with self.subTest(change=change):self.assertIsNone(valuation(s,ASOF)['windows']['10']['rank'])
        s=copy.deepcopy(original);s.pop('allow_partial_window')
        self.assertIsNone(valuation(s,ASOF)['windows']['10']['rank'])
        s=copy.deepcopy(original);s['rows']=s['rows'][500:];s['history_start']='2017-05-26'
        self.assertIsNone(valuation(s,ASOF)['windows']['10']['rank'])


if __name__=='__main__':unittest.main()
