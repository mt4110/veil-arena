#!/usr/bin/env python3
"""Fixed F0 AppArmor exec smoke; policy ownership never starts at syntax check."""
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time

from k3s_guest import Failure

PROFILE = 'veil_keeper_m0_exec_check_20261016'
MODES = ['baseline_before', 'negative_control', 'confined', 'baseline_after']


def checker_valid(record, mode):
    if not isinstance(record, dict) or not isinstance(record.get('report'), dict):
        return False
    expected = 'baseline' if mode.startswith('baseline') else 'deny-true'
    report = record.get('report', {})
    negative = mode == 'negative_control'
    confined = mode == 'confined'
    if (record.get('returncode') != (1 if negative else 0)
            or record.get('uid') != 501 or record.get('gid') != 1000
            or report.get('schema_version') != 1 or report.get('expectation') != expected
            or report.get('current_profile') != (PROFILE+' (enforce)' if confined else 'unconfined')
            or report.get('expected_profile_matches') is not (not negative)
            or report.get('passed') is not (not negative)):
        return False
    cases = report.get('cases', [])
    if not isinstance(cases,list) or len(cases) != 2:
        return False
    for row, command, code in zip(cases, ['/usr/bin/true','/usr/bin/false'], [0,1], strict=True):
        if not isinstance(row,dict) or row.get('command') != command:
            return False
        if confined and code == 0:
            if row.get('kind') != 'exec_error' or row.get('errno') not in {errno.EACCES,errno.EPERM} or row.get('passed') is not True:
                return False
        elif (row.get('kind') != 'exited' or row.get('returncode') != code
              or row.get('passed') is not (not negative or code == 1)):
            return False
    return True


def target(lines):
    return [line for line in lines if line.rsplit(' (',1)[0] == PROFILE
            or line.rsplit(' (',1)[0].startswith(PROFILE+'//')]


def inventory():
    return sorted(Path('/sys/kernel/security/apparmor/profiles').read_text().splitlines())


def fixed_inputs(inputs, root=Path('/inputs')):
    for name, expected in inputs['keeper']['input_sha256'].items():
        path = root / Path(name).name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024**2:
            raise ValueError('fixed Keeper input is not a bounded regular file')
        if path.stat().st_mode & 0o222 or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('fixed Keeper input hash or read-only mode differs')


def command(args, deadline, stage, out, **identity):
    remaining = min(20, deadline-time.monotonic())
    if remaining <= 0:
        raise Failure(stage, 'workflow deadline before Keeper command', 4)
    started = time.monotonic()
    with subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True, **identity) as child:
        try:
            stdout, stderr = child.communicate(timeout=remaining)
        except BaseException as error:
            # Exactly this owned process group, including the checker's fixed ELF children.
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.communicate(timeout=5)
            if isinstance(error, subprocess.TimeoutExpired):
                (out/'keeper-command-timeout.json').write_text(json.dumps({
                    'stage':stage,'argv':args,'budget_seconds':remaining,
                    'elapsed_seconds':time.monotonic()-started,'owned_child_reaped':child.poll() is not None},indent=2)+'\n')
                raise Failure(stage,'Keeper command timeout',4 if time.monotonic()>=deadline else 3) from None
            raise
        if len(stdout)+len(stderr) > 1024**2:
            raise Failure(stage,'Keeper command evidence too large')
        if time.monotonic() >= deadline:
            raise Failure(stage,'Keeper command returned after workflow deadline',4)
        return subprocess.CompletedProcess(args,child.returncode,stdout,stderr)


