"""PR #23: fixture origin, whole-probe deadlines, and retained run failures."""
import contextlib
import errno
import time
import importlib.util
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import k3s_f2_guest as guest
import k3s_f2_host as host

spec = importlib.util.spec_from_file_location("arena_f2_fixture_review", SCRIPTS.parent / "tests/k3s/f2_fixture.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class FixtureOrigin(unittest.TestCase):
    context = dict(action="deny", protocol="tcp", address="10.42.0.9", nonce="a" * 32)

    def check(self, cp):
        return guest.probe_output(cp, "deny", **self.context)

    def test_transport_failure_without_fixture_evidence_is_environment_error(self):
        for stderr in [b"API server unavailable", b"pod not found", b"exec transport closed"]:
            with self.subTest(stderr=stderr), self.assertRaises(guest.Failure) as error:
                self.check(subprocess.CompletedProcess([], 1, b"", stderr))
            self.assertEqual(error.exception.code, 3)

    def test_executed_fixture_mismatch_is_validation_failure(self):
        output = io.StringIO()
        with patch.object(fixture, "probe", side_effect=fixture.ProbeMismatch("target TCP unexpectedly passed")), contextlib.redirect_stdout(output):
            code = fixture.probe_main("deny", "tcp", "10.42.0.9", "a" * 32)
        self.assertEqual(code, 1)
        with self.assertRaises(guest.Failure) as error:
            self.check(subprocess.CompletedProcess([], code, output.getvalue().encode(), b""))
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(error.exception.stage, "deny")
        self.assertEqual(error.exception.detail, "target TCP unexpectedly passed")

    def test_local_fixture_error_does_not_emit_mismatch(self):
        output = io.StringIO()
        with patch.object(fixture, "probe", side_effect=FileNotFoundError("missing executable")), contextlib.redirect_stdout(output):
            with self.assertRaises(FileNotFoundError):
                fixture.probe_main("deny", "tcp", "10.42.0.9", "a" * 32)
        self.assertEqual(output.getvalue(), "")

    def test_incomplete_wrong_identity_or_contradictory_frame_is_environment_error(self):
        valid = {"schema_version": 1, "kind": "arena-f2-probe-mismatch", "action": "deny", "protocol": "tcp",
                 "host": "10.42.0.9", "nonce": "a" * 32, "detail": "target unexpectedly passed"}
        cases = [(1, {}), (1, []), (1, {**valid, "nonce": "b" * 32}),
                 (1, {**valid, "host": "another"}), (1, {**valid, "action": "probe"}),
                 (1, {**valid, "protocol": "udp"}), (1, {**valid, "schema_version": 2}),
                 (1, {**valid, "detail": None}), (0, valid), (137, valid)]
        for code, frame in cases:
            with self.subTest(code=code, frame=frame), self.assertRaises(guest.Failure) as error:
                self.check(subprocess.CompletedProcess([], code, json.dumps(frame).encode(), b""))
            self.assertEqual(error.exception.code, 3)

    def test_successful_echo_and_expected_deny_remain_success(self):
        for row in [{"received": "echo", "count": 1}, {"outcome": "denied", "errno": 111}]:
            self.assertEqual(self.check(subprocess.CompletedProcess([], 0, json.dumps(row).encode(), b"")), row)


class ProbeDeadline(unittest.TestCase):
    def exercise(self, logs_elapsed=1, workflow_end=600, stalled=False, exec_elapsed=2, deny=False):
        clock = [0.0]
        ends = []
        payload = []
        def kube(args, end, required=True):
            ends.append(("exec", end, end - clock[0]))
            self.assertFalse(required)
            nonce = args[-1]
            payload.append(f"arena-f2:tcp:{nonce}")
            clock[0] += exec_elapsed
            row = {"protocol": "tcp", "host": "10.42.0.9", "addresses": ["10.42.0.9"], "payload": payload[0]}
            row.update({"outcome": "denied", "errno": errno.ECONNREFUSED} if deny else {"received": payload[0], "count": 1})
            return subprocess.CompletedProcess([], 0, json.dumps(row).encode(), b"")
        def logs(end):
            ends.append(("logs", end, end - clock[0]))
            if stalled:
                clock[0] = end
                raise guest.Failure("baseline", "log API exceeded deadline", 4 if end == workflow_end else 3)
            clock[0] += min(logs_elapsed, .01) if deny else logs_elapsed
            return [] if deny else [{"protocol": "tcp", "payload": payload[0]}]
        with patch.object(guest.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(guest.time, "sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0]+seconds)):
            try:
                row = guest.traffic_probe(kube, logs, ["python", "fixture"], "baseline", workflow_end,
                                          "client", "tcp", "10.42.0.9", deny)
                return row, ends, None
            except guest.Failure as error:
                return None, ends, error

    def test_exec_and_logs_share_one_absolute_five_second_budget(self):
        row, ends, error = self.exercise()
        self.assertIsNone(error)
        self.assertEqual(row["receiver_count"], 1)
        self.assertEqual(ends, [("exec", 5, 5), ("logs", 5, 3)])

    def test_stalled_logs_do_not_take_workflow_budget_or_retry(self):
        row, ends, error = self.exercise(stalled=True)
        self.assertIsNone(row)
        self.assertEqual(error.code, 3)
        self.assertEqual(ends, [("exec", 5, 5), ("logs", 5, 3)])

    def test_late_log_response_cannot_pass(self):
        row, ends, error = self.exercise(logs_elapsed=4)
        self.assertIsNone(row)
        self.assertEqual(error.code, 3)
        self.assertEqual(ends[-1], ("logs", 5, 3))

    def test_expired_exec_does_not_start_a_second_api(self):
        row, ends, error = self.exercise(exec_elapsed=5)
        self.assertIsNone(row)
        self.assertEqual(error.code, 3)
        self.assertEqual(ends, [("exec", 5, 5)])

    def test_shorter_workflow_deadline_is_retained(self):
        row, ends, error = self.exercise(workflow_end=3, stalled=True)
        self.assertIsNone(row)
        self.assertEqual(error.code, 4)
        self.assertEqual(ends, [("exec", 3, 3), ("logs", 3, 1)])

    def test_real_stalled_log_child_is_bounded_and_reaped(self):
        children = []
        ends = []
        def execute(argv, end):
            with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
                children.append(child)
                try:
                    out, err = child.communicate(timeout=max(0.001, end - time.monotonic()))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
                    raise guest.Failure("baseline", "owned dummy log timed out", 3) from None
                return subprocess.CompletedProcess(argv, child.returncode, out, err)
        def kube(args, end, required=True):
            ends.append(end)
            nonce = args[-1]
            payload = f"arena-f2:tcp:{nonce}"
            row = {"protocol": "tcp", "host": "10.42.0.9", "addresses": ["10.42.0.9"],
                   "payload": payload, "received": payload, "count": 1}
            return execute([sys.executable, "-c", "import time; time.sleep(.2); print(" + repr(json.dumps(row)) + ")"], end)
        def logs(end):
            ends.append(end)
            execute([sys.executable, "-c", "import time; time.sleep(30)"], end)
            self.fail("stalled log unexpectedly completed")
        with self.assertRaises(guest.Failure) as error:
            guest.traffic_probe(kube, logs, ["fixed"], "baseline", time.monotonic() + 600,
                                "client", "tcp", "10.42.0.9")
        self.assertEqual(error.exception.code, 3)
        self.assertEqual(len(ends), 2)
        self.assertEqual(ends[0], ends[1])
        self.assertTrue(all(child.poll() is not None for child in children))
        # A new, unrelated-to-the-stall owned child can still complete normally.
        self.assertEqual(subprocess.run([sys.executable, "-c", "print('ready')"], capture_output=True,
                                        timeout=5, check=True).stdout.strip(), b"ready")

    def test_expected_deny_retains_same_deadline_and_zero_receiver_count(self):
        row, ends, error = self.exercise(deny=True)
        self.assertIsNone(error)
        self.assertEqual(row["receiver_count"], 0)
        self.assertEqual(ends[0], ("exec", 5, 5))
        self.assertGreater(len(ends), 2)
        self.assertTrue(all(kind == "logs" and end == 5 for kind, end, remaining in ends[1:]))


class RunSummary(unittest.TestCase):
    def exercise(self, failure_code, failure_role, verified=False, bad_cleanup=False):
        expected_status = {1: "failed", 3: "environment_error", 4: "timeout", 130: "cancelled"}[failure_code]
        failed = {"status": expected_status, "exit_code": failure_code, "stage": "deny", "detail": "fixed guest failure"}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            inputs = {role: {"role": role, "scenario": "k3s-f2", "hostUid": 501, "system": "aarch64-linux",
                             "fixture_config_digest": "fixed"} for role in host.ROLES}
            children = {}
            original_exists = Path.exists
            def exists(path):
                return True if path.name == "lan.sock" else original_exists(path)
            def spawn(args, **kwargs):
                out = Path(args[5])
                role = out.name
                child = Mock()
                child.poll.return_value = 0 if verified or role == failure_role else None
                child.returncode = 0
                children[role] = child
                stop = {"qemu_reaped": True, "escalated": bad_cleanup and role == failure_role, "evidence_synced": True}
                (out / "vm-stop.json").write_text(json.dumps(stop))
                (out / "result.json").write_text(json.dumps(failed if role == failure_role else {"status": "passed", "exit_code": 0}))
                return child
            with patch.object(host, "check", side_effect=lambda p: (inputs[p.name], {"files": {}})), \
                    patch.object(host.os, "getuid", return_value=501), patch.object(host.os, "cpu_count", return_value=6), \
                    patch.object(host.platform, "machine", return_value="arm64"), \
                    patch.object(host, "available_memory_mib", return_value=30000), \
                    patch.object(host.shutil, "disk_usage", return_value=Mock(free=200 * 1024**3)), \
                    patch.object(host, "qemu_args", return_value=["owned-qemu"]), patch.object(host, "sha", return_value="fixed"), \
                    patch.object(host.subprocess, "run"), patch.object(host.subprocess, "Popen", side_effect=spawn), \
                    patch.object(Path, "exists", exists), \
                    patch.object(host, "verify_cluster", side_effect=host.GuestFailure(failure_role, failed)):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    code = host.workflow(501, {r: base / r for r in host.ROLES}, "daily", base / "output")
            summary = json.loads((base / "output" / "summary.json").read_text())
            for role, child in children.items():
                if not verified and role != failure_role:
                    child.send_signal.assert_called_once_with(signal.SIGTERM)
                else:
                    child.send_signal.assert_not_called()
                child.wait.assert_called_once_with(timeout=15)
            return code, summary, failed

    def test_guest_failure_fields_survive_monitor_and_post_verification_paths(self):
        for role in host.ROLES:
            for code in [1, 3, 4, 130]:
                for verified in [False, True]:
                    with self.subTest(role=role, code=code, verified=verified):
                        actual, summary, failed = self.exercise(code, role, verified)
                        run = summary["runs"][0]
                        self.assertEqual(actual, code)
                        self.assertEqual(summary["status"], failed["status"])
                        for key in ["status", "exit_code", "stage", "detail"]:
                            self.assertEqual(run[key], failed[key])
                            self.assertEqual(run["nodes"][role][key], failed[key])
                        self.assertEqual(run["failed_node"], role)
                        self.assertEqual(run["cleanup_errors"], [])

    def test_cleanup_failure_remains_environment_error_and_keeps_guest_cause(self):
        actual, summary, failed = self.exercise(1, "agent", bad_cleanup=True)
        run = summary["runs"][0]
        self.assertEqual(actual, 3)
        self.assertEqual(summary["status"], "environment_error")
        self.assertEqual(run["status"], "environment_error")
        self.assertEqual(run["exit_code"], 3)
        self.assertEqual(run["stage"], "cleanup")
        self.assertEqual(run["guest_failure"], {"role": "agent", **failed})


if __name__ == "__main__":
    unittest.main()
