# SPDX-License-Identifier: MIT
"""Negative contracts: fixtures are not runtime proof."""
import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import warden_observe_host as host
import warden_observe_guest as guest
import warden_observe_terminal as terminal


def fixture(how='term'):
    clients=[{'family':fam,'address':addr,'port':1234+i,'pid':42+i,'tid':142+i,'cgroup_id':100+i,
              'scope':host.SCOPE+'/client.service','errno':0,'receiver_delta':1}
             for i,(fam,addr) in enumerate([(2,'127.0.0.1'),(10,'::1')])]
    records=[{'type':'startup','ready':False},{'type':'ready','ready':True,'links':2}]
    for c in clients:
        records.append({'type':'event','pid':c['pid'],'tid':c['tid'],'cgroup_id':str(c['cgroup_id']),
            'destination_ip':c['address'],'destination_port':c['port'],'protocol':'tcp','family':4 if c['family']==2 else 6,
            'observation':'connect_attempt','decision':'allow','policy_id':0,'connection_result':'unknown'})
    records.append(dict(type='stats',final=how=='term',attempted='2',emitted='2',decoded='2',**{k:'0' for k in ['denied','ring_dropped','decode_errors','queue_dropped','stats_dropped']}))
    if how=='term':records.append({'type':'shutdown','success':True,'ready':False,'attachment_state':'owned_links_released','final_stats_available':True})
    for i,r in enumerate(records):r.update(schema_version=1,instance_id='fixture-'+how,scope=host.SCOPE,mode='observe',sequence=str(i),elapsed_ns=str(i))
    maps={'mode':[{'key':['0x00']*4,'value':['0x00']*4}],'deny':[],'mode_id':100,'deny_id':101}
    return {'signal':how,'returncode':0 if how=='term' else -9,'argv':['cli','connect','--object','bpf','--log-format','json','--interval-ms','100'],
            'links_before':[],'links_after':[],'attached_before':[],'attached_after':[],
            'programs_before':[], 'programs_live':[{'id':10},{'id':11}], 'programs_after':[],
            'links_live':[{'id':1,'prog_id':10,'attach_type':'cgroup_inet4_connect'},{'id':2,'prog_id':11,'attach_type':'cgroup_inet6_connect'}],
            'attached_live':[{'id':10},{'id':11}], 'maps':maps,'maps_before_stop':copy.deepcopy(maps),
            'clients':clients,'restored_clients':copy.deepcopy(clients),
            'outside_client':dict(clients[0],scope='/system.slice/client.service'),
            'signal_sent_ns':1,'stopped_ns':2,'records':records}


