#!/usr/bin/env python3
"""Loopback JSON API client. No browser, third-party packages or stored credentials."""
from __future__ import annotations
import argparse
import http.cookiejar
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


PORTS = {'dev': 8877, 'production': 8765}


def default_environment():
    # Resolve the script location, never the caller's working directory. During
    # prepare the release tree exists before release.json has been written.
    root = Path(__file__).resolve().parents[1]
    installed = root.parent.name == 'releases' and root.parent.parent.name == 'runtime'
    return 'production' if installed or (root / 'release.json').is_file() else 'dev'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError('API redirects are not allowed')


class Client:
    # GET /api/capabilities exposes storage policy; GET /api/jobs is only the
    # latest 20 jobs (UI shows 5), not a complete audit log. Price counts distinguish saved
    # changed versions from unchanged rows. Sync never changes locked trade FX.
    # corporate_actions checks source data, not an assertion that shares exist.
    # Read notes and dividend candidate note for cash amount/class differences;
    # a successful retry clears the matching reminder without deleting old jobs.
    # Listed fund dividends are checked by ex-date against the same code's NAV;
    # inspect the dividends sync item before relying on a newly published row.
    def __init__(self, base_url=None, *, environment=None, expected_data_dir=None):
        if environment is not None and environment not in PORTS:
            raise ValueError('Unknown target environment')
        if base_url is None:
            environment = environment or default_environment()
            base_url = f'http://127.0.0.1:{PORTS[environment]}'
        parsed = urllib.parse.urlsplit(base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment
                or parsed.port is None):
            raise ValueError('Only an explicit loopback HTTP origin is allowed')
        port_environment = next((key for key, port in PORTS.items() if port == parsed.port), None)
        if environment is None:
            environment = port_environment
        if environment is None or (port_environment is not None and port_environment != environment):
            raise ValueError('Target port and environment must agree; custom ports require --environment')
        self.environment = environment
        self.expected_data_dir = Path(expected_data_dir).resolve() if expected_data_dir is not None else None
        self.base = base_url.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())
        self.csrf = ''

    def request(self, path, body=None, method=None):
        if not path.startswith('/api/') or '..' in path.split('/') or '#' in path or '\\' in path:
            raise ValueError('Use an /api/ path on the same local service')
        health = self._request('/api/health')
        if (not isinstance(health, dict) or health.get('ok') is not True
                or health.get('application') != 'portfolio-journal'
                or health.get('environment') != self.environment):
            raise RuntimeError('Service identity or environment does not match the target; request stopped')
        if self.expected_data_dir is not None and (
                not isinstance(health.get('data_directory'), str)
                or Path(health['data_directory']).resolve() != self.expected_data_dir):
            raise RuntimeError('Service ledger directory does not match the target; request stopped')
        if path == '/api/health' and body is None and method in (None, 'GET'):
            return health
        return self._request(path, body, method)

    def _request(self, path, body=None, method=None):
        headers = {'Accept': 'application/json', 'Origin': self.base}
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf})
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers=headers, method=method or ('POST' if body is not None else 'GET'))
        try:
            with self.opener.open(req, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try: message = json.loads(exc.read())
            except (ValueError, UnicodeDecodeError): message = {'error': exc.reason}
            raise RuntimeError(f'HTTP {exc.code}: {json.dumps(message, ensure_ascii=False)}') from None

    def connect(self):
        value = self.request('/api/bootstrap')
        self.csrf = value.pop('csrf_token')
        return value

    def preview(self, payload, path='/api/events/preview'):
        """Return the full preview, including split_changes after historical backfills."""
        if not isinstance(payload, dict): raise ValueError('Command must be a JSON object')
        if path not in ('/api/events/preview', '/api/family/preview', '/api/research/indices/resolve', '/api/research/collection/preview') and not re.fullmatch(r'/api/portfolios/[^/]+/purchase-plans/preview', path):
            raise ValueError('Use a documented preview endpoint')
        if (path == '/api/family/preview' or path.endswith('/purchase-plans/preview')) and (type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 0):
            raise ValueError('Preview requires the original ledger revision')
        self.connect()
        return self.request(path, payload)

    def write(self, path, payload, method):
        if not isinstance(payload, dict): raise ValueError('Command must be a JSON object')
        # Only the documented, atomically revision-checked mutations are exposed here.
        core = (method == 'POST' and path in ('/api/portfolios', '/api/events') or
                method == 'POST' and re.fullmatch(r'/api/portfolios/[^/]+/purchase-plans', path) or
                method == 'PUT' and re.fullmatch(r'/api/(portfolios/[^/]+(?:/instruments/[^/]+(?:/labels)?)?|events/[^/]+|instruments/[^/]+/name)', path) or
                method == 'DELETE' and re.fullmatch(r'/api/(events/[^/]+|portfolios/[^/]+/purchase-plans/[^/]+)', path) or
                method == 'POST' and re.fullmatch(r'/api/dividends/[^/]+/(confirm|review)', path))
        notes = (method == 'POST' and re.fullmatch(r'/api/portfolios/[^/]+/instruments/[^/]+/notes(?:/[^/]+/restore)?', path) or
                 method in ('PUT', 'DELETE') and re.fullmatch(r'/api/portfolios/[^/]+/instruments/[^/]+/notes/[^/]+', path))
        assets = method == 'POST' and path == '/api/family/commands'
        revision_field='expected_revision'
        research=(method=='PUT' and path=='/api/research/collection' or
                  method=='POST' and path=='/api/market-waterlevel/snapshots')
        settings=method=='PUT' and path in ('/api/settings/data-updates','/api/market-waterlevel/settings')
        update=method=='POST' and path in ('/api/data-updates/run','/api/market-waterlevel/updates')
        if research:revision_field='expected_research_revision'
        if settings:revision_field='expected_settings_revision'
        if not (core or notes or assets or research or settings or update):raise ValueError('Use a documented mutation')
        if not update and (type(payload.get(revision_field)) is not int or payload[revision_field]<0):
            raise ValueError('Include the original '+revision_field+' from the reviewed read/preview')
        if update and not isinstance(payload.get('request_id'),str):raise ValueError('Include the original request_id')
        self.connect()
        # Never retry a mutation or replace its expected_revision automatically.
        return self.request(path, payload, method)

    def purchase_plan_preview(self, portfolio_id, payload):
        return self.preview(payload, '/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/purchase-plans/preview')

    def purchase_plan_deletion_preview(self, portfolio_id, instrument_id):
        path = ('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/purchase-plans/'
                + urllib.parse.quote(instrument_id, safe='') + '/deletion-preview')
        return self.request(path)

    def allocation_analysis(self, portfolio_id, **filters):
        return self.request('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/allocation-analysis?' + urllib.parse.urlencode(filters))

    def cashflow(self, year, include_fund=True):
        return self.request('/api/family/cashflow?' + urllib.parse.urlencode(dict(year=year, include_fund=str(include_fund).lower())))

    def resolve_index(self,code):
        return self.preview({'code':code},'/api/research/indices/resolve')

    def collection_preview(self,code,modules,history_start=None,*,primary_valuation=None):
        """Preview collection and its verification source (official for catalog overseas indices)."""
        return self.preview(dict(code=code, modules=modules, history_start=history_start, primary_valuation=primary_valuation),
                            '/api/research/collection/preview')

    def research_studio(self,index_id,**options):
        return self.request('/api/research/indices/'+urllib.parse.quote(index_id,safe='')+'/studio?'+urllib.parse.urlencode(options))

    def research_indices(self, page=1, method='mcw', kind=None, *, sort_by=None, sort_direction='desc', valuation_group=None):
        params = dict(page=page, method=method)
        if kind is not None:
            params['kind'] = kind
        if sort_by is not None:
            params.update(sort_by=sort_by, sort_direction=sort_direction)
        if valuation_group is not None:
            params['valuation_group'] = valuation_group
        return self.request('/api/research/indices?' + urllib.parse.urlencode(params))

    def market_waterlevel(self, method='mcw', asof=None, facts=False):
        params = dict(method=method)
        if asof is not None:
            params['asof'] = asof
        return self.request('/api/market-waterlevel' + ('/facts' if facts else '') + '?' + urllib.parse.urlencode(params))

    def market_data_sources(self):
        """Market update resources, observation dates and the instance's weekly schedule."""
        return self.request('/api/market-waterlevel/sources')

    def market_activity(self, asof=None):
        """Daily margin balances and turnover from the currently published local snapshots."""
        query = '?' + urllib.parse.urlencode({'asof': asof}) if asof is not None else ''
        return self.request('/api/market-waterlevel/activity' + query)

    def research_index(self,index_id,basis='price',window=3,method='mcw'):
        return self.request('/api/research/indices/'+urllib.parse.quote(index_id,safe='')+'?'+urllib.parse.urlencode({'basis':basis,'window':window,'method':method}))

    def research_part(self,index_id,part,*,basis='price',window=3,method='mcw',analysis_id=None,page=1,page_size=20):
        if part not in ('series','constituents','financials','sources'):raise ValueError('Unknown research part')
        params=dict(basis=basis,window=window,method=method,page=page,page_size=page_size)
        if analysis_id is not None:params['analysis_id']=analysis_id
        return self.request('/api/research/indices/'+urllib.parse.quote(index_id,safe='')+'/'+part+'?'+urllib.parse.urlencode(params))

    def update_settings(self):
        return self.request('/api/settings/data-updates')

    def family_bounds(self):
        return self.request('/api/family/bounds')

    def update_history(self):
        """Five unified display records; use the job endpoint for durable outcomes."""
        return self.request('/api/data-updates/history')

    def refresh_index(self, index_id, request_id):
        """Authorized current-index refresh; caller retains the stable request_id."""
        if not isinstance(index_id,str) or not index_id:raise ValueError('An exact index ID is required')
        return self.write('/api/data-updates/run',dict(targets=[index_id],
            scopes=['index_prices','memberships','valuations','financials'],request_id=request_id),'POST')

    def notification_status(self):
        """One pending OS request; deferred_count is waiting work, not a failure.

        Subsequent requests require the local service to keep running. Neither
        pending nor delivered IDs establish that the person saw the reminder.
        """
        return self.request('/api/notifications')

    def research_source_check(self,index_id,analysis_id):
        """Explicitly authorized official request; consumes no Lixinger quota."""
        self.connect()
        return self.request('/api/research/indices/'+urllib.parse.quote(index_id,safe='')+'/source-check',dict(analysis_id=analysis_id))

    def research_scenario(self,index_id,payload):
        """Read-only scenario; read scenario_metrics and obtain explicit uniform-scaling assumptions first."""
        if not isinstance(payload,dict):raise ValueError('Scenario must be a JSON object')
        self.connect()
        return self.request('/api/research/indices/'+urllib.parse.quote(index_id,safe='')+'/scenarios',payload)

    def portfolio_overview(self):
        """Read the same cross-portfolio CNY totals and returns as the UI."""
        return self.request('/api/portfolios/overview')

    def investment_summary(self, portfolio_id):
        """Read canonical cost/net input and cash/reinvested dividend components; never recalculate."""
        return self.request('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='')
                            + '/dashboard?view=summary')['capital_summary']

    def holding_labels(self, portfolio_id, instrument_id):
        """Read current labels and the original revision; review the diff before PUT."""
        return self.request('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/instruments/'
                            + urllib.parse.quote(instrument_id, safe='') + '/labels')

    def holding_distribution(self, portfolio_id, basis='holdings'):
        if basis not in ('holdings', 'assets'):
            raise ValueError('basis must be holdings or assets')
        return self.request('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='')
                            + '/holding-analysis?' + urllib.parse.urlencode({'basis': basis}))

    def deletion_preview(self, event_id):
        """Read the complete causal deletion list; submit its original revision and IDs."""
        return self.request('/api/events/' + urllib.parse.quote(event_id, safe='') + '/deletion-preview')

    def instrument_detail(self, portfolio_id, instrument_id):
        """Read holding and market_changes; quote ratios are not investment returns."""
        return self.request('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/instruments/'
                            + urllib.parse.quote(instrument_id, safe=''))

    def plan_check(self, portfolio_id, instrument_id, target_pct):
        """Preview remaining budget allocation and independent plan cash shortfall."""
        path = ('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/instruments/'
                + urllib.parse.quote(instrument_id, safe='') + '/plan-check')
        return self.request(path + '?' + urllib.parse.urlencode({'target_pct': target_pct}))

    def reinvestment_estimate(self, portfolio_id, instrument_id, date, gross_amount):
        """Read an estimate only; caller must review quantity before event preview/write."""
        path = ('/api/portfolios/' + urllib.parse.quote(portfolio_id, safe='') + '/instruments/'
                + urllib.parse.quote(instrument_id, safe='') + '/reinvestment-estimate')
        return self.request(path + '?' + urllib.parse.urlencode({'date': date, 'gross_amount': gross_amount}))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--environment', choices=PORTS, help='Default: dev in source; production in installed releases')
    parser.add_argument('--base-url', help='Explicit loopback origin; standard ports identify their environment')
    parser.add_argument('--expected-data-dir', type=Path, help='Require health to identify this exact ledger directory')
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('target', help='Describe the selected target without connecting or changing data')
    read = commands.add_parser('read'); read.add_argument('path')
    preview = commands.add_parser('preview'); preview.add_argument('file', type=Path); preview.add_argument('--path', default='/api/events/preview')
    scenario = commands.add_parser('scenario', help='Read-only index calculation; requires the reviewed analysis_id and assumptions')
    scenario.add_argument('index_id'); scenario.add_argument('file', type=Path)
    write = commands.add_parser('write'); write.add_argument('method', choices=['POST', 'PUT', 'DELETE'])
    write.add_argument('path'); write.add_argument('file', type=Path)
    write.add_argument('--confirm-write', action='store_true', help='The human has authorized this exact mutation')
    args = parser.parse_args(); client = Client(args.base_url, environment=args.environment, expected_data_dir=args.expected_data_dir)
    if args.command == 'target':
        value = {'environment': client.environment, 'base_url': client.base,
                 'expected_data_directory': str(client.expected_data_dir) if client.expected_data_dir else None}
    elif args.command == 'read':
        value = client.connect() if args.path == '/api/bootstrap' else client.request(args.path)
    else:
        payload = json.loads(args.file.read_text())
        if args.command == 'preview': value = client.preview(payload, args.path)
        elif args.command == 'scenario': value = client.research_scenario(args.index_id, payload)
        else:
            if not args.confirm_write: parser.error('write requires --confirm-write; inspect the preview and user authorization first')
            value = client.write(args.path, payload, args.method)
    # The session token is never printed, logged or written to disk.
    if isinstance(value, dict): value.pop('csrf_token', None)
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except (ValueError, RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr); raise SystemExit(1)
