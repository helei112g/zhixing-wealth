import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import macos_launchers as launchers
import source_check
from backend.common import DomainError


class MacLauncherTests(unittest.TestCase):
    def fixture(self, base):
        code=base/"source space ' $quoted";logo=code/'frontend/public/zhixing-logo.png'
        logo.parent.mkdir(parents=True);logo.write_bytes(b'icon fixture')
        return code

    def test_app_metadata_scope_and_safe_rebuild(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();code=self.fixture(root);output=root/'entries'
            def icon(_,destination,__):destination.write_bytes(b'icns fixture')
            with patch.object(launchers.sys,'platform','darwin'),patch.object(launchers,'make_icon',side_effect=icon):
                first=launchers.build_launchers(root,'dev',code_root=code,output=output)
                again=launchers.build_launchers(root,'dev',code_root=code,output=output)
            self.assertEqual(first,again)
            for action,path in zip(('start','stop'),first['launchers']):
                app=Path(path);info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
                self.assertEqual(info['CFBundleIdentifier'],launchers.bundle_id('dev',action))
                self.assertEqual((app/'Contents/Resources/AppIcon.icns').read_bytes(),b'icns fixture')
                self.assertTrue(os.access(app/'Contents/MacOS/launch',os.X_OK))
                self.assertIn('（开发）',app.name)

    def test_existing_unknown_application_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();code=self.fixture(root);app=root/'启动知行财富.app';app.mkdir();(app/'original').write_text('keep')
            with patch.object(launchers.sys,'platform','darwin'):
                with self.assertRaises(DomainError):launchers.build_launchers(root,'production',code_root=code,output=root)
            self.assertEqual((app/'original').read_text(),'keep')

    @unittest.skipUnless(shutil.which('zsh'),'macOS launcher requires zsh')
    def test_shell_quoting_and_exact_environment_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();code=self.fixture(root);(code/'.venv/bin').mkdir(parents=True);(code/'.venv/bin/python').symlink_to(sys.executable)
            script=code/'scripts/manage.py';script.parent.mkdir();script.write_text('import json,os,sys\nwith open(os.environ["LAUNCHER_TEST_REPORT"],"a") as f:f.write(json.dumps(sys.argv[1:])+"\\n")\n')
            output=root/'entry space';file=output/'测试.app/Contents/Resources/action.command';file.parent.mkdir(parents=True)
            report=root/'report.jsonl'
            for environment in ('dev','test'):
                for action in ('start','stop'):
                    file.write_text(launchers.command_script(output,code,root,environment,action))
                    subprocess.run(['zsh',str(file)],env={**os.environ,'LAUNCHER_TEST_REPORT':str(report)},check=True,capture_output=True)
            values=[json.loads(line) for line in report.read_text().splitlines()]
            self.assertEqual([v[2] for v in values],['init','start','stop']*2)
            self.assertTrue(all(v[:2]==['--workspace',str(output/'..')] for v in values))
            self.assertEqual([v[v.index('--environment')+1] for v in values],['dev']*3+['test']*3)
            subprocess.run(['zsh','-n'],input=launchers.LAUNCH,text=True,check=True,capture_output=True)

    def test_production_launcher_resolves_current_on_every_run(self):
        script=launchers.command_script('/workspace/runtime','/workspace/runtime/releases/v1','/workspace','production','start')
        self.assertIn('current',script);self.assertNotIn('releases/v1',script)
        self.assertIn('--environment production --open',script)

    def test_generated_apps_cannot_enter_public_source(self):
        self.assertFalse(source_check.allowed(ROOT/'启动知行财富（开发）.app/Contents/Info.plist'))
        self.assertFalse(source_check.allowed(ROOT/'启动知行财富（开发）.app/Contents/Resources/action.command'))


if __name__=='__main__':unittest.main()
