# SPDX-License-Identifier: MIT
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import warden_preflight as scenario
import warden_preflight_host as host
import warden_preflight_terminal as terminal


class Nonloading(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='arena-nonloading-proof-'))
        self.boot = '12345678-1234-1234-1234-123456789abc'
        self.result = {'schema': 'arena-warden-nonloading-v1', 'status': 'passed-nonloading',
                       'boot_id': self.boot, 'runtime_protection_verified': False, 'cases': []}
        self.result['package_manifest'] = {'schema': 'veil-warden-package-v1', 'system': 'aarch64-linux',
            'deployment_ready': False, 'sha256': {'bin/veil-warden': 'a' * 64, 'lib/veil-warden/veil-warden-ebpf': 'b' * 64}}
        for name, _, code, identity in scenario.CASES:
            self.result['cases'].append({'id': name, 'returncode': code})
            stdout = '' if identity is None else '\n'.join([
                'schema=veil-warden-preflight-v1', 'read_only=true', 'bpf_loaded=false',
                'apply_supported=false', 'deployment_ready=false', f'service_identity={identity}',
                'canonical_unit=arena-fixture.service']) + '\n'
            (self.root / f'{name}.stdout').write_text(stdout)
            (self.root / f'{name}.stderr').write_text('')
            (self.root / f'{name}.trace').write_text('123 +++ exited with ' + str(code) + ' +++\n')
        (self.root / 'guest-boot-id.txt').write_text(self.boot + '\n')
        (self.root / 'service-result.txt').write_text('success\n')

    def verify(self):
        (self.root / 'preflight-result.json').write_text(json.dumps(self.result))
        return scenario.verify(self.root)

    def test_complete_is_only_nonloading_acceptance(self):
        self.assertFalse(self.verify()['runtime_protection_verified'])

    def test_forbidden_syscalls_even_failed_attempts(self):
        for trace in ['bpf(BPF_PROG_LOAD, 0, 0) = -1 EPERM\n', 'socket(AF_INET, SOCK_STREAM, 0) = -1 EPERM\n',
                      'socket(AF_PACKET, SOCK_RAW, 0) = -1 EPERM\n', 'socket(AF_VSOCK, SOCK_STREAM, 0) = -1 EPERM\n', 'garbage', '']:
            with self.subTest(trace=trace), self.assertRaises(ValueError):
                scenario.verify_case(self.result['cases'][0], (self.root / 'running.stdout').read_text(), trace)

    def test_missing_contract_exit_and_boot_fail(self):
        original = copy.deepcopy(self.result)
        for bad in [True, 0, 3, -9]:
            self.result['cases'][0]['returncode'] = bad
            with self.assertRaises(ValueError): self.verify()
        self.result = original
        self.result['boot_id'] = 'other'
        with self.assertRaises(ValueError): self.verify()

    def test_malformed_or_incomplete_evidence(self):
        for result in [[], {}, dict(self.result, cases=[{}]), dict(self.result, cases=[None]),
                       dict(self.result, runtime_protection_verified=True), dict(self.result, package_manifest={})]:
            self.result = result
            with self.assertRaises(ValueError): self.verify()

    def test_bundle_type_and_seal_before_any_vm_operation(self):
        bundle = self.root / 'bundle'; bundle.mkdir()
        for name in host.FILES: (bundle / name).write_text('x')
        (bundle / 'toplevel').write_text('/nix/store/' + 'a' * 32 + '-nixos-system-arena-warden-preflight-test\n')
        (bundle / 'scenario').write_text('warden-nonloading-v1\n')
        def seal():
            (bundle / 'SHA256SUMS').write_text(''.join(host.sha(bundle / n) + '  ' + n + '\n' for n in host.FILES))
        seal()
        self.assertEqual(len(host.check_bundle(bundle)), 7)
        (bundle / 'scenario').write_text('warden-policy\n'); seal()
        with patch.object(host, 'execution') as execution, patch.object(host.os, 'getuid', return_value=1000):
            with self.assertRaises(ValueError): host.workflow(1000, bundle, self.root / 'output')
            execution.assert_not_called()

    def test_stop_requires_synced_reaped_success(self):
        good = {'status': 'passed', 'qemu_reaped': True, 'evidence_synced': True,
                'escalated': False, 'qemu_exit_code': 0}
        (self.root / 'vm-stop.json').write_text(json.dumps(good))
        host.verify_stop(self.root)
        for key, value in [('qemu_exit_code', True), ('escalated', True), ('qemu_reaped', False), ('evidence_synced', False)]:
            (self.root / 'vm-stop.json').write_text(json.dumps(dict(good, **{key: value})))
            with self.assertRaises(ValueError): host.verify_stop(self.root)

    def workflow(self, *, timeout=False, stop_bad=False):
        output = self.root / ('timeout-output' if timeout else 'workflow-output')
        process = Mock()
        process.wait.side_effect = __import__('subprocess').TimeoutExpired('controller', 150) if timeout else None
        process.wait.return_value = 0
        result = self.verify()
        with patch.object(host.os, 'getuid', return_value=1000), \
             patch.object(host, 'check_bundle', return_value={'seal': 'a'}), \
             patch.object(host, 'host_route', return_value={'arch': 'aarch64'}), \
             patch.object(host, 'read', return_value='aarch64-linux'), \
             patch.object(host, 'execution', return_value={'arch': 'aarch64', 'cpu': 'max', 'accelerator': 'tcg'}), \
             patch.object(host, 'available_memory_mib', return_value=8192), \
             patch.object(host, 'verify_stop', side_effect=ValueError('missing stop') if stop_bad else None), \
             patch.object(host, 'verify_terminal'), \
             patch.object(host, 'verify', return_value=result), \
             patch.object(host.subprocess, 'Popen', return_value=process) as launch, \
             patch.object(host, 'stop_controller', return_value=False) as stop:
            code = host.workflow(1000, self.root / 'bundle', output)
        return code, json.loads((output / 'summary.json').read_text()), launch, stop

    def test_host_records_no_nic_and_requires_stop(self):
        code, report, launch, stop = self.workflow()
        self.assertEqual(code, 0)
        self.assertEqual(report['status'], 'passed-nonloading')
        argv = launch.call_args.args[0]
        self.assertEqual(argv[argv.index('-nic') + 1], 'none')
        self.assertIn('readonly=on', ' '.join(argv))
        stop.assert_not_called()

    def test_timeout_is_failure_and_reaps_owned_controller(self):
        code, report, _, stop = self.workflow(timeout=True)
        self.assertEqual(code, 3)
        self.assertEqual(report['status'], 'failed')
        stop.assert_called_once()

    def test_guest_success_without_vm_stop_is_failure(self):
        code, report, _, _ = self.workflow(stop_bad=True)
        self.assertEqual(code, 3)
        self.assertEqual(report['status'], 'failed')

    def test_terminal_success_matches_controller_and_guest_paths(self):
        root = self.root / 'terminal-success'; root.mkdir(); (root / 'nonloading').mkdir()
        terminal.finish(root, 'success')
        host.verify_terminal(root)
        self.assertEqual((root / 'service-result.txt').read_text(), 'success\n')
        (root / 'nonloading/service-result.txt').write_text('exit-code\n')
        with self.assertRaises(ValueError): host.verify_terminal(root)

    def test_start_failure_without_diagnostic_directory_still_terminates(self):
        root = self.root / 'terminal-failure'; root.mkdir()
        terminal.finish(root, 'exit-code')
        self.assertEqual((root / 'service-result.txt').read_text(), 'exit-code\n')
        self.assertFalse(json.loads((root / 'terminal.json').read_text())['diagnostic_directory_present'])
        with self.assertRaises(ValueError): host.verify_terminal(root)

    def test_native_terminal_entry_logs_startup_failure_on_private_owned_output(self):
        import os
        import subprocess
        root = self.root / 'native-terminal'; root.mkdir(mode=0o700)
        self.assertEqual(root.stat().st_uid, os.getuid())
        script = Path(__file__).resolve().parents[1] / 'scripts/warden_preflight_terminal.py'
        result = subprocess.run([sys.executable, '-B', str(script), str(root)],
                                env=dict(os.environ, SERVICE_RESULT='exit-code'),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('nonloading terminal: exit-code', result.stderr)
        self.assertEqual((root / 'service-result.txt').read_text(), 'exit-code\n')
        with self.assertRaises(ValueError): host.verify_terminal(root)

    def test_terminal_marker_is_last_and_failed_publication_is_not_success(self):
        root = self.root / 'terminal-order'; root.mkdir(); (root / 'nonloading').mkdir()
        real = terminal.durable_new
        order = []
        def write(path, text):
            order.append(path.relative_to(root).as_posix())
            if path.name == 'terminal.json': raise OSError('simulated full filesystem')
            real(path, text)
        with patch.object(terminal, 'durable_new', side_effect=write):
            with self.assertRaises(OSError): terminal.finish(root, 'success')
        self.assertEqual(order, ['nonloading/service-result.txt', 'terminal.json'])
        self.assertFalse((root / 'service-result.txt').exists())

    def test_wrong_output_owner_is_rejected_before_publication(self):
        root = self.root / 'wrong-owner'; root.mkdir()
        with patch.object(terminal.os, 'geteuid', return_value=root.stat().st_uid + 1):
            with self.assertRaises(ValueError): terminal.finish(root, 'success')
        self.assertFalse((root / 'service-result.txt').exists())

    def test_terminal_rejects_overwrite_and_symlink(self):
        root = self.root / 'terminal-preserved'; root.mkdir(); (root / 'nonloading').mkdir()
        terminal.finish(root, 'success')
        with self.assertRaises(FileExistsError): terminal.finish(root, 'success')
        self.assertEqual((root / 'service-result.txt').read_text(), 'success\n')
        other = self.root / 'terminal-link'; other.mkdir()
        (other / 'nonloading').symlink_to(root / 'nonloading', target_is_directory=True)
        with self.assertRaises(ValueError): terminal.finish(other, 'success')
        with self.assertRaises(ValueError): terminal.finish(other, 'success\nexit-code')

    def test_guest_has_independent_scope_and_kernel_enforced_no_load(self):
        text = (Path(__file__).resolve().parents[1] / 'environments/vm/warden-preflight.nix').read_text()
        for fragment in ['~bpf', 'CapabilityBoundingSet = ""', 'AF_UNIX', '-nic none', 'arena-warden-preflight', 'mountHostNixStore = false']:
            self.assertIn(fragment, text)
        self.assertNotIn('autoPatchelf', text)
        for fragment in ['uid = hostUid;', 'User = "arena-diagnose"', 'journal+console', 'warden_preflight_terminal.py']:
            self.assertIn(fragment, text)
        self.assertNotIn('CAP_DAC_OVERRIDE', text)


if __name__ == '__main__': unittest.main()
