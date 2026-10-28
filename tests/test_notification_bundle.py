"""Notification app branding and upgrades, using synthetic bundle resources."""
import hashlib
import json
import os
import plistlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.common import DomainError
from scripts import build_notifications as notifications


class NotificationBundleTests(unittest.TestCase):
    def fixture(self, base):
        root = base / 'source'
        for relative, data in {
            'native/FamilyNotifications.swift': b'synthetic swift source',
            'native-build/FamilyNotifications': b'synthetic precompiled executable',
            'frontend/public/zhixing-logo.png': b'synthetic logo v1',
            'scripts/macos_launchers.py': b'synthetic icon builder',
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return root, base / 'agent', base / 'ledger'

    @staticmethod
    def icon(logo, destination, scratch):
        destination.write_bytes(b'icns:' + logo.read_bytes())

    def test_brand_is_packaged_and_changed_logo_rebuilds_same_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            args = (*self.fixture(Path(temp)), 'test', 8876, '/synthetic/python')
            with patch.object(notifications, 'make_icon', side_effect=self.icon) as icon, \
                    patch.object(notifications.subprocess, 'run') as command:
                app = notifications.build(*args)
                info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
                resources = app / 'Contents/Resources'
                self.assertEqual(info['CFBundleIconFile'], 'AppIcon.icns')
                self.assertEqual((resources / info['CFBundleIconFile']).read_bytes(), b'icns:synthetic logo v1')
                self.assertEqual(notifications.build(*args), app)
                self.assertEqual(icon.call_count, 1)
                (args[0] / 'frontend/public/zhixing-logo.png').write_bytes(b'synthetic logo v2')
                notifications.build(*args)
                self.assertEqual(icon.call_count, 2)
                self.assertEqual((resources / 'AppIcon.icns').read_bytes(), b'icns:synthetic logo v2')
                self.assertEqual(plistlib.loads((app / 'Contents/Info.plist').read_bytes())['CFBundleIdentifier'], info['CFBundleIdentifier'])
                self.assertTrue(all(call.args[0][0] == '/usr/bin/codesign' for call in command.call_args_list))

    def test_legacy_cached_bundle_without_icon_is_upgraded(self):
        with tempfile.TemporaryDirectory() as temp:
            args = (*self.fixture(Path(temp)), 'test', 8876, '/synthetic/python')
            with patch.object(notifications, 'make_icon', side_effect=self.icon) as icon, \
                    patch.object(notifications.subprocess, 'run'):
                app = notifications.build(*args)
                resources = app / 'Contents/Resources'
                config = json.loads((resources / 'config.json').read_text())
                legacy = hashlib.sha256((args[0] / 'native/FamilyNotifications.swift').read_bytes()
                                        + json.dumps(config, sort_keys=True).encode()).hexdigest()
                (resources / 'build-hash').write_text(legacy)
                (resources / 'AppIcon.icns').unlink()
                os.utime(app, (1, 1))
                notifications.build(*args)
                self.assertEqual(icon.call_count, 2)
                self.assertTrue((resources / 'AppIcon.icns').is_file())
                self.assertNotEqual((resources / 'build-hash').read_text(), legacy)
                self.assertGreater(app.stat().st_mtime, 1)

    def test_failed_signing_is_not_cached_as_success(self):
        with tempfile.TemporaryDirectory() as temp:
            args = (*self.fixture(Path(temp)), 'test', 8876, '/synthetic/python')
            with patch.object(notifications, 'make_icon', side_effect=self.icon), \
                    patch.object(notifications.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'codesign')):
                with self.assertRaises(DomainError):
                    notifications.build(*args)
            self.assertFalse((args[1] / 'FamilyNotifications.app/Contents/Resources/build-hash').exists())

    def test_development_compile_cache_is_temporary_on_success_and_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            args = (*self.fixture(Path(temp)), 'test', 8876, '/synthetic/python')
            (args[0] / 'native-build/FamilyNotifications').unlink()
            def compile_command(command, **kwargs):
                if 'swiftc' in command:
                    cache = Path(command[command.index('-module-cache-path') + 1])
                    cache.mkdir(parents=True); (cache / 'large-cache').write_bytes(b'synthetic')
            with patch.object(notifications, 'make_icon', side_effect=self.icon), patch.object(notifications.subprocess, 'run', side_effect=compile_command):
                notifications.build(*args)
            self.assertFalse(list(args[1].glob('.swift-*')))
            (args[1] / 'FamilyNotifications.app/Contents/Resources/build-hash').unlink()
            def failed(command, **kwargs):
                compile_command(command, **kwargs)
                raise subprocess.CalledProcessError(1, 'swiftc')
            with patch.object(notifications.subprocess, 'run', side_effect=failed):
                with self.assertRaises(DomainError): notifications.build(*args)
            self.assertFalse(list(args[1].glob('.swift-*')))


if __name__ == '__main__':
    unittest.main()
