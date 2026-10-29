#!/usr/bin/env python3
"""Prepare immutable local releases and operate isolated environments. Never initializes Git."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import webbrowser
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from backend.common import DomainError, now
from backend.environment import LABELS, PORTS, LedgerProcessLock, claim_environment, config_path, read_config, workspace_for
from backend.migration import SCHEMA, schema_version
from backend.version import VERSION
from backend.deployment_guard import capture as capture_ledger, verify as verify_ledger, require_empty
import source_check


def atomic_json(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.pending')
    with temp.open('w') as handle:
        json.dump(value,handle,ensure_ascii=False,indent=2);handle.flush();os.fsync(handle.fileno())
    os.replace(temp,path)


def source_manifest(root):
    root=Path(root).resolve()
    previous=source_check.ROOT
    source_check.ROOT=Path(root).resolve()
    try:
        paths=source_check.collect();problems=source_check.check(paths)
        if problems:raise DomainError('\n'.join(problems))
        return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    finally:source_check.ROOT=previous


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def init_environment(workspace, environment):
    path=config_path(workspace,environment)
    if not path.exists():
        path.parent.mkdir(parents=True,exist_ok=True)
        data='browser-test' if environment=='test' else 'data'
        content=f'environment = "{environment}"\nport = {PORTS[environment]}\ndata_dir = "{data}"\nruntime_dir = ".runtime"\nauto_sync = '+('true' if environment=='production' else 'false')+'\n'
        with path.open('x') as handle:handle.write(content)
    values=read_config(path)
    if values['environment']!=environment:raise DomainError('配置所属环境不一致')
    claim_environment(values['data_dir'],environment)
    values['runtime_dir'].mkdir(parents=True,exist_ok=True)
    (path.parent/'logs').mkdir(exist_ok=True)
    return path,values


def get_config(workspace,environment):
    path=config_path(workspace,environment)
    if not path.exists():raise DomainError('环境尚未配置，请先运行 init')
    values=read_config(path)
    if values['environment']!=environment:raise DomainError('配置所属环境不一致')
    claim_environment(values['data_dir'],environment)
    return path,values


def health(values):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{values['port']}/api/health",timeout=2) as response:value=json.load(response)
    except Exception:return None
    if value.get('application')!='portfolio-journal' or value.get('environment')!=values['environment'] or value.get('data_directory')!=str(values['data_dir']):
        raise DomainError('端口上的服务不属于目标环境，已停止操作')
    return value


def release_path(workspace, name):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',name):raise DomainError('版本标识不合法')
    path=Path(workspace)/'runtime/releases'/name
    if path.is_symlink():raise DomainError('版本目录不能是符号链接')
    return path


def verify_release(path):
    path=Path(path).resolve()
    manifest=json.loads((path/'release.json').read_text())
    if manifest.get('schema')!=SCHEMA or manifest.get('checks',{}).get('browser') is not True:
        raise DomainError('版本缺少受支持的数据库声明或完整验证')
    if source_manifest(path)!=manifest['source_files']:
        raise DomainError('候选版本源码在验证后发生变化，请重新准备版本')
    assets={p.relative_to(path).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (path/'frontend/dist').rglob('*') if p.is_file()}
    if assets!=manifest['assets']:raise DomainError('候选版本前端文件在验证后发生变化')
    native = manifest.get('native_assets', {})
    if any(not (path / name).is_file() or hashlib.sha256((path / name).read_bytes()).hexdigest() != checksum for name, checksum in native.items()):
        raise DomainError('候选版本原生通知组件校验失败')
    if (path / 'native/FamilyNotifications.swift').is_file() and sys.platform == 'darwin' and not native:
        raise DomainError('macOS 候选版本缺少已构建的原生通知组件')
    if not (path/'.venv/bin/python').is_file():raise DomainError('版本缺少独立 Python 运行环境')
    # Legacy releases can still be inspected/started. apply/rollback separately
    # require the data guard. New releases must also verify the AI target here.
    if manifest.get('checks', {}).get('ai_client') is True:
        verify_ai_target(path)
    return manifest


def verify_ai_target(path, data_dir=None):
    path = Path(path).resolve()
    command = [sys.executable, str(path/'scripts/ai_client.py')]
    result = subprocess.run(command+['target'], capture_output=True, text=True, check=True)
    target = json.loads(result.stdout)
    if target.get('environment') != 'production' or target.get('base_url') != f'http://127.0.0.1:{PORTS["production"]}':
        raise DomainError('发布包 AI 客户端没有默认连接正式环境')
    if data_dir is not None:
        result = subprocess.run(command+['--expected-data-dir', str(data_dir), 'read', '/api/health'],
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    return target


def current_release(workspace):
    link=Path(workspace)/'runtime/current'
    if not link.is_symlink():raise DomainError('尚未安装正式版本')
    target=link.resolve()
    if target.parent!=(Path(workspace)/'runtime/releases').resolve():raise DomainError('正式版本指针超出 releases 目录')
    return target


def start(workspace,environment,*,no_sync=False,open_browser=False,read_only=False):
    config,values=get_config(workspace,environment)
    if environment=='production' and (values['runtime_dir']/'deployment-guard.json').exists() and not read_only:
        raise DomainError('正式部署尚未通过数据核验，禁止开放业务；先核对阻断记录')
    existing=health(values)
    if existing:
        if bool(existing.get('read_only',False))!=read_only:
            raise DomainError('运行中的服务与要求的只读模式不一致；请先核对并停止实例')
        if open_browser:webbrowser.open(f"http://127.0.0.1:{values['port']}")
        return existing
    root=current_release(workspace) if environment=='production' else ROOT
    if environment=='production':verify_release(root)
    python=root/'.venv/bin/python'
    if not python.exists():raise DomainError('缺少该环境的 Python 虚拟环境')
    log=config.parent/'logs/server.log';log.parent.mkdir(exist_ok=True)
    command=[str(python),str(root/'run.py'),'--config',str(config)]
    if not open_browser:command.append('--no-open')
    if no_sync:command.append('--no-sync')
    if read_only:command.append('--read-only')
    with log.open('ab') as handle:
        process=subprocess.Popen(command,cwd=root,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        for _ in range(100):
            found=health(values)
            if found:return found
            if process.poll() is not None:raise DomainError('启动失败，请检查该环境 logs/server.log')
            time.sleep(.1)
        raise DomainError('启动检查超时；已终止本次启动，请检查日志及 status')
    except BaseException:
        # Own this exact child, not whichever process occupies the port. A slow
        # child must never open writes after the deployment was declared failed.
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=5)
        raise


def stop(workspace,environment):
    config,values=get_config(workspace,environment)
    current=health(values)
    if current:
        root=Path(current['root'])
        # run.py verifies the recorded instance, PID and data path before signalling.
        subprocess.run([str(root/'.venv/bin/python') if (root/'.venv/bin/python').exists() else sys.executable,
                        str(root/'run.py'),'--config',str(config),'--stop'],check=True)
    wait_for_ledger_stop(values['data_dir'])


def wait_for_ledger_stop(directory, timeout=15):
    # Older launchers return when HTTP closes, before shutdown releases the
    # ledger lock. Offline backup/migration must wait for that exact lock too.
    deadline=time.monotonic()+timeout
    while True:
        try:
            with LedgerProcessLock(directory):return
        except DomainError as exc:
            if str(exc)!='该数据目录已有服务或维护操作运行，请先停止对应实例':raise
        if time.monotonic()>=deadline:
            raise DomainError('实例仍占用数据锁，未继续备份或迁移；请核对退出中的服务')
        time.sleep(.1)


def backup(workspace,environment):
    config,values=get_config(workspace,environment)
    target=values['data_dir']/'backups'/('full-'+now().replace(':','-')+'.zip')
    target.parent.mkdir(parents=True,exist_ok=True)
    if health(values):
        with urllib.request.urlopen(f"http://127.0.0.1:{values['port']}/api/backup",timeout=120) as response:data=response.read()
        checksum=hashlib.sha256(data).hexdigest()
        for saved in sorted(target.parent.glob('manual-*.zip'), key=lambda p:p.stat().st_mtime_ns, reverse=True):
            if not saved.is_symlink() and saved.stat().st_size==len(data) and hashlib.sha256(saved.read_bytes()).hexdigest()==checksum:
                return {'backup':str(saved),'sha256':checksum}
        with target.open('xb') as handle:handle.write(data)
        return {'backup':str(target),'sha256':hashlib.sha256(data).hexdigest()}
    from maintenance import snapshot
    with LedgerProcessLock(values['data_dir']):return snapshot(values['data_dir'],target)


def release_source(root):
    """Gate Git builds on the published master tip; archives keep hash identity."""
    root=Path(root).resolve()
    git=subprocess.run(['git','rev-parse','--show-toplevel'],cwd=root,capture_output=True,text=True)
    if git.returncode==0:
        if Path(git.stdout.strip()).resolve()!=root:raise DomainError('Git 仓库边界错误')
        def read(*args):
            result=subprocess.run(['git',*args],cwd=root,capture_output=True,text=True,timeout=30)
            if result.returncode:raise DomainError('无法核对发布来源，请检查 master、origin 和网络后重试')
            return result.stdout.strip()
        if read('status','--porcelain'):raise DomainError('发布前请提交需求分支改动并通过 MR 合并到 master')
        if read('branch','--show-current')!='master':raise DomainError('正式版本只能从 master 准备；请先完成需求分支测试和 MR 合并')
        if read('rev-parse','--abbrev-ref','@{upstream}')!='origin/master':raise DomainError('master 必须跟踪 origin/master')
        commit=read('rev-parse','HEAD')
        remote=read('ls-remote','--exit-code','origin','refs/heads/master').split()
        if len(remote)!=2 or remote[0]!=commit or read('rev-parse','origin/master')!=commit:
            raise DomainError('本地 master 与远端不一致；请核对 MR 已合并并执行 git pull --ff-only')
        return {'kind':'git','branch':'master','commit':commit}
    # A downloaded source archive has no Git metadata. Its master provenance
    # must be checked by the installer; the release fixes its complete hash.
    if (root/'.git').exists() or (root/'.git').is_symlink():raise DomainError('Git 元数据不可用，不能当作源码包绕过发布检查')
    return {'kind':'archive','commit':None}


def prepare(workspace):
    provenance=release_source(ROOT)
    sources=source_manifest(ROOT)
    source_id=digest(sources)
    commit=provenance['commit']
    name=f'v{VERSION}-{source_id[:12]}' + (f'-{commit[:8]}' if commit else '')
    destination=release_path(workspace,name)
    if destination.exists():
        existing=verify_release(destination)
        if existing.get('provenance')!=provenance:
            raise DomainError('已有候选包的发布来源不一致，请核对并重新准备，不能复用未记录来源的版本')
        return {'release':name,'path':str(destination),'reused':True}
    destination.mkdir(parents=True)
    try:
        for relative in sources:
            target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/relative,target)
        subprocess.run([sys.executable,'-m','venv',str(destination/'.venv')],check=True)
        python=destination/'.venv/bin/python'
        subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(destination/'requirements.lock.txt')],check=True)
        subprocess.run([str(python),'-m','pip','check'],check=True)
        subprocess.run(['npm','--prefix',str(destination/'frontend'),'ci'],check=True)
        native_assets = {}
        if sys.platform == 'darwin':
            from build_notifications import compile_binary
            binary = compile_binary(destination, destination / 'native-build/FamilyNotifications')
            native_assets[binary.relative_to(destination).as_posix()] = hashlib.sha256(binary.read_bytes()).hexdigest()
            shutil.rmtree(binary.parent / 'module-cache')
        check_out=Path(workspace)/'local/work'/name
        subprocess.run([str(python),str(destination/'scripts/verify.py'),'--browser','--output',str(check_out)],cwd=destination,check=True)
        verification=json.loads((check_out/'passed.json').read_text())
        if verification.get('empty_install') is not True or verification.get('scenario')!='all':
            raise DomainError('发布缺少空白安装或完整回归证据')
        verify_ai_target(destination)
        if source_manifest(ROOT)!=sources:raise DomainError('准备期间开发源码发生变化，请重新准备发布')
        if release_source(ROOT)!=provenance:raise DomainError('准备期间 master 发布来源变化，请重新准备发布')
        assets={p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (destination/'frontend/dist').rglob('*') if p.is_file()}
        atomic_json(destination/'release.json',{'release':name,'version':VERSION,'schema':SCHEMA,'supported_schemas':[SCHEMA],
                    'source_id':source_id,'commit':commit,'provenance':provenance,'built_at':now(),'source_files':sources,'assets':assets,'native_assets':native_assets,
                    'checks':{'unit':True,'frontend':True,'notes':True,'source':True,'browser':True,'ai_client':True,
                              'empty_install':True,'deployment_guard':True}})
        shutil.rmtree(destination/'frontend/node_modules')
        (destination/'frontend/tsconfig.tsbuildinfo').unlink(missing_ok=True)
        verify_release(destination)
        return {'release':name,'path':str(destination),'source_id':source_id,'commit':commit}
    except Exception:
        atomic_json(destination/'prepare-failed.json',{'failed_at':now(),'message':'准备失败；未切换正式版本，查看本地输出。'})
        raise


def unpack_rehearsal(archive, directory):
    """Unpack only a freshly generated, checksummed DB + referenced note snapshot."""
    from backend.notes import validate_path
    directory=Path(directory);directory.mkdir(mode=0o700)
    with zipfile.ZipFile(archive) as saved:
        manifest=json.loads(saved.read('manifest.json'))
        payload=saved.read('portfolio.sqlite')
        if hashlib.sha256(payload).hexdigest()!=manifest['sha256']:
            raise DomainError('演练备份数据库校验失败')
        (directory/'portfolio.sqlite').write_bytes(payload)
        for name,checksum in manifest['note_files'].items():
            validate_path(name)
            data=saved.read(name)
            if hashlib.sha256(data).hexdigest()!=checksum:raise DomainError('演练备份笔记校验失败')
            target=directory/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)


def rehearse_deployment(destination, values, archive, baseline, *, rollback=False):
    # Private copies are never test fixtures, source files or published artifacts.
    with tempfile.TemporaryDirectory(prefix='deployment-rehearsal-',dir=values['runtime_dir']) as temp:
        data=Path(temp)/'data'
        if archive:unpack_rehearsal(archive, data)
        else:data.mkdir()
        claim_environment(data,values['environment'])
        subprocess.run([str(destination/'.venv/bin/python'),str(destination/'scripts/maintenance.py'),
                        'inspect' if rollback else 'migrate','--data-dir',str(data),'--environment',values['environment']],
                       capture_output=True,text=True,check=True)
        return verify_ledger(baseline,capture_ledger(data,baseline)) if baseline else require_empty(data)


def apply(workspace,name,*,rollback=False):
    destination=release_path(workspace,name);manifest=verify_release(destination)
    if manifest.get('checks',{}).get('deployment_guard') is not True:
        raise DomainError('目标版本不支持受保护的安装/回退；请先准备含数据核验的版本，不能跳过保护')
    config,values=get_config(workspace,'production')
    active=Path(workspace)/'runtime/current'
    previous=active.resolve() if active.is_symlink() else None
    before_schema=schema_version(values['data_dir']/'portfolio.sqlite')
    if rollback and before_schema not in manifest['supported_schemas']:
        raise DomainError('旧代码不支持当前数据库，禁止直接回退；请准备数据回迁方案')
    from maintenance import snapshot
    guard=values['runtime_dir']/'deployment-guard.json'
    label=now().replace(':','-')+'-'+uuid.uuid4().hex[:8]
    receipt=values['runtime_dir']/('deployment-'+label+'.json')
    with LedgerProcessLock(values['runtime_dir']):
        if guard.exists():raise DomainError('存在未完成或失败的部署，请先核对阻断记录；不会自动继续或覆盖账本')
        atomic_json(guard,{'release':name,'previous':previous.name if previous else None,'status':'checking','receipt':str(receipt)})
        evidence={'release':name,'previous':previous.name if previous else None,'at':now(),'status':'checking'}
        try:
            stop(workspace,'production')
            with LedgerProcessLock(values['data_dir']):
                if (values['data_dir']/'portfolio.sqlite').exists():
                    baseline=capture_ledger(values['data_dir'])
                    evidence['before_backup']=snapshot(values['data_dir'],values['data_dir']/'backups'/('protected-before-deployment-'+label+'.zip'))
                else:
                    if (values['data_dir']/'notes').exists() and any((values['data_dir']/'notes').rglob('*')):
                        raise DomainError('缺少数据库但存在笔记；禁止作为空白安装继续')
                    baseline=None
            evidence['before_fingerprint']=baseline
            atomic_json(receipt,evidence)
            evidence['rehearsal']=rehearse_deployment(destination,values,
                evidence.get('before_backup',{}).get('backup'),baseline,rollback=rollback)
            atomic_json(receipt,evidence)
            if not rollback:
                subprocess.run([str(destination/'.venv/bin/python'),str(destination/'scripts/maintenance.py'),
                    'migrate','--data-dir',str(values['data_dir']),'--environment','production'],check=True)
            with LedgerProcessLock(values['data_dir']):
                migrated=capture_ledger(values['data_dir'],baseline)
                evidence['migration']=verify_ledger(baseline,migrated) if baseline else require_empty(values['data_dir'])
            temporary=active.with_name('current.pending')
            if temporary.exists() or temporary.is_symlink():raise DomainError('存在未完成的版本切换，请先核对')
            temporary.symlink_to(Path('releases')/name);os.replace(temporary,active)
            found=start(workspace,'production',no_sync=True,read_only=True)
            if found.get('version')!=manifest['version'] or found.get('schema')!=manifest['schema'] or found.get('read_only') is not True:
                raise DomainError('新版本健康检查不符合发布清单')
            if manifest.get('checks', {}).get('ai_client') is True:
                verify_ai_target(destination, values['data_dir'])
            stop(workspace,'production')
            with LedgerProcessLock(values['data_dir']):
                accepted=capture_ledger(values['data_dir'],migrated)
                evidence['startup']=verify_ledger(migrated,accepted)
                evidence['after_fingerprint']=accepted
                if baseline is None:require_empty(values['data_dir'])
                evidence['after_backup']=snapshot(values['data_dir'],values['data_dir']/'backups'/('protected-after-deployment-'+label+'.zip'))
            evidence['status']='verified';atomic_json(receipt,evidence)
            guard.unlink()
            found=start(workspace,'production')
            if found.get('read_only',False) or found.get('version')!=manifest['version']:
                raise DomainError('正式服务未进入目标版本的正常运行状态')
            atomic_json(Path(workspace)/'runtime/last-deployment.json',{'release':name,'previous':previous.name if previous else None,'at':now()})
            result={'release':name,'health':found,'data_verification':str(receipt)}
            if sys.platform=='darwin':
                launcher=subprocess.run([str(destination/'.venv/bin/python'),str(destination/'scripts/manage.py'),
                    '--workspace',str(workspace),'launchers','--environment','production'],capture_output=True,text=True)
                if launcher.returncode==0:result['launchers']=json.loads(launcher.stdout)
                else:result['launchers_warning']='正式服务已启动，但应用入口更新失败：'+launcher.stderr.strip()
            return result
        except Exception:
            # A compatible schema alone does not prove preserved data. Never
            # restart an old version or restore a stale backup automatically.
            evidence['status']='blocked';atomic_json(receipt,evidence)
            atomic_json(guard,{'release':name,'status':'blocked','receipt':str(receipt)})
            try:
                stop(workspace,'production')
            except Exception:
                atomic_json(guard,{'release':name,'status':'blocked','receipt':str(receipt),'stop_failed':True})
            raise


def refresh_dev(workspace):
    archive=Path(backup(workspace,'production')['backup'])
    config,values=get_config(workspace,'dev')
    stop(workspace,'dev')
    if (values['data_dir']/'portfolio.sqlite').exists():backup(workspace,'dev')
    subprocess.run([sys.executable,str(ROOT/'scripts/maintenance.py'),'restore','--archive',str(archive),
                    '--data-dir',str(values['data_dir']),'--environment','dev'],check=True)
    return {'refreshed':'dev','source_backup':str(archive)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,default=workspace_for(ROOT))
    sub=parser.add_subparsers(dest='action',required=True)
    for action in ('init','start','stop','status','backup'):
        p=sub.add_parser(action);p.add_argument('--environment',choices=LABELS,default='dev')
        if action=='start':p.add_argument('--open',action='store_true');p.add_argument('--no-sync',action='store_true')
    sub.add_parser('prepare')
    launchers=sub.add_parser('launchers',help='生成带图标的 macOS 应用入口')
    launchers.add_argument('--environment',choices=LABELS,default='dev')
    launchers.add_argument('--output',type=Path)
    for action in ('apply','rollback','check-release'):
        sub.add_parser(action).add_argument('release')
    sub.add_parser('refresh-dev')
    plan=sub.add_parser('storage-plan',help='只生成可清理文件的校验清单，不删除')
    plan.add_argument('--output',type=Path,required=True)
    cleanup=sub.add_parser('storage-cleanup',help='复核并执行已审阅的清单')
    cleanup.add_argument('--plan',type=Path,required=True)
    args=parser.parse_args();workspace=args.workspace.resolve()
    if args.action=='init':
        path,values=init_environment(workspace,args.environment);result={'config':str(path),'data_dir':str(values['data_dir'])}
    elif args.action=='start':result=start(workspace,args.environment,no_sync=args.no_sync,open_browser=args.open)
    elif args.action=='stop':stop(workspace,args.environment);result={'stopped':args.environment}
    elif args.action=='status':
        _,values=get_config(workspace,args.environment);result={'environment':args.environment,'health':health(values),
            'data_dir':str(values['data_dir']),'schema':schema_version(values['data_dir']/'portfolio.sqlite')}
    elif args.action=='backup':result=backup(workspace,args.environment)
    elif args.action=='prepare':result=prepare(workspace)
    elif args.action=='storage-plan':
        from storage_cleanup import make_plan
        result=make_plan(workspace)
        if args.output.exists():raise DomainError('清单已存在，拒绝覆盖')
        atomic_json(args.output,result)
        result={'plan':str(args.output),'candidates':len(result['candidates']),'bytes':sum(r['bytes'] for r in result['candidates']),'kept_releases':result['kept_releases']}
    elif args.action=='storage-cleanup':
        from storage_cleanup import apply_plan
        result=apply_plan(workspace,json.loads(args.plan.read_text()))
    elif args.action=='launchers':
        from macos_launchers import build_launchers
        result=build_launchers(workspace,args.environment,output=args.output)
    elif args.action in ('apply','rollback'):result=apply(workspace,args.release,rollback=args.action=='rollback')
    elif args.action=='check-release':result=verify_release(release_path(workspace,args.release))
    else:result=refresh_dev(workspace)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except (DomainError, subprocess.CalledProcessError) as exc:raise SystemExit(str(exc)) from None