def trial(inputs, out, deadline, root=Path('/inputs')):
    result = {'schema_version':1,'profile':PROFILE,'input_sha256':inputs['keeper']['input_sha256'],
              'uid':501,'gid':1000,'status':'environment_error','exit_code':3,'steps':{},'actions':[]}
    owned=False
    before=None
    stage='keeper_preflight'
    steps=result['steps']
    def save():
        (out/'keeper-result.json').write_text(json.dumps(result,indent=2)+'\n')
    def parser(action):
        nonlocal owned
        fixed_inputs(inputs,root)
        args=[inputs['apparmor_parser'],'--skip-cache',action,str(root/'m0-exec-check.profile')]
        cp=command(args,deadline,stage,out)
        if action=='--add' and cp.returncode==0:
            owned=True
            result['ownership_started_after_add']=True
        result['actions'].append({'action':action,'returncode':cp.returncode,'stderr':cp.stderr.decode(errors='replace')[:8192]})
        save()
        if cp.returncode:
            raise Failure(stage,'fixed AppArmor parser action failed: '+action)
    def check(mode):
        fixed_inputs(inputs,root)
        expectation='baseline' if mode.startswith('baseline') else 'deny-true'
        args=['/usr/bin/python3',str(root/'m0_exec_check.py'),'--expect',expectation]
        if mode=='confined':
            args=['/usr/bin/aa-exec','-p',PROFILE,'--',*args]
        cp=command(args,deadline,stage,out,user=501,group=1000,extra_groups=[])
        try:
            report=json.loads(cp.stdout)
        except ValueError:
            raise Failure(stage,'checker JSON unavailable') from None
        row={'returncode':cp.returncode,'uid':501,'gid':1000,'report':report,'stderr':cp.stderr.decode(errors='replace')[:8192]}
        steps[mode]=row
        save()
        if not checker_valid(row,mode):
            raise Failure(stage,'fixed checker expectation differs: '+mode,1)
    try:
        save()
        if os.geteuid()!=0 or os.uname().sysname!='Linux':
            raise Failure(stage,'dedicated Linux guest root required')
        fixed_inputs(inputs,root)
        if (Path('/sys/module/apparmor/parameters/enabled').read_text().strip()!='Y'
                or 'apparmor' not in Path('/sys/kernel/security/lsm').read_text().strip().split(',')):
            raise Failure(stage,'AppArmor LSM unavailable')
        before=inventory();result['inventory_before']=before
        if target(before):
            raise Failure(stage,'target profile collision; no add/remove attempted')
        stage='baseline_before';check(stage)
        stage='negative_control';check(stage)
        stage='syntax';parser('--skip-kernel-load')
        syntax_inventory=inventory();result['inventory_after_syntax']=syntax_inventory
        if syntax_inventory!=before or target(syntax_inventory):
            raise Failure(stage,'syntax changed the profile inventory')
        stage='add';parser('--add')
        loaded=inventory();result['inventory_loaded']=loaded
        if target(loaded)!=[PROFILE+' (enforce)'] or [x for x in loaded if x not in target(loaded)]!=before:
            raise Failure(stage,'loaded enforce profile or other inventory differs')
        stage='confined';check(stage)
    except Failure as error:
        result.update(stage=error.stage,detail=error.detail,exit_code=error.code,
                      status={1:'failed',3:'environment_error',4:'timeout',130:'cancelled'}[error.code])
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        result.update(stage=stage,detail=f'{type(error).__name__}: {error}')
    finally:
        original={k:result.get(k) for k in ['stage','detail','exit_code','status']}
        try:
            if owned:
                stage='remove';parser('--remove')
                result['owned_profile_removed']=True
            after=inventory();result['inventory_after']=after
            result['inventory_restored']=before is not None and after==before and (not owned or not target(after))
            if before is not None and not result['inventory_restored']:
                raise Failure('cleanup','profile inventory not restored')
        except (Failure,OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
            result['original_failure']=original
            result.update(status='environment_error',exit_code=3,stage='cleanup',detail=str(error))
        else:
            if owned and result.get('owned_profile_removed') and 'confined' in steps:
                try:
                    stage='baseline_after';check(stage)
                except Failure as error:
                    result['original_failure']=original
                    result.update(stage=error.stage,detail=error.detail,exit_code=error.code,
                                  status={1:'failed',3:'environment_error',4:'timeout',130:'cancelled'}[error.code])
                except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
                    result.update(status='environment_error',exit_code=3,stage=stage,detail=str(error))
            if (all(m in steps and checker_valid(steps[m],m) for m in MODES)
                    and result.get('owned_profile_removed') is True and result['inventory_restored'] is True
                    and not original.get('detail') and not result.get('detail')):
                result.update(status='passed',exit_code=0)
        save()
    return result
