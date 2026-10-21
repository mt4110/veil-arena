"""Host routing and F4 aggregation without root, BPF or a real VM."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import k3s_execution as routing
import k3s_f4_host as host

TCG = {"arch": "aarch64", "accelerator": "tcg", "cpu": "max"}


class Routing(unittest.TestCase):
    def test_explicit_host_routes_and_unsupported_host(self):
        for system, machine, expected in [("Darwin", "arm64", TCG),
                ("Linux", "x86_64", {"arch": "x86_64", "accelerator": "kvm", "cpu": "host"})]:
            with patch.object(routing.platform, "system", return_value=system), \
                    patch.object(routing.platform, "machine", return_value=machine):
                self.assertEqual(routing.route(), expected)
        with patch.object(routing.platform, "system", return_value="Linux"), \
                patch.object(routing.platform, "machine", return_value="aarch64"):
            with self.assertRaisesRegex(ValueError, "unsupported runtime"):
                routing.route()

    def test_kvm_probe_closes_owned_fds_and_has_no_fallback(self):
        import fcntl
        with patch.object(routing, "route", return_value={"accelerator": "kvm"}), \
                patch.object(routing.os, "open", return_value=10), \
                patch.object(routing.os, "close") as close, \
                patch.object(fcntl, "ioctl", side_effect=[12, 11]) as ioctl:
            routing.execution()
            self.assertEqual([x.args for x in ioctl.call_args_list], [(10, 0xAE00, 0), (10, 0xAE01, 0)])
            self.assertEqual([x.args[0] for x in close.call_args_list], [11, 10])
        for responses in ([11], [12, PermissionError("KVM_CREATE_VM denied")]):
            with patch.object(routing, "route", return_value={"accelerator": "kvm"}), \
                    patch.object(routing.os, "open", return_value=10), \
                    patch.object(routing.os, "close") as close, \
                    patch.object(fcntl, "ioctl", side_effect=responses):
                with self.assertRaises((ValueError, PermissionError)):
                    routing.execution()
                close.assert_called_once_with(10)

    def test_qemu_uses_host_cpu_and_kvm_on_linux(self):
        import k3s_f2_host as f2
        with patch.object(f2, "route", return_value={"arch": "x86_64", "accelerator": "kvm", "cpu": "host"}), \
                patch.object(f2, "bounded", side_effect=lambda p: b"/nix/store/" + b"a" * 32 + b"-nixos-system-fixture" if p.name == "toplevel" else b"params"):
            args = f2.qemu_args("x86_64", Path("/bundle"), Path("/out"), Path("/control"), Path("/private/lan"), "server", {})
            self.assertEqual(args[args.index("-accel") + 1], "kvm")
            self.assertEqual(args[args.index("-cpu") + 1], "host")
            self.assertNotIn("tcg", args)


class F4(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "out"
        self.bundles = {r: self.base / r for r in ("server", "agent", "keeper")}
        self.inputs = {r: {"scenario": "k3s-f3" if r == "keeper" else "k3s-f2",
                          "role": r, "hostUid": 501, "system": "aarch64-linux",
                          "fixture_config_digest": "fixed"} for r in self.bundles}

    def invoke(self, f2=0, f3=0, invalid=None, cancel=False):
        calls = []
        def component(stage, code, out):
            calls.append(stage)
            out.mkdir()
            report = {"schema_version": 1, "scenario": "k3s-" + stage, "mode": "acceptance",
                      "exit_code": code, "status": "passed" if code == 0 else "failed",
                      "detail": "target unexpectedly allowed", "runs": [{"status": "passed"}] * 2}
            (out / "summary.json").write_text(json.dumps(report))
            if cancel:
                # Existing workflows restore the outer handler before returning.
                signal.raise_signal(signal.SIGTERM)
            return code
        with patch.object(host, "preflight", return_value=(TCG, self.inputs)), \
                patch.object(host, "check", side_effect=lambda p: (self.inputs[p.name], {"files": {}})), \
                patch.object(host, "cluster_workflow", side_effect=lambda uid, b, mode, out: component("f2", f2, out)) as first, \
                patch.object(host, "single_workflow", side_effect=lambda uid, b, mode, out, **kw: component("f3", f3, out)) as second, \
                patch.object(host, "verify_component", side_effect=ValueError("raw lifecycle missing") if invalid else lambda stage, *args: [stage + "-one", stage + "-two"]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = host.workflow(501, self.bundles, self.root)
        return code, json.loads((self.root / "summary.json").read_text()), calls, first, second

    def test_success_runs_existing_acceptance_modes_in_order(self):
        code, report, calls, first, second = self.invoke()
        self.assertEqual((code, report["status"], calls), (0, "passed", ["f2", "f3"]))
        self.assertEqual(first.call_args.args[2], "acceptance")
        self.assertEqual(second.call_args.args[2], "acceptance")
        self.assertEqual(second.call_args.kwargs["scenario"], "k3s-f3")
        self.assertEqual(report["component_workflow_seconds"], 600)

    def test_failure_exit_and_stage_preserved_without_running_next(self):
        for code in (1, 3, 4, 130):
            self.root = self.base / f"failure-{code}"
            actual, report, calls, _, _ = self.invoke(f2=code)
            self.assertEqual(actual, code)
            self.assertEqual(calls, ["f2"])
            self.assertEqual(report["stage"], "f2")
            self.assertEqual(report["detail"], "target unexpectedly allowed")
        self.root = self.base / "keeper-failed"
        actual, report, calls, _, _ = self.invoke(f3=1)
        self.assertEqual((actual, report["stage"], calls), (1, "f3", ["f2", "f3"]))

    def test_green_summary_cannot_hide_invalid_raw_evidence(self):
        code, report, calls, _, _ = self.invoke(invalid=True)
        self.assertEqual((code, report["status"], calls), (3, "environment_error", ["f2"]))

    def test_cancel_between_components_keeps_evidence_and_stops(self):
        code, report, calls, _, _ = self.invoke(cancel=True)
        self.assertEqual((code, report["status"], calls), (130, "cancelled", ["f2"]))
        self.assertTrue((self.root / "f2/summary.json").is_file())

    def test_preflight_rejects_resources_and_existing_output_before_vm(self):
        with patch.object(host, "execution", return_value=TCG), \
                patch.object(host.os, "getuid", return_value=501), \
                patch.object(host, "check", side_effect=lambda p: (self.inputs[p.name], {})), \
                patch.object(host, "available_memory_mib", return_value=24576), \
                patch.object(host.shutil, "disk_usage", return_value=Mock(free=194 * 1024**3)), \
                patch.object(host.os, "cpu_count", return_value=4):
            with self.assertRaisesRegex(ValueError, "6 host CPUs"):
                host.preflight(501, self.bundles, self.root)
            self.assertFalse(self.root.exists())
            with patch.object(host.os, "cpu_count", return_value=6):
                host.preflight(501, self.bundles, self.root)
                with patch.object(host.shutil, "disk_usage", return_value=Mock(free=193 * 1024**3)):
                    with self.assertRaisesRegex(ValueError, "194 GiB"):
                        host.preflight(501, self.bundles, self.root)
                self.root.mkdir()
                (self.root / "owner").write_text("keep")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    host.preflight(501, self.bundles, self.root)
                self.assertEqual((self.root / "owner").read_text(), "keep")

    def test_nested_summary_schema_and_boot_identity_rechecked(self):
        self.root.mkdir()
        good = {"schema_version": 1, "scenario": "k3s-f2", "mode": "acceptance", "status": "passed", "exit_code": 0,
                "runs": [{"status": "passed", "boot_ids": {"server": "s1", "agent": "a1"}},
                         {"status": "passed", "boot_ids": {"server": "s2", "agent": "a2"}}]}
        report_path = self.root / "summary.json"
        report_path.write_text(json.dumps(good))
        with patch.object(host, "verify_cluster", side_effect=[good["runs"][0]["boot_ids"], good["runs"][1]["boot_ids"]]):
            self.assertEqual(host.verify_component("f2", self.root, self.inputs), ["s1", "a1", "s2", "a2"])
        for broken in ([], {**good, "exit_code": False}, {**good, "runs": []}):
            report_path.write_text(json.dumps(broken))
            with self.assertRaisesRegex(ValueError, "summary invalid"):
                host.verify_component("f2", self.root, self.inputs)
        report_path.write_text(json.dumps(good))
        with patch.object(host, "verify_cluster", side_effect=ValueError("abnormal VM lifecycle")):
            with self.assertRaisesRegex(ValueError, "lifecycle"):
                host.verify_component("f2", self.root, self.inputs)


if __name__ == "__main__":
    unittest.main()
