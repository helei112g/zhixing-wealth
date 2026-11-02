#!/usr/bin/env python3
"""Compare two source versions on one fresh synthetic ledger, without a server."""
import argparse
import cProfile
import hashlib
import inspect
import json
import os
from pathlib import Path
import pstats
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import timedelta

ROOT = Path(__file__).resolve().parents[1]


def comparable(value):
    # Projection timestamps describe calculation time, not a financial fact.
    # Keep every source record, financial value, unknown flag and as-of date.
    if isinstance(value, dict): return {k:comparable(v) for k,v in value.items() if k != 'generated_at'}
    if isinstance(value, list): return [comparable(v) for v in value]
    return value


def seed(directory):
    from backend.common import today
    from backend.environment import claim_environment
    from backend.family_service import FamilyService
    from backend.service import Service
    from backend.store import Store
    service = Service(Store(claim_environment(directory, 'test')))
    family = FamilyService(service.store)
    end = today(); start = end - timedelta(days=1400)
    portfolios = [service.save_portfolio(dict(name='合成基准组合'+str(n), start_date=str(start), planned_amount='1000000')) for n in range(2)]
    instruments = []
    for n in range(18):
        instrument = dict(market='US', code='SYNTH'+chr(65+n), name='合成基准标的'+str(n)) if n%3==0 else dict(market='CN', code=str(600500+n), name='合成基准标的'+str(n))
        for p in portfolios[:2 if n<4 else 1]:
            event = service.save_event(dict(portfolio_id=p['id'], kind='BUY', date=str(start), instrument=instrument,
                                           quantity='100', gross_amount='1000', fx_rate='7' if n%3==0 else '1'))
        instruments.append(event['instrument_id'])
    for n, iid in enumerate(instruments):
        service.cache_prices(iid, [dict(date=str(start+timedelta(days=d)), price=str(10+n/10+(d%30)/100),
            kind=kind, source='synthetic-benchmark') for d in range(1401) for kind in ('raw','adjusted')])
        if n<12:
            service.cache_actions(iid, [dict(kind='split_notice', date=str(start+timedelta(days=100)), factor='2', source='synthetic-benchmark')])
            if n>=6:
                service.save_event(dict(portfolio_id=portfolios[0]['id'], instrument_id=iid, kind='SPLIT',
                                        date=str(start+timedelta(days=100)), factor='2', fx_rate='1'))
    service.cache_fx([dict(currency='USD', date=str(start+timedelta(days=d)), rate='7', source='synthetic-benchmark') for d in range(1401)])
    for p in portfolios:
        family.command('link', dict(portfolio_id=p['id'], effective_on=str(start), share='1'), service.store.state()['revision'])
    family.command('item', dict(category='cash', currency='CNY', name='合成现金', effective_on=str(start), household_amount='100000'), service.store.state()['revision'])


def worker(source, directory):
    sys.path.insert(0, str(source))
    from backend.common import today, wire
    from backend.family_service import FamilyService
    from backend.ledger import Market
    from backend.service import Service
    from backend.store import Store
    assert json.loads((directory/'.environment.json').read_text())['environment'] == 'test'
    service = Service(Store(directory)); family = FamilyService(service.store)
    state = service.store.state(); revision = state['revision']; pid = state['portfolios'][0]['id']
    end = str(today()); start = end[:4]+'-01-01'
    tasks = {
        'asset_summary': lambda: family.summary('month', end[:7]),
        'asset_overview': lambda: family.read(end),
        'asset_history': lambda: family.history(start, end),
        'portfolio_full': lambda: service.dashboard(pid, end=end),
        'transactions': lambda: service.transaction_page(pid, page=1, page_size=20),
    }
    if 'view' in inspect.signature(Service.dashboard).parameters:
        tasks['portfolio_summary'] = lambda: service.dashboard(pid, end=end, view='summary')
    result = {}
    for name, load in tasks.items():
        cold, warm = [], []
        for _ in range(3):
            service.store.cache.clear(); tick = time.perf_counter(); value = load()
            cold.append((time.perf_counter()-tick)*1000)
        for _ in range(5):
            tick = time.perf_counter(); value = load(); warm.append((time.perf_counter()-tick)*1000)
        data = json.dumps(comparable(wire(value)), sort_keys=True, ensure_ascii=False).encode()
        result[name] = dict(cold_ms=round(statistics.median(cold),2), warm_ms=round(statistics.median(warm),2),
                            response_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    service.store.cache.clear(); profiler = cProfile.Profile(); profiler.runcall(tasks['asset_summary'])
    code = Market.__init__.__code__
    result['asset_summary_market_builds'] = pstats.Stats(profiler).stats[(code.co_filename,code.co_firstlineno,'__init__')][1]
    full = service.dashboard(pid, end=end)
    financial = {k:full[k] for k in ('capital_summary','holdings','todos','portfolio')}
    result['financial_sha256'] = hashlib.sha256(json.dumps(financial, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    result['shape'] = dict(instruments=len(state['instruments']), prices=len(state['prices']), fx=len(state['fx']), events=len(state['events']))
    assert service.store.state()['revision'] == revision, 'read benchmark changed its synthetic ledger'
    print(json.dumps(result))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, help='Existing reference source tree; never a data directory')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--source', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--data', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.source.resolve(), args.data.resolve()); return
    if not args.reference or not args.output: parser.error('--reference and --output are required')
    if not (args.reference/'backend/service.py').is_file(): parser.error('reference must be an existing source tree')
    if args.output.exists(): parser.error('refuse to overwrite an existing output file')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    with tempfile.TemporaryDirectory(prefix='synthetic-read-benchmark-', dir=args.output.parent) as temporary:
        directory = Path(temporary)/'data'; seed(directory)
        values = {}
        for name, source in (('reference',args.reference), ('candidate',ROOT)):
            print('Benchmarking '+name, flush=True)
            run = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', '--source', str(source.resolve()), '--data', str(directory)],
                cwd=source, env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'}, check=True, text=True, capture_output=True)
            values[name] = json.loads(run.stdout)
        for name, expected in values['reference'].items():
            if isinstance(expected, dict) and 'sha256' in expected:
                assert values['candidate'][name]['sha256'] == expected['sha256'], name+' changed result values'
        assert values['reference']['financial_sha256'] == values['candidate']['financial_sha256']
        values.update(source='fresh synthetic test ledger; no HTTP/browser time', cold_samples=3, warm_samples=5,
                      financial_results_identical=True, excluded_projection_metadata=['generated_at'], author='Leo')
        args.output.write_text(json.dumps(values, ensure_ascii=False, indent=2)+'\n')
        for name, old in values['reference'].items():
            if isinstance(old, dict) and 'cold_ms' in old:
                new = values['candidate'][name]
                print(f"{name}: cold {old['cold_ms']} -> {new['cold_ms']} ms; warm {old['warm_ms']} -> {new['warm_ms']} ms", flush=True)
        print('All shared result hashes match; output: '+str(args.output))


if __name__ == '__main__': main()
