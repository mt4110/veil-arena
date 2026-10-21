"""F0 input integrity, actual checker semantics, profile ownership and retained evidence."""
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import keeper_inputs
import keeper_trial as trial
import k3s_f3_host as host
import k3s_bundle as bundle


def checker(mode):
    confined=mode=='confined';negative=mode=='negative_control'
    cases=[{'command':'/usr/bin/true','kind':'exec_error','errno':errno.EACCES,'passed':True} if confined else
           {'command':'/usr/bin/true','kind':'exited','returncode':0,'passed':not negative},
           {'command':'/usr/bin/false','kind':'exited','returncode':1,'passed':True}]
    return {'returncode':1 if negative else 0,'uid':501,'gid':1000,'report':{
        'schema_version':1,'expectation':'baseline' if mode.startswith('baseline') else 'deny-true',
        'current_profile':trial.PROFILE+' (enforce)' if confined else 'unconfined',
        'expected_profile_matches':not negative,'cases':cases,'passed':not negative}}


class FixedInputs(unittest.TestCase):
    def test_contract_constants_match(self):
        c=json.loads((ROOT/'docs/contracts/f0-inputs.json').read_text())['keeper']
        self.assertEqual(c['input_sha256'],bundle.KEEPER_INPUTS)
        self.assertEqual(c['profile_name'],trial.PROFILE)
        self.assertEqual(c['apparmor_lifecycle']['command_timeout_seconds'],20)

    def test_snapshot_validates_all_inputs_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);src=root/'owner';src.mkdir();(src/'tools').mkdir();p=src/'tools/check.py';p.write_bytes(b'fixed')
            contract={'keeper':{'input_sha256':{'tools/check.py':hashlib.sha256(b'fixed').hexdigest()}}}
            dest=root/'copy';keeper_inputs.snapshot(src,dest,contract)
            self.assertEqual((dest/'check.py').read_bytes(),b'fixed')
            self.assertFalse((dest/'check.py').stat().st_mode & 0o222)
            with self.assertRaises(ValueError):keeper_inputs.snapshot(src,dest,contract)
            p.write_bytes(b'changed')
            with self.assertRaises(ValueError):keeper_inputs.snapshot(src,root/'bad',contract)
            self.assertFalse((root/'bad').exists())
            link=src/'tools/link.py';link.symlink_to(p)
            contract['keeper']['input_sha256']={'tools/link.py':hashlib.sha256(b'changed').hexdigest()}
            with self.assertRaises(OSError):keeper_inputs.snapshot(src,root/'linked',contract)

    def test_runtime_hash_and_writable_input_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'check.py';p.write_bytes(b'fixed')
            inputs={'keeper':{'input_sha256':{'tools/check.py':hashlib.sha256(b'fixed').hexdigest()}}}
            with self.assertRaises(ValueError):trial.fixed_inputs(inputs,root)
            p.chmod(0o444);trial.fixed_inputs(inputs,root)
            with patch.object(Path,'read_bytes',return_value=b'changed'):
                with self.assertRaises(ValueError):trial.fixed_inputs(inputs,root)


class CheckerSemantics(unittest.TestCase):
    def test_all_modes_and_missing_denial_are_distinct(self):
        for mode in trial.MODES:
            self.assertTrue(trial.checker_valid(checker(mode),mode))
        for mode in trial.MODES:
            with self.subTest(mode=mode):
                row=checker(mode);row['report']['cases'][0]['kind']='timeout'
                self.assertFalse(trial.checker_valid(row,mode))
        row=checker('confined');row['report']['cases'][0]={'command':'/usr/bin/true','kind':'exited','returncode':0,'passed':False}
        self.assertFalse(trial.checker_valid(row,'confined'))
        for report in [None,[],{},'bad']:
            self.assertFalse(trial.checker_valid({'report':report},'confined'))

    def test_wrong_errno_label_uid_or_control_exit_never_passes(self):
        for mutation in [lambda r:r['report']['cases'][0].update(errno=errno.ENOENT),
                         lambda r:r['report'].update(current_profile='unconfined'),
                         lambda r:r.update(uid=0),lambda r:r['report']['cases'][1].update(returncode=0)]:
            row=checker('confined');mutation(row);self.assertFalse(trial.checker_valid(row,'confined'))


