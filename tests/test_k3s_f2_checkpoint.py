"""Two-node fixture evidence, peer cleanup, shared deadlines and normal recovery."""
import copy
import json
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import k3s_f2_checkpoint as checkpoint
import k3s_f2_host as host


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for r in ['server','agent','private-control']:(self.root/r).mkdir()
        self.ids = {'server':'00000000-0000-0000-0000-000000000001',
                    'agent':'00000000-0000-0000-0000-000000000002'}
        self.inputs = {r:{'versions':{'k3s':'fixed','containerd':'fixed'},'fixture_config_digest':'fixed'} for r in self.ids}
        self.records = {
            'server/lifecycle-control.json':dict(checkpoint.CAPABILITY,action='cancel'),
            'server/result.json':{'schema_version':1,'scenario':'k3s-f2','role':'server','inputs':self.inputs['server'],
                'boot_id':self.ids['server'],'stage':checkpoint.CHECKPOINT,'lifecycle_fixture':dict(checkpoint.CAPABILITY,action='cancel'),
                'status':'environment_error','exit_code':3,'checks':[{'stage':'nodes','status':'passed','names':['arena-k3s-agent','arena-k3s-server']}]},
            'server/cluster-nodes.json':[{'metadata':{'name':'arena-k3s-'+r},'status':{'conditions':[{'type':'Ready','status':'True'}],
                 'nodeInfo':{'kubeletVersion':'vfixed','containerRuntimeVersion':'containerd://fixed'}}} for r in ['server','agent']],
            'server/checkpoint-state.json':dict(checkpoint.CAPABILITY,boot_id=self.ids['server'],runner_pid=10,observer_pid=11,
                proc_status='State:\tT (stopped)\nPid:\t10\n',traffic_not_started=True,peer_boot_id=self.ids['agent']),
            'server/watchdog-ready.json':{'schema_version':1,'scenario':'guest-runner-stopped','checkpoint':checkpoint.CHECKPOINT,
                 'boot_id':self.ids['server'],'batch_pid':10,'observer_pid':11,'state':'T','signal':19},
            'agent/agent-ready.json':{'schema_version':1,'boot_id':self.ids['agent']},
            'agent/result.json':{'schema_version':1,'scenario':'k3s-f2','role':'agent','inputs':self.inputs['agent'],
                'boot_id':self.ids['agent'],'status':'environment_error','exit_code':3,
                'checks':[{'stage':'agent_ready','status':'passed','image_config_digest':'fixed'}]}}
        stop={'schema_version':2,'status':'environment_error','lifecycle_exit_code':3,'reason':'cancelled','watchdog_fixture':False,
              'qemu_reaped':True,'qemu_exit_code':0,'escalated':False,'evidence_synced':True,'evidence_synced_before_stop':True,'sync_error':None}
        for role in self.ids:self.records[role+'/vm-stop.json']=copy.deepcopy(stop)
        self.write()

    def write(self):
        for p,v in self.records.items():(self.root/p).write_text(json.dumps(v))

    def test_cancel_and_timeout_recover_both_nodes(self):
        self.assertEqual(checkpoint.verify(self.root,self.inputs,'cancel',{'server':3,'agent':3},True),self.ids)
        self.records['server/lifecycle-control.json']['action']='timeout'
        self.records['server/result.json']['lifecycle_fixture']['action']='timeout'
        self.records['server/vm-stop.json'].update(reason='watchdog',watchdog_fixture=True,checkpoint_seen=True,
            startup_timeout_ms=120000,timeout_after_checkpoint_ms=2000,checkpoint_elapsed_ms=2001)
        self.write()
        self.assertEqual(checkpoint.verify(self.root,self.inputs,'timeout',{'server':3,'agent':3},False),self.ids)
        for mutation in [{'checkpoint_elapsed_ms':1999},{'checkpoint_seen':False},{'startup_timeout_ms':120001}]:
            saved=self.records['server/vm-stop.json'].copy();self.records['server/vm-stop.json'].update(mutation);self.write()
            with self.assertRaises(ValueError):checkpoint.verify(self.root,self.inputs,'timeout',{'server':3,'agent':3},False)
            self.records['server/vm-stop.json']=saved

    def test_either_node_cleanup_failure_is_never_expected_success(self):
        original=copy.deepcopy(self.records)
        for role in self.ids:
            for mutation in [{'qemu_reaped':False},{'qemu_exit_code':1},{'escalated':True},{'evidence_synced':False},
                             {'evidence_synced_before_stop':False},{'sync_error':'failed'},{'reason':'service_completed'}]:
                self.records=copy.deepcopy(original);self.records[role+'/vm-stop.json'].update(mutation);self.write()
                with self.subTest(role=role,mutation=mutation),self.assertRaises(ValueError):
                    checkpoint.verify(self.root,self.inputs,'cancel',{'server':3,'agent':3},True)

    def test_partial_node_peer_stop_and_no_traffic_are_required(self):
        original=copy.deepcopy(self.records)
        changes=[('server/result.json',{'checks':[]}),('server/result.json',{'status':'passed'}),
                 ('server/checkpoint-state.json',{'proc_status':'State:\tS\nPid:\t10\n'}),
                 ('server/checkpoint-state.json',{'peer_boot_id':'wrong'}),('server/watchdog-ready.json',{'batch_pid':11}),
                 ('agent/result.json',{'checks':[]}),('agent/agent-ready.json',{'boot_id':'wrong'})]
        for name,mutation in changes:
            self.records=copy.deepcopy(original);self.records[name].update(mutation);self.write()
            with self.subTest(name=name,mutation=mutation),self.assertRaises(ValueError):checkpoint.proof(self.root,self.inputs)
        self.records=original;self.write()
        for name in ['server/arena-f2-pods.json','server/service-result.txt','private-control/finish']:
            p=self.root/name;p.write_text('{}')
            with self.assertRaises(ValueError):checkpoint.proof(self.root,self.inputs)
            # Move this test-owned injected file aside, retain it as evidence.
            p.rename(p.with_name(p.name+'.retained'))

    def test_absent_control_and_old_guest_capability(self):
        self.assertIsNone(checkpoint.configuration(self.root/'agent'))
        with self.assertRaisesRegex(ValueError,'checkpoint-capable'):checkpoint.capability({})
        with self.assertRaises(ValueError):checkpoint.verify(self.root,self.inputs,'cancel',{'server':3,'agent':0},True)
        with self.assertRaises(ValueError):checkpoint.verify(self.root,self.inputs,'cancel',{'server':3,'agent':3},False)


