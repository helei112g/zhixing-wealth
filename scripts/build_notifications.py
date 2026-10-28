"""Build a data-directory-isolated macOS notification agent; output is never source."""
import hashlib
import json
import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path
from backend.common import DomainError
from backend.version import VERSION
from scripts.macos_launchers import make_icon


def compile_binary(root, target):
    target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', '-module-cache-path', str(target.parent / 'module-cache'),
                    str(Path(root) / 'native/FamilyNotifications.swift'), '-o', str(target)],
                   capture_output=True, check=True, timeout=180)
    return target


def build(root, output, data, environment, port, python):
    source = Path(root) / 'native/FamilyNotifications.swift'
    logo = Path(root) / 'frontend/public/zhixing-logo.png'
    output = Path(output); output.mkdir(parents=True, exist_ok=True, mode=0o700)
    app = output / 'FamilyNotifications.app'
    contents = app / 'Contents'; binaries = contents / 'MacOS'; resources = contents / 'Resources'
    binaries.mkdir(parents=True, exist_ok=True); resources.mkdir(exist_ok=True)
    identity = 'local.zhixing.family.' + environment + '.' + hashlib.sha256(str(Path(data).resolve()).encode()).hexdigest()[:16]
    config = {'ipcDirectory': str(output.resolve()), 'dataDirectory': str(Path(data).resolve()),
              'python': python, 'launcher': str(Path(root) / 'run.py'), 'environment': environment, 'port': port}
    # Bundle metadata and branding must invalidate previously built agents too.
    marker = hashlib.sha256(source.read_bytes() + logo.read_bytes() + Path(__file__).read_bytes()
                            + (Path(root) / 'scripts/macos_launchers.py').read_bytes()
                            + VERSION.encode() + json.dumps(config, sort_keys=True).encode()).hexdigest()
    if ((resources / 'AppIcon.icns').is_file() and (resources / 'build-hash').exists()
            and (resources / 'build-hash').read_text() == marker):
        return app
    prebuilt = Path(root) / 'native-build/FamilyNotifications'
    try:
        if prebuilt.exists(): shutil.copy2(prebuilt, binaries / 'FamilyNotifications')
        else:
            with tempfile.TemporaryDirectory(prefix='.swift-', dir=output) as scratch:
                subprocess.run(['/usr/bin/xcrun', 'swiftc', '-O', '-module-cache-path', str(Path(scratch) / 'module-cache'),
                                str(source), '-o', str(binaries / 'FamilyNotifications')], capture_output=True, check=True, timeout=120)
        display_name = '知行财富通知' + {'dev': ' · 开发', 'test': ' · 测试', 'production': ''}[environment]
        info = {'CFBundleIdentifier': identity, 'CFBundleName': display_name,
                'CFBundleDisplayName': display_name, 'CFBundleExecutable': 'FamilyNotifications',
                'CFBundleIconFile': 'AppIcon.icns', 'CFBundleShortVersionString': VERSION,
                'CFBundlePackageType': 'APPL', 'CFBundleVersion': VERSION, 'LSUIElement': True,
                'NSUserNotificationAlertStyle': 'banner', 'LSMinimumSystemVersion': '12.0'}
        with tempfile.TemporaryDirectory(prefix='.icon-', dir=output) as scratch:
            make_icon(logo, resources / 'AppIcon.icns', Path(scratch))
        (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
        (resources / 'config.json').write_text(json.dumps(config)); (resources / 'config.json').chmod(0o600)
        (resources / 'build-hash').write_text(marker)
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', identity, str(app)], capture_output=True, check=True, timeout=30)
        # Launch Services watches the bundle directory's mtime, not its nested
        # resources. Refresh registration on the next open after an icon update.
        app.touch()
        return app
    except (OSError, subprocess.SubprocessError) as exc:
        (resources / 'build-hash').unlink(missing_ok=True)
        raise DomainError('通知组件构建失败；开发环境需安装 Xcode Command Line Tools，页面提醒仍可使用') from exc