class Ownership(unittest.TestCase):
    def exercise(self, problem=None):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);lines=['other (enforce)'];actions=[];calls=[]
            if problem=='collision':lines.append(trial.PROFILE+' (enforce)');lines.sort()
            def execute(argv,deadline,stage,out,**kwargs):
                calls.append((argv,kwargs))
                if argv[0]=='fixed-parser':
                    action=argv[2];actions.append(action)
                    if action=='--add':
                        if problem=='add':return subprocess.CompletedProcess(argv,1,b'',b'add failed')
                        lines.append(trial.PROFILE+' (enforce)');lines.sort()
                    if action=='--skip-kernel-load' and problem=='syntax':lines.append('unexpected (enforce)')
                    if action=='--remove':
                        if problem=='remove':return subprocess.CompletedProcess(argv,1,b'',b'remove failed')
                        lines.remove(trial.PROFILE+' (enforce)')
                    return subprocess.CompletedProcess(argv,0,b'',b'')
                mode='confined' if argv[0]=='/usr/bin/aa-exec' else ('baseline_after' if '--remove' in actions else 'baseline_before')
                if argv[-1]=='deny-true' and mode!='confined':mode='negative_control'
                row=checker(mode)
                if problem==mode:row['report']['cases'][0]['kind']='timeout'
                return subprocess.CompletedProcess(argv,row['returncode'],json.dumps(row['report']).encode(),b'')
            original=Path.read_text
            def read(path,*args,**kwargs):
                if str(path).endswith('/parameters/enabled'):return 'Y'
                if str(path)=='/sys/kernel/security/lsm':return 'lockdown,capability,apparmor'
                return original(path,*args,**kwargs)
            write=Path.write_text
            failed_save=[False]
            def save(path,data,*args,**kwargs):
                if (problem=='save_after_add' and path.name=='keeper-result.json'
                        and json.loads(data).get('actions') and json.loads(data)['actions'][-1]['action']=='--add'
                        and not failed_save[0]):
                    failed_save[0]=True
                    raise OSError('evidence save failed after successful add')
                return write(path,data,*args,**kwargs)
            with patch.object(Path,'write_text',save),patch.object(trial,'fixed_inputs'),patch.object(trial,'inventory',side_effect=lambda:list(lines)), \
                    patch.object(trial,'command',side_effect=execute),patch.object(trial.os,'geteuid',return_value=0), \
                    patch.object(trial.os,'uname',return_value=Mock(sysname='Linux')),patch.object(Path,'read_text',read):
                result=trial.trial({'apparmor_parser':'fixed-parser','keeper':{'input_sha256':bundle.KEEPER_INPUTS}},out,time.monotonic()+600)
            for argv,identity in calls:
                if argv[0]!='fixed-parser':self.assertEqual(identity,{'user':501,'group':1000,'extra_groups':[]})
            self.assertEqual(result,json.loads((out/'keeper-result.json').read_text()))
            return result,actions

    def test_normal_denial_recovery_and_negative_control(self):
        result,actions=self.exercise()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(actions,['--skip-kernel-load','--add','--remove'])
        self.assertEqual(result['inventory_before'],result['inventory_after'])
        self.assertEqual(result['steps']['negative_control']['returncode'],1)

    def test_collision_never_adds_or_removes_foreign_profile(self):
        result,actions=self.exercise('collision');self.assertEqual(actions,[])
        self.assertEqual(result['exit_code'],3);self.assertIn('collision',result['detail'])

    def test_add_and_syntax_failure_never_claim_ownership_or_remove(self):
        for problem in ['add','syntax']:
            with self.subTest(problem=problem):
                result,actions=self.exercise(problem)
                self.assertNotIn('--remove',actions)
                self.assertNotIn('ownership_started_after_add',result)
                self.assertEqual(result['exit_code'],3)

    def test_checker_failure_keeps_original_code_and_recovers(self):
        for mode in ['baseline_before','negative_control','confined','baseline_after']:
            with self.subTest(mode=mode):
                result,actions=self.exercise(mode)
                self.assertEqual(result['exit_code'],1)
                if mode in ['confined','baseline_after']:
                    self.assertIn('--remove',actions);self.assertTrue(result['inventory_restored'])
                else:self.assertNotIn('--add',actions)

    def test_successful_add_is_owned_even_if_following_evidence_save_fails(self):
        result,actions=self.exercise('save_after_add')
        self.assertEqual(result['exit_code'],3)
        self.assertTrue(result['ownership_started_after_add'])
        self.assertTrue(result['owned_profile_removed'])
        self.assertTrue(result['inventory_restored'])
        self.assertEqual(actions,['--skip-kernel-load','--add','--remove'])

    def test_remove_failure_has_environment_priority_and_retains_original(self):
        result,actions=self.exercise('remove')
        self.assertEqual(result['stage'],'cleanup');self.assertEqual(result['exit_code'],3)
        self.assertIn('original_failure',result)
        self.assertNotIn('owned_profile_removed',result)


