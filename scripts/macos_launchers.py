"""Build local macOS app launchers with a branded icon and explicit terminal choice."""
from __future__ import annotations
import hashlib
import json
import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.common import DomainError
from backend.version import VERSION

LOCATE = '''ObjC.import("AppKit");
function run(argv) {
  var url = $.NSWorkspace.sharedWorkspace.URLForApplicationWithBundleIdentifier(argv[0]);
  return url.isNil() ? "" : ObjC.unwrap(url.path);
}
'''

# Opening the command file through LaunchServices uses the terminal's native
# document handler. It does not type into an existing session or change defaults.
LAUNCH = '''#!/bin/zsh
set -eu
resources="${0:A:h:h}/Resources"
iterm=$(/usr/bin/osascript -l JavaScript "$resources/locate.js" com.googlecode.iterm2 2>/dev/null) || iterm=""
if [[ -n "$iterm" && -d "$iterm" ]]; then
  exec /usr/bin/open -a "$iterm" "$resources/action.command"
fi
terminal=$(/usr/bin/osascript -l JavaScript "$resources/locate.js" com.apple.Terminal 2>/dev/null) || terminal=""
if [[ -z "$terminal" ]]; then terminal=/System/Applications/Utilities/Terminal.app; fi
exec /usr/bin/open -a "$terminal" "$resources/action.command"
'''


def command_script(output, code_root, workspace, environment, action):
    if environment not in ('dev', 'production', 'test') or action not in ('start', 'stop'):
        raise DomainError('未知环境或启动动作')
    output, code_root, workspace = map(lambda p: Path(p).resolve(), (output, code_root, workspace))
    code = workspace/'runtime/current' if environment == 'production' else code_root
    code_relative = shlex.quote(os.path.relpath(code, output))
    workspace_relative = shlex.quote(os.path.relpath(workspace, output))
    title = {'dev':'开发', 'test':'测试', 'production':'正式'}[environment]
    lines = ['#!/bin/zsh', 'set -eu', 'location="${0:A:h:h:h:h}"',
             'cd "$location"', 'code="$location"/'+code_relative,
             'workspace="$location"/'+workspace_relative,
             'if [[ ! -x "$code/.venv/bin/python" ]]; then',
             "  print '缺少运行环境，请先按 README 完成安装。'", '  exit 1', 'fi',
             "print '知行财富 · "+title+" · "+('启动' if action=='start' else '停止')+"'"]
    base = '"$code/.venv/bin/python" "$code/scripts/manage.py" --workspace "$workspace" '
    if action == 'start':lines.append(base+'init --environment '+environment)
    lines.append('exec '+base+action+' --environment '+environment+(' --open' if action=='start' else ''))
    return '\n'.join(lines)+'\n'


def make_icon(logo, destination, scratch):
    iconset = scratch/'AppIcon.iconset';iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            name=f'icon_{size}x{size}'+('@2x' if scale==2 else '')+'.png'
            subprocess.run(['/usr/bin/sips','-z',str(size*scale),str(size*scale),str(logo),
                            '--out',str(iconset/name)],check=True,stdout=subprocess.DEVNULL)
    subprocess.run(['/usr/bin/iconutil','-c','icns',str(iconset),'-o',str(destination)],check=True)


def bundle_id(environment, action):
    return 'local.zhixingwealth.'+environment+'.'+action


def build_launchers(workspace, environment, *, code_root=ROOT, output=None):
    if environment not in ('dev','production','test'):raise DomainError('未知环境')
    if sys.platform != 'darwin':raise DomainError('带图标的应用入口仅用于 macOS；其他平台使用 manage.py')
    workspace, code_root = Path(workspace).resolve(), Path(code_root).resolve()
    if output is None:
        output = code_root if environment=='dev' else workspace/('runtime' if environment=='production' else 'local/test')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    logo=code_root/'frontend/public/zhixing-logo.png'
    suffix={'dev':'（开发）','test':'（测试）','production':''}[environment]
    targets={action:output/(label+'知行财富'+suffix+'.app') for action,label in [('start','启动'),('stop','停止')]}
    # Do not replace another application or a user-created directory at the same path.
    for action,target in targets.items():
        if target.is_symlink():raise DomainError('应用入口不能是符号链接')
        if target.exists():
            try:
                info=plistlib.loads((target/'Contents/Info.plist').read_bytes())
                marker=json.loads((target/'Contents/Resources/launcher.json').read_text())
                valid=info['CFBundleIdentifier']==bundle_id(environment,action) and marker['managed_by']=='zhixing-launchers-v1'
            except (OSError,ValueError,KeyError):valid=False
            if not valid:raise DomainError('目标存在非本工具生成的应用，未覆盖：'+target.name)
    with tempfile.TemporaryDirectory(prefix='.launchers-',dir=output) as tmp:
        scratch=Path(tmp);icon=scratch/'AppIcon.icns';make_icon(logo,icon,scratch)
        for action,target in targets.items():
            staged=scratch/target.name;contents=staged/'Contents';resources=contents/'Resources'
            resources.mkdir(parents=True);(contents/'MacOS').mkdir()
            info={'CFBundleName':target.stem,'CFBundleDisplayName':target.stem,
                  'CFBundleIdentifier':bundle_id(environment,action),'CFBundleExecutable':'launch',
                  'CFBundleIconFile':'AppIcon.icns','CFBundlePackageType':'APPL',
                  'CFBundleShortVersionString':VERSION,'CFBundleVersion':VERSION,
                  'LSUIElement':True,'NSHighResolutionCapable':True}
            (contents/'Info.plist').write_bytes(plistlib.dumps(info))
            (contents/'MacOS/launch').write_text(LAUNCH);(contents/'MacOS/launch').chmod(0o755)
            (resources/'action.command').write_text(command_script(output,code_root,workspace,environment,action))
            (resources/'action.command').chmod(0o755);(resources/'locate.js').write_text(LOCATE)
            shutil.copy2(icon,resources/'AppIcon.icns')
            (resources/'launcher.json').write_text(json.dumps({'managed_by':'zhixing-launchers-v1',
                'environment':environment,'action':action,'logo_sha256':hashlib.sha256(logo.read_bytes()).hexdigest()}))
        for action,target in targets.items():
            old=scratch/(target.name+'.previous')
            if target.exists():target.rename(old)
            try:(scratch/target.name).rename(target)
            except Exception:
                if old.exists():old.rename(target)
                raise
    # Retire only the exact legacy production wrappers generated by version 0.7.0.
    if environment=='production' and output==workspace/'runtime':
        for action,label in [('start','启动'),('stop','停止')]:
            legacy=output/(label+'知行财富.command')
            expected='#!/bin/zsh\nset -e\ncd "${0:A:h}"\nexec ./current/.venv/bin/python ./current/scripts/manage.py --workspace "${PWD:h}" '+action+' --environment production'+(' --open' if action=='start' else '')+'\n'
            if legacy.is_file() and not legacy.is_symlink() and legacy.read_text()==expected:legacy.unlink()
    return {'environment':environment,'launchers':[str(p) for p in targets.values()],
            'terminal_preference':['iTerm','Terminal']}
