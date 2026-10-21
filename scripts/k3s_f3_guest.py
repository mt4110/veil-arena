#!/usr/bin/env python3
"""F3: a Ready K3s VM and one fixed Keeper exec smoke trial."""
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import time

from k3s_guest import Failure, ready, request_timeout, executable_entry
from keeper_trial import command, trial


def run(inputs, out=Path('/out')):
    deadline=time.monotonic()+600
    stage='preflight'
    result={'schema_version':1,'scenario':'k3s-f3','inputs':inputs,
            'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'status':'environment_error','exit_code':3,'checks':[]}
    def save():
        (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    def cancelled(signum,frame):
        raise Failure(stage,'owned guest trial cancelled',130)
    handlers={s:signal.signal(s,cancelled) for s in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]}
    try:
        save()
        if inputs['scenario']!='k3s-f3' or os.uname().release!=inputs['versions']['kernel']:
            raise Failure(stage,'guest scenario/kernel differs')
        for name,row in inputs['compat_elf'].items():
            p=Path(row['destination']);data=p.read_bytes()
            if p.is_symlink() or not data.startswith(b'\x7fELF') or hashlib.sha256(data).hexdigest()!=row['sha256'] or p.stat().st_mode & 0o777!=0o555:
                raise Failure(stage,'compatibility ELF differs: '+name)
        for name,key in [('python3','compat_python'),('aa-exec','compat_aa_exec')]:
            if not executable_entry(Path('/usr/bin')/name,Path(inputs[key])):
                raise Failure(stage,'compatibility executable differs: '+name)
        if Path('/proc/self/attr/current').read_text().strip()!='unconfined':
            raise Failure(stage,'guest manager is not unconfined')
        stage='node_ready';end=min(deadline,time.monotonic()+300)
        cp=command(['systemctl','start','--no-block','k3s'],end,stage,out)
        if cp.returncode:raise Failure(stage,'K3s start failed')
        while time.monotonic()<end:
            cp=command(['k3s','kubectl','--request-timeout='+request_timeout(min(end,time.monotonic()+20),deadline,time.monotonic()),'get','nodes','-o','json'],end,stage,out)
            if cp.returncode==0:
                nodes=json.loads(cp.stdout)['items']
                (out/'cluster-nodes.json').write_text(json.dumps(nodes,indent=2)+'\n')
                if len(nodes)==1 and ready(nodes):
                    info=nodes[0]['status']['nodeInfo']
                    if (nodes[0]['metadata']['name']!='arena-k3s-server'
                            or info.get('kubeletVersion')!='v'+inputs['versions']['k3s']
                            or info.get('containerRuntimeVersion')!='containerd://'+inputs['versions']['containerd']):
                        raise Failure(stage,'fixed Node version/identity differs',1)
                    break
            time.sleep(min(.25,max(0,end-time.monotonic())))
        else:raise Failure(stage,'Node not Ready by original 300s deadline')
        result['checks'].append({'stage':stage,'status':'passed','count':1});save()
        stage='keeper_exec'
        keeper=trial(inputs,out,deadline)
        if keeper['status']!='passed':
            raise Failure(keeper.get('stage',stage),keeper.get('detail','Keeper trial failed'),keeper['exit_code'])
        result['checks'].append({'stage':stage,'status':'passed'})
        result.update(status='passed',exit_code=0)
    except Failure as error:
        result.update(stage=error.stage,detail=error.detail,exit_code=error.code,
                      status={1:'failed',3:'environment_error',4:'timeout',130:'cancelled'}[error.code])
    except (OSError,ValueError,KeyError,TypeError) as error:
        result.update(stage=stage,detail=f'{type(error).__name__}: {error}')
    finally:
        for s,h in handlers.items():signal.signal(s,h)
        save()
    return result['exit_code']


if __name__=='__main__':
    sys.exit(run(json.loads(Path(sys.argv[1]).read_text())))
