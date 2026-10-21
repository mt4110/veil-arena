#!/usr/bin/env python3
"""F3 verifier and the existing single-VM owned lifecycle entry."""
import json
from pathlib import Path
import re
import sys

from k3s_bundle import bounded, check
from k3s_host import workflow, recovered
from k3s_guest import ready
from keeper_trial import PROFILE, MODES, checker_valid, target


def verify_run(out, inputs):
    stop=json.loads(bounded(out/'vm-stop.json'))
    if (not isinstance(stop,dict) or stop.get('status')!='passed' or not recovered(stop) or stop.get('lifecycle_exit_code')!=0
            or stop.get('evidence_synced_before_stop') is not True):
        raise ValueError('F3 VM lifecycle/evidence failed')
    row=json.loads(bounded(out/'result.json'))
    if (not isinstance(row,dict) or row.get('schema_version')!=1 or row.get('scenario')!='k3s-f3' or row.get('inputs')!=inputs
            or not re.fullmatch(r'[0-9a-f-]{36}',row.get('boot_id',''))):
        raise ValueError('F3 guest identity differs')
    if row.get('status')!='passed' or row.get('exit_code')!=0:
        code=row.get('exit_code')
        if type(code) is not int or code not in {1,3,4,130}:raise ValueError('invalid F3 failure exit')
        return code,row
    if (bounded(out/'service-result.txt').strip()!=b'success'
            or row.get('checks')!=[{'stage':'node_ready','status':'passed','count':1},{'stage':'keeper_exec','status':'passed'}]):
        raise ValueError('F3 completion stages missing')
    nodes=json.loads(bounded(out/'cluster-nodes.json'))
    if (not isinstance(nodes,list) or len(nodes)!=1 or not ready(nodes)
            or nodes[0]['metadata'].get('name')!='arena-k3s-server'
            or nodes[0]['status']['nodeInfo'].get('kubeletVersion')!='v'+inputs['versions']['k3s']
            or nodes[0]['status']['nodeInfo'].get('containerRuntimeVersion')!='containerd://'+inputs['versions']['containerd']):
        raise ValueError('F3 Ready Node proof differs')
    report=json.loads(bounded(out/'keeper-result.json'))
    if not isinstance(report,dict):raise ValueError('invalid Keeper report')
    before=report.get('inventory_before')
    if (report.get('schema_version')!=1 or report.get('status')!='passed' or report.get('exit_code')!=0
            or report.get('profile')!=PROFILE or report.get('input_sha256')!=inputs['keeper']['input_sha256']
            or report.get('uid')!=501 or report.get('gid')!=1000
            or not isinstance(before,list) or any(not isinstance(x,str) for x in before) or target(before)
            or report.get('inventory_after_syntax')!=before or report.get('inventory_after')!=before
            or report.get('inventory_loaded')!=sorted(before+[PROFILE+' (enforce)'])
            or report.get('inventory_restored') is not True or report.get('owned_profile_removed') is not True
            or report.get('ownership_started_after_add') is not True):
        raise ValueError('F3 profile lifecycle proof differs')
    actions=report.get('actions',[])
    if (not isinstance(actions,list) or any(not isinstance(x,dict) for x in actions)
            or not isinstance(report.get('steps'),dict)
            or [x.get('action') for x in actions]!=['--skip-kernel-load','--add','--remove']
            or any(x.get('returncode')!=0 for x in actions)
            or any(not checker_valid(report.get('steps',{}).get(m,{}),m) for m in MODES)):
        raise ValueError('F3 checker or parser proof differs')
    return 0,row


if __name__=='__main__':
    try:
        if len(sys.argv)!=5:raise ValueError('usage: arena-keeper BUNDLE daily|acceptance NEW_OUTPUT')
        bundle=Path(sys.argv[2]).absolute()
        if check(bundle)[0].get('scenario')!='k3s-f3':raise ValueError('F3 bundle required')
        sys.exit(workflow(int(sys.argv[1]),bundle,sys.argv[3],Path(sys.argv[4]).absolute(),
                          scenario='k3s-f3',verifier=verify_run))
    except (OSError,ValueError,KeyError,TypeError) as error:
        print('F3 preflight: '+str(error),file=sys.stderr);sys.exit(3)
