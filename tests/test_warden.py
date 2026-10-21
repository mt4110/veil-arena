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
        self.record = {'schema_version': 1, 'scenario': 'warden-policy', 'status': 'passed',
            'cleanup_complete': True, 'boot_id': '00000000-0000-0000-0000-000000000001',
            'monitors': [{'links_restored': True}] * 3,
            'steps': [{'id': name} for name in warden_host.STEPS]}
        steps = self.record['steps']
        steps[0]['receiver_delta'] = [1, 1]
        steps[1].update(mutations_rejected=True, event={'decision': 'allow'})
        steps[2]['proofs'] = [{'receiver_delta': 0,
            'client': {'errno': errno.EPERM, 'scope': '/warden.slice/warden-test.slice/client.service', 'family': family},
            'event': {'decision': 'deny', 'policy_id': '1', 'family': event_family}}
            for family, event_family in [(2, '4'), (10, '6')]]
        for i in [3, 4, 6]: steps[i]['receiver_delta'] = 1
        steps[5]['client'] = {'errno': 0, 'scope': '/system.slice/client.service'}
        steps[7]['errno'] = 0
        for s, code in zip(steps[8:], [0, -9]):
            s.update(returncode=code, links_restored=True, control_endpoint_closed=True,
                     clients=[{'errno': 0}, {'errno': 0}])
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

    def workflow_fixture(self, mode, *, change_after_final=False, hup=False, cleanup_timeout=False):
        base = Path(tempfile.mkdtemp(prefix='workflow-', dir=self.root))
        bundle = base / 'bundle'; bundle.mkdir()
        (bundle / 'inputs.json').write_text('{"guest_system":"aarch64-linux"}')
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
        with patch.object(warden_host, 'execution', return_value={'arch': 'aarch64', 'cpu': 'max', 'accelerator': 'tcg'}), \
             patch.object(warden_host.os, 'getuid', return_value=1000), \
             patch.object(warden_host, 'available_memory_mib', return_value=8192), \
             patch.object(warden_host, 'check_bundle', side_effect=seal), \
             patch.object(warden_host, 'verify', side_effect=[{'boot_id': f'boot-{i}'} for i in range(count)]), \
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
