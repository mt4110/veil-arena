"""Ordinary host errors retain their stage and survive cleanup failures."""
import contextlib
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import k3s_f2_host as host


class HostErrors(unittest.TestCase):
    def exercise(self, failure, bad_cleanup=False):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            inputs = {role: dict(role=role, scenario='k3s-f2', hostUid=501,
                                system='aarch64-linux', fixture_config_digest='fixed') for role in host.ROLES}
            children = {}
            clock = [0.0]
            exists = Path.exists
            def socket_exists(path):
                if path.name == 'lan.sock':
                    return failure != 'lan'
                return exists(path)
            def spawn(args, **kwargs):
                role = Path(args[5]).name
                if failure == 'spawn' and role == 'agent':
                    raise OSError('owned agent launch failed')
                child = Mock(returncode=0)
                child.poll.return_value = 9 if failure == 'controller' and role == 'agent' else None
                children[role] = child
                (Path(args[5]) / 'vm-stop.json').write_text(json.dumps(dict(
                    qemu_reaped=True, escalated=bad_cleanup and role=='server', evidence_synced=True)))
                return child
            def image(*args, **kwargs):
                if failure == 'image':
                    raise subprocess.CalledProcessError(1, ['qemu-img'])
            def check(bundle):
                if failure == 'final' and len(children)==2:
                    raise ValueError('final immutable input check failed')
                return inputs[bundle.name], {'files': {}}
            def verify(*args):
                if failure == 'verify':
                    raise ValueError('cluster verification failed')
                return {'server': 'server-boot', 'agent': 'agent-boot'}
            def sleep(seconds):
                clock[0] += seconds
                if failure in {'verify', 'final', 'healthy'} and len(children)==2:
                    for child in children.values():
                        child.poll.return_value = 0
            with patch.object(host, 'check', side_effect=check), \
                    patch.object(host.os, 'getuid', return_value=501), \
                    patch.object(host.os, 'cpu_count', return_value=6), \
                    patch.object(host.platform, 'machine', return_value='arm64'), \
                    patch.object(host, 'available_memory_mib', return_value=30000), \
                    patch.object(host.shutil, 'disk_usage', return_value=Mock(free=200*1024**3)), \
                    patch.object(host, 'qemu_args', return_value=['owned-qemu']), \
                    patch.object(host, 'sha', return_value='fixed'), \
                    patch.object(host.subprocess, 'run', side_effect=image), \
                    patch.object(host.subprocess, 'Popen', side_effect=spawn), \
                    patch.object(host, 'verify_cluster', side_effect=verify), \
                    patch.object(Path, 'exists', socket_exists), \
                    patch.object(host.time, 'monotonic', side_effect=lambda:clock[0]), \
                    patch.object(host.time, 'sleep', side_effect=sleep):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = host.workflow(501, {r:base/r for r in host.ROLES}, 'daily', base/'output')
            summary = json.loads((base/'output/summary.json').read_text())
            for child in children.values():
                child.wait.assert_called_once_with(timeout=15)
                if child.poll.return_value is None:
                    child.send_signal.assert_called_once_with(signal.SIGTERM)
                else:
                    child.send_signal.assert_not_called()
            return code, summary, clock[0]

    def test_ordinary_errors_have_consistent_run_evidence(self):
        for failure, stage in [('image','node_startup'), ('spawn','node_startup'),
                               ('lan','lan_startup'), ('controller','running'),
                               ('verify','verification'), ('final','final_verification')]:
            with self.subTest(failure=failure):
                code, summary, elapsed = self.exercise(failure)
                run = summary['runs'][0]
                self.assertEqual(code, 3)
                self.assertEqual(summary['status'], 'environment_error')
                self.assertEqual(run['exit_code'], 3)
                self.assertEqual(run['status'], 'environment_error')
                self.assertEqual(run['stage'], stage)
                self.assertEqual(run['detail'], summary['detail'])
                self.assertEqual(run['host_failure']['detail'], summary['detail'])
                self.assertEqual(run['cleanup_errors'], [])
                if failure == 'lan':
                    self.assertGreaterEqual(elapsed, 5)
                    self.assertLess(elapsed, 5.0251)

    def test_cleanup_failure_wins_and_keeps_original_error(self):
        for failure in ['spawn', 'lan', 'controller', 'verify']:
            with self.subTest(failure=failure):
                code, summary, _ = self.exercise(failure, bad_cleanup=True)
                run = summary['runs'][0]
                self.assertEqual(code, 3)
                self.assertEqual(run['stage'], 'cleanup')
                self.assertEqual(run['exit_code'], 3)
                self.assertEqual(run['detail'], summary['detail'])
                self.assertIn('abnormal QEMU/evidence recovery', run['detail'])
                self.assertNotEqual(run['host_failure']['stage'], 'cleanup')
                self.assertNotEqual(run['host_failure']['detail'], run['detail'])

    def test_next_healthy_run_has_no_failure_fields(self):
        self.exercise('spawn')
        code, summary, _ = self.exercise('healthy')
        self.assertEqual(code, 0)
        self.assertEqual(summary['status'], 'passed')
        self.assertEqual(summary['runs'][0]['status'], 'passed')
        self.assertNotIn('host_failure', summary['runs'][0])
        self.assertNotIn('detail', summary['runs'][0])


if __name__ == '__main__':
    unittest.main()
