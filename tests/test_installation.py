"""Blank installs and release preservation use only disposable synthetic ledgers."""
import json
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import DomainError
from backend.deployment_guard import capture, verify, require_empty
from backend.environment import claim_environment
from backend.service import Service
from backend.store import Store
from backend.migration import SCHEMA

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import manage
from maintenance import snapshot


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.data=claim_environment(self.root/'data','test')
        self.service=Service(Store(self.data))

    def tearDown(self): self.temp.cleanup()

    def populated(self):
        p=self.service.save_portfolio({'name':'合成升级组合','planned_amount':'1000','start_date':'2024-01-01'})
        e=self.service.save_event({'portfolio_id':p['id'],'kind':'BUY','date':'2024-01-02',
            'instrument':{'market':'CN','code':'510300'},'quantity':'1','gross_amount':'10','fx_rate':'1'})
        note=self.service.notes.save(p['id'],e['instrument_id'],{'title':'合成笔记','body':'synthetic old body',
            'expected_revision':self.service.store.state()['revision']})
        self.service.notes.save(p['id'],e['instrument_id'],{'title':'合成笔记','body':'synthetic new body',
            'expected_revision':self.service.store.state()['revision']},note['id'])
        return p,e,note

    def test_empty_restart_and_automatic_sync_never_seed_or_fetch(self):
        before=capture(self.data)
        with patch('backend.market.exchange_rates') as rates,patch('backend.market.quotes') as quotes:
            for _ in range(2):
                with TestClient(create_app(self.data,testing=True,auto_sync=True)) as client:
                    boot=client.get('/api/bootstrap').json()
                    self.assertEqual(boot['portfolios'],[]);self.assertEqual(boot['instruments'],[])
                    self.assertEqual(client.app.state.service.start_sync(trigger='schedule')['reason'],'empty_ledger')
                    self.assertTrue(require_empty(self.data)['empty'])
            rates.assert_not_called();quotes.assert_not_called()
        verify(before,capture(self.data,before))

    def test_empty_checker_refuses_existing_data_without_mutation(self):
        self.populated();before=capture(self.data)
        with self.assertRaisesRegex(DomainError,'不会清空'): require_empty(self.data)
        result=subprocess.run([sys.executable,str(ROOT/'scripts/check_empty_install.py'),
            '--data-dir',str(self.data),'--environment','test'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        verify(before,capture(self.data,before))

    def test_readonly_acceptance_blocks_writes_and_background_work(self):
        self.populated();before=capture(self.data)
        app=create_app(self.data,testing=True,auto_sync=True,read_only=True)
        with patch.object(app.state.service,'start_sync') as sync, TestClient(app) as client:
            boot=client.get('/api/bootstrap').json()
            self.assertTrue(client.get('/api/health').json()['read_only'])
            headers={'X-CSRF-Token':boot['csrf_token']}
            for method,path in [('POST','/api/portfolios'),('POST','/api/family/commands'),
                                ('DELETE','/api/events/synthetic'),('POST','/api/sync')]:
                self.assertEqual(client.request(method,path,json={},headers=headers).status_code,503)
            sync.assert_not_called()
        verify(before,capture(self.data,before))

    def test_exact_records_and_all_referenced_note_versions_are_protected(self):
        _,event,note=self.populated();before=capture(self.data)
        self.assertEqual(before['note_versions'],2)
        with self.service.store.connection() as db:
            original=db.execute('SELECT data FROM events WHERE id=?',(event['id'],)).fetchone()[0]
            changed=json.loads(original);changed['fx_rate']='9'
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(changed),event['id']));db.commit()
        with self.assertRaisesRegex(DomainError,'events'):verify(before,capture(self.data,before))
        with self.service.store.connection() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(original,event['id']));db.commit()
        old=self.data/note['path'];old.write_bytes(old.read_bytes().replace(b'old body',b'changed body'))
        with self.assertRaisesRegex(DomainError,'notes_sha256'):verify(before,capture(self.data,before))

    def test_guard_created_after_boot_blocks_new_requests(self):
        before=capture(self.data);guard=self.root/'deployment-guard.json'
        app=create_app(self.data,testing=True,auto_sync=False,deployment_guard_path=guard)
        with TestClient(app) as client:
            boot=client.get('/api/bootstrap').json()
            self.assertFalse(client.get('/api/health').json()['read_only'])
            guard.write_text('{"status":"blocked"}')
            self.assertTrue(client.get('/api/health').json()['read_only'])
            response=client.post('/api/portfolios',json={'name':'must not be written'},
                                 headers={'X-CSRF-Token':boot['csrf_token']})
            self.assertEqual(response.status_code,503)
        verify(before,capture(self.data,before))

    def test_additive_schema_is_allowed_but_seeded_new_tables_are_not(self):
        self.populated();before=capture(self.data)
        with self.service.store.connection() as db:
            db.execute('ALTER TABLE portfolios ADD COLUMN optional_metadata TEXT')
            db.execute('CREATE TABLE future_records(id TEXT PRIMARY KEY,data TEXT)');db.commit()
        self.assertTrue(verify(before,capture(self.data,before))['preserved'])
        with self.service.store.connection() as db:
            db.execute("INSERT INTO future_records VALUES ('demo','{}')");db.commit()
        with self.assertRaisesRegex(DomainError,'future_records'):verify(before,capture(self.data,before))

    def test_snapshot_and_rehearsal_preserve_history_without_using_an_old_dev_copy(self):
        self.populated();before=capture(self.data)
        backup=snapshot(self.data,self.root/'protected.zip')
        staged=self.root/'staged';manage.unpack_rehearsal(backup['backup'],staged)
        self.assertTrue(verify(before,capture(staged,before))['preserved'])
        self.assertNotEqual((staged/'portfolio.sqlite').stat().st_ino,(self.data/'portfolio.sqlite').stat().st_ino)
        # A later real record must be represented by the latest backup, not the rehearsal copy.
        self.service.save_portfolio({'name':'合成期间新增','planned_amount':'200','start_date':'2024-01-01'})
        with self.assertRaises(DomainError):verify(capture(self.data),capture(staged,capture(self.data)))

    def test_schema7_label_upgrade_preserves_deployment_revision_and_note_history(self):
        self.populated()
        with sqlite3.connect(self.data/'portfolio.sqlite') as db:
            db.execute('DROP TABLE holding_labels')
            db.execute("UPDATE meta SET value='7' WHERE key='schema'")
            db.execute("UPDATE alembic_version SET version_num='0006_storage'")
        before=capture(self.data)
        backup=snapshot(self.data,self.root/'schema7-protected.zip')
        runtime=self.root/'rehearsal-runtime';runtime.mkdir()
        result=manage.rehearse_deployment(ROOT,{'runtime_dir':runtime,'environment':'test'},backup['backup'],before)
        self.assertTrue(result['preserved'])
        self.assertEqual(capture(self.data),before)
        Store(self.data,migration_backup=False)
        after=capture(self.data,before)
        self.assertEqual(after['schema'],8)
        self.assertEqual(after['counts']['holding_labels'],0)
        self.assertEqual(after['metadata_sha256'],before['metadata_sha256'])
        self.assertEqual(after['note_versions'],2)
        self.assertTrue(verify(before,after)['preserved'])

    def test_public_archive_contains_guides_but_no_runtime_or_ledger_files(self):
        archive=self.root/'source.tar.gz'
        subprocess.run([sys.executable,str(ROOT/'scripts/source_check.py'),'--output',str(archive)],
                       capture_output=True,text=True,check=True)
        with tarfile.open(archive) as saved:
            names=[m.name for m in saved.getmembers()]
        self.assertIn('portfolio-app/docs/install.md',names)
        self.assertIn('portfolio-app/scripts/check_empty_install.py',names)
        for name in names:
            self.assertFalse(any(p in ('runtime','local','node_modules','.venv','notes','backups') for p in Path(name).parts),name)
            self.assertFalse(name.endswith(('.sqlite','.db','.zip','.log')),name)

    def test_real_offline_migration_rehearsal_detects_candidate_corruption(self):
        self.populated();before=capture(self.data)
        backup=snapshot(self.data,self.root/'candidate-input.zip')
        candidate=self.root/'candidate';(candidate/'scripts').mkdir(parents=True)
        script=candidate/'scripts/maintenance.py';shutil.copy2(ROOT/'scripts/maintenance.py',script)
        (candidate/'backend').symlink_to(ROOT/'backend',target_is_directory=True)
        # Reuse the test interpreter's dependencies, not just its executable:
        # relocating only the executable would lose pyvenv.cfg/site-packages.
        (candidate/'.venv').symlink_to(Path(sys.prefix),target_is_directory=True)
        runtime=self.root/'runtime';runtime.mkdir()
        values={'runtime_dir':runtime,'environment':'test'}
        result=manage.rehearse_deployment(candidate,values,backup['backup'],before)
        self.assertTrue(result['preserved']);verify(before,capture(self.data,before))
        with script.open('a') as handle:
            handle.write("\nimport sqlite3\np=sys.argv[sys.argv.index('--data-dir')+1]\nwith sqlite3.connect(Path(p)/'portfolio.sqlite') as db:\n    db.execute('DELETE FROM events')\n")
        with self.assertRaisesRegex(DomainError,'events'):
            manage.rehearse_deployment(candidate,values,backup['backup'],before)
        verify(before,capture(self.data,before))
        self.assertEqual(list(runtime.iterdir()),[])  # Only this run's private rehearsal copy is cleaned.


class DeploymentFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        # All business data are synthetic. The production paths are not consulted:
        # mock configuration points at an explicitly marked test ledger.
        self.data=claim_environment(self.root/'synthetic-data','test')
        self.service=Service(Store(self.data))
        self.service.save_portfolio({'name':'合成保护','planned_amount':'100','start_date':'2024-01-01'})
        self.runtime=self.root/'run';self.runtime.mkdir()
        self.destination=self.root/'runtime/releases/candidate';self.destination.mkdir(parents=True)
        self.manifest={'version':'synthetic','schema':SCHEMA,'supported_schemas':[SCHEMA],
                       'checks':{'deployment_guard':True}}
        self.values={'environment':'test','data_dir':self.data,'runtime_dir':self.runtime,'port':8876}
        self.starts=[]

    def tearDown(self): self.temp.cleanup()

    def test_stop_waits_for_lock_after_http_is_already_closed(self):
        script="import fcntl,sys,time; f=open(sys.argv[1], 'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); time.sleep(.5)"
        child=subprocess.Popen([sys.executable,'-c',script,str(self.data/'.instance.lock')],stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'locked')
            with patch.object(manage,'get_config',return_value=(self.root/'config.toml',self.values)), \
                 patch.object(manage,'health',return_value=None):
                started=time.monotonic();manage.stop(self.root,'production')
            self.assertGreaterEqual(time.monotonic()-started,.3)
            with manage.LedgerProcessLock(self.data):pass
        finally:
            child.wait(timeout=5);child.stdout.close()

    def test_stop_lock_timeout_keeps_holder_and_ledger_unchanged(self):
        before=capture(self.data)
        with manage.LedgerProcessLock(self.data):
            with self.assertRaisesRegex(DomainError,'仍占用数据锁'):
                manage.wait_for_ledger_stop(self.data,timeout=.05)
        verify(before,capture(self.data,before))

    def start(self,workspace,environment,**kwargs):
        self.starts.append(kwargs)
        return {'version':'synthetic','schema':SCHEMA,'read_only':kwargs.get('read_only',False)}

    def run_apply(self, *, corrupt_rehearsal=False, corrupt_live=False, corrupt_start=False):
        def rehearsal(*args,**kwargs):
            if corrupt_rehearsal:raise DomainError('synthetic rehearsal discrepancy')
            return {'preserved':True}
        def command(args,**kwargs):
            if 'migrate' in args and not (self.data/'portfolio.sqlite').exists():
                Store(self.data)
            if 'migrate' in args and corrupt_live:
                with self.service.store.connection() as db:db.execute('DELETE FROM portfolios');db.commit()
            return subprocess.CompletedProcess(args,0,stdout='{}')
        def start(*args,**kwargs):
            value=self.start(*args,**kwargs)
            if kwargs.get('read_only') and corrupt_start:
                with self.service.store.connection() as db:db.execute('DELETE FROM portfolios');db.commit()
            return value
        with patch.object(manage,'verify_release',return_value=self.manifest), \
             patch.object(manage,'get_config',return_value=(self.root/'config.toml',self.values)), \
             patch.object(manage,'rehearse_deployment',side_effect=rehearsal), \
             patch.object(manage,'start',side_effect=start),patch.object(manage,'stop') as stopped, \
             patch.object(manage.subprocess,'run',side_effect=command):
            return manage.apply(self.root,'candidate')

    def test_success_preserves_records_and_saves_two_full_recovery_points(self):
        before=capture(self.data);result=self.run_apply()
        verify(before,capture(self.data,before))
        self.assertEqual([s.get('read_only',False) for s in self.starts],[True,False])
        self.assertFalse((self.runtime/'deployment-guard.json').exists())
        evidence=json.loads(Path(result['data_verification']).read_text())
        self.assertEqual(evidence['status'],'verified')
        self.assertTrue(verify(evidence['before_fingerprint'],evidence['after_fingerprint'])['preserved'])
        for key in ('before_backup','after_backup'):self.assertTrue(Path(evidence[key]['backup']).is_file())

    def test_rehearsal_failure_leaves_original_data_and_never_starts_business(self):
        before=capture(self.data)
        with self.assertRaises(DomainError):self.run_apply(corrupt_rehearsal=True)
        verify(before,capture(self.data,before));self.assertEqual(self.starts,[])
        self.assertTrue((self.runtime/'deployment-guard.json').is_file())

    def test_fresh_apply_creates_an_empty_ledger_without_a_source_database(self):
        self.data=claim_environment(self.root/'fresh-data','test');self.values['data_dir']=self.data
        result=self.run_apply()
        self.assertTrue(require_empty(self.data)['empty'])
        evidence=json.loads(Path(result['data_verification']).read_text())
        self.assertIsNone(evidence['before_fingerprint']);self.assertNotIn('before_backup',evidence)
        self.assertTrue(evidence['migration']['empty'])
        self.assertTrue(Path(evidence['after_backup']['backup']).is_file())
        self.assertEqual([s.get('read_only',False) for s in self.starts],[True,False])

    def test_migration_or_startup_discrepancy_blocks_without_restoring_a_backup(self):
        for phase in ('live','start'):
            with self.subTest(phase=phase):
                if phase=='start':
                    (self.runtime/'deployment-guard.json').unlink()
                    self.service.save_portfolio({'name':'合成重新准备','planned_amount':'100','start_date':'2024-01-01'})
                self.starts=[]
                with self.assertRaises(DomainError):self.run_apply(corrupt_live=phase=='live',corrupt_start=phase=='start')
                self.assertTrue((self.runtime/'deployment-guard.json').is_file())
                self.assertFalse(any(not s.get('read_only',False) for s in self.starts))
                self.assertEqual(self.service.store.state()['portfolios'],[])  # No hidden restoration.

    def test_blocked_deployment_cannot_be_bypassed_by_normal_start(self):
        (self.runtime/'deployment-guard.json').write_text('{}')
        with patch.object(manage,'get_config',return_value=(self.root/'config.toml',self.values)), \
             patch.object(manage,'health') as health:
            with self.assertRaisesRegex(DomainError,'禁止开放业务'):manage.start(self.root,'production')
            health.assert_not_called()

    def test_start_timeout_terminates_only_the_child_it_created(self):
        python=self.destination/'.venv/bin/python';python.parent.mkdir(parents=True);python.touch()
        for graceful in (True,False):
            with self.subTest(graceful=graceful), \
                 patch.object(manage,'get_config',return_value=(self.root/'config.toml',self.values)), \
                 patch.object(manage,'health',return_value=None), \
                 patch.object(manage,'current_release',return_value=self.destination), \
                 patch.object(manage,'verify_release',return_value=self.manifest), \
                 patch.object(manage.subprocess,'Popen') as popen, \
                 patch.object(manage.time,'sleep'):
                child=popen.return_value;child.poll.return_value=None
                if not graceful:child.wait.side_effect=[subprocess.TimeoutExpired('synthetic',15),None]
                with self.assertRaisesRegex(DomainError,'启动检查超时'):manage.start(self.root,'production')
                child.terminate.assert_called_once()
                self.assertEqual(child.kill.call_count,0 if graceful else 1)


if __name__=='__main__':unittest.main()
