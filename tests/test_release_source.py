"""Real local Git repositories exercise the master release gate without GitHub."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from scripts import manage, source_check
from backend.common import DomainError


class ReleaseSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'checkout';self.root.mkdir()
        self.remote=Path(self.temp.name)/'origin.git'
        self.git('init','-b','master')
        self.git('config','user.name','Synthetic')
        self.git('config','user.email','synthetic@example.invalid')
        (self.root/'README.md').write_text('synthetic source')
        self.git('add','README.md');self.git('commit','-m','Initial')
        self.git('init','--bare',str(self.remote))
        self.git('remote','add','origin',str(self.remote))
        self.git('push','-u','origin','master')

    def tearDown(self):self.temp.cleanup()

    def git(self,*args):
        return subprocess.check_output(['git',*args],cwd=self.root,text=True,stderr=subprocess.DEVNULL).strip()

    def test_accepts_clean_published_master(self):
        self.assertEqual(manage.release_source(self.root),{'kind':'git','branch':'master','commit':self.git('rev-parse','HEAD')})

    def test_rejects_branch_detached_dirty_and_unpublished_commit(self):
        head=self.git('rev-parse','HEAD')
        self.git('switch','-c','feat/synthetic')
        with self.assertRaisesRegex(DomainError,'只能从 master'):manage.release_source(self.root)
        self.git('checkout','--detach',head)
        with self.assertRaisesRegex(DomainError,'只能从 master'):manage.release_source(self.root)
        self.git('switch','master')
        (self.root/'README.md').write_text('uncommitted')
        with self.assertRaisesRegex(DomainError,'提交需求分支'):manage.release_source(self.root)
        self.git('add','.');self.git('commit','-m','Unpublished')
        with self.assertRaisesRegex(DomainError,'远端不一致'):manage.release_source(self.root)

    def test_reads_actual_remote_even_if_tracking_ref_is_stale(self):
        original=self.git('rev-parse','HEAD')
        (self.root/'README.md').write_text('new remote version')
        self.git('commit','-am','Remote change');self.git('push')
        self.git('reset','--hard',original)
        self.git('update-ref','refs/remotes/origin/master',original)
        with self.assertRaisesRegex(DomainError,'远端不一致'):manage.release_source(self.root)

    def test_rejects_wrong_upstream_and_missing_remote(self):
        self.git('branch','--unset-upstream')
        with self.assertRaises(DomainError):manage.release_source(self.root)
        self.git('branch','--set-upstream-to=origin/master')
        self.git('remote','set-url','origin',str(self.remote/'missing'))
        with self.assertRaises(DomainError):manage.release_source(self.root)

    def test_archive_and_broken_git_are_distinguished(self):
        archive=Path(self.temp.name)/'archive';archive.mkdir()
        self.assertEqual(manage.release_source(archive),{'kind':'archive','commit':None})
        (archive/'.git').write_text('gitdir: missing')
        with self.assertRaises(DomainError):manage.release_source(archive)

    def test_source_image_allowlist_stays_narrow(self):
        self.assertTrue(source_check.allowed(source_check.ROOT/'docs/images/assets-overview.jpg'))
        self.assertFalse(source_check.allowed(source_check.ROOT/'docs/images/private.jpg'))
        self.assertTrue(source_check.sensitive('docs/images/assets-overview.jpg',b'not an image'))

    def test_hooks_reject_mainline_writes(self):
        hooks=Path(manage.ROOT)/'.githooks'
        commit=subprocess.run(['sh',str(hooks/'pre-commit')],cwd=self.root,capture_output=True,text=True)
        self.assertNotEqual(commit.returncode,0);self.assertIn('需求分支',commit.stderr)
        push=subprocess.run(['sh',str(hooks/'pre-push')],cwd=self.root,input='refs/heads/feature 123 refs/heads/master 456\n',capture_output=True,text=True)
        self.assertNotEqual(push.returncode,0);self.assertIn('禁止直接推送',push.stderr)


if __name__=='__main__':unittest.main()