class OwnedCommands(unittest.TestCase):
    def test_real_child_timeout_reaped_then_normal_command(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);children=[];popen=subprocess.Popen
            def launch(*a,**kw):
                child=popen(*a,**kw);children.append(child);return child
            with patch.object(subprocess,'Popen',side_effect=launch):
                with self.assertRaises(trial.Failure):trial.command([sys.executable,'-c','import time;time.sleep(30)'],time.monotonic()+.15,'test',out)
                row=json.loads((out/'keeper-command-timeout.json').read_text());self.assertTrue(row['owned_child_reaped'])
                cp=trial.command([sys.executable,'-c','print("ready")'],time.monotonic()+5,'next',out)
                self.assertEqual(cp.stdout.strip(),b'ready')
            self.assertTrue(all(c.poll() is not None for c in children))


class CompletionProof(unittest.TestCase):
    def proof(self, out):
        inputs={'keeper':{'input_sha256':bundle.KEEPER_INPUTS},'versions':{'k3s':'1.36.4+k3s1','containerd':'2.3.4-k3s1.36'}}
        row={'schema_version':1,'scenario':'k3s-f3','status':'passed','exit_code':0,'inputs':inputs,
             'boot_id':'00000000-0000-0000-0000-000000000001',
             'checks':[{'stage':'node_ready','status':'passed','count':1},{'stage':'keeper_exec','status':'passed'}]}
        report={'schema_version':1,'profile':trial.PROFILE,'input_sha256':bundle.KEEPER_INPUTS,
                'uid':501,'gid':1000,'status':'passed','exit_code':0,'steps':{m:checker(m) for m in trial.MODES},
                'inventory_before':[],'inventory_after_syntax':[],'inventory_after':[],
                'inventory_loaded':[trial.PROFILE+' (enforce)'],'inventory_restored':True,
                'owned_profile_removed':True,'ownership_started_after_add':True,
                'actions':[{'action':a,'returncode':0} for a in ['--skip-kernel-load','--add','--remove']]}
        files={'vm-stop.json':{'status':'passed','lifecycle_exit_code':0,'evidence_synced_before_stop':True,
                              'qemu_reaped':True,'qemu_exit_code':0,'escalated':False,'evidence_synced':True},
               'result.json':row,'keeper-result.json':report,
               'cluster-nodes.json':[{'metadata':{'name':'arena-k3s-server'},'status':{
                   'conditions':[{'type':'Ready','status':'True'}],
                   'nodeInfo':{'kubeletVersion':'v1.36.4+k3s1','containerRuntimeVersion':'containerd://2.3.4-k3s1.36'}}}]}
        for name,value in files.items():(out/name).write_text(json.dumps(value))
        (out/'service-result.txt').write_text('success\n')
        return inputs,report

    def test_complete_proof_and_tampered_checker_or_lifecycle(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);inputs,report=self.proof(out)
            self.assertEqual(host.verify_run(out,inputs)[0],0)
            mutations=[lambda r:r.update(owned_profile_removed=False),
                       lambda r:r.update(inventory_after=['unexpected (enforce)']),
                       lambda r:r['steps'].pop('negative_control'),
                       lambda r:r['steps']['confined']['report'].update(current_profile='unconfined'),
                       lambda r:r.update(actions=[{'action':'--replace','returncode':0}]),
                       lambda r:r.update(steps=[]),lambda r:r.update(actions=[None])]
            for mutation in mutations:
                bad=copy.deepcopy(report);mutation(bad)
                (out/'keeper-result.json').write_text(json.dumps(bad))
                with self.assertRaises(ValueError):host.verify_run(out,inputs)

    def test_failed_guest_keeps_mismatch_and_timeout_codes(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);inputs,_=self.proof(out)
            for code,status in [(1,'failed'),(3,'environment_error'),(4,'timeout'),(130,'cancelled')]:
                row=json.loads((out/'result.json').read_text());row.update(exit_code=code,status=status)
                (out/'result.json').write_text(json.dumps(row))
                self.assertEqual(host.verify_run(out,inputs)[0],code)

    def test_host_rejects_missing_lifecycle_before_partial_guest(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);(out/'vm-stop.json').write_text(json.dumps({'status':'environment_error'}))
            (out/'result.json').write_text('{')
            with self.assertRaisesRegex(ValueError,'lifecycle'):host.verify_run(out,{})


if __name__=='__main__':unittest.main()
