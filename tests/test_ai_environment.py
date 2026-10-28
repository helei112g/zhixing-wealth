"""Environment routing and daily entry regressions; no real ledger access."""
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.environment import PORTS
from scripts import ai_client, init_daily_workspace
from scripts.ai_client import Client

ROOT = Path(__file__).resolve().parents[1]


class AIEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def install_client(self, root):
        (root/'scripts').mkdir(parents=True)
        shutil.copy2(ROOT/'scripts/ai_client.py', root/'scripts/ai_client.py')
        (root/'.venv/bin').mkdir(parents=True)
        (root/'.venv/bin/python').symlink_to(sys.executable)
        return root/'scripts/ai_client.py'

    def cli(self, script, *args, cwd=None):
        return subprocess.run([sys.executable, '-B', str(script), *args], cwd=cwd or self.root,
                              capture_output=True, text=True, timeout=10)

    def test_source_release_symlink_and_export_defaults_do_not_depend_on_cwd(self):
        source = self.install_client(self.root/'source')
        release = self.install_client(self.root/'runtime/releases/synthetic')
        current = self.root/'runtime/current'
        current.symlink_to('releases/synthetic')
        for script, environment in ((source, 'dev'), (release, 'production'),
                                     (current/'scripts/ai_client.py', 'production')):
            with self.subTest(script=script):
                result = self.cli(script, 'target', cwd=release.parent)
                self.assertEqual(result.returncode, 0, result.stderr)
                target = json.loads(result.stdout)
                self.assertEqual(target['environment'], environment)
                self.assertEqual(target['base_url'], f'http://127.0.0.1:{PORTS[environment]}')
        exported = self.install_client(self.root/'exported')
        (exported.parent.parent/'release.json').write_text('{}')
        self.assertEqual(json.loads(self.cli(exported, 'target').stdout)['environment'], 'production')

    def test_explicit_test_environment_overrides_installed_default(self):
        script = self.install_client(self.root/'runtime/releases/synthetic')
        result = self.cli(script, '--environment', 'test', 'target')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['base_url'], 'http://127.0.0.1:8876')
        self.assertEqual(ai_client.PORTS, PORTS)

    def test_explicit_url_environment_and_invalid_origins(self):
        for environment, port in PORTS.items():
            self.assertEqual(Client(f'http://localhost:{port}').environment, environment)
        for url in ('http://127.0.0.1', 'http://example.org:8765', 'https://127.0.0.1:8765',
                    'http://user:pass@127.0.0.1:8765', 'http://127.0.0.1:8765/elsewhere',
                    'http://127.0.0.1:8765?query=1', 'http://127.0.0.1:8765#fragment'):
            with self.subTest(url=url), self.assertRaises(ValueError): Client(url)
        with self.assertRaises(ValueError): Client('http://127.0.0.1:8877', environment='production')
        with self.assertRaises(ValueError): Client('http://127.0.0.1:18876')
        self.assertEqual(Client('http://127.0.0.1:18876', environment='test').environment, 'test')

    def test_wrong_service_or_ledger_never_receives_business_request(self):
        client = Client(environment='production', expected_data_dir=self.root/'ledger')
        health = {'ok': True, 'application': 'portfolio-journal', 'environment': 'production',
                  'data_directory': str(self.root/'ledger')}
        for bad in ({**health, 'environment':'dev'}, {**health, 'application':'other'},
                    {**health, 'ok':False}, {**health, 'data_directory':str(self.root/'other')}, {}, []):
            with self.subTest(health=bad), patch.object(client, '_request', return_value=bad) as request:
                with self.assertRaises(RuntimeError): client.request('/api/events', {'expected_revision':1}, 'POST')
                request.assert_called_once_with('/api/health')
        with patch.object(client, '_request', side_effect=OSError('synthetic offline')) as request:
            with self.assertRaises(OSError): client.request('/api/family/catalog')
            request.assert_called_once_with('/api/health')

    def test_recheck_environment_on_every_request_preserves_original_payload(self):
        client = Client(environment='test')
        good = {'ok':True, 'application':'portfolio-journal', 'environment':'test'}
        payload = {'expected_revision':7}
        with patch.object(client, '_request', side_effect=[good, {'saved':True}, {**good,'environment':'production'}]) as request:
            self.assertEqual(client.request('/api/events', payload, 'POST'), {'saved':True})
            self.assertEqual(request.call_args.args, ('/api/events', payload, 'POST'))
            self.assertEqual(payload['expected_revision'], 7)
            with self.assertRaises(RuntimeError): client.request('/api/family/catalog')
            self.assertEqual(request.call_count, 3)

    def test_health_is_checked_once_and_redirects_are_refused(self):
        client = Client(environment='test')
        health = {'ok':True, 'application':'portfolio-journal', 'environment':'test'}
        with patch.object(client.opener, 'open', return_value=io.BytesIO(json.dumps(health).encode())) as opened:
            self.assertEqual(client.request('/api/health'), health)
            self.assertEqual(opened.call_count, 1)
        with self.assertRaises(RuntimeError):
            ai_client.NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1:8765')

    def test_daily_workspace_locks_target_follows_current_and_preserves_existing_directory(self):
        workspace = self.root/'workspace with spaces'
        self.install_client(workspace/'runtime/releases/one')
        current = workspace/'runtime/current';current.symlink_to('releases/one')
        daily = self.root/'日常使用'
        init_daily_workspace.initialize(daily, workspace)
        self.assertEqual({p.name for p in daily.iterdir()}, {'AGENTS.md', 'zhixing'})
        def run(*args):
            return subprocess.run([str(daily/'zhixing'), *args], cwd=self.root,
                                  capture_output=True, text=True, timeout=10)
        target = json.loads(run('target').stdout)
        self.assertEqual(target['environment'], 'production')
        self.assertEqual(target['base_url'], 'http://127.0.0.1:8765')
        self.assertEqual(target['expected_data_directory'], str((workspace/'runtime/data').resolve()))
        for args in (('--environment','test','target'), ('--base-url=http://127.0.0.1:8877','target'),
                     ('--expected-data-dir','different','target'), ('--env','test','target')):
            self.assertNotEqual(run(*args).returncode, 0)
        before = (daily/'AGENTS.md').read_bytes()
        with self.assertRaises(ValueError): init_daily_workspace.initialize(daily, workspace)
        self.assertEqual((daily/'AGENTS.md').read_bytes(), before)
        self.install_client(workspace/'runtime/releases/two')
        current.unlink();current.symlink_to('releases/two')
        (workspace/'runtime/releases/one/scripts/ai_client.py').write_text('raise SystemExit(99)')
        self.assertEqual(run('target').returncode, 0)
        current.unlink()
        self.assertNotEqual(run('target').returncode, 0)

    def test_release_gate_catches_a_reintroduced_development_default(self):
        sys.path.insert(0, str(ROOT/'scripts'))
        import manage
        release = self.root/'runtime/releases/synthetic'
        script = self.install_client(release)
        self.assertEqual(manage.verify_ai_target(release)['environment'], 'production')
        script.write_text('import json\nprint(json.dumps({"environment":"dev","base_url":"http://127.0.0.1:8877"}))\n')
        with self.assertRaisesRegex(manage.DomainError, '默认连接正式环境'):
            manage.verify_ai_target(release)


if __name__ == '__main__': unittest.main()
