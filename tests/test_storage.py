import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError, json_dumps
from backend.environment import claim_environment
from backend.ledger import Market
from backend.migration import SCHEMA, migrate, schema_version
from backend.service import Service
from backend.store import JOB_HISTORY_LIMIT, Store
from scripts.storage_cleanup import apply_plan, make_plan


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(claim_environment(self.root / 'data', 'test'))
        self.service = Service(self.store)
        self.portfolio = self.service.save_portfolio({'name': '合成存储验证', 'planned_amount': '10000', 'start_date': '2024-01-01'})
        self.trade = self.service.save_event({'portfolio_id': self.portfolio['id'], 'kind': 'BUY',
            'date': '2024-01-02', 'instrument': {'market': 'US', 'code': 'VTI'},
            'quantity': '1', 'gross_amount': '100', 'fx_rate': '6.9'})
        self.iid = self.trade['instrument_id']

    def tearDown(self): self.temp.cleanup()

    def quote(self, price='100', **extra):
        return {'date': '2024-01-02', 'price': price, 'source': 'synthetic', **extra}

    def test_identical_sync_is_idempotent_but_a_b_a_is_a_correction(self):
        self.assertEqual(self.service.cache_prices(self.iid, [self.quote(), self.quote()]), {'saved': 1, 'unchanged': 1})
        audits = self.service.revisions('price', self.iid)
        self.assertEqual(self.service.cache_prices(self.iid, [self.quote(fetched_at='different')]), {'saved': 0, 'unchanged': 1})
        self.assertEqual(audits, self.service.revisions('price', self.iid))
        for row in [self.quote('101'), self.quote(), self.quote(source='other source'), self.quote(kind='adjusted')]:
            self.assertEqual(self.service.cache_prices(self.iid, [row])['saved'], 1)
        self.assertEqual(len(self.store.state()['prices']), 5)
        self.assertEqual(Market(self.store.state()).price(self.iid, '2024-01-02')['source'], 'other source')
        self.assertEqual(self.store.state()['events'][0]['fx_rate'], '6.9')

    def legacy(self):
        note = self.service.notes.save(self.portfolio['id'], self.iid, {'title': '合成笔记', 'body': '合成历史正文', 'expected_revision': self.store.state()['revision']})
        with self.store.connection() as db:
            for n, row in enumerate([self.quote(), self.quote(), self.quote('101'), self.quote(),
                                     self.quote(source='other'), self.quote(manual=True), self.quote(manual=True)]):
                row = dict(row, instrument_id=self.iid, kind='raw', manual=row.get('manual', False), fetched_at=str(n))
                db.execute('INSERT INTO prices(instrument_id,date,kind,manual,data) VALUES (?,?,?,?,?)',
                           (self.iid, row['date'], 'raw', int(row['manual']), json_dumps(row)))
            db.execute('DROP TABLE holding_labels')
            db.execute('CREATE INDEX fx_scope ON fx(currency,date)')
            for table in ('research_heads','research_snapshots','research_indices','research_meta','research_price_chunks','data_update_tasks','data_update_policies','data_update_receipts'):
                db.execute('DROP TABLE IF EXISTS '+table)
            db.execute("UPDATE meta SET value='6' WHERE key='schema'")
            db.execute("UPDATE alembic_version SET version_num='0005_family_income'")
            db.commit()
        return note

    def test_schema6_migration_preserves_effective_series_business_manual_and_notes(self):
        self.legacy()
        from backend.storage_audit import capture, verify
        audit_before = capture(self.store.directory)
        before = self.store.state()
        effective = Market(before).price(self.iid, '2024-01-02')
        note_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.store.directory / 'notes').rglob('*.md')}
        self.assertTrue(migrate(self.store.path))
        self.assertFalse(migrate(self.store.path))
        self.assertTrue(verify(audit_before, capture(self.store.directory))['equivalent'])
        after = self.store.state()
        self.assertEqual(len(after['prices']), 5)
        self.assertEqual(Market(after).price(self.iid, '2024-01-02'), effective)
        for key in before:
            if key not in ('prices', 'revision'): self.assertEqual(after[key], before[key], key)
        self.assertEqual(note_hashes, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.store.directory / 'notes').rglob('*.md')})
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM prices WHERE manual=1').fetchone()[0], 2)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='fx_scope'").fetchone())
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_schema6_failure_rolls_back_then_old_backup_restores(self):
        self.legacy()
        from backend import migration
        actual = migration.command.upgrade
        def fail(*args, **kwargs):
            actual(*args, **kwargs)
            raise RuntimeError('synthetic failure after migration')
        with patch.object(migration.command, 'upgrade', side_effect=fail):
            with self.assertRaises(RuntimeError): migrate(self.store.path)
        self.assertEqual(schema_version(self.store.path), 6)
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM prices').fetchone()[0], 7)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='fx_scope'").fetchone())
        old = self.store.backup('synthetic-old')
        with zipfile.ZipFile(old.with_suffix('.notes.zip')) as z:
            notes = {name: z.read(name) for name in z.namelist()}
        target = Store(claim_environment(self.root / 'restored', 'test'))
        target.restore(old.read_bytes(), notes)
        self.assertEqual(schema_version(target.path), SCHEMA)
        self.assertEqual(len(target.state()['prices']), 5)
        self.assertEqual(target.state()['events'], self.store.state()['events'])

    def test_bad_batch_rolls_back_quotes_and_audit(self):
        self.service.cache_prices(self.iid, [self.quote()])
        before = self.store.state()
        with self.assertRaises(DomainError):
            self.service.cache_prices(self.iid, [self.quote('102'), self.quote('-1')])
        self.assertEqual(self.store.state(), before)

    def test_jobs_limit_protects_running_and_uses_start_order(self):
        for n in range(JOB_HISTORY_LIMIT + 7):
            self.store.save_job({'id': str(n), 'started_at': f'2024-01-{n % 28 + 1:02d}T10:00:00', 'status': 'error' if n % 2 else 'complete'})
        self.store.save_job({'id': 'active', 'started_at': '1900-01-01T00:00:00', 'status': 'running'})
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], JOB_HISTORY_LIMIT)
            self.assertIsNotNone(db.execute("SELECT id FROM jobs WHERE id='active'").fetchone())
        self.assertEqual(len(self.store.jobs()), 20)
        self.store.recover_jobs()
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], JOB_HISTORY_LIMIT)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE json_extract(data,'$.status')='running'").fetchone()[0], 0)

    def test_startup_marks_interruption_and_api_exposes_policy(self):
        self.store.save_job({'id': 'interrupted', 'started_at': '2024-01-01', 'status': 'running', 'trigger': 'startup'})
        with TestClient(create_app(self.store.directory, testing=True, auto_sync=False)) as client:
            job = client.get('/api/jobs').json()['jobs'][0]
            self.assertEqual(job['status'], 'error'); self.assertTrue(job['interrupted'])
            self.assertEqual(job['trigger'], 'startup')
            self.assertEqual(client.get('/api/capabilities').json()['storage']['jobs_finished_retained'], 20)
            self.assertEqual(client.get('/api/capabilities').json()['storage']['jobs_visible'], 5)
            self.assertEqual(client.get('/api/capabilities').json()['storage']['jobs_total_retained'], 20)
            self.assertIn('最多保存20条', client.get('/api/openapi.json').json()['paths']['/api/jobs']['get']['description'])

    def test_startup_trims_old_history_without_changing_business_data(self):
        before = self.store.state()
        with self.store.connection() as db:
            for n in range(200):
                db.execute('INSERT INTO jobs VALUES (?,?)', (str(n), json_dumps({
                    'id': str(n), 'started_at': '2024-01-01',
                    'status': 'running' if n == 199 else 'complete'})))
            db.commit()
        self.store.recover_jobs()
        self.assertEqual([j['id'] for j in self.store.jobs()], [str(n) for n in range(199, 179, -1)])
        self.assertTrue(self.store.jobs()[0]['interrupted'])
        self.assertEqual(self.store.state(), before)

    def test_running_jobs_take_slots_without_being_evicted(self):
        for n in range(25):
            self.store.save_job({'id': str(n), 'started_at': '2024-01-01', 'status': 'running'})
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 25)
        # An abnormal surplus of active jobs is protected until completion/recovery.
        for n in range(6):
            self.store.save_job({'id': str(n), 'started_at': '2024-01-01', 'status': 'complete'})
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 20)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM jobs WHERE json_extract(data,'$.status')='running'").fetchone()[0], 19)

    def test_export_keeps_one_compressed_full_backup_and_roundtrips(self):
        self.service.notes.save(self.portfolio['id'], self.iid, {'title': '合成笔记', 'body': '合成正文', 'expected_revision': self.store.state()['revision']})
        exported = self.service.backup_download()
        saved = list(self.store.backups.glob('manual-*.zip'))
        self.assertEqual(len(saved), 1); self.assertEqual(saved[0].read_bytes(), exported)
        self.assertFalse(list(self.store.backups.glob('manual-*.sqlite')))
        again = self.service.backup_download()
        self.assertEqual(len(list(self.store.backups.glob('manual-*.zip'))), 2)
        target = Service(Store(claim_environment(self.root / 'roundtrip', 'test')))
        target.restore(again)
        self.assertEqual(target.store.state()['events'], self.store.state()['events'])
        self.assertEqual(len(target.notes.list(self.portfolio['id'], self.iid)['items']), 1)

    def test_export_disk_failure_preserves_uncompressed_recovery_pair(self):
        with patch('backend.service.os.link', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError): self.service.backup_download()
        files = list(self.store.backups.glob('manual-*.sqlite'))
        self.assertEqual(len(files), 1); self.assertTrue(files[0].with_suffix('.notes.zip').is_file())
        self.assertFalse(list(self.store.backups.glob('*.pending-*')))


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root / 'runtime/releases').mkdir(parents=True)
        for n, schema in enumerate([3, 3, 6, 6, 7, 7, 7]):
            p = self.root / 'runtime/releases' / f'v{n}'; p.mkdir()
            (p / 'release.json').write_text(json.dumps({'release': p.name, 'schema': schema, 'built_at': str(n), 'checks': {'browser': True}}))
            work = self.root / 'local/work' / p.name; work.mkdir(parents=True)
            (work / 'screenshot.png').write_bytes(b'synthetic')
        (self.root / 'runtime/current').symlink_to('releases/v6')
        (self.root / 'runtime/last-deployment.json').write_text(json.dumps({'previous': 'v2'}))
        self.cache = self.root / 'local/test/synthetic/.native-notifications/module-cache'; self.cache.mkdir(parents=True)
        (self.cache / 'cache').write_bytes(b'rebuildable')

    def tearDown(self): self.temp.cleanup()

    def test_retains_schema_recovery_current_predecessor_and_recent_build(self):
        plan = make_plan(self.root)
        self.assertEqual(set(plan['kept_releases']), {'v1', 'v2', 'v3', 'v5', 'v6'})
        self.assertTrue(self.cache.exists())
        report = apply_plan(self.root, plan)
        self.assertIn('runtime/releases/v0', report['deleted'])
        self.assertTrue((self.root / 'runtime/current/release.json').is_file())
        self.assertTrue((self.root / 'local/work/v6/screenshot.png').is_file())
        self.assertFalse(self.cache.exists())

    def test_changed_or_forged_plan_deletes_nothing(self):
        plan = make_plan(self.root)
        (self.cache / 'cache').write_bytes(b'changed')
        with self.assertRaises(DomainError): apply_plan(self.root, plan)
        self.assertTrue((self.root / 'runtime/releases/v0').exists())
        plan = make_plan(self.root); plan['candidates'][0]['path'] = '../outside'
        with self.assertRaises(DomainError): apply_plan(self.root, plan)

    def test_nested_cache_is_covered_by_parent_verification_directory(self):
        nested = self.root / 'local/work/v0/native/module-cache'; nested.mkdir(parents=True)
        (nested / 'cache').write_text('synthetic')
        plan = make_plan(self.root)
        self.assertIn('local/work/v0', [r['path'] for r in plan['candidates']])
        self.assertNotIn('local/work/v0/native/module-cache', [r['path'] for r in plan['candidates']])
        apply_plan(self.root, plan)
        self.assertFalse(nested.exists())

    def test_changed_current_release_and_symlink_are_protected(self):
        plan = make_plan(self.root)
        (self.root / 'runtime/current').unlink(); (self.root / 'runtime/current').symlink_to('releases/v0')
        with self.assertRaises(DomainError): apply_plan(self.root, plan)
        outside = self.root / 'outside'; outside.mkdir(); (outside / 'important').write_text('preserve')
        (self.cache / 'link').symlink_to(outside, target_is_directory=True)
        apply_plan(self.root, make_plan(self.root))
        self.assertEqual((outside / 'important').read_text(), 'preserve')

    def test_only_hash_matched_manual_pairs_are_removed(self):
        directory = self.root / 'runtime/data/backups'; directory.mkdir(parents=True)
        db = b'synthetic snapshot'; body = b'synthetic note'
        database = directory / 'manual-synthetic.sqlite'; database.write_bytes(db)
        with zipfile.ZipFile(database.with_suffix('.notes.zip'), 'w') as z: z.writestr('notes/synthetic.md', body)
        archive = directory / 'protected-synthetic.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('portfolio.sqlite', db); z.writestr('notes/synthetic.md', body)
            z.writestr('manifest.json', json.dumps({'sha256': hashlib.sha256(db).hexdigest(), 'note_files': {'notes/synthetic.md': hashlib.sha256(body).hexdigest()}}))
        protected = directory / 'protected-before.sqlite'; protected.write_bytes(db)
        plan = make_plan(self.root)
        self.assertEqual(len([r for r in plan['candidates'] if r['kind'] == 'duplicate_manual_backup']), 2)
        apply_plan(self.root, plan)
        self.assertFalse(database.exists()); self.assertTrue(archive.exists()); self.assertTrue(protected.exists())


if __name__ == '__main__': unittest.main()
