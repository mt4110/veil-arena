import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))
import diagnose_netns as module


class DiagnosisTests(unittest.TestCase):
    def directory(self):
        return pathlib.Path(tempfile.mkdtemp(prefix="arena-diagnosis-"))

    def write(self, root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def fixture(self, execution_status="failed", include_detail=True):
        root = self.directory()
        self.write(root, "summary.json", {
            "scenario": "netns-workflow", "status": "environment_error", "detail": "daily failed",
            "runs": [{"id": "daily", "output": "daily", "status": "failed"}],
        })
        self.write(root, "daily/netns-batch/result.json", {
            "scenario": "netns-batch", "cases": [
                {"id": "route-fault-001", "status": "passed", "expected_exit_code": 1,
                 "execution": {"status": "failed", "exit_code": 1, "duration_ms": 1500}},
                {"id": "tc-restored-001", "status": "failed", "expected_exit_code": 0,
                 "report": "tc-restored-001/result.json",
                 "execution": {"status": execution_status, "exit_code": 3, "duration_ms": 4000,
                               "stdout_log": "tc-restored-001.stdout.log", "stderr_log": "tc-restored-001.stderr.log"}},
                {"id": "tc-netem-loss-001", "status": "not_run"},
            ],
        })
        if include_detail:
            self.write(root, "daily/netns-batch/tc-restored-001/result.json", {
                "detail": "echo helper readiness timeout/cancellation",
                "steps": [{"id": "ping", "status": "passed", "duration_ms": 100}],
            })
        for name in ["daily.launcher.log", "daily/serial.log",
                     "daily/netns-batch/tc-restored-001.stdout.log", "daily/netns-batch/tc-restored-001.stderr.log"]:
            (root / name).write_text("")
        return root

    def test_expected_fault_is_not_reported_as_first_failure(self):
        root = self.fixture()
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}
        diagnosis = module.diagnose(root)
        problem = diagnosis["first_problem"]
        self.assertEqual(problem["case"], "tc-restored-001")
        self.assertEqual((problem["exit_code"], problem["expected_exit_code"]), (3, 0))
        self.assertEqual(problem["kind"], "case_failed")
        self.assertIn("readiness", problem["detail"])
        self.assertEqual(diagnosis["root_cause"], "undetermined")
        self.assertTrue(any(p.endswith("tc-restored-001.stderr.log") for p in problem["logs"]))
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()})

    def test_supervisor_timeout_is_distinct_from_child_failure(self):
        problem = module.diagnose(self.fixture("timeout"))["first_problem"]
        self.assertEqual(problem["kind"], "batch_supervisor_timeout")
        self.assertEqual(problem["preceding_case_duration_ms"], 1500)
        self.assertEqual(problem["duration_ms"], 4000)
        self.assertEqual(problem["recorded_step_duration_ms"], 100)

    def test_missing_child_report_keeps_partial_failure_and_marks_missing(self):
        diagnosis = module.diagnose(self.fixture(include_detail=False))
        self.assertEqual(diagnosis["first_problem"]["case"], "tc-restored-001")
        self.assertIsNone(diagnosis["first_problem"]["detail"])
        self.assertIn("daily/netns-batch/tc-restored-001/result.json", diagnosis["missing_evidence"])

    def test_path_escape_is_rejected(self):
        root = self.directory()
        self.write(root, "summary.json", {"scenario": "netns-workflow", "status": "failed",
                                         "runs": [{"output": "../outside", "status": "failed"}]})
        with self.assertRaisesRegex(ValueError, "escapes"):
            module.diagnose(root)

    def test_symlink_in_evidence_is_rejected(self):
        root = self.directory()
        (root / "daily").symlink_to(self.directory(), target_is_directory=True)
        self.write(root, "summary.json", {"scenario": "netns-workflow", "status": "failed",
                                         "runs": [{"output": "daily", "status": "failed"}]})
        with self.assertRaisesRegex(ValueError, "symlink"):
            module.diagnose(root)

    def test_oversized_report_is_rejected(self):
        root = self.directory()
        (root / "summary.json").write_bytes(b" " * (module.LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            module.diagnose(root)

    def test_cli_success_is_diagnosis_success_not_a_passed_verdict(self):
        root = self.fixture()
        result = subprocess.run([sys.executable, str(pathlib.Path(module.__file__)), str(root), "--json"],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout)["reported_status"], "environment_error")
        invalid = subprocess.run([sys.executable, str(pathlib.Path(module.__file__)), str(self.directory())],
                                 capture_output=True, text=True, timeout=5)
        self.assertEqual(invalid.returncode, 3)
        self.assertIn("required evidence missing", invalid.stderr)

    def test_recorded_success_does_not_invent_a_failure(self):
        root = self.directory()
        self.write(root, "summary.json", {"scenario": "netns-workflow", "status": "passed",
                                         "runs": [{"status": "passed"}]})
        diagnosis = module.diagnose(root)
        self.assertIsNone(diagnosis["first_problem"])
        self.assertEqual(diagnosis["root_cause"], "not_applicable")

    def test_vm_timeout_without_a_case_report_uses_stop_evidence(self):
        root = self.directory()
        self.write(root, "summary.json", {"scenario": "netns-workflow", "status": "environment_error",
                                         "runs": [{"id": "daily", "output": "daily", "status": "failed"}]})
        self.write(root, "daily/vm-stop.json", {"status": "environment_error", "reason": "watchdog_timeout"})
        problem = module.diagnose(root)["first_problem"]
        self.assertEqual(problem["kind"], "vm_lifecycle_failure")
        self.assertEqual(problem["detail"], "watchdog_timeout")
        self.assertIsNone(problem["case"])

    def test_watchdog_lifecycle_fields_must_be_complete_and_strictly_typed(self):
        valid = {"schema_version": 2, "status": "environment_error", "reason": "watchdog",
                 "lifecycle_exit_code": 3, "qemu_reaped": True, "qemu_exit_code": 0,
                 "escalated": False, "evidence_synced": True, "evidence_synced_before_stop": True,
                 "sync_error": None, "watchdog_fixture": True, "checkpoint_seen": True}
        for stage in ('pending', 'passed'):
            for key in valid:
                variants = ('missing', 'wrong_type', 'wrong_value') if valid[key] is not None else ('missing', 'wrong_type')
                for variant in variants:
                    with self.subTest(stage=stage, field=key, variant=variant):
                        root = self.directory()
                        self.write(root, 'summary.json', {
                            'scenario': 'netns-workflow', 'status': 'environment_error',
                            'runs': [{'id': 'daily-watchdog', 'output': 'daily-watchdog', 'status': 'failed',
                                      'watchdog_fixture': True, 'exit_code': 3, 'expected_exit_code': 3,
                                      'evidence_verification': stage}]})
                        stop = {k: v for k, v in valid.items() if k != key}
                        if variant == 'wrong_type':
                            # bools must not accept 0/1; integers must not accept strings.
                            stop[key] = (int(valid[key]) if type(valid[key]) is bool
                                         else 0 if type(valid[key]) is str else str(valid[key]))
                        elif variant == 'wrong_value':
                            stop[key] = (not valid[key] if type(valid[key]) is bool
                                         else valid[key] + 1 if type(valid[key]) is int else 'unexpected')
                        self.write(root, 'daily-watchdog/vm-stop.json', stop)
                        problem = module.diagnose(root)['first_problem']
                        self.assertEqual(problem['kind'], 'vm_lifecycle_failure')
                        self.assertIn(key, problem['detail'])


if __name__ == "__main__":
    unittest.main()
