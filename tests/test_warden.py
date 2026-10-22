"""Reject false pass evidence: network errors are not proof of policy denial."""
import copy
import errno
import json
import signal
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import warden_host


class WardenEvidence(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='arena-warden-proof-'))
        self.record = {'schema_version': 3, 'scenario': 'warden-policy', 'status': 'passed',
            'cleanup_complete': True, 'boot_id': '00000000-0000-0000-0000-000000000001',
            'monitors': [{'links_restored': True}] * 3,
            'steps': [{'id': name} for name in warden_host.STEPS]}
        steps = self.record['steps']
        steps[0]['receiver_delta'] = [1, 1]
        steps[1].update(mutations_rejected=True, event={'decision': 'allow'})
        steps[2]['proofs'] = [{'receiver_delta': 0,
            'client': {'errno': errno.EPERM, 'scope': '/warden.slice/warden-test.slice/client.service', 'family': family,
                       'pid': 747, 'address': address, 'port': 39889, 'protocol': 'tcp'},
            'event': {'decision': 'deny', 'policy_id': '1', 'family': event_family,
                      'tgid': '747', 'destination': destination, 'protocol': 'tcp'}}
            for family, event_family, address, destination in [
                (2, '4', '127.0.0.1', '127.0.0.1:39889'),
                (10, '6', '::1', '[::1]:39889')]]
        steps[1].update(errno=0, client=dict(steps[2]['proofs'][0]['client'], errno=0),
                        event=dict(steps[2]['proofs'][0]['event'], decision='allow'))
        for i in [3, 4, 6]: steps[i]['receiver_delta'] = 1
        steps[0].update(errno=0, families=[4, 6], clients=[dict(p['client'], errno=0) for p in steps[2]['proofs']])
        steps[4]['client'] = dict(steps[2]['proofs'][0]['client'], errno=0, protocol='udp')
        steps[6]['client'] = dict(steps[2]['proofs'][0]['client'], errno=0, address='127.0.0.2')
        before = dict(steps[2]['proofs'][0]['client'], errno=0, local_address='127.0.0.1',
                      local_port=45000, connected_ns=1, exchanged_ns=2, sequence=1)
        steps[3].update(before=before, after=dict(before, exchanged_ns=4, sequence=2), rules_added_ns=3)
        steps[5]['client'] = dict(steps[2]['proofs'][0]['client'], errno=0, scope='/system.slice/client.service')
        steps[7].update(errno=0, families=[4, 6], clients=[dict(p['client'], errno=0) for p in steps[2]['proofs']])
        for s, code in zip(steps[8:], [0, -9]):
            s.update(returncode=code, links_restored=True, control_endpoint_closed=True,
                     clients=[dict(p['client'], errno=0) for p in steps[2]['proofs']])
        self.stop = {'status': 'passed', 'qemu_reaped': True, 'evidence_synced': True,
                     'escalated': False, 'qemu_exit_code': 0}
        (self.root / 'service-result.txt').write_text('success\n')
        (self.root / 'guest-boot-id.txt').write_text(self.record['boot_id'])

    def verify(self):
        (self.root / 'warden-result.json').write_text(json.dumps(self.record))
        (self.root / 'vm-stop.json').write_text(json.dumps(self.stop))
        return warden_host.verify(self.root)

    def test_complete_evidence(self):
        self.assertEqual(self.verify()['status'], 'passed')

    def test_timeout_or_received_data_is_not_denial(self):
        original = copy.deepcopy(self.record)
        for change in ['timeout', 'received', 'outside']:
            self.record = copy.deepcopy(original)
            proof = self.record['steps'][2]['proofs'][0]
            if change == 'timeout': proof['client']['errno'] = errno.ETIMEDOUT
            if change == 'received': proof['receiver_delta'] = 1
            if change == 'outside': proof['client']['scope'] = '/system.slice/client.service'
            with self.subTest(change=change), self.assertRaises(ValueError): self.verify()

    def test_missing_stop_recovery_or_vm_cleanup_fails(self):
        self.record['steps'].pop()
        with self.assertRaises(ValueError): self.verify()
        self.setUp(); self.stop['qemu_reaped'] = False
        with self.assertRaises(ValueError): self.verify()

    def test_changed_boot_identity_fails(self):
        (self.root / 'guest-boot-id.txt').write_text('another boot')
        with self.assertRaises(ValueError): self.verify()

    def test_deny_requires_one_matching_proof_per_family(self):
        original = copy.deepcopy(self.record)
        for case in ['ipv4-twice', 'ipv6-twice', 'mismatched-event']:
            self.record = copy.deepcopy(original)
            proofs = self.record['steps'][2]['proofs']
            if case == 'ipv4-twice': proofs[1] = copy.deepcopy(proofs[0])
            if case == 'ipv6-twice': proofs[0] = copy.deepcopy(proofs[1])
            if case == 'mismatched-event': proofs[1]['event']['family'] = '4'
            with self.subTest(case=case), self.assertRaises(ValueError): self.verify()

    def test_malformed_policy_id_is_failed_evidence(self):
        original = copy.deepcopy(self.record)
        for value in [None, [], {}, True, 1.5, 0, -1, 'invalid', '9' * 30, 2**32]:
            self.record = copy.deepcopy(original)
            self.record['steps'][2]['proofs'][0]['event']['policy_id'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.verify()

    def test_deny_event_must_identify_tested_client(self):
        original = copy.deepcopy(self.record)
        for index in [0, 1]:
            for key, value in [('tgid', '748'), ('destination', '127.0.0.2:39889'),
                               ('destination', '[::1]:39890'), ('protocol', 'udp')]:
                self.record = copy.deepcopy(original)
                self.record['steps'][2]['proofs'][index]['event'][key] = value
                with self.subTest(index=index, key=key, value=value), self.assertRaises(ValueError):
                    self.verify()
        for key, value in [('pid', True), ('port', 0), ('port', '39889'),
                           ('address', 'invalid'), ('address', '::1')]:
            self.record = copy.deepcopy(original)
            self.record['steps'][2]['proofs'][0]['client'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.verify()

    def test_malformed_evidence_shapes_are_value_errors(self):
        original, stop = copy.deepcopy(self.record), copy.deepcopy(self.stop)
        paths = [(), ('steps',), ('steps', 1), ('steps', 1, 'event'),
                 ('steps', 2, 'proofs'), ('steps', 2, 'proofs', 0),
                 ('steps', 2, 'proofs', 0, 'client'), ('steps', 2, 'proofs', 0, 'event'),
                 ('steps', 2, 'proofs', 0, 'client', 'scope'), ('steps', 5, 'client'),
                 ('steps', 5, 'client', 'scope'), ('steps', 8, 'clients'),
                 ('steps', 8, 'clients', 0), ('monitors',), ('monitors', 0), ('boot_id',)]
        for path in paths:
            for value in [None, [], {}, True, 7]:
                self.record = copy.deepcopy(original)
                if not path: self.record = value
                else:
                    parent = self.record
                    for key in path[:-1]: parent = parent[key]
                    parent[path[-1]] = value
                with self.subTest(path=path, value=value), self.assertRaises(ValueError): self.verify()
        self.record = original
        for value in [None, [], True, 'invalid']:
            self.stop = value
            with self.subTest(stop=value), self.assertRaises(ValueError): self.verify()
        self.stop = stop

    def test_malformed_result_marks_workflow_failed(self):
        code, report, _ = self.workflow_fixture('daily', malformed_result=True)
        self.assertEqual(code, 3)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('evidence object', report['error'])

    def test_numeric_evidence_rejects_booleans_and_floats(self):
        original = copy.deepcopy(self.record)
        paths = [('schema_version',), ('steps', 0, 'receiver_delta', 0),
                 ('steps', 2, 'proofs', 0, 'receiver_delta'),
                 ('steps', 2, 'proofs', 0, 'client', 'errno'),
                 ('steps', 2, 'proofs', 0, 'client', 'family'),
                 ('steps', 3, 'receiver_delta'), ('steps', 5, 'client', 'errno'),
                 ('steps', 7, 'errno'), ('steps', 8, 'returncode'),
                 ('steps', 8, 'clients', 0, 'errno')]
        for path in paths:
            for value in [True, False, 0.0, 1.0, 2.0]:
                self.record = copy.deepcopy(original)
                parent = self.record
                for key in path[:-1]: parent = parent[key]
                parent[path[-1]] = value
                with self.subTest(path=path, value=value), self.assertRaises(ValueError): self.verify()
        self.record = original
        for value in [False, 0.0]:
            self.stop['qemu_exit_code'] = value
            with self.subTest(stop=value), self.assertRaises(ValueError): self.verify()

    def test_stop_recovery_requires_both_original_destinations_in_scope(self):
        original = copy.deepcopy(self.record)
        for index in [0, 7, 8, 9]:
            for change in ['ipv4-twice', 'ipv6-twice', 'family', 'address', 'port', 'scope']:
                self.record = copy.deepcopy(original)
                clients = self.record['steps'][index]['clients']
                if change == 'ipv4-twice': clients[1] = copy.deepcopy(clients[0])
                elif change == 'ipv6-twice': clients[0] = copy.deepcopy(clients[1])
                else:
                    clients[0][change] = {'family': 10, 'address': '127.0.0.2', 'port': 12345,
                                          'scope': '/system.slice/client.service'}[change]
                with self.subTest(index=index, change=change), self.assertRaises(ValueError): self.verify()

    def test_input_manifest_must_be_object(self):
        for value in [None, [], True, 'invalid', 1]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.workflow_fixture('daily', manifest=value)

    def test_observe_success_must_match_client_and_event(self):
        original = copy.deepcopy(self.record)
        changes = [('errno', errno.EPERM), ('client', 'errno', errno.EPERM),
                   ('event', 'tgid', '999'), ('event', 'destination', '127.0.0.2:39889'),
                   ('event', 'protocol', 'udp'), ('client', 'scope', '/system.slice/client.service')]
        for change in changes:
            self.record = copy.deepcopy(original)
            step = self.record['steps'][1]
            if len(change) == 2: step[change[0]] = change[1]
            else: step[change[0]][change[1]] = change[2]
            with self.subTest(change=change), self.assertRaises(ValueError): self.verify()

    def test_remove_requires_recorded_families_and_clients(self):
        original = copy.deepcopy(self.record)
        for value in [None, [], [4, 4], [6, 6]]:
            self.record = copy.deepcopy(original)
            self.record['steps'][7]['families'] = value
            with self.subTest(value=value), self.assertRaises(ValueError): self.verify()
        self.record = original
        del self.record['steps'][7]['clients']
        with self.assertRaises(ValueError): self.verify()

    def test_baseline_requires_both_families_success_and_endpoints(self):
        original = copy.deepcopy(self.record)
        for key, value in [('families', None), ('families', [4, 4]), ('errno', errno.EPERM), ('clients', [])]:
            self.record = copy.deepcopy(original)
            self.record['steps'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.verify()

    def test_pass_through_clients_must_match_protocol_scope_and_destination(self):
        original = copy.deepcopy(self.record)
        for index in [4, 5, 6]:
            for key, value in [('address', '127.0.0.3'), ('port', 12345), ('family', 10),
                               ('protocol', 'udp' if index != 4 else 'tcp'), ('pid', None), ('errno', errno.EPERM)]:
                self.record = copy.deepcopy(original)
                self.record['steps'][index]['client'][key] = value
                with self.subTest(index=index, key=key), self.assertRaises(ValueError): self.verify()
        self.record = copy.deepcopy(original)
        self.record['steps'][6]['client']['address'] = '127.0.0.1'
        with self.assertRaises(ValueError): self.verify()

    def test_existing_connection_requires_same_socket_before_and_after_rules(self):
        original = copy.deepcopy(self.record)
        for key, value in [('local_port', 45001), ('local_port', 45000.0), ('pid', 748),
                           ('connected_ns', 3), ('sequence', 1), ('protocol', 'udp'), ('errno', errno.EPERM)]:
            self.record = copy.deepcopy(original)
            self.record['steps'][3]['after'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): self.verify()
        self.record = copy.deepcopy(original)
        self.record['steps'][3]['rules_added_ns'] = 5
        with self.assertRaises(ValueError): self.verify()

    def workflow_fixture(self, mode, *, change_after_final=False, hup=False, cleanup_timeout=False, malformed_result=False, manifest=...):
        base = Path(tempfile.mkdtemp(prefix='workflow-', dir=self.root))
        bundle = base / 'bundle'; bundle.mkdir()
        (bundle / 'inputs.json').write_text(json.dumps(
            {'guest_system': 'aarch64-linux'} if manifest is Ellipsis else manifest))
        (bundle / 'toplevel').write_text('/nix/store/' + 'a' * 32 + '-nixos-system-veil-warden-sandbox-test\n')
        (bundle / 'kernel-params').write_text('arena.warden=1 root=fstab\n')
        count = 2 if mode == 'acceptance' else 1
        check_count = 0
        def seal(_bundle):
            nonlocal check_count
            check_count += 1
            return {'Image': 'changed'} if change_after_final and check_count > count + 1 else {}
        proc = Mock(); proc.poll.return_value = None; proc.pid = 123456
        waits = 0
        def wait(**kwargs):
            nonlocal waits
            waits += 1
            if hup and waits == 1: signal.raise_signal(signal.SIGHUP)
            if cleanup_timeout and waits in [1, 2]: raise subprocess.TimeoutExpired('controller', 15)
            return 0
        proc.wait.side_effect = wait
        verifier = warden_host.verify
        def verify_result(root):
            (root / 'warden-result.json').write_text('[]')
            return verifier(root)
        verification = verify_result if malformed_result else [{'boot_id': f'boot-{i}'} for i in range(count)]
        with patch.object(warden_host, 'execution', return_value={'arch': 'aarch64', 'cpu': 'max', 'accelerator': 'tcg'}), \
             patch.object(warden_host.os, 'getuid', return_value=1000), \
             patch.object(warden_host, 'available_memory_mib', return_value=8192), \
             patch.object(warden_host, 'check_bundle', side_effect=seal), \
             patch.object(warden_host, 'verify', side_effect=verification), \
             patch.object(warden_host.subprocess, 'Popen', return_value=proc):
            code = warden_host.workflow(1000, bundle, mode, base / 'output')
        return code, json.loads((base / 'output/summary.json').read_text()), proc

    def test_final_bundle_mutation_fails_daily_and_acceptance(self):
        for mode in ['daily', 'acceptance']:
            with self.subTest(mode=mode):
                code, report, _ = self.workflow_fixture(mode, change_after_final=True)
                self.assertEqual(code, 3)
                self.assertEqual(report['status'], 'failed')
                self.assertIn('bundle', report['error'])

    def test_sighup_terminates_and_waits_for_owned_controller(self):
        handlers = warden_host.install_signal_handlers()
        try:
            code, report, proc = self.workflow_fixture('daily', hup=True)
        finally:
            for signum, handler in handlers.items(): signal.signal(signum, handler)
        self.assertEqual(code, 130)
        self.assertEqual(report['status'], 'cancelled')
        proc.terminate.assert_called_once_with()
        self.assertEqual([c.kwargs for c in proc.wait.call_args_list], [{'timeout': 660}, {'timeout': 15}])

    def test_stalled_cleanup_kills_owned_group_and_reports_failure(self):
        for hup in [False, True]:
            handlers = warden_host.install_signal_handlers()
            try:
                with self.subTest(hup=hup), patch.object(warden_host.os, 'killpg') as kill_group:
                    code, report, proc = self.workflow_fixture('daily', hup=hup, cleanup_timeout=True)
                    self.assertEqual(code, 3)
                    self.assertEqual(report['status'], 'failed')
                    self.assertTrue(report['interruption_cleanup']['escalated'])
                    kill_group.assert_called_once_with(proc.pid, signal.SIGKILL)
                    self.assertEqual([c.kwargs for c in proc.wait.call_args_list],
                                     [{'timeout': 660}, {'timeout': 15}, {'timeout': 5}])
            finally:
                for signum, handler in handlers.items(): signal.signal(signum, handler)

    def test_boot_uses_init_executable_and_no_external_nic(self):
        bundle = self.root / 'bundle'; bundle.mkdir()
        (bundle / 'inputs.json').write_text('{"guest_system":"aarch64-linux"}')
        system = '/nix/store/' + 'a' * 32 + '-nixos-system-veil-warden-sandbox-test'
        (bundle / 'toplevel').write_text(system + '\n')
        (bundle / 'kernel-params').write_text('arena.warden=1 root=fstab\n')
        proc = Mock(); proc.wait.return_value = 0
        with patch.object(warden_host, 'execution', return_value={'arch': 'aarch64', 'cpu': 'max', 'accelerator': 'tcg'}), \
             patch.object(warden_host.os, 'getuid', return_value=1000), \
             patch.object(warden_host, 'available_memory_mib', return_value=8192), \
             patch.object(warden_host, 'check_bundle', return_value={}), \
             patch.object(warden_host, 'verify', return_value={'boot_id': self.record['boot_id']}), \
             patch.object(warden_host.subprocess, 'Popen', return_value=proc) as launch:
            self.assertEqual(warden_host.workflow(1000, bundle, 'daily', self.root / 'launch'), 0)
        args = launch.call_args.args[0]
        self.assertTrue(launch.call_args.kwargs['start_new_session'])
        self.assertIn('init=' + system + '/init ', args[args.index('-append') + 1])
        self.assertEqual(args[args.index('-nic') + 1], 'none')

    def test_changed_boot_input_is_rejected(self):
        bundle = self.root / 'sealed'; bundle.mkdir()
        for name in warden_host.FILES:
            (bundle / name).write_text('fixture')
        (bundle / 'toplevel').write_text('/nix/store/' + 'a' * 32 + '-nixos-system-veil-warden-sandbox-test')
        (bundle / 'SHA256SUMS').write_text(''.join(
            warden_host.sha(bundle / name) + '  ' + name + '\n' for name in warden_host.FILES))
        self.assertEqual(len(warden_host.check_bundle(bundle)), 6)
        (bundle / 'Image').write_text('changed')
        with self.assertRaises(ValueError): warden_host.check_bundle(bundle)


if __name__ == '__main__': unittest.main()
