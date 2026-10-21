"""Two-node proof contract and owned-process recovery, without real network/VMs."""
import copy
import errno
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
import k3s_f2_host as host
import k3s_f2_guest as guest
import k3s_bundle as bundle


class F2(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.inputs={r:{"role":r,"fixture_config_digest":"sha256:fixed"} for r in host.ROLES}
        self.pods=[{"metadata":{"name":name},"spec":{"nodeName":"arena-k3s-"+("server" if name=="server" else "agent")},"status":{"podIP":"10.42.0.9"}} for name in ["server","client","control"]]
        self.dns="echo.arena-f2.svc.cluster.local"
        self.counter=0
        def probes(stage):
            tuples=([(a,p,d,"allow") for d in ["10.42.0.9",self.dns] for a in ["client"] for p in ["tcp","udp"]]
                    if stage!="deny" else [("client","tcp",d,"deny") for d in ["10.42.0.9",self.dns]]+[("control","tcp",self.dns,"allow"),("client","udp",self.dns,"allow")])
            rows=[]
            for client,p,d,expected in tuples:
                self.counter+=1;payload=f"arena-f2:{p}:{self.counter:032x}"
                probe={"protocol":p,"host":d,"addresses":["10.43.0.9" if d==self.dns else d],"payload":payload}
                probe.update({"outcome":"denied","errno":110} if expected=="deny" else {"received":payload,"count":1})
                rows.append({"client":client,"protocol":p,"destination":d,"expected":expected,"probe":probe,"receiver_count":0 if expected=="deny" else 1})
            return rows
        self.results={"server":{"checks":[{"stage":"nodes","status":"passed","names":sorted(guest.NODES)},
                    {"stage":"pods","status":"passed","placement":{p['metadata']['name']:p['spec']['nodeName'] for p in self.pods}}]+
                    [{"stage":s,"status":"passed","probes":probes(s)} for s in ["baseline","deny","recovery"]]},
                      "agent":{"checks":[{"stage":"agent_ready","status":"passed","image_config_digest":"sha256:fixed"}]}}
        for i,(role,row) in enumerate(self.results.items()):
            row.update(schema_version=1,scenario="k3s-f2",role=role,inputs=self.inputs[role],boot_id=f"{i+1:08x}-0000-0000-0000-000000000000",status="passed",exit_code=0)
            out=self.root/role;out.mkdir()
            self.write(role,"result.json",row)
            self.write(role,"vm-stop.json",{"status":"passed","qemu_reaped":True,"qemu_exit_code":0,"escalated":False,"evidence_synced":True,"evidence_synced_before_stop":True,"lifecycle_exit_code":0})
            (out/"service-result.txt").write_text("success\n")
        self.results['server']['checks'][3]['policy_uid']='fixed-policy-uid'
        self.write('server','result.json',self.results['server'])
        self.write('server','policy-state.json',guest.policy() | {'metadata':{'uid':'fixed-policy-uid'}})
        for name,text in [('before','arena-selective'),('after','arena-selective'),('removed','no-policy')]:
            (self.root/'server'/f'policy-rules-{name}.txt').write_text(text)
        self.write("server","arena-f2-pods.json",self.pods)
        self.write("server","service-state.json",{"spec":{"clusterIP":"10.43.0.9"}})
        self.write("agent","fixture-image-state.json",{"id":"sha256:fixed"})

    def write(self,role,name,value):
        (self.root/role/name).write_text(json.dumps(value))

    def test_complete_cross_node_proof(self):
        self.assertEqual(len(set(host.verify_cluster(self.root,self.inputs).values())),2)

    def test_missing_deny_or_control_is_not_success(self):
        for index in [0,2,3]:
            row=copy.deepcopy(self.results['server']);row['checks'][3]['probes'].pop(index)
            self.write('server','result.json',row)
            with self.assertRaisesRegex(ValueError,'missing target/control'):
                host.verify_cluster(self.root,self.inputs)

    def test_local_error_cannot_become_network_deny(self):
        row=copy.deepcopy(self.results['server']);row['checks'][3]['probes'][0]['probe']['errno']=errno.ENOENT
        self.write('server','result.json',row)
        with self.assertRaisesRegex(ValueError,'network refusal'):
            host.verify_cluster(self.root,self.inputs)

    def test_wrong_dns_receiver_or_reused_nonce_rejected(self):
        for key,value in [('addresses',['10.43.0.8']),('payload','invalid')]:
            row=copy.deepcopy(self.results['server']);row['checks'][2]['probes'][0]['probe'][key]=value
            self.write('server','result.json',row)
            with self.assertRaises(ValueError):host.verify_cluster(self.root,self.inputs)
        row=copy.deepcopy(self.results['server']);row['checks'][3]['probes'][0]['receiver_count']=1
        self.write('server','result.json',row)
        with self.assertRaises(ValueError):host.verify_cluster(self.root,self.inputs)

    def test_same_node_and_same_boot_rejected(self):
        pods=copy.deepcopy(self.pods);pods[1]['spec']['nodeName']='arena-k3s-server'
        self.write('server','arena-f2-pods.json',pods)
        with self.assertRaisesRegex(ValueError,'placement'):host.verify_cluster(self.root,self.inputs)
        self.write('server','arena-f2-pods.json',self.pods)
        row=copy.deepcopy(self.results['agent']);row['boot_id']=self.results['server']['boot_id'];self.write('agent','result.json',row)
        with self.assertRaisesRegex(ValueError,'boot identity'):host.verify_cluster(self.root,self.inputs)

    def test_lifecycle_failure_precedes_partial_result(self):
        (self.root/'server'/'result.json').write_text('{partial')
        self.write('server','vm-stop.json',{'status':'environment_error','qemu_reaped':False})
        with self.assertRaisesRegex(ValueError,'lifecycle'):host.verify_cluster(self.root,self.inputs)

    def test_lan_is_unix_only_and_resources_match_contract(self):
        contract=json.loads((SCRIPTS.parent/'docs/contracts/f0-inputs.json').read_text())
        for role,(cpu,ram) in host.ROLES.items():
            self.assertEqual(contract['f2_limits'][role],{'vcpus':cpu,'memory_mib':ram,'writable_run_disk_virtual_gib':32})
            (self.root/'toplevel').write_text('/nix/store/'+'a'*32+'-nixos-system-fixture')
            (self.root/'kernel-params').write_text('root=fstab')
            with patch.object(host, 'route', return_value={'arch':'aarch64','accelerator':'tcg','cpu':'max'}):
                args=host.qemu_args('aarch64',self.root,self.root,self.root,self.root/'lan.sock',role,{})
            self.assertIn('addr.type=unix',args[args.index('-netdev')+1]);self.assertNotIn('user',args)
            self.assertEqual(args[args.index('-smp')+1],str(cpu));self.assertEqual(args[args.index('-m')+1],str(ram))
        for k in ['node_ready_seconds','pod_ready_seconds','io_probe_seconds','workflow_seconds','host_watchdog_seconds']:
            self.assertEqual(contract['f2_limits'][k],contract['f1_limits'][k])

    def test_guest_failure_code_preserved(self):
        row=copy.deepcopy(self.results['server']);row.update(status='failed',exit_code=1,stage='deny',detail='target passed')
        self.write('server','result.json',row)
        (self.root/'server'/'service-result.txt').write_text('exit-code')
        with self.assertRaises(host.GuestFailure) as raised:host.verify_cluster(self.root,self.inputs)
        self.assertEqual(raised.exception.code,1)

    def test_policy_not_established_cannot_pass(self):
        self.write('server','policy-state.json',{'spec':{}})
        with self.assertRaisesRegex(ValueError,'policy configuration'):host.verify_cluster(self.root,self.inputs)

    def test_collect_signals_only_owned_live_controllers(self):
        live,done=Mock(),Mock();live.poll.return_value=None;done.poll.return_value=0
        self.assertEqual(host.collect({'server':live,'agent':done}),[])
        live.send_signal.assert_called_once_with(signal.SIGTERM);done.send_signal.assert_not_called()
        live.wait.assert_called_once_with(timeout=15);done.wait.assert_called_once_with(timeout=15)

    def test_existing_output_is_never_overwritten(self):
        owner=self.root/'owner-evidence';owner.write_text('keep')
        inputs={role:{'role':role,'scenario':'k3s-f2','hostUid':501,'system':'aarch64-linux','fixture_config_digest':'fixed'} for role in host.ROLES}
        with patch.object(host,'check',side_effect=lambda path:(inputs[path.name],{})), \
                patch.object(host.os,'getuid',return_value=501),patch.object(host.os,'cpu_count',return_value=6), \
                patch.object(host, "execution", return_value={"arch": "aarch64", "accelerator": "tcg", "cpu": "max"}), \
                patch.object(host,'available_memory_mib',return_value=30000), \
                patch.object(host.shutil,'disk_usage',return_value=Mock(free=200*1024**3)), \
                patch.object(host.subprocess,'Popen') as spawn:
            with self.assertRaises(FileExistsError):
                host.workflow(501,{r:self.root/r for r in host.ROLES},'daily',self.root)
            spawn.assert_not_called()
        self.assertEqual(owner.read_text(),'keep')

    def test_agent_launch_failure_reclaims_server_and_keeps_error(self):
        output=self.root/'new-run'
        inputs={role:{'role':role,'scenario':'k3s-f2','hostUid':501,'system':'aarch64-linux','fixture_config_digest':'fixed'} for role in host.ROLES}
        child=Mock();child.poll.return_value=None;child.returncode=-15
        original_exists=Path.exists
        def exists(path):
            return True if path.name=='lan.sock' else original_exists(path)
        def spawn(args,**kwargs):
            out=Path(args[5])
            if out.name=='agent':raise OSError('agent launch fixture failure')
            self.write_stop(out)
            return child
        with patch.object(host,'check',side_effect=lambda path:(inputs[path.name],{'files':{}})), \
                patch.object(host.os,'getuid',return_value=501),patch.object(host.os,'cpu_count',return_value=6), \
                patch.object(host, "execution", return_value={"arch": "aarch64", "accelerator": "tcg", "cpu": "max"}), \
                patch.object(host,'available_memory_mib',return_value=30000), \
                patch.object(host.shutil,'disk_usage',return_value=Mock(free=200*1024**3)), \
                patch.object(host,'qemu_args',return_value=['owned-qemu']),patch.object(host,'sha',return_value='fixed'), \
                patch.object(host.subprocess,'run'),patch.object(host.subprocess,'Popen',side_effect=spawn), \
                patch.object(Path,'exists',exists):
            self.assertEqual(host.workflow(501,{r:self.root/r for r in host.ROLES},'daily',output),3)
        child.send_signal.assert_called_once_with(signal.SIGTERM)
        child.wait.assert_called_once_with(timeout=15)
        summary=json.loads((output/'summary.json').read_text())
        self.assertEqual(summary['status'],'environment_error')
        self.assertIn('agent launch fixture failure',summary['detail'])
        self.assertTrue(summary['runs'][0]['recovery']['server']['qemu_reaped'])

    @staticmethod
    def write_stop(out):
        (out/'vm-stop.json').write_text(json.dumps({'qemu_reaped':True,'escalated':False,'evidence_synced':True}))

    def test_fixed_probe_failure_is_not_a_pass_or_environment_success(self):
        with self.assertRaises(guest.Failure) as raised:
            guest.probe_output(Mock(returncode=1,stdout=b''),'deny',action='deny',protocol='tcp',address='fixed',nonce='a'*32)
        self.assertEqual(raised.exception.code,3)
        self.assertEqual(raised.exception.stage,'deny')
        with self.assertRaises(guest.Failure):guest.probe_output(Mock(returncode=0,stdout=b'{partial'),'baseline',action='probe',protocol='tcp',address='fixed',nonce='a'*32)

    def test_policy_is_selective_and_ingress_only(self):
        p=guest.policy()['spec'];self.assertEqual(p['policyTypes'],['Ingress'])
        self.assertEqual(p['podSelector'],{'matchLabels':{'app':'echo'}})
        self.assertEqual(p['ingress'][0],{'ports':[{'protocol':'UDP','port':18080}]})
        self.assertEqual(p['ingress'][1]['from'],[{'podSelector':{'matchLabels':{'app':'control'}}}])

    def test_fixture_does_not_treat_local_failure_as_deny(self):
        spec=importlib.util.spec_from_file_location('f2_fixture',SCRIPTS.parent/'tests/k3s/f2_fixture.py')
        fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
        sock=Mock();sock.__enter__=Mock(return_value=sock);sock.__exit__=Mock(return_value=False)
        with patch.object(fixture.socket,'socket',return_value=sock),patch.object(fixture.socket,'getaddrinfo',return_value=[(0,0,0,0,('10.42.0.9',18080))]):
            sock.connect.side_effect=FileNotFoundError(errno.ENOENT,'local')
            with self.assertRaises(FileNotFoundError):fixture.probe('tcp','fixed','a'*32,deny=True)
            sock.connect.side_effect=TimeoutError()
            with patch('sys.stdout',new_callable=io.StringIO) as output:
                fixture.probe('tcp','fixed','b'*32,deny=True)
                self.assertEqual(json.loads(output.getvalue())['errno'],errno.ETIMEDOUT)


if __name__=='__main__':unittest.main()
