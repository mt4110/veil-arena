"""Real host workflow binary with dummy launchers; no VM, sudo, BPF or NIC."""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "scripts"
LAUNCHER = r'''
import hashlib, json, os, pathlib, subprocess, sys
fixture = sys.argv[1] == '--watchdog-fixture'
if fixture:
    del sys.argv[1]
if sys.argv[1] == '--describe':
    print(json.dumps({'scenario': 'netns-batch', 'watchdog_fixture_supported': True}))
    sys.exit(0)
root = pathlib.Path(sys.argv[1])
root.mkdir()
kind = pathlib.Path(__file__).with_name('kind').read_text()
def write(name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
child = subprocess.Popen([sys.executable, '-c', 'pass'])
child.wait()
write('owned-child.json', {'pid': child.pid, 'launcher_pid': os.getpid(), 'reaped': True})
write('vm-stop.json', {'status': 'passed', 'reason': 'service_completed',
      'qemu_exit_code': 0, 'escalated': False, 'evidence_synced': True,
      'evidence_synced_before_stop': True, 'qemu_reaped': True})
(root / 'serial.log').write_text('dummy guest output')
codes = [0, 1, 0, 0, 1, 3, 3, 0, 3, 0, 0, 0, 0, 0, 3, 0]
names = ['normal-001', 'route-fault-001', 'normal-002', 'tc-ingress-001',
 'tc-drop-disabled-001', 'tc-wrong-hook-001', 'tc-receiver-stopped-001',
 'bpf-test-run-001', 'bpf-load-failure-001', 'batch-cancel-001',
 'tc-after-cancel-001', 'batch-timeout-001', 'tc-restored-001',
 'tc-netem-loss-001', 'tc-netem-unconfigured-001', 'tc-after-netem-001']
cases = [{'id': name, 'status': 'passed', 'expected_exit_code': code,
          'report': name + '/result.json', 'execution': {'exit_code': code}}
         for name, code in zip(names, codes)]
exit_code = 0
if kind in ('case', 'timeout'):
    cases[12].update(status='failed')
    cases[12]['execution'] = {'exit_code': 3, 'status': 'timeout' if kind == 'timeout' else 'failed', 'duration_ms': 4000}
    write('netns-batch/tc-restored-001/result.json', {'detail': 'dummy failure', 'steps': []})
    exit_code = 1 if kind == 'case' else 3
if not fixture:
    write('netns-batch/result.json', {'schema_version': 7, 'scenario': 'netns-batch',
          'status': 'passed' if exit_code == 0 else 'failed', 'exit_code': exit_code,
          'vm_boots': 1, 'cases': cases})
write('vm-manifest.json', {'scenario': 'netns-batch', 'watchdog_fixture_supported': True})
(root / 'guest-boot-id.txt').write_text('12345678-1234-1234-1234-123456789abc' if fixture else '12345678-1234-1234-1234-123456789abd')
(root / 'boot-inputs.sha256').write_text('dummy boot inputs')
if not fixture:
    (root / 'service-result.txt').write_text('success')
else:
    write('vm-stop.json', {'schema_version': 2, 'status': 'environment_error',
          'reason': 'startup_timeout' if kind == 'watchdog-wrong-stop' else 'watchdog',
          'lifecycle_exit_code': 3, 'qemu_exit_code': 0, 'qemu_reaped': True,
          'escalated': False, 'evidence_synced': True, 'evidence_synced_before_stop': True,
          'sync_error': None, 'watchdog_fixture': True, 'checkpoint_seen': True})
    write('watchdog-ready.json', {'state': 'T', 'batch_pid': 42})
    write('netns-batch/batch-progress.json', {'status': 'incomplete', 'runner_pid': 42, 'cases': []})
    if kind == 'watchdog-ok':
        write('watchdog-verification.json', {'status': 'passed', 'scenario': 'netns-watchdog'})
    exit_code = 3
if kind == 'vm':
    write('vm-stop.json', {'status': 'environment_error', 'reason': 'watchdog'})
    exit_code = 3
if kind == 'malformed':
    (root / 'netns-batch/result.json').write_text('broken json')
    exit_code = 3
if kind == 'missing':
    # No summary is produced by the launcher; controller still saves its own.
    # Report references a missing child, while the case failure is retained.
    cases[12].update(status='failed', report='missing/result.json')
    cases[12]['execution'] = {'exit_code': 3, 'status': 'failed'}
    write('netns-batch/result.json', {'scenario': 'netns-batch', 'cases': cases})
    exit_code = 3
# Snapshot before the reader starts, including mtime; ledger outside evidence.
ledger = {str(p.relative_to(root)): [hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns]
          for p in root.rglob('*') if p.is_file()}
root.with_suffix('.ledger.json').write_text(json.dumps(ledger))
sys.exit(exit_code)
'''


