#!/usr/bin/env python3
"""Reproducible regression: only synthetic, explicitly marked test ledgers."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.environment import LedgerProcessLock, claim_environment, workspace_for
from backend.deployment_guard import require_empty

SCENARIOS = ('empty','acceptance','pagination','profit','holdings','entry','reinvestment','notes','plan_forward','brand','family','settings')


def run(command, **kwargs):
    print('+ '+' '.join(map(str,command)),flush=True)
    subprocess.run(list(map(str,command)),cwd=ROOT,check=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',action='store_true')
    parser.add_argument('--scenario', choices=SCENARIOS, help='Run one browser scenario after unit/build/source verification')
    parser.add_argument('--output',type=Path,default=workspace_for(ROOT)/'local/work/verification')
    parser.add_argument('--port',type=int,default=8876)
    args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    run([sys.executable,'-m','unittest','discover','-s','tests'])
    run(['npm','--prefix','frontend','run','test:notes'])
    run(['npm','--prefix','frontend','run','test:amounts'])
    run(['npm','--prefix','frontend','run','build'])
    with (output/'source-check.json').open('w') as handle:run([sys.executable,'scripts/source_check.py'],stdout=handle)
    if not args.browser:return
    # Each scenario gets a fresh synthetic ledger so large pagination fixtures
    # cannot alter another scenario's FX choices, history or response time.
    for name in ((args.scenario,) if args.scenario else SCENARIOS):
        case_output=output/name;case_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='synthetic-',dir=case_output) as temp:
            base=Path(temp);data=base/'browser-test';runtime=base/'.runtime'
            claim_environment(data,'test')
            env={**os.environ,'PORTFOLIO_TEST_URL':f'http://127.0.0.1:{args.port}',
                 'PORTFOLIO_TEST_DATA_DIR':str(data),'PORTFOLIO_TEST_OUTPUT':str(case_output),
                 'PLAYWRIGHT_MODULE_PATH':str(ROOT/'frontend/node_modules/playwright')}
            command=[sys.executable,str(ROOT/'run.py'),'--environment','test','--data-dir',str(data),
                     '--runtime-dir',str(runtime),'--port',str(args.port),'--no-open','--no-sync']
            if name == 'empty':
                config=base/'config.toml'
                config.write_text(f'environment = "test"\nport = {args.port}\ndata_dir = "browser-test"\nruntime_dir = ".runtime"\nauto_sync = true\n')
                command=[sys.executable,str(ROOT/'run.py'),'--config',str(config),'--no-open']
            with (case_output/'server.log').open('w') as handle:
                process=subprocess.Popen(command,cwd=ROOT,env=env,stdout=handle,stderr=subprocess.STDOUT)
                try:
                    ready=False
                    for _ in range(100):
                        if process.poll() is not None:raise RuntimeError('测试服务启动失败，查看 '+str(case_output/'server.log'))
                        try:
                            with urllib.request.urlopen(env['PORTFOLIO_TEST_URL']+'/api/health',timeout=1) as response:info=json.load(response)
                            if info['data_directory']!=str(data) or info['environment']!='test':raise RuntimeError('测试端口属于其他实例')
                            ready=True;break
                        except (urllib.error.URLError,ConnectionError):time.sleep(.1)
                    if not ready:raise RuntimeError('测试服务启动超时')
                    run(['node',f'scripts/browser_{name}.cjs'],env=env)
                    if name == 'empty':
                        (case_output/'empty-install.json').write_text(json.dumps(require_empty(data),indent=2))
                    if name in ('family', 'reinvestment'): run([sys.executable,'scripts/verify_ai.py'],env=env)
                finally:
                    process.terminate()
                    try:process.wait(timeout=15)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
    (output/'passed.json').write_text(json.dumps({'unit':True,'notes':True,'source':True,'browser':True,
        'empty_install':args.scenario in (None,'empty'),'scenario':args.scenario or 'all'}))


if __name__=='__main__':main()
