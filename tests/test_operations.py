import hashlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import manage
from maintenance import snapshot
from backend.common import DomainError
from backend.environment import claim_environment
from backend.service import Service
from backend.store import Store, SCHEMA


class OperationsTests(unittest.TestCase):
    def test_running_backup_reuses_verified_compressed_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,values=manage.init_environment(root,'test')
            service=Service(Store(values['data_dir']))
            content=service.backup_download()
            with patch.object(manage,'health',return_value={'environment':'test'}), \
                    patch.object(manage.urllib.request,'urlopen',return_value=io.BytesIO(content)):
                result=manage.backup(root,'test')
            self.assertEqual(Path(result['backup']).read_bytes(),content)
            self.assertTrue(Path(result['backup']).name.startswith('manual-'))
            self.assertFalse(list((values['data_dir']/'backups').glob('full-*')))

    def test_start_existing_instance_opens_browser_without_starting_another_process(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);manage.init_environment(root,'dev')
            with patch.object(manage,'health',return_value={'environment':'dev'}) as health, \
                 patch.object(manage.webbrowser,'open') as opened, \
                 patch.object(manage.subprocess,'Popen') as popen:
                self.assertEqual(manage.start(root,'dev',open_browser=True),{'environment':'dev'})
                opened.assert_called_once_with('http://127.0.0.1:8877')
                popen.assert_not_called()

    def test_release_hash_guard_detects_source_and_asset_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'README.md').write_text('Synthetic source')
            (root/'frontend/dist').mkdir(parents=True)
            (root/'frontend/dist/index.html').write_text('Synthetic frontend')
            (root/'.venv/bin').mkdir(parents=True)
            (root/'.venv/bin/python').write_text('synthetic interpreter marker')
            manifest={'schema':SCHEMA,'checks':{'browser':True},'source_files':manage.source_manifest(root),
                'assets':{'frontend/dist/index.html':hashlib.sha256(b'Synthetic frontend').hexdigest()}}
            (root/'release.json').write_text(json.dumps(manifest))
            self.assertEqual(manage.verify_release(root),manifest)
            (root/'frontend/dist/index.html').write_text('changed')
            with self.assertRaises(DomainError):manage.verify_release(root)
            (root/'frontend/dist/index.html').write_text('Synthetic frontend')
            (root/'README.md').write_text('changed')
            with self.assertRaises(DomainError):manage.verify_release(root)

    def test_environment_init_does_not_overwrite_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            path,_=manage.init_environment(root,'dev')
            text=path.read_text().replace('8877','18877');path.write_text(text)
            _,values=manage.init_environment(root,'dev')
            self.assertEqual(values['port'],18877);self.assertEqual(path.read_text(),text)
            with self.assertRaises(DomainError):manage.release_path(root,'../../escape')

    def test_offline_snapshot_restores_referenced_note_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);s=Service(Store(root/'source'))
            p=s.save_portfolio({'name':'合成维护','planned_amount':'1000','start_date':'2024-01-01'})
            e=s.save_event({'portfolio_id':p['id'],'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'510300'},'quantity':'1','gross_amount':'10','fx_rate':'1'})
            note=s.notes.save(p['id'],e['instrument_id'],{'title':'合成笔记','body':'合成正文','expected_revision':s.store.state()['revision']})
            s.notes.save(p['id'],e['instrument_id'],{'title':'合成笔记','body':'新合成正文','expected_revision':s.store.state()['revision']},note['id'])
            target=root/'snapshot.zip';result=snapshot(root/'source',target)
            self.assertEqual(result['notes'],2)
            with self.assertRaises(DomainError):snapshot(root/'source',target)
            restored=Service(Store(root/'target'));restored.restore(target.read_bytes())
            self.assertEqual(restored.notes.get(p['id'],e['instrument_id'],note['id'])['body'],'新合成正文')
            self.assertEqual((root/'source'/note['path']).read_bytes(),(root/'target'/note['path']).read_bytes())

    def test_fixed_port_refuses_collision_without_creating_a_database(self):
        with tempfile.TemporaryDirectory() as temp,socket.socket() as occupied:
            root=Path(temp);(root/'frontend').mkdir();(root/'frontend/index.html').write_text('test')
            occupied.bind(('127.0.0.1',0));occupied.listen()
            port=occupied.getsockname()[1]
            result=subprocess.run([sys.executable,str(ROOT/'run.py'),'--environment','test',
                '--port',str(port),'--data-dir',str(root/'data'),'--runtime-dir',str(root/'runtime'),
                '--frontend-dir',str(root/'frontend'),'--no-sync','--no-open'],capture_output=True,text=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('已占用',result.stderr)
            self.assertFalse((root/'data/portfolio.sqlite').exists())


if __name__=='__main__':unittest.main()
