import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.common import ConflictError, DomainError
from backend.notes import validate_path
from backend.service import Service
from backend.store import Store


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'original')
        self.s = Service(self.store)
        self.p = self.s.save_portfolio({'name':'笔记测试甲','planned_amount':'10000','start_date':'2024-01-01'})['id']
        self.q = self.s.save_portfolio({'name':'笔记测试乙','planned_amount':'10000','start_date':'2024-01-01'})['id']
        self.e = self.buy(self.p, '510300')
        self.other = self.buy(self.p, '159915')
        self.buy(self.q, '510300')
        self.i = self.e['instrument_id']
    def tearDown(self):
        self.tmp.cleanup()
    def buy(self, pid, code):
        return self.s.save_event({'portfolio_id':pid,'kind':'BUY','date':'2024-01-02',
            'instrument':{'market':'CN','code':code},'quantity':'10','gross_amount':'100','fx_rate':'1'})
    def revision(self):
        return self.store.state()['revision']
    def save(self, title='独立标题', body='## 观察\n\n**重点**\n\n1. 等待\n2. 核对', **kwargs):
        return self.s.notes.save(self.p, self.i, {'title':title,'body':body,'expected_revision':self.revision()}, **kwargs)
    def test_scope_and_disk_only_storage(self):
        before = self.store.state()['events']
        a = self.save()
        b = self.s.notes.save(self.q,self.i,{'title':'另一个组合','body':'完全独立','expected_revision':self.revision()})
        self.assertEqual([x['id'] for x in self.s.notes.list(self.p,self.i)['items']], [a['id']])
        self.assertEqual([x['id'] for x in self.s.notes.list(self.q,self.i)['items']], [b['id']])
        self.assertEqual(self.s.notes.list(self.p,self.other['instrument_id'])['total'],0)
        with self.assertRaises(DomainError): self.s.notes.get(self.q,self.i,a['id'])
        with self.assertRaises(DomainError): self.s.notes.get(self.p,self.other['instrument_id'],a['id'])
        with self.assertRaises(DomainError): self.s.notes.save(self.q,self.i,{'title':'覆盖','body':'不允许','expected_revision':self.revision()},a['id'])
        with self.assertRaises(DomainError): self.s.notes.set_deleted(self.q,self.i,a['id'],deleted=True,expected_revision=self.revision())
        with self.store.connection() as db:
            cols = {r['name'] for r in db.execute('PRAGMA table_info(investment_notes)')}
            self.assertEqual(cols,{'id','portfolio_id','instrument_id','path','deleted'})
            index = json.dumps([dict(r) for r in db.execute('SELECT * FROM investment_notes')],ensure_ascii=False)
            audit = json.dumps([dict(r) for r in db.execute("SELECT * FROM revisions WHERE entity='investment_note'")],ensure_ascii=False)
            for text in (a['body'],a['title'],b['title']): self.assertNotIn(text,index+audit)
        raw = (self.store.directory/a['path']).read_text()
        self.assertTrue(raw.endswith(a['body']))
        self.assertEqual(self.store.state()['events'],before)
    def test_edit_immutable_versions_delete_and_undo(self):
        a = self.save(); original=(self.store.directory/a['path']).read_bytes()
        b = self.save(body='新的正文',note_id=a['id'])
        self.assertEqual(a['created_at'],b['created_at']);self.assertNotEqual(a['path'],b['path'])
        self.assertEqual((self.store.directory/a['path']).read_bytes(),original)
        self.s.notes.set_deleted(self.p,self.i,a['id'],deleted=True,expected_revision=self.revision())
        self.assertEqual(self.s.notes.list(self.p,self.i)['total'],0)
        with self.assertRaises(DomainError):self.s.notes.get(self.p,self.i,a['id'])
        with self.assertRaises(DomainError):self.s.notes.set_deleted(self.q,self.i,a['id'],deleted=False,expected_revision=self.revision())
        self.s.notes.set_deleted(self.p,self.i,a['id'],deleted=False,expected_revision=self.revision())
        self.assertEqual(self.s.notes.get(self.p,self.i,a['id'])['body'],'新的正文')
    def test_relative_references_survive_moving_the_data_directory(self):
        a=self.save(body='# \n\n1. ')
        b=self.save(body='## 移动前版本',note_id=a['id'])
        with self.store.connection() as db:
            index=[tuple(r) for r in db.execute('SELECT * FROM investment_notes')]
            audit=[tuple(r) for r in db.execute("SELECT * FROM revisions WHERE entity='investment_note'")]
        for note in (a,b):
            self.assertFalse(Path(note['path']).is_absolute())
            self.assertEqual(validate_path(note['path'])[1:4],(self.p,self.i,a['id']))
        relocated=Path(self.tmp.name)/'relocated'/'data'
        relocated.parent.mkdir();shutil.move(self.store.directory,relocated)
        moved=Service(Store(relocated))
        self.assertEqual(moved.notes.get(self.p,self.i,a['id'])['body'],b['body'])
        self.assertEqual(moved.notes.get(self.p,self.i,a['id'])['path'],b['path'])
        self.assertEqual(moved.store.note_files.read(a['path'])['body'],a['body'])
        with moved.store.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM investment_notes')],index)
            self.assertEqual([tuple(r) for r in db.execute("SELECT * FROM revisions WHERE entity='investment_note'")],audit)
        new=moved.notes.save(self.p,self.i,{'title':'移动后编辑','body':'继续保存','expected_revision':moved.store.state()['revision']},a['id'])
        self.assertFalse(Path(new['path']).is_absolute())
        self.assertTrue((relocated/new['path']).is_file())
        self.assertFalse(self.store.directory.exists())
    def test_stale_and_concurrent_writers(self):
        a=self.save();rev=self.revision();before=set(self.store.directory.rglob('*.md'))
        with self.assertRaises(ConflictError):self.s.notes.save(self.p,self.i,{'title':'过期','body':'内容','expected_revision':rev-1},a['id'])
        self.assertEqual(before,set(self.store.directory.rglob('*.md')))
        def write(n):
            try:return self.s.notes.save(self.p,self.i,{'title':str(n),'body':'并发','expected_revision':rev},a['id'])
            except ConflictError:return None
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(write,[1,2]))
        self.assertEqual(sum(r is not None for r in results),1)
    def test_failures_do_not_leave_dangling_paths_or_change_body(self):
        a=self.save();before=set(self.store.directory.rglob('*.md'));rev=self.revision()
        with patch.object(self.store,'audit',side_effect=sqlite3.OperationalError('test rollback')):
            with self.assertRaises(sqlite3.OperationalError):self.save(body='不会保存',note_id=a['id'])
        self.assertEqual(before,set(self.store.directory.rglob('*.md')));self.assertEqual(self.revision(),rev)
        with patch.object(self.store.note_files,'install',side_effect=OSError('test disk failure')):
            with self.assertRaises(OSError):self.save(body='不会保存',note_id=a['id'])
        self.assertEqual(self.s.notes.get(self.p,self.i,a['id'])['path'],a['path'])
    def test_markdown_paths_validation_and_missing_file_visible(self):
        a=self.save(body='<script>alert(1)</script>\n\n**保留原文**')
        self.assertEqual(self.s.notes.get(self.p,self.i,a['id'])['body'],a['body'])
        for path in ('../notes/x.md','/notes/x/y/z/file.md','notes/a/b/c/../../x','notes/a//b/c/'+'a'*32+'.md'):
            with self.assertRaises(DomainError):validate_path(path)
        target=self.store.directory/a['path'];target.unlink()
        target.symlink_to(self.store.path)
        with self.assertRaises(DomainError):self.s.notes.get(self.p,self.i,a['id'])
        item=self.s.notes.list(self.p,self.i)['items'][0]
        self.assertFalse(item['available']);self.assertEqual(item['id'],a['id'])
        target.unlink()
        self.assertEqual(self.s.notes.list(self.p,self.i)['total'],1)
    def test_pagination(self):
        notes=[self.save(title='笔记'+str(n)) for n in range(23)]
        one=self.s.notes.list(self.p,self.i);two=self.s.notes.list(self.p,self.i,page=2)
        self.assertEqual((len(one['items']),len(two['items'])),(20,3))
        self.assertEqual([x['id'] for x in one['items']+two['items']],list(reversed([n['id'] for n in notes])))
        self.assertTrue(all('body' not in n and n['excerpt'] for n in one['items']))
    def test_index_cannot_redirect_to_another_portfolio_file(self):
        a=self.save()
        b=self.s.notes.save(self.q,self.i,{'title':'乙组合私有正文','body':'不能串读','expected_revision':self.revision()})
        with self.store.connection() as db:
            # Keep the UNIQUE constraint intact while simulating a mismatched disk reference.
            db.execute('DELETE FROM investment_notes WHERE id=?',(b['id'],))
            db.execute('UPDATE investment_notes SET path=? WHERE id=?',(b['path'],a['id']));db.commit()
        with self.assertRaises(DomainError):self.s.notes.get(self.p,self.i,a['id'])
        listing=self.s.notes.list(self.p,self.i)['items'][0]
        self.assertFalse(listing['available']);self.assertNotIn('乙组合',str(listing));self.assertNotIn('不能串读',str(listing))
    def test_backup_companion_collision_preserves_existing_file(self):
        orphan=self.store.backups/'manual-check.notes.zip';orphan.write_bytes(b'preserve-this-file')
        result=self.store.backup('manual-check')
        self.assertNotEqual(result.name,'manual-check.sqlite')
        self.assertEqual(orphan.read_bytes(),b'preserve-this-file')
        self.assertTrue(result.with_suffix('.notes.zip').exists())
    def test_restore_disk_failure_preserves_target_database(self):
        self.save();archive=self.s.backup_download()
        target=Service(Store(Path(self.tmp.name)/'failed-restore'))
        target.save_portfolio({'name':'不可丢失的目标组合','planned_amount':'123','start_date':'2024-01-01'})
        before=target.store.state()
        with patch.object(target.store.note_files,'install',side_effect=OSError('test restore disk failure')):
            with self.assertRaises(OSError):target.restore(archive)
        self.assertEqual(target.store.state(),before)
        self.assertTrue(list(target.store.backups.glob('before-restore-*.sqlite')))
    def test_complete_backup_restore_and_deleted_history(self):
        a=self.save();b=self.save(body='第二版',note_id=a['id']);deleted=self.save(title='待恢复笔记')
        self.s.notes.set_deleted(self.p,self.i,deleted['id'],deleted=True,expected_revision=self.revision())
        before=self.store.state()['events'];archive=self.s.backup_download()
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            manifest=json.loads(z.read('manifest.json'))
            self.assertEqual(manifest['format'],2);self.assertEqual(len(manifest['note_files']),3)
            for name,digest in manifest['note_files'].items():self.assertEqual(hashlib.sha256(z.read(name)).hexdigest(),digest)
        target=Service(Store(Path(self.tmp.name)/'restore'))
        target.save_portfolio({'name':'恢复前目标记录','planned_amount':'1','start_date':'2024-01-01'})
        result=target.restore(archive);self.assertTrue(result['restored'])
        self.assertEqual(target.store.state()['events'],before)
        self.assertEqual(target.notes.get(self.p,self.i,a['id'])['body'],'第二版')
        self.assertEqual((target.store.directory/a['path']).read_bytes(),(self.store.directory/a['path']).read_bytes())
        target.notes.set_deleted(self.p,self.i,deleted['id'],deleted=False,expected_revision=target.store.state()['revision'])
        self.assertEqual(target.notes.list(self.p,self.i)['total'],2)
        preserved=target.store.backups/result['preserved_backup']
        self.assertTrue(preserved.exists());self.assertTrue(preserved.with_suffix('.notes.zip').exists())
        reopened=Service(Store(target.store.directory));self.assertEqual(reopened.notes.get(self.p,self.i,a['id'])['path'],b['path'])
        self.assertTrue(reopened.backup_download())
    def test_invalid_backup_never_replaces_target(self):
        a=self.save();archive=self.s.backup_download();before=self.store.state();note_before=self.s.notes.get(self.p,self.i,a['id'])
        with zipfile.ZipFile(io.BytesIO(archive)) as z: contents={n:z.read(n) for n in z.namelist()}
        for mode in ('missing','tampered','extra','wrong-scope'):
            altered=dict(contents)
            if mode=='missing':del altered[a['path']]
            elif mode=='tampered':altered[a['path']]+=b'tampered'
            elif mode=='extra':altered['../../escape']=b'test'
            else:
                altered[a['path']]=altered[a['path']].replace(self.p.encode(),self.q.encode())
                manifest=json.loads(altered['manifest.json']);manifest['note_files'][a['path']]=hashlib.sha256(altered[a['path']]).hexdigest();altered['manifest.json']=json.dumps(manifest).encode()
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:
                for name,data in altered.items():z.writestr(name,data)
            with self.assertRaises(DomainError,msg=mode):self.s.restore(buf.getvalue())
            self.assertEqual(self.store.state(),before);self.assertEqual(self.s.notes.get(self.p,self.i,a['id']),note_before)
    def test_schema_one_upgrade_preserves_legacy_rows(self):
        legacy=Path(self.tmp.name)/'legacy';store=Store(legacy);Service(store)
        with store.connection() as db:
            # Reconstruct the actual pre-Alembic layout, not a current DB with a forged version.
            db.execute('DROP TABLE alembic_version');db.execute('DROP TABLE fx_overrides')
            db.execute('DROP INDEX fx_daily_unique')
            from backend.family_domain import TABLES
            for table in TABLES: db.execute('DROP TABLE ' + table)
            db.execute('DROP TABLE holding_labels')
            for table in ('research_heads','research_snapshots','research_indices','research_meta','research_price_chunks','data_update_tasks','data_update_policies','data_update_receipts'):
                db.execute('DROP TABLE IF EXISTS '+table)
            db.execute('DROP TABLE investment_notes');db.execute("UPDATE meta SET value='1' WHERE key='schema'")
            db.execute('INSERT INTO reviews VALUES (?,?)',('old-event',json.dumps({'note':'旧复盘正文','completed':True})));db.commit()
            before=[tuple(r) for r in db.execute('SELECT * FROM reviews')]
        upgraded=Store(legacy)
        with upgraded.connection() as db:
            self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM reviews')],before)
            from backend.migration import SCHEMA
            self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0],str(SCHEMA))
        protection=list(upgraded.backups.glob('before-schema-upgrade-*.sqlite'));self.assertEqual(len(protection),1)
        target=Store(Path(self.tmp.name)/'legacy-restore');target.restore(protection[0].read_bytes(),{})
        self.assertEqual(target.state()['reviews'],[{'note':'旧复盘正文','completed':True}])


class NotesApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,testing=True,auto_sync=False)
        self.c=TestClient(self.app);self.c.__enter__();boot=self.c.get('/api/bootstrap').json();self.h={'x-csrf-token':boot['csrf_token']}
        self.s=self.app.state.service
        self.p=self.s.save_portfolio({'name':'API笔记测试','planned_amount':'10000','start_date':'2024-01-01'})['id']
        self.e=self.s.save_event({'portfolio_id':self.p,'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'510300'},'quantity':'10','gross_amount':'100','fx_rate':'1'})
        self.base='/api/portfolios/'+self.p+'/instruments/'+self.e['instrument_id']+'/notes'
    def tearDown(self):self.c.__exit__(None,None,None);self.tmp.cleanup()
    def test_contract_revision_and_forbidden_path(self):
        page=self.c.get(self.base).json();payload={'title':'AI笔记','body':'## 结论\n\n明确的观察','expected_revision':page['revision']}
        self.assertEqual(self.c.post(self.base,json=payload).status_code,403)
        self.assertEqual(self.c.post(self.base,json={**payload,'path':'custom.md'},headers=self.h).status_code,422)
        missing={k:v for k,v in payload.items() if k!='expected_revision'}
        self.assertEqual(self.c.post(self.base,json=missing,headers=self.h).status_code,422)
        response=self.c.post(self.base,json=payload,headers=self.h);self.assertEqual(response.status_code,200,response.text)
        note=response.json();self.assertEqual(self.c.get(self.base+'/'+note['id']).json()['body'],payload['body'])
        self.assertEqual(self.c.put(self.base+'/'+note['id'],json=payload,headers=self.h).status_code,409)
        self.assertEqual(self.c.get('/api/revisions/investment_note/'+note['id']).status_code,422)
        self.assertIn(self.base.replace(self.p,'{portfolio_id}').replace(self.e['instrument_id'],'{instrument_id}'),self.c.get('/api/openapi.json').json()['paths'])
    def test_retired_reviews_and_integrated_transaction_details(self):
        before=self.s.store.state()
        self.assertEqual(self.c.get('/api/portfolios/'+self.p+'/reviews').status_code,404)
        self.assertIn(self.c.put('/api/reviews/'+self.e['id'],json={'note':'禁止'},headers=self.h).status_code,(404,405))
        self.assertEqual(self.c.get('/api/portfolios/'+self.p+'/transactions?page=1&scope=all').status_code,422)
        self.assertEqual(self.s.store.state(),before)
        other=self.s.save_event({'portfolio_id':self.p,'kind':'BUY','date':'2024-01-02','instrument':{'market':'CN','code':'159915'},'quantity':'10','gross_amount':'100','fx_rate':'1'})
        self.s.save_event({**self.e,'gross_amount':'99','price':'9.9','change_reason':'核对更正'},self.e['id'])
        detail=self.c.get('/api/portfolios/'+self.p+'/transactions/'+self.e['id']).json()
        self.assertEqual([r['id'] for r in detail['neighbors']],[self.e['id']]);self.assertEqual(len(detail['revisions']),2)
        self.assertNotIn('review',detail);self.assertNotIn('windows',detail)
        row=next(r for r in self.s.transaction_rows(self.p) if r['event']['id']==self.e['id'])
        self.assertEqual(row['actual'],detail['actual']);self.assertTrue(row['can_view_profit']);self.assertNotIn('can_review',row)
        self.assertEqual(self.c.get('/api/portfolios/not-this-portfolio/transactions/'+other['id']).status_code,422)


if __name__=='__main__':unittest.main()