class Workflow(unittest.TestCase):
    def exercise(self,*,cleanup_bad=False,expire=False,proof_bad=False):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td);out=base/'out';clock=[0.0];spawned=[]
            inputs={r:{'role':r,'scenario':'k3s-f2','hostUid':501,'system':'aarch64-linux','fixture_config_digest':'fixed'} for r in host.ROLES}
            old_exists=Path.exists
            def exists(p):return True if p.name=='lan.sock' else old_exists(p)
            def spawn(args,**kw):
                p=Path(args[6] if '--watchdog-fixture' in args else args[5]);role=p.name;index=int(p.parent.name.split('-')[1]);fixture=index in [1,3]
                child=Mock();child.pid=100+len(spawned);child.returncode=None if fixture else 0
                child.poll.side_effect=lambda:child.returncode
                def terminate(sig):child.returncode=3
                child.send_signal.side_effect=terminate;child.wait.side_effect=lambda **k:child.returncode
                (p/'result.json').write_text('{"status":"passed"}')
                (p/'vm-stop.json').write_text(json.dumps({'qemu_reaped':not(cleanup_bad and role=='agent'),
                    'escalated':False,'evidence_synced':True,'evidence_synced_before_stop':True,'sync_error':None,
                    'schema_version':2,'status':'environment_error','lifecycle_exit_code':3,'qemu_exit_code':0}))
                if fixture and role=='server':(p/'watchdog-ready.json').write_text('{}')
                clock[0]+=160 if expire else 10
                spawned.append((args,child))
                return child
            def proof(run,ins):
                if proof_bad:raise ValueError('invalid stop proof')
                if run.name=='fresh-3':spawned[-2][1].returncode=3
                return {'server':'s'+run.name,'agent':'a'+run.name}
            def verify(run,*args):return {'server':'s'+run.name,'agent':'a'+run.name}
            with patch.object(host,'check',side_effect=lambda p:(inputs[p.name],{'files':{}})), \
                 patch.object(host.os,'getuid',return_value=501),patch.object(host.os,'cpu_count',return_value=6), \
                 patch.object(host,'execution',return_value={'arch':'aarch64','accelerator':'tcg'}), \
                 patch.object(host,'available_memory_mib',return_value=30000),patch.object(host.shutil,'disk_usage',return_value=Mock(free=300*1024**3)), \
                 patch.object(host,'qemu_args',return_value=['qemu']),patch.object(host,'sha',return_value='hash'), \
                 patch.object(host.subprocess,'run'),patch.object(host.subprocess,'Popen',side_effect=spawn),patch.object(Path,'exists',exists), \
                 patch.object(host.time,'monotonic',side_effect=lambda:clock[0]),patch.object(host.time,'sleep',side_effect=lambda s:clock.__setitem__(0,clock[0]+s)), \
                 patch.object(checkpoint,'capability'),patch.object(checkpoint,'peer',return_value='peer'), \
                 patch.object(checkpoint,'proof',side_effect=proof),patch.object(checkpoint,'verify',side_effect=verify), \
                 patch.object(host,'verify_cluster',side_effect=verify):
                code=host.workflow(501,{r:base/r for r in host.ROLES},'lifecycle',out)
            return code,json.loads((out/'summary.json').read_text()),spawned

    def test_faults_followed_by_fresh_normals_and_watchdog_only_on_server(self):
        code,summary,spawned=self.exercise();self.assertEqual(code,0);self.assertEqual(len(spawned),8)
        self.assertEqual([r['fixture'] for r in summary['runs']],['cancel',None,'timeout',None])
        self.assertEqual([r.get('fixture_verified') for r in summary['runs']],[True,None,True,None])
        self.assertEqual([i for i,(args,_) in enumerate(spawned) if '--watchdog-fixture' in args],[4])
        self.assertEqual([r.get('observed_exit_code') for r in summary['runs']],[130,None,3,None])
        self.assertEqual(len({b for r in summary['runs'] for b in r['boot_ids'].values()}),8)

    def test_peer_cleanup_and_bad_checkpoint_stop_following_vm(self):
        for kw in [{'cleanup_bad':True},{'proof_bad':True}]:
            code,summary,spawned=self.exercise(**kw);self.assertEqual(code,3);self.assertEqual(len(spawned),2)
            self.assertNotIn('fixture_verified',summary['runs'][0])
            for _,child in spawned:child.send_signal.assert_called()
            if kw.get('cleanup_bad'):self.assertEqual(summary['runs'][0]['stage'],'cleanup')

    def test_one_shared_deadline_is_not_reset_for_next_cluster(self):
        code,summary,spawned=self.exercise(expire=True);self.assertEqual(code,4)
        self.assertLess(len(spawned),8)


if __name__=='__main__':unittest.main()