class AutomaticDiagnosisTests(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="arena-auto-diagnosis-"))
        self.launcher = self.root / "launcher"
        self.launcher.write_text("#!" + sys.executable + "\n" + LAUNCHER)
        self.launcher.chmod(0o700)
        self.binary = os.environ["ARENA_TEST_WORKFLOW"]

    def run_workflow(self, kind, reader=None, python=None, mode='daily'):
        (self.root / "kind").write_text(kind)
        output = self.root / (kind + "-" + str(len(list(self.root.iterdir()))))
        command = [self.binary, str(self.launcher), mode, str(output), "--diagnose",
                   python or sys.executable, str(reader or SCRIPTS / "diagnose_netns.py")]
        result = subprocess.run(command, capture_output=True, text=True, timeout=12)
        run = output / ('daily-watchdog' if mode.startswith('watchdog-') else 'daily')
        ledger = json.loads(run.with_suffix('.ledger.json').read_text())
        actual = {str(p.relative_to(run)): [hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns]
                  for p in run.rglob("*") if p.is_file()}
        self.assertEqual(ledger, actual, "reader modified evidence")
        child = json.loads((run / "owned-child.json").read_text())
        self.assertTrue(child['reaped'])
        with self.assertRaises(ProcessLookupError):
            os.kill(child['pid'], 0)
        return result, output

    def test_expected_watchdog_stop_with_missing_verification_is_not_vm_failure(self):
        for mode in ('watchdog-daily', 'watchdog-acceptance'):
            with self.subTest(mode=mode):
                result, output = self.run_workflow('watchdog-missing', mode=mode)
                self.assertEqual(result.returncode, 3)
                self.assertIn('Kind: watchdog_evidence_verification_failure', result.stdout)
                self.assertNotIn('Kind: vm_lifecycle_failure', result.stdout)
                self.assertIn('watchdog-verification.json', result.stdout)
                run = json.loads((output / 'summary.json').read_text())['runs'][0]
                self.assertIs(run['watchdog_fixture'], True)
                self.assertEqual(run['expected_exit_code'], 3)

    def test_unexpected_watchdog_stop_remains_vm_failure(self):
        result, _ = self.run_workflow('watchdog-wrong-stop', mode='watchdog-daily')
        self.assertEqual(result.returncode, 3)
        self.assertIn('Kind: vm_lifecycle_failure', result.stdout)
        self.assertIn('startup_timeout', result.stdout)

    def test_verified_watchdog_and_normal_recovery_do_not_run_diagnosis(self):
        result, _ = self.run_workflow('watchdog-ok', mode='watchdog-daily')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(result.stderr, '')

    def test_success_is_concise_and_does_not_start_reader(self):
        reader = self.root / "forbidden.py"
        reader.write_text("raise Exception('must not run')")
        result, _ = self.run_workflow('success', reader)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertEqual(result.stderr, '')

    def test_case_and_timeout_are_diagnosed_then_recover(self):
        for kind, code, label in [('case', 1, 'case_failed'), ('timeout', 3, 'batch_supervisor_timeout')]:
            result, output = self.run_workflow(kind)
            self.assertEqual(result.returncode, code)
            self.assertIn('tc-restored-001', result.stdout)
            self.assertIn('Kind: ' + label, result.stdout)
            self.assertIn('Exit: 3 / expected 0', result.stdout)
            self.assertIn('dummy failure', result.stdout)
            self.assertIn(str(output / 'daily/vm-stop.json'), result.stdout)
        recovered, _ = self.run_workflow('success')
        self.assertEqual(recovered.returncode, 0)

    def test_vm_failure_and_missing_child_are_explicit(self):
        result, _ = self.run_workflow('vm')
        self.assertEqual(result.returncode, 3)
        self.assertIn('Kind: vm_lifecycle_failure', result.stdout)
        result, _ = self.run_workflow('missing')
        self.assertEqual(result.returncode, 3)
        self.assertIn('Missing evidence:', result.stdout)

    def test_malformed_evidence_does_not_hide_failure(self):
        result, _ = self.run_workflow('malformed')
        self.assertEqual(result.returncode, 3)
        self.assertIn('diagnosis unavailable:', result.stderr)
        self.assertIn('original workflow exit 3 retained', result.stderr)

    def test_reader_starts_only_after_launcher_reaped(self):
        reader = self.root / 'check_order.py'
        reader.write_text(
            'import json, os, pathlib, runpy, sys\n'
            'pid = json.loads((pathlib.Path(sys.argv[1]) / "daily/owned-child.json").read_text())["launcher_pid"]\n'
            'try:\n os.kill(pid, 0)\nexcept ProcessLookupError:\n pass\nelse:\n sys.exit(9)\n'
            'runpy.run_path(' + repr(str(SCRIPTS / 'diagnose_netns.py')) + ', run_name="__main__")\n')
        result, _ = self.run_workflow('case', reader)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Kind: case_failed', result.stdout)
        self.assertNotIn('diagnosis unavailable:', result.stderr)

    def test_unavailable_and_crashed_reader_keep_original_exit(self):
        result, _ = self.run_workflow('case', python=str(self.root / 'no-python'))
        self.assertEqual(result.returncode, 1)
        self.assertIn('diagnosis unavailable:', result.stderr)
        reader = self.root / 'crash.py'
        reader.write_text('import sys; sys.exit(7)')
        result, _ = self.run_workflow('case', reader)
        self.assertEqual(result.returncode, 1)
        self.assertIn('reader exited', result.stderr)

    def test_reader_timeout_is_reaped_and_recovery_passes(self):
        reader = self.root / 'hang.py'
        pidfile = self.root / 'reader.pid'
        reader.write_text('import os, pathlib, time\npathlib.Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid()))\ntime.sleep(60)')
        result, _ = self.run_workflow('case', reader)
        self.assertEqual(result.returncode, 1)
        self.assertIn('reader exceeded 5s; stopped and reaped', result.stderr)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pidfile.read_text()), 0)
        recovered, _ = self.run_workflow('success')
        self.assertEqual(recovered.returncode, 0)

    def test_existing_output_is_not_diagnosed_or_overwritten(self):
        output = self.root / 'existing'
        output.mkdir()
        summary = output / 'summary.json'
        summary.write_text('owner evidence')
        result = subprocess.run([self.binary, str(self.launcher), 'daily', str(output),
                                 '--diagnose', sys.executable, str(SCRIPTS / 'diagnose_netns.py')],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(summary.read_text(), 'owner evidence')
        self.assertIn('workflow did not complete', result.stderr)
        self.assertNotIn('Recorded workflow:', result.stdout)


if __name__ == '__main__':
    # CI's build container starts as root; exercise the real ordinary-user CLI
    # without relaxing its production privilege check.
    if os.geteuid() == 0:
        os.setgroups([])
        os.setgid(65534)
        os.setuid(65534)
    unittest.main()