class ObserveContract(unittest.TestCase):
    def test_actual_guest_output_regression(self):
        data=json.loads((Path(__file__).parent/'fixtures/warden-observe-runtime-20261023.json').read_text())
        for case in data['cases']:
            # Preserve actual evidence verbatim; it cannot prove global program release.
            with self.assertRaisesRegex(ValueError,'global program release proof'):
                host.verify_case(case)
            self.assertNotEqual(case['attached_before'],case['attached_after'])
            self.assertEqual(case['links_before'],case['links_after'])
            invalid=copy.deepcopy(case)
            owned=next(x for x in invalid['attached_live'] if x['id'] not in {r['id'] for r in invalid['attached_before']})
            invalid['attached_after'].append(owned)
            with self.assertRaises(ValueError):host.verify_case(invalid)

    def test_both_signal_contracts(self):
        for how in ['term','kill']:host.verify_case(fixture(how))

    def test_systemd_ids_can_change_but_owned_ids_must_disappear(self):
        c=fixture()
        before={'id':99,'attach_type':'cgroup_inet_egress','name':'sd_fw_egress'}
        c['attached_before']=[before];c['attached_live'].append(before)
        c['attached_after']=[dict(before,id=199)]
        host.verify_case(c)
        c['links_after']=[c['links_live'][0]]
        with self.assertRaises(ValueError):host.verify_case(c)

    def test_preexisting_global_links_must_all_survive(self):
        baseline=[{'id':90,'prog_id':900,'type':'tracing'},
                  {'id':91,'prog_id':901,'type':'tracing'}]
        for how in ['term','kill']:
            c=fixture(how)
            c['links_before']=copy.deepcopy(baseline)
            c['links_live'].extend(copy.deepcopy(baseline))
            c['links_after']=list(reversed(copy.deepcopy(baseline)))
            host.verify_case(c)  # Enumeration order does not change the collection.
            self.assertTrue(guest.same_links(c['links_before'],c['links_after']))
            for after in [baseline[:1],[],baseline+[{'id':92,'prog_id':902}],
                          [dict(baseline[0],prog_id=999),baseline[1]]]:
                invalid=copy.deepcopy(c)
                invalid['links_after']=copy.deepcopy(after)
                self.assertFalse(guest.same_links(invalid['links_before'],invalid['links_after']))
                with self.assertRaises(ValueError):host.verify_case(invalid)

    def test_detached_but_globally_loaded_programs_are_rejected(self):
        for how in ['term','kill']:
            c=fixture(how)
            c['programs_before']=[{'id':99,'name':'unrelated'}]
            c['programs_live']=[{'id':99},{'id':10},{'id':11}]
            c['programs_after']=[{'id':99},{'id':10}]
            with self.assertRaises(ValueError):host.verify_case(c)
            self.assertFalse(guest.programs_released(c['programs_before'],c['programs_live'],c['programs_after'],[10,11]))
            c['programs_after']=[{'id':99}]
            host.verify_case(c)
            self.assertTrue(guest.programs_released(c['programs_before'],c['programs_live'],c['programs_after'],[10,11]))

    def test_global_program_evidence_is_required_and_strict(self):
        for how in ['term','kill']:
            for key in ['programs_before','programs_live','programs_after']:
                for value in [None,False,[{'id':True}],[{'id':10},{'id':10}]]:
                    c=fixture(how);c[key]=value
                    with self.assertRaisesRegex(ValueError,'global program release proof'):host.verify_case(c)
            for key,value in [('programs_before',[{'id':10}]),('programs_live',[{'id':10}]),
                              ('programs_after',[{'id':11}])]:
                c=fixture(how);c[key]=value
                with self.assertRaisesRegex(ValueError,'global program release proof'):host.verify_case(c)

    def test_missing_restoration_and_wrong_scope(self):
        mutations=[lambda c:c.update(links_after=[{'id':9}]),lambda c:c.update(attached_after=[{'id':10}]),
                   lambda c:c['restored_clients'][0].update(errno=110),
                   lambda c:c['clients'][0].update(scope='/system.slice/x'),
                   lambda c:c.update(returncode=True),lambda c:c.update(attached_live=[{'id':99}])]
        for mutate in mutations:
            c=fixture();mutate(c)
            with self.assertRaises(ValueError):host.verify_case(c)

    def test_no_false_enforce_or_missing_events(self):
        mutations=[lambda c:c['maps']['mode'][0].update(value=['0x01','0x00','0x00','0x00']),
                   lambda c:c['maps']['deny'].append({}),lambda c:c['records'][2].update(family=True),
                   lambda c:c['records'][2].update(destination_port=5555),lambda c:c['records'][2].update(connection_result='success'),
                   lambda c:c['records'][2].update(argv='secret'),lambda c:c['records'][4].update(ring_dropped='1'),
                   lambda c:c['records'][0].update(schema_version=True),lambda c:c['records'][2].update(pid=99)]
        for mutate in mutations:
            c=fixture();mutate(c)
            with self.assertRaises(ValueError):host.verify_case(c)

    def test_kill_never_claims_graceful_final(self):
        c=fixture('kill');c['records'][-1]['final']=True
        with self.assertRaises(ValueError):host.verify_case(c)
        c=fixture();c['records'].pop()
        with self.assertRaises(ValueError):host.verify_case(c)

    def test_bpftool_single_object_or_one_element_list(self):
        self.assertEqual(guest.one({'id':1}),guest.one([{'id':1}]))
        for bad in [[],[{},{}],False]:
            with self.assertRaises(ValueError):guest.one(bad)

    def test_terminal_owner_and_startup_failure(self):
        root=Path(tempfile.mkdtemp()); source=root/'absent';out=root/'out';out.mkdir()
        terminal.finish(source,out,'exit-code')
        r=json.loads((out/'terminal.json').read_text())
        self.assertFalse(r['proof_directory_present']);self.assertEqual((out/'service-result.txt').read_text(),'exit-code\n')
        other=root/'other';other.mkdir()
        with patch.object(terminal.os,'geteuid',return_value=os.getuid()+1):
            with self.assertRaises(ValueError):terminal.finish(source,other,'success')

    def test_outer_deadline_cleans_owned_controller(self):
        root=Path(tempfile.mkdtemp())/'run'
        route={'arch':'aarch64','accelerator':'tcg','cpu':'max'}
        process=Mock();process.wait.side_effect=subprocess.TimeoutExpired('fixture',180)
        with patch.object(host,'preflight',return_value=({}, {}, route)),patch.object(host,'execution',return_value=route),patch.object(host,'read',return_value='/nix/store/fixture'),patch.object(host.subprocess,'Popen',return_value=process),patch.object(host,'stop_controller',return_value=False) as stop:
            self.assertEqual(host.workflow(os.getuid(),Path('/bundle'),root),3)
        stop.assert_called_once_with(process)
        self.assertEqual(json.loads((root/'summary.json').read_text())['status'],'failed')

    def test_malformed_nested_evidence_finishes_as_failed(self):
        for field in ['records','maps']:
            case=fixture()
            if field=='records':case['records'][0]=[]
            else:case['maps']['mode'][0]=[]
            root=Path(tempfile.mkdtemp())/'run'
            route={'arch':'aarch64','accelerator':'tcg','cpu':'max'}
            process=Mock();process.wait.return_value=0
            with patch.object(host,'preflight',return_value=({}, {}, route)),patch.object(host,'execution',return_value=route),patch.object(host,'read',return_value='/nix/store/fixture'),patch.object(host.subprocess,'Popen',return_value=process),patch.object(host,'verify',side_effect=lambda *args:host.verify_case(case)):
                self.assertEqual(host.workflow(os.getuid(),Path('/bundle'),root),3)
            summary=json.loads((root/'summary.json').read_text())
            self.assertEqual(summary['status'],'failed')
            self.assertTrue(summary['error'])

    def test_non_object_terminal_json_is_failed(self):
        root=Path(tempfile.mkdtemp())/'run'
        route={'arch':'aarch64','accelerator':'tcg','cpu':'max'}
        process=Mock();process.wait.return_value=0
        def read(path):
            return '[]' if path.name=='terminal.json' else '/nix/store/fixture'
        with patch.object(host,'preflight',return_value=({}, {}, route)),patch.object(host,'execution',return_value=route),patch.object(host,'read',side_effect=read),patch.object(host.subprocess,'Popen',return_value=process):
            self.assertEqual(host.workflow(os.getuid(),Path('/bundle'),root),3)
        self.assertEqual(json.loads((root/'summary.json').read_text())['status'],'failed')

    def test_bundle_path_cannot_inject_qemu_drive_options(self):
        for name in ['/fixture,readonly=off','/fixture\nunsafe']:
            with patch.object(host,'sealed_bundle') as sealed:
                with self.assertRaises(ValueError):host.check_bundle(Path(name))
                sealed.assert_not_called()

    def test_bundle_is_separate_from_policy(self):
        with patch.object(host,'sealed_bundle',return_value={}),patch.object(host,'read',return_value=json.dumps({'scenario':'warden-policy','guest_system':'aarch64-linux'})):
            with self.assertRaises(ValueError):host.check_bundle(Path('/fixture'))


if __name__=='__main__':unittest.main()
