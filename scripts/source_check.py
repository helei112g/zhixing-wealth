#!/usr/bin/env python3
"""Validate/export an allowlisted source tree; never collect a ledger or local work."""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {'README.md','AGENTS.md','.gitignore','LICENSE','requirements.txt','requirements.lock.txt','run.py'}
AREAS = {'backend': {'.py'}, 'tests': {'.py'}, 'scripts': {'.py','.cjs'}, 'native': {'.swift'}, 'docs': {'.md'},
         'frontend/src': {'.ts','.tsx','.css'}, 'migrations': {'.py'}, '.github/workflows': {'.yml'}}
STATIC = {'frontend/public/zhixing-logo.png', '.githooks/pre-commit', '.githooks/pre-push'}
SKILLS = {'zhixing-assets', 'zhixing-portfolios', 'zhixing-notes'}
STATIC |= {f'.agents/skills/{name}/{part}' for name in SKILLS for part in ('SKILL.md','agents/openai.yaml')}
FRONTEND = {'package.json','package-lock.json','index.html','tsconfig.json','tsconfig.app.json','tsconfig.node.json','vite.config.ts'}


def allowed(path):
    try: parts = path.relative_to(ROOT).parts
    except ValueError: return False
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != ROOT and p.is_relative_to(ROOT)):
        return False
    if path.relative_to(ROOT).as_posix() in STATIC: return True
    if len(parts) == 1: return parts[0] in ROOT_FILES or parts[0].endswith('.command')
    if len(parts) == 2 and parts[0] == 'frontend': return parts[1] in FRONTEND
    for area, suffixes in AREAS.items():
        prefix = Path(area).parts
        if parts[:len(prefix)] == prefix and '__pycache__' not in parts and path.suffix in suffixes:
            return True
    return False


def collect():
    paths = [ROOT/name for name in ROOT_FILES if (ROOT/name).exists()]
    paths += list(ROOT.glob('*.command'))
    paths += [ROOT/'frontend'/name for name in FRONTEND if (ROOT/'frontend'/name).exists()]
    paths += [ROOT/name for name in STATIC if (ROOT/name).is_file()]
    for area in AREAS:
        paths += [p for p in (ROOT/area).rglob('*') if p.is_file() and allowed(p)]
    return sorted(set(paths))


def public_metadata(info):
    # A source archive must not carry the local account name or numeric identity.
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    return info


def sensitive(relative, data):
    if relative == 'frontend/public/zhixing-logo.png':
        return not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 4 * 1024 * 1024
    content = data.decode('utf-8')
    patterns = (r'/(?:Users|home)/[A-Za-z0-9_.-]+/', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                r'gh[pousr]_[A-Za-z0-9]{25,}', r'github_pat_[A-Za-z0-9_]{30,}', r'AKIA[A-Z0-9]{16}')
    return any(re.search(pattern, content) for pattern in patterns)


def check(paths):
    problems = []
    for path in paths:
        relative = str(path.relative_to(ROOT))
        if not allowed(path): problems.append(relative+': not a regular allowlisted source file'); continue
        if sensitive(relative, path.read_bytes()):
            problems.append(relative+': possible personal path or credential; inspect locally')
    if not shutil.which('git'): return problems
    git = subprocess.run(['git','rev-parse','--show-toplevel'],cwd=ROOT,text=True,capture_output=True)
    if git.returncode == 0:
        if Path(git.stdout.strip()).resolve() != ROOT:
            problems.append('Initialize/publish portfolio-app itself, not its parent directory')
        else:
            tracked = subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
            for item in filter(None, tracked):
                if not allowed(ROOT/item): problems.append(item+': tracked file outside source allowlist')
            staged = subprocess.check_output(['git','diff','--cached','--name-only','--diff-filter=ACMR','-z'],cwd=ROOT).decode().split('\0')
            for item in filter(None, staged):
                if not allowed(ROOT/item):
                    problems.append(item+': staged file outside source allowlist')
                elif sensitive(item, subprocess.check_output(['git','show',':'+item],cwd=ROOT)):
                    problems.append(item+': staged content may contain private information')
    return problems


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,help='Optional source-only .tar.gz, preferably under work/')
    args=parser.parse_args();paths=collect();problems=check(paths)
    if problems: raise SystemExit('\n'.join(problems))
    manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    if args.output:
        output=args.output.resolve()
        if output.exists(): raise SystemExit('Refuse to overwrite an existing archive')
        if allowed(output): raise SystemExit('Archive must not replace a source path')
        output.parent.mkdir(parents=True,exist_ok=True)
        with tarfile.open(output,'w:gz') as archive:
            for path in paths: archive.add(path,arcname='portfolio-app/'+str(path.relative_to(ROOT)),recursive=False,filter=public_metadata)
    print(json.dumps({'source_files':len(paths),'checks':'passed','archive':str(args.output) if args.output else None,
                      'manifest':manifest},ensure_ascii=False,indent=2))


if __name__=='__main__': main()
