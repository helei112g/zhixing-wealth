#!/usr/bin/env python3
"""Fixed-port, environment-aware local launcher. Data locks survive release switches."""
from __future__ import annotations
import argparse
import json
import os
import signal
import socket
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from backend.common import DomainError
from backend.environment import LABELS, PORTS, LedgerProcessLock, claim_environment, config_path, read_config, workspace_for
from backend.migration import SCHEMA, schema_version
from backend.version import APPLICATION, NAME

ROOT = Path(__file__).resolve().parent


def health(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
            value = json.load(response)
            return value if value.get('application') == APPLICATION else None
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(description=NAME + ' · 本机独立环境启动器')
    parser.add_argument('--config', type=Path)
    parser.add_argument('--environment', choices=LABELS)
    parser.add_argument('--port', type=int)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--runtime-dir', type=Path)
    parser.add_argument('--frontend-dir', type=Path)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--no-sync', action='store_true')
    parser.add_argument('--read-only', action='store_true', help='Deployment acceptance: refuse API writes and background tasks')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    workspace = workspace_for(ROOT)
    environment = args.environment or 'dev'
    config = args.config or config_path(workspace, environment)
    if config.exists():
        values = read_config(config)
        if args.environment and args.environment != values['environment']:
            raise DomainError('命令行环境与配置不一致')
        if args.config and any((args.data_dir, args.port, args.runtime_dir)):
            raise DomainError('使用配置文件时不能覆盖其中的端口和数据目录')
        environment = values['environment']
    elif args.config:
        raise DomainError('配置文件不存在')
    else:
        base = workspace / ('runtime' if environment == 'production' else f'local/{environment}')
        values = {'data_dir': base / ('browser-test' if environment == 'test' else 'data'),
                  'runtime_dir': base / '.runtime', 'port': PORTS[environment],
                  'auto_sync': environment == 'production'}
    directory = (args.data_dir or values['data_dir']).resolve()
    runtime = (args.runtime_dir or values['runtime_dir']).resolve()
    port = args.port or values['port']
    if not 1024 <= port <= 65535:
        raise DomainError('端口不在允许范围内')
    runtime.mkdir(parents=True, exist_ok=True)
    record = runtime / 'server.json'
    if args.stop:
        if not record.exists():
            print('未发现运行记录。'); return
        saved = json.loads(record.read_text())
        current = health(saved['port'])
        if not current or current.get('instance') != saved.get('instance') or current.get('data_directory') != str(directory):
            raise DomainError('运行记录与目标实例不匹配，未停止任何进程')
        os.kill(saved['pid'], signal.SIGTERM)
        for _ in range(150):
            if not health(saved['port']):
                print(NAME + '已停止。'); return
            time.sleep(.1)
        raise DomainError('实例仍在退出，未继续后续操作')
    claim_environment(directory, environment)
    if environment == 'production' and (runtime/'deployment-guard.json').exists() and not args.read_only:
        raise DomainError('正式部署尚未通过数据核验；保持只读或停机，禁止跳过阻断记录')
    current = health(port)
    if current and current.get('data_directory') == str(directory) and current.get('environment') == environment:
        print('应用已在运行：' + f'http://127.0.0.1:{port}')
        if not args.no_open: webbrowser.open(f'http://127.0.0.1:{port}')
        return
    if environment == 'production':
        if not (ROOT / 'release.json').is_file():
            raise DomainError('正式环境只能从已准备的发布版本启动')
        if schema_version(directory / 'portfolio.sqlite') != SCHEMA:
            raise DomainError('正式库需先通过发布工具完成备份及迁移')
    frontend = (args.frontend_dir or ROOT / 'frontend/dist').resolve()
    if not (frontend / 'index.html').is_file():
        raise DomainError('前端尚未构建，请先运行 npm --prefix frontend run build')
    with LedgerProcessLock(directory):
        sock = socket.socket()
        # Match the HTTP server: a recently stopped listener may leave TIME_WAIT
        # connections, which must not block an immediate verified restart.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
        except OSError:
            raise DomainError(f'端口 {port} 已占用；请停止对应实例，不会自动切换端口') from None
        finally:
            sock.close()
        from backend.app import create_app
        import uvicorn
        if environment == 'production':
            from backend.deployment_guard import capture, verify
            before_start = capture(directory)
        app = create_app(directory, environment=environment,
                         auto_sync=values.get('auto_sync', False) and not args.no_sync,
                         frontend_directory=frontend, read_only=args.read_only,
                         deployment_guard_path=runtime/'deployment-guard.json' if environment == 'production' else None)
        if environment == 'production':
            verify(before_start, capture(directory, before_start))
        info = {'pid': os.getpid(), 'port': port, 'instance': app.state.instance,
                'root': str(ROOT), 'data_directory': str(directory), 'environment': environment}
        temp = record.with_suffix('.pending')
        temp.write_text(json.dumps(info)); os.replace(temp, record)
        url = f'http://127.0.0.1:{port}'
        print(f'{NAME} · {LABELS[environment]}：{url}\n数据目录：{directory}', flush=True)
        if not args.no_open:
            def open_later():
                for _ in range(60):
                    if health(port): webbrowser.open(url); return
                    time.sleep(.2)
            threading.Thread(target=open_later, daemon=True).start()
        try:
            uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning', access_log=False)
        finally:
            if record.exists() and json.loads(record.read_text()).get('instance') == info['instance']:
                record.unlink()


if __name__ == '__main__':
    try: main()
    except DomainError as exc: raise SystemExit(str(exc)) from None
