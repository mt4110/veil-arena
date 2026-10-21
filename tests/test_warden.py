"""Reject false pass evidence: network errors are not proof of policy denial."""
import copy
import errno
import json
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
            'client': {'errno': errno.EPERM, 'scope': '/warden.slice/warden-test.slice/client.service'},
            'event': {'decision': 'deny', 'policy_id': '1'}} for _ in range(2)]
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
