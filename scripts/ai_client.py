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


PORTS = {'dev': 8877, 'production': 8765, 'test': 8876}


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
        if path not in ('/api/events/preview', '/api/family/preview'):
            raise ValueError('Use a documented preview endpoint')
        if path == '/api/family/preview' and (type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 0):
            raise ValueError('Asset preview requires the revision from catalog')
        self.connect()
        return self.request(path, payload)

    def write(self, path, payload, method):
        if not isinstance(payload, dict): raise ValueError('Command must be a JSON object')
        # Only the documented, atomically revision-checked mutations are exposed here.
        core = (method == 'POST' and path in ('/api/portfolios', '/api/events') or
                method == 'PUT' and re.fullmatch(r'/api/(portfolios/[^/]+(?:/instruments/[^/]+)?|events/[^/]+)', path) or
                method == 'DELETE' and re.fullmatch(r'/api/events/[^/]+', path) or
                method == 'POST' and re.fullmatch(r'/api/dividends/[^/]+/confirm', path))
        notes = (method == 'POST' and re.fullmatch(r'/api/portfolios/[^/]+/instruments/[^/]+/notes(?:/[^/]+/restore)?', path) or
                 method in ('PUT', 'DELETE') and re.fullmatch(r'/api/portfolios/[^/]+/instruments/[^/]+/notes/[^/]+', path))
        assets = method == 'POST' and path == '/api/family/commands'
        if not (core or notes or assets) or type(payload.get('expected_revision')) is not int or payload['expected_revision'] < 0:
            raise ValueError('Use a documented core mutation and include expected_revision from the reviewed read/preview')
        self.connect()
        # Never retry a mutation or replace its expected_revision automatically.
        return self.request(path, payload, method)

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
    preview = commands.add_parser('preview'); preview.add_argument('file', type=Path); preview.add_argument('--path', choices=['/api/events/preview','/api/family/preview'], default='/api/events/preview')
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
