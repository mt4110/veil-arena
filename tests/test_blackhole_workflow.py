import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("workflow", Path(__file__).parents[1] / "scripts/blackhole_workflow.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


class WorkflowTests(unittest.TestCase):
    def report(self):
        return {"schema_version": 1, "suite": "blackhole-offline", "status": "passed",
                "source_unchanged": True, "binary_unchanged": True,
                "source_after": {"files": {"Cargo.toml": {"bytes": 1, "sha256": "1" * 64}}, "sha256": "2" * 64},
                "binary_sha256": "3" * 64,
                "steps": [{"id": name, "status": "passed"} for name in [
                    "rust-version", "cargo-version", "python-version", "git-root", "git-head", "git-status",
                    "build", "rust-tests", "python-tests", "replay-ipv4", "replay-ipv6", "replay-dns",
                    "reject-dns-short-header", "replay-bpf", "checksum-ipv4", "checksum-ipv6",
                    "checksum-ipv6-options", "reject-udp-length", "reject-udp-checksum"]]}

    def test_expected_failure_requires_correct_stage_and_skipped_tail(self):
        report = self.report()
        report["status"] = "failed"
        report["steps"][7] = {"id": "rust-tests", "status": "failed"}
        for step in report["steps"][8:]:
            step["status"] = "not_run"
        self.assertEqual(workflow.assess(report, 1, "rust-tests"), "passed")
        self.assertEqual(workflow.assess(report, 1, "python-tests"), "failed")
        report["steps"][8]["status"] = "passed"
        self.assertEqual(workflow.assess(report, 1, "rust-tests"), "failed")

    def test_unknown_or_reordered_stages_cannot_be_complete(self):
        for mutate in [lambda steps: steps[0].update(id="unknown-stage"),
                       lambda steps: steps.reverse()]:
            report = self.report()
            mutate(report["steps"])
            with self.assertRaises(ValueError):
                workflow.assess(report, 0)

    def test_empty_source_and_invalid_binary_hash_cannot_pass(self):
        report = self.report()
        report["source_after"] = {}
        report["binary_sha256"] = ""
        with self.assertRaises(ValueError):
            workflow.assess(report, 0)

    def test_pass_cannot_substitute_for_expected_failure(self):
        self.assertEqual(workflow.assess(self.report(), 0, "rust-tests"), "failed")

    def test_code_disagreement_changed_source_and_missing_stage_are_errors(self):
        with self.assertRaises(ValueError):
            workflow.assess(self.report(), 1)
        for field in ["source_unchanged", "binary_unchanged"]:
            report = self.report()
            report[field] = False
            with self.assertRaises(ValueError):
                workflow.assess(report, 0)
        report = self.report()
        report["steps"].pop()
        with self.assertRaises(ValueError):
            workflow.assess(report, 0)
        with self.assertRaises(ValueError):
            workflow.assess([], 0)

    def test_environment_error_is_not_expected_failure(self):
        report = self.report()
        report["status"] = "environment_error"
        self.assertEqual(workflow.assess(report, 3, "rust-tests"), "environment_error")

    def test_partial_timeout_and_cancellation_keep_their_status(self):
        for status, code in [("timeout", 4), ("cancelled", 130), ("environment_error", 3)]:
            report = {"schema_version": 1, "suite": "blackhole-offline", "status": status, "steps": []}
            self.assertEqual(workflow.assess(report, code, "rust-tests"), status)

    def test_pass_requires_every_stage_to_pass(self):
        report = self.report()
        report["steps"][5]["status"] = "not_run"
        self.assertEqual(workflow.assess(report, 0), "environment_error")

    def test_output_inside_project_and_symlink_are_refused(self):
        root = Path(tempfile.mkdtemp(prefix="arena-workflow-path-"))
        project = root / "project"
        project.mkdir()
        with self.assertRaises(ValueError):
            workflow.directory(project / "out", [project])
        link = root / "link"
        link.symlink_to(project, target_is_directory=True)
        with self.assertRaises(ValueError):
            workflow.directory(link / "out", [])
        self.assertFalse((project / "out").exists())

    def test_evidence_is_exclusive_and_private(self):
        root = Path(tempfile.mkdtemp(prefix="arena-workflow-json-"))
        path = root / "summary.json"
        workflow.write_json(path, {"status": "failed"})
        with self.assertRaises(FileExistsError):
            workflow.write_json(path, {"status": "passed"})
        self.assertEqual(json.loads(path.read_text())["status"], "failed")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_before_requires_the_specific_regression_log(self):
        for text, log_name, expected in [
            ("target regression ... FAILED", "rust-tests.stdout.log", "passed"),
            ("unrelated regression ... FAILED", "rust-tests.stdout.log", "failed"),
            ("target regression ... FAILED", "../outside.log", "environment_error"),
        ]:
            root = Path(tempfile.mkdtemp(prefix="arena-workflow-evidence-"))
            def fake_command(args, *_):
                output = Path(args[args.index("--artifacts") + 1]) / "blackhole-offline-fixture"
                output.mkdir()
                report = self.report()
                report.update(status="failed", duration_ms=1)
                report["steps"][7] = {"id": "rust-tests", "status": "failed", "stdout_log": log_name}
                for step in report["steps"][8:]:
                    step["status"] = "not_run"
                (output / "result.json").write_text(json.dumps(report))
                (output / "rust-tests.stdout.log").write_text(text)
                return {"status": "completed", "exit_code": 1}
            with patch.object(workflow, "command", side_effect=fake_command):
                row = workflow.suite(root / "runner", root, root, {}, 1, root, "before",
                                     "rust-tests", "target regression ... FAILED")
            self.assertEqual(row["status"], expected)

    def test_preparation_timeout_and_launch_error_are_distinct(self):
        root = Path(tempfile.mkdtemp(prefix="arena-workflow-command-"))
        timed = workflow.command([sys.executable, "-c", "import time; time.sleep(60)"], root,
                                 os.environ.copy(), 0.05, root, "timeout")
        self.assertEqual(timed["status"], "timeout")
        self.assertIsNotNone(timed["exit_code"])
        failed = workflow.command([str(root / "missing")], root, os.environ.copy(), 1, root, "missing")
        self.assertEqual(failed["status"], "environment_error")
        self.assertIsNone(failed["exit_code"])

    def test_python_failure_requires_stderr_evidence(self):
        for channel, log_name, expected in [
            ("stderr", "python-tests.stderr.log", "passed"),
            ("stdout", "python-tests.stderr.log", "failed"),
            ("stderr", "../outside.log", "environment_error"),
        ]:
            with self.subTest(channel=channel, log_name=log_name):
                root = Path(tempfile.mkdtemp(prefix="arena-python-evidence-"))
                def fake_command(args, *_):
                    output = Path(args[args.index("--artifacts") + 1]) / "blackhole-offline-fixture"
                    output.mkdir()
                    report = self.report()
                    report.update(status="failed", duration_ms=1)
                    report["steps"][8] = {"id": "python-tests", "status": "failed",
                                          "stdout_log": "python-tests.stdout.log", "stderr_log": log_name}
                    for step in report["steps"][9:]:
                        step["status"] = "not_run"
                    (output / "result.json").write_text(json.dumps(report))
                    for stream in ("stdout", "stderr"):
                        (output / f"python-tests.{stream}.log").write_text(
                            "FAIL: test_regression (fixture.TestCase)" if stream == channel else "")
                    return {"status": "completed", "exit_code": 1}
                with patch.object(workflow, "command", side_effect=fake_command):
                    row = workflow.suite(root / "runner", root, root, {}, 1, root, "before",
                                         "python-tests", "FAIL: test_regression")
                self.assertEqual(row["status"], expected)


if __name__ == "__main__":
    unittest.main()
