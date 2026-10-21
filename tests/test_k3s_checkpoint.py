"""Checkpoint failure proofs and ordinary-run isolation, without actual VMs."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import k3s_checkpoint as checkpoint
import k3s_host as host
import k3s_bundle as bundle
from keeper_trial import PROFILE


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name)
        self.inputs = {"versions": {"k3s": "fixed", "containerd": "fixed"}}
        self.boot = "12345678-0000-0000-0000-000000000001"
        self.control = dict(checkpoint.CAPABILITY, action="timeout")
        self.records = {
            "lifecycle-control.json": self.control,
            "result.json": {"schema_version": 1, "scenario": "k3s-f3", "inputs": self.inputs,
                "boot_id": self.boot, "lifecycle_fixture": self.control, "stage": checkpoint.CHECKPOINT,
                "status": "environment_error", "exit_code": 3,
                "checks": [{"stage": "node_ready", "status": "passed", "count": 1}]},
            "checkpoint-state.json": dict(checkpoint.CAPABILITY, boot_id=self.boot,
                runner_pid=10, observer_pid=11, proc_status="State:\tT (stopped)\nPid:\t10\n",
                profiles=[], keeper_not_started=True),
            "watchdog-ready.json": {"schema_version": 1, "scenario": "guest-runner-stopped",
                "checkpoint": checkpoint.CHECKPOINT, "boot_id": self.boot, "batch_pid": 10,
                "observer_pid": 11, "state": "T", "signal": 19},
            "cluster-nodes.json": [{"metadata": {"name": "arena-k3s-server"}, "status": {
                "conditions": [{"type": "Ready", "status": "True"}],
                "nodeInfo": {"kubeletVersion": "vfixed", "containerRuntimeVersion": "containerd://fixed"}}}],
            "vm-stop.json": {"schema_version": 2, "status": "environment_error", "reason": "watchdog",
                "lifecycle_exit_code": 3, "qemu_reaped": True, "qemu_exit_code": 0, "escalated": False,
                "evidence_synced": True, "evidence_synced_before_stop": True, "sync_error": None,
                "watchdog_fixture": True, "checkpoint_seen": True, "timeout_after_checkpoint_ms": 2000,
                "checkpoint_elapsed_ms": 2001, "startup_timeout_ms": 120000}}
        self.write()

    def write(self):
        for name, value in self.records.items():
            (self.out / name).write_text(json.dumps(value))

    def test_timeout_and_cancel_have_separate_proofs(self):
        self.assertEqual(checkpoint.verify(self.out, self.inputs, "timeout", 3, False), self.boot)
        self.records["lifecycle-control.json"]["action"] = "cancel"
        self.records["vm-stop.json"].update(reason="cancelled", watchdog_fixture=False)
        self.write()
        self.assertEqual(checkpoint.verify(self.out, self.inputs, "cancel", 3, True), self.boot)
        with self.assertRaisesRegex(ValueError, "cancellation"):
            checkpoint.verify(self.out, self.inputs, "cancel", 3, False)

    def test_cleanup_failure_cannot_become_expected_fixture_success(self):
        original = copy.deepcopy(self.records)
        for mutation in ({"qemu_reaped": False}, {"qemu_exit_code": 1}, {"escalated": True},
                         {"evidence_synced": False}, {"evidence_synced_before_stop": False},
                         {"sync_error": "failed"}):
            self.records = copy.deepcopy(original)
            self.records["vm-stop.json"].update(mutation)
            self.write()
            with self.assertRaisesRegex(ValueError, "lifecycle"):
                checkpoint.verify(self.out, self.inputs, "timeout", 3, False)

    def test_partial_checkpoint_identity_deadline_and_profile_are_required(self):
        original = copy.deepcopy(self.records)
        for name, mutation in [("vm-stop.json", {"checkpoint_elapsed_ms": 1999}),
                ("vm-stop.json", {"reason": "guest_exit"}),
                ("vm-stop.json", {"checkpoint_seen": False}),
                ("checkpoint-state.json", {"profiles": [PROFILE + " (enforce)"]}),
                ("checkpoint-state.json", {"proc_status": "State:\tS\nPid:\t10\n"}),
                ("watchdog-ready.json", {"batch_pid": 11}),
                ("watchdog-ready.json", {"boot_id": "wrong"}),
                ("result.json", {"checks": []}), ("result.json", {"status": "passed"})]:
            self.records = copy.deepcopy(original)
            self.records[name].update(mutation)
            self.write()
            with self.assertRaises(ValueError):
                checkpoint.verify(self.out, self.inputs, "timeout", 3, False)
        self.records = original
        self.write()
        (self.out / "checkpoint-error.json").write_text('{}')
        with self.assertRaisesRegex(ValueError, "proof differs"):
            checkpoint.proof(self.out, self.inputs)

    def test_atomic_publication_keeps_existing_evidence(self):
        p = self.out / "new.json"
        checkpoint.publish(p, {"complete": True})
        self.assertEqual(json.loads(p.read_text()), {"complete": True})

        with self.assertRaises(FileExistsError):
            checkpoint.publish(p, {"complete": False})
        self.assertEqual(json.loads(p.read_text()), {"complete": True})

    @unittest.skipUnless(sys.platform.startswith("linux"), "guest observer requires Linux procfs")
    def test_real_owned_dummy_runner_stops_before_publishing_gate(self):
        root = self.out / "dummy"
        root.mkdir()
        code = ("import sys;from pathlib import Path;import keeper_trial;"
                "keeper_trial.inventory=lambda: [];import k3s_checkpoint as c;"
                "c.stop_at_checkpoint(Path(sys.argv[1]),{'boot_id':'fixed'})")
        child = subprocess.Popen([sys.executable, "-B", "-c", code, str(root)],
                                 env={**os.environ, "PYTHONPATH": str(Path(checkpoint.__file__).parent)},
                                 start_new_session=True, stdin=subprocess.DEVNULL)
        try:
            end = time.monotonic() + 5
            while not (root / "watchdog-ready.json").exists() and time.monotonic() < end:
                if child.poll() is not None:
                    self.fail("dummy runner exited before stopped proof")
                time.sleep(.01)
            gate = json.loads((root / "watchdog-ready.json").read_text())
            self.assertEqual(gate["batch_pid"], child.pid)
            state = Path(f"/proc/{child.pid}/status").read_text()
            self.assertTrue(any(line.startswith("State:\tT") for line in state.splitlines()))
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)


class Workflow(unittest.TestCase):
    def test_shared_600_second_deadline_stops_before_next_fresh_vm(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            inputs = {"scenario":"k3s-f3", "hostUid":501, "system":"aarch64-linux", "limits":bundle.LIMITS,
                      "lifecycle_checkpoint_sha256": host.sha(Path(checkpoint.__file__))}
            (base / "toplevel").write_text("/nix/store/" + "a" * 32 + "-nixos-system-fixture")
            (base / "kernel-params").write_text("fixed")
            (base / "bundle.json").write_text("{}")
            child = Mock()
            child.poll.return_value = child.wait.return_value = 3
            def spawn(*args, **kwargs):
                (base / "out/fresh-1/watchdog-ready.json").write_text("{}")
                return child
            with patch.object(host, "check", return_value=(inputs, {"files":{}})), \
                    patch.object(host.os, "getuid", return_value=501), patch.object(host.os, "cpu_count", return_value=4), \
                    patch.object(host, "execution", return_value={"arch":"aarch64","accelerator":"tcg","cpu":"max"}), \
                    patch.object(host, "available_memory_mib", return_value=20000), \
                    patch.object(host.shutil, "disk_usage", return_value=Mock(free=140*1024**3)), \
                    patch.object(host.subprocess, "run"), patch.object(host.subprocess, "Popen", side_effect=spawn) as start, \
                    patch.object(checkpoint, "proof", return_value="proof"), patch.object(checkpoint, "verify", return_value="boot"), \
                    patch.object(host.time, "monotonic", side_effect=[0, 0, 0, 601]), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(host.workflow(501, base, "lifecycle", base/"out", scenario="k3s-f3"), 4)
            start.assert_called_once()
            self.assertFalse((base/"out/fresh-2").exists())
            report = json.loads((base/"out/summary.json").read_text())
            self.assertEqual(report["status"], "timeout")
            self.assertIn("600s", report["detail"])

    def test_invalid_checkpoint_proof_reclaims_owned_controller_and_retains_failure(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            inputs = {"scenario":"k3s-f3", "hostUid":501, "system":"aarch64-linux", "limits":bundle.LIMITS,
                      "lifecycle_checkpoint_sha256": host.sha(Path(checkpoint.__file__))}
            (base / "toplevel").write_text("/nix/store/" + "a" * 32 + "-nixos-system-fixture")
            (base / "kernel-params").write_text("fixed")
            (base / "bundle.json").write_text("{}")
            child = Mock()
            child.poll.return_value = None
            child.wait.return_value = 3
            def spawn(*args, **kwargs):
                (base / "out/fresh-1/watchdog-ready.json").write_text("{}")
                return child
            with patch.object(host, "check", return_value=(inputs, {"files":{}})), \
                    patch.object(host.os, "getuid", return_value=501), patch.object(host.os, "cpu_count", return_value=4), \
                    patch.object(host, "execution", return_value={"arch":"aarch64","accelerator":"tcg","cpu":"max"}), \
                    patch.object(host, "available_memory_mib", return_value=20000), \
                    patch.object(host.shutil, "disk_usage", return_value=Mock(free=140*1024**3)), \
                    patch.object(host.subprocess, "run"), patch.object(host.subprocess, "Popen", side_effect=spawn) as start, \
                    patch.object(checkpoint, "proof", side_effect=ValueError("stopped proof corrupt")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(host.workflow(501, base, "lifecycle", base/"out", scenario="k3s-f3"), 3)
            start.assert_called_once()
            child.send_signal.assert_called_once_with(signal.SIGTERM)
            child.wait.assert_called_once_with(timeout=15)
            report = json.loads((base/"out/summary.json").read_text())
            self.assertIn("stopped proof corrupt", report["detail"])
            self.assertFalse((base/"out/fresh-2").exists())
            self.assertTrue((base/"out/fresh-1/watchdog-ready.json").is_file())

    def test_faults_each_followed_by_fresh_healthy_run_and_one_shared_deadline(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            inputs = {"scenario": "k3s-f3", "hostUid": 501, "system": "aarch64-linux", "limits": bundle.LIMITS,
                      "lifecycle_checkpoint_sha256": host.sha(Path(checkpoint.__file__))}
            (base / "toplevel").write_text("/nix/store/" + "a" * 32 + "-nixos-system-fixture")
            (base / "kernel-params").write_text("fixed")
            (base / "bundle.json").write_text("{}")
            def spawn(args, **kw):
                out = Path(args[6] if "--watchdog-fixture" in args else args[5])
                # timeout options (4), controller (1), optional fixture, then output.
                child = Mock()
                action = json.loads((out / "lifecycle-control.json").read_text())["action"] if (out / "lifecycle-control.json").exists() else None
                child.wait.return_value = 3 if action else 0
                child.poll.return_value = 3 if action else 0
                child.action = action
                if action == "cancel": (out / "watchdog-ready.json").write_text("{}")
                return child
            with patch.object(host, "check", return_value=(inputs, {"files": {}})), \
                    patch.object(host.os, "getuid", return_value=501), patch.object(host.os, "cpu_count", return_value=4), \
                    patch.object(host, "execution", return_value={"arch":"aarch64","accelerator":"tcg","cpu":"max"}), \
                    patch.object(host, "available_memory_mib", return_value=20000), \
                    patch.object(host.shutil, "disk_usage", return_value=Mock(free=140*1024**3)), \
                    patch.object(host.subprocess, "run"), patch.object(host.subprocess, "Popen", side_effect=spawn) as start, \
                    patch.object(checkpoint, "proof", return_value="proof"), \
                    patch.object(checkpoint, "verify", side_effect=["cancel-boot", "timeout-boot"]) as verify, \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                normal = Mock(side_effect=[(0,{"status":"passed","boot_id":"healthy-one"}),
                                           (0,{"status":"passed","boot_id":"healthy-two"})])
                self.assertEqual(host.workflow(501, base, "lifecycle", base/"out", scenario="k3s-f3", verifier=normal), 0)
            report = json.loads((base/"out/summary.json").read_text())
            self.assertEqual([r["fixture"] for r in report["runs"]], ["cancel", None, "timeout", None])
            self.assertEqual(start.call_count, 4)
            self.assertEqual(normal.call_count, 2)
            self.assertEqual(verify.call_count, 2)
            self.assertEqual(verify.call_args_list[0].args[4], True)
            self.assertEqual(verify.call_args_list[1].args[4], False)
            self.assertTrue(all(c.kwargs["start_new_session"] for c in start.call_args_list))


if __name__ == "__main__":
    unittest.main()
