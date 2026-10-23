# SPDX-License-Identifier: MIT
"""Negative contracts: fixtures are not runtime proof."""
import copy
import hashlib
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
              'scope':host.SCOPE+'/client.service','errno':0,'receiver_delta':1,
              'attempt_started_ns':1,'attempt_finished_ns':2}
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
            'programs_before':[], 'programs_live':[{'id':10,'map_ids':[100,101]},{'id':11,'map_ids':[100,101]}], 'programs_after':[],
            'links_live':[{'id':1,'prog_id':10,'attach_type':'cgroup_inet4_connect'},{'id':2,'prog_id':11,'attach_type':'cgroup_inet6_connect'}],
            'attached_live':[{'id':10},{'id':11}], 'maps':maps,'maps_before_stop':copy.deepcopy(maps),
            'clients':clients,'restored_clients':[dict(c,attempt_started_ns=5,attempt_finished_ns=6) for c in clients],
            'outside_client':dict(clients[0],scope='/system.slice/client.service'),
            'signal_sent_ns':3,'stopped_ns':4,'records':records}


def export_fixture(root, mutate=lambda result:None):
    """Synthetic exported evidence only; no VM/BPF execution."""
    (root/'observe').mkdir()
    meta={'runtime_cli_sha256':'a'*64,'files':{'veil-warden-ebpf':'b'*64},
          'runtime_cli_path':'/nix/store/'+'a'*32+'-fixture/bin/veil-warden',
          'bpf_path':'/nix/store/'+'b'*32+'-fixture/veil-warden-ebpf'}
    cases=[fixture('term'),fixture('kill')]
    for c in cases:c['argv'][0]=meta['runtime_cli_path'];c['argv'][3]=meta['bpf_path']
    result={'schema':'arena-warden-observe-v1','status':'passed','cleanup_complete':True,
            'drop_applied':False,'boot_id':'a'*8+'-'+('b'*4+'-')*3+'c'*12,
            'runtime_cli_sha256':meta['runtime_cli_sha256'],'bpf_sha256':meta['files']['veil-warden-ebpf'],
            'runtime_cli_path':meta['runtime_cli_path'],'bpf_path':meta['bpf_path'],
            'baseline_clients':copy.deepcopy(cases[0]['clients']),'cases':cases,'receiver_errors':[[],[]]}
    mutate(result)
    (root/'observe/result.json').write_text(json.dumps(result))
    for c in cases:
        (root/'observe'/ (c['signal']+'.jsonl')).write_text(''.join(json.dumps(r)+'\n' for r in c['records']))
        (root/'observe'/ (c['signal']+'.stderr')).write_text('')
    names=['result.json','term.jsonl','term.stderr','kill.jsonl','kill.stderr']
    terminal={'schema':'arena-warden-observe-terminal-v1','service_result':'success',
              'proof_directory_present':True,'writer_uid':os.getuid(),'output_owner_uid':os.getuid(),
              'files':{name:hashlib.sha256((root/'observe'/name).read_bytes()).hexdigest() for name in names}}
    (root/'terminal.json').write_text(json.dumps(terminal));(root/'service-result.txt').write_text('success\n')
    return meta


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

    def test_map_ids_must_belong_to_both_owned_programs(self):
        for how in ['term','kill']:
            for key,value in [('mode_id',999),('deny_id',999),('mode_id',True),('deny_id',100)]:
                c=fixture(how)
                for name in ['maps','maps_before_stop']:c[name][key]=value
                with self.assertRaises(ValueError):host.verify_case(c)
            for index in [0,1]:
                c=fixture(how);c['programs_live'][index]['map_ids']=[100]
                with self.assertRaises(ValueError):host.verify_case(c)

    def test_executed_argv_must_match_sealed_paths(self):
        root=Path(tempfile.mkdtemp());meta=export_fixture(root)
        with patch.object(host,'verify_stop'):host.verify(root,meta)
        for case_index in [0,1]:
            for arg_index in [0,3]:
                root=Path(tempfile.mkdtemp())
                def mutate(result):result['cases'][case_index]['argv'][arg_index]='/wrong/artifact'
                meta=export_fixture(root,mutate)
                with patch.object(host,'verify_stop'):
                    with self.assertRaises(ValueError):host.verify(root,meta)

    def test_missing_or_malformed_program_map_references(self):
        for reference in [None,True,[],[100,True],[100,101,101]]:
            c=fixture();c['programs_live'][0]['map_ids']=reference
            with self.assertRaisesRegex(ValueError,'policy maps not bound'):host.verify_case(c)
            self.assertFalse(guest.maps_bound(c['maps'],c['programs_live'],[10,11]))
        c=fixture();self.assertTrue(guest.maps_bound(c['maps'],c['programs_live'],[10,11]))

    def test_artifact_metadata_and_configured_paths_are_required(self):
        for field in ['runtime_cli_path','bpf_path']:
            root=Path(tempfile.mkdtemp());meta=export_fixture(root,lambda result:result.update({field:'/wrong/artifact'}))
            with patch.object(host,'verify_stop'):
                with self.assertRaisesRegex(ValueError,'configured artifact paths differ'):host.verify(root,meta)
            for value in [None,'/tmp/artifact',True]:
                invalid=dict(meta);invalid[field]=value
                with self.assertRaisesRegex(ValueError,'sealed runtime artifact paths'):host.require_artifact_paths(invalid)

    def test_target_cgroup_itself_is_not_outside(self):
        for how in ['term','kill']:
            for scope in [host.SCOPE,host.SCOPE+'/client.service']:
                c=fixture(how);c['outside_client']['scope']=scope
                with self.assertRaises(ValueError):host.verify_case(c)
        c=fixture()['clients'][0]
        host.client(dict(c,scope=host.SCOPE))
        host.client(dict(c,scope=host.SCOPE+'-other/client.service'),inside=False)

    def test_cached_pre_stop_clients_cannot_prove_restoration(self):
        for how in ['term','kill']:
            c=fixture(how);c['restored_clients']=copy.deepcopy(c['clients'])
            with self.assertRaises(ValueError):host.verify_case(c)

    def test_client_attempt_timing_boundaries(self):
        for how in ['term','kill']:
            c=fixture(how);host.verify_case(c)
            for start,end in [(None,6),(True,6),(5,None),(6,5),(4,6),(3,6)]:
                invalid=copy.deepcopy(c)
                invalid['restored_clients'][0].update(attempt_started_ns=start,attempt_finished_ns=end)
                with self.assertRaises(ValueError):host.verify_case(invalid)
            invalid=copy.deepcopy(c);invalid['clients'][0]['attempt_finished_ns']=5
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
            c['programs_live']=[{'id':99},{'id':10,'map_ids':[100,101]},{'id':11,'map_ids':[100,101]}]
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

    def test_startup_and_ready_cannot_repeat(self):
        for how in ['term','kill']:
            for kind in ['startup','ready']:
                for position in range(2,len(fixture(how)['records'])):
                    c=fixture(how)
                    duplicate=copy.deepcopy(c['records'][0 if kind=='startup' else 1])
                    c['records'].insert(position,duplicate)
                    for index,row in enumerate(c['records']):
                        row.update(sequence=str(index),elapsed_ns=str(index))
                    with self.assertRaises(ValueError):host.verify_case(c)

    def test_elapsed_clock_is_nondecreasing(self):
        for how in ['term','kill']:
            c=fixture(how)
            for row in c['records']:row['elapsed_ns']='7'
            host.verify_case(c)  # Equal timestamps are valid.
            c['records'][2]['elapsed_ns']='6'
            with self.assertRaises(ValueError):host.verify_case(c)
            for invalid in ['', '-1', '1.0', True, '²']:
                c=fixture(how);c['records'][2]['elapsed_ns']=invalid
                with self.assertRaises(ValueError):host.verify_case(c)

    def test_every_periodic_stats_counter_is_valid(self):
        for how in ['term','kill']:
            for key in ['attempted','emitted','decoded']:
                for value in [None,True,2,'','-1','2.0','²','3']:
                    c=fixture(how)
                    periodic=copy.deepcopy(c['records'][-2 if how=='term' else -1])
                    periodic['final']=False
                    if value is None:periodic.pop(key)
                    else:periodic[key]=value
                    c['records'].insert(2,periodic)
                    for index,row in enumerate(c['records']):row.update(sequence=str(index),elapsed_ns=str(index))
                    with self.assertRaises(ValueError):host.verify_case(c)

    def test_periodic_stats_progress_and_flags(self):
        for how in ['term','kill']:
            c=fixture(how)
            periodic=copy.deepcopy(c['records'][-2 if how=='term' else -1]);periodic['final']=False
            rows=[]
            for count in ['0','1','2']:
                row=copy.deepcopy(periodic);row.update(attempted=count,emitted=count,decoded=count);rows.append(row)
            c['records'][2:2]=rows
            for index,row in enumerate(c['records']):row.update(sequence=str(index),elapsed_ns=str(index))
            host.verify_case(c)
            for key in ['attempted','emitted','decoded']:
                invalid=copy.deepcopy(c);invalid['records'][4][key]='0'
                with self.assertRaises(ValueError):host.verify_case(invalid)
            for flag in [None,1,'false']:
                invalid=copy.deepcopy(c);invalid['records'][2]['final']=flag
                with self.assertRaises(ValueError):host.verify_case(invalid)

    def test_term_final_stats_is_unique_and_immediately_before_shutdown(self):
        for layout in ['duplicate','event_after','periodic_after']:
            c=fixture();final=copy.deepcopy(c['records'][-2])
            if layout=='duplicate':c['records'].insert(-2,final)
            elif layout=='event_after':c['records'].insert(3,c['records'].pop(-2))
            else:
                periodic=copy.deepcopy(final);periodic['final']=False
                c['records'][-2:-2]=[final,periodic]
            for index,row in enumerate(c['records']):row.update(sequence=str(index),elapsed_ns=str(index))
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
