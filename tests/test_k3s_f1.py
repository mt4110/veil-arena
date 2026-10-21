"""F1 acceptance must reject missing, malformed and contradictory evidence."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import k3s_bundle as bundle
import k3s_guest as guest
import k3s_host as host


class F1Evidence(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        os.umask(previous_umask)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.stop = {"status": "passed", "qemu_reaped": True, "qemu_exit_code": 0,
                     "escalated": False, "evidence_synced": True, "lifecycle_exit_code": 0}
        checks = [{"stage": "node_ready", "status": "passed", "count": 1},
                  {"stage": "pod_ready", "status": "passed", "count": 2}]
        for protocol in ["tcp", "udp"]:
            payload = f"arena-f1:{protocol}:" + "a" * 32
            checks.append({"stage": "dns_" + protocol, "status": "passed", "receiver_count": 1,
                           "probe": {"protocol": protocol, "count": 1, "payload": payload,
                                     "received": payload, "host": "echo.arena-f1.svc.cluster.local",
                                     "addresses": ["10.43.1.2"]}})
        self.result = {"schema_version": 1, "scenario": "k3s-f1", "status": "passed", "exit_code": 0, "inputs": {},
                       "boot_id": "7c2d66b7-8e1d-45a4-a968-56776fd547a6", "checks": checks}
        self.write()

    def write(self):
        (self.out / "vm-stop.json").write_text(json.dumps(self.stop))
        (self.out / "result.json").write_text(json.dumps(self.result))
        (self.out / "service-result.txt").write_text("success\n")

    def test_complete_evidence(self):
        self.assertEqual(host.verify_run(self.out, {})[0], 0)

    def test_missing_required_compatibility_evidence_rejects_pass(self):
        inputs = {"compat_elf": {"true": {"destination": "/usr/bin/true", "sha256": "0" * 64}}}
        self.result["inputs"] = inputs
        self.write()
        with self.assertRaisesRegex(ValueError, "compat-state"):
            host.verify_run(self.out, inputs)

    def test_lifecycle_failure_precedes_partial_guest(self):
        self.stop["qemu_reaped"] = False
        self.write()
        (self.out / "result.json").write_text('{"status":')
        with self.assertRaisesRegex(ValueError, "lifecycle"):
            host.verify_run(self.out, {})

    def test_abnormal_cleanup_is_not_a_verified_deadline_recovery(self):
        self.assertTrue(host.recovered(self.stop))
        for field, value in [("qemu_reaped", False), ("qemu_exit_code", 1),
                             ("escalated", True), ("evidence_synced", False)]:
            broken = dict(self.stop, **{field: value})
            with self.subTest(field=field):
                self.assertFalse(host.recovered(broken))

    def test_missing_case_or_echo_mismatch_is_not_success(self):
        for mutation in [lambda r: r["checks"].pop(),
                         lambda r: r["checks"][2]["probe"].update(received="other"),
                         lambda r: r["checks"][3].update(receiver_count=2),
                         lambda r: r.update(inputs={"different": True})]:
            with self.subTest(mutation=mutation):
                saved = copy.deepcopy(self.result)
                mutation(self.result)
                self.write()
                with self.assertRaises(ValueError):
                    host.verify_run(self.out, {})
                self.result = saved

    def test_preserve_guest_failure_and_missing_exit_is_error(self):
        for code in [1, 3, 4, 130]:
            self.result.update(status="failed", exit_code=code)
            self.write()
            self.assertEqual(host.verify_run(self.out, {})[0], code)
        self.result.pop("exit_code")
        self.write()
        with self.assertRaises(ValueError):
            host.verify_run(self.out, {})

    def test_api_uses_stage_budget_and_probe_remains_bounded(self):
        self.assertEqual(guest.request_timeout(220, 700, 100), "120000ms")
        self.assertEqual(guest.request_timeout(400, 250, 100), "150000ms")
        self.assertEqual(guest.request_timeout(105, 700, 100), "5000ms")
        self.assertEqual(guest.request_timeout(105, 700, 104.999), "1ms")

    def test_pod_manifest_reference_must_belong_to_the_pinned_config(self):
        config = "sha256:" + "a" * 64
        reference = "docker.io/veil-arena/fixture@sha256:" + "b" * 64
        image = {"id": config, "repoDigests": [reference]}
        self.assertTrue(guest.image_matches(image, config, reference))
        self.assertTrue(guest.image_matches(image, config, config))
        self.assertFalse(guest.image_matches(image, "sha256:" + "c" * 64, reference))
        self.assertFalse(guest.image_matches(image, config, reference + "other"))

    def test_dangling_compatibility_link_is_not_an_executable_entry(self):
        target = self.out / "fixed-executable"
        entry = self.out / "compat-python"
        entry.symlink_to(target)
        self.assertFalse(guest.executable_entry(entry, target))
        target.write_text("fixed bytes")
        target.chmod(0o500)
        self.assertTrue(guest.executable_entry(entry, target))

    def test_ready_requires_every_member(self):
        yes = {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        self.assertTrue(guest.ready([yes]))
        self.assertFalse(guest.ready([]))
        self.assertFalse(guest.ready([yes, {}]))

    def test_existing_output_is_preserved(self):
        marker = self.out / "owner-evidence"
        marker.write_text("keep")
        with patch.object(host, "check", return_value=({"hostUid": 501, "system": "aarch64-linux", "limits": bundle.LIMITS}, {})), \
                patch.object(host.os, "getuid", return_value=501), \
                patch.object(host.os, "cpu_count", return_value=bundle.LIMITS["vcpus"]), \
                patch.object(host, "execution", return_value={"arch": "aarch64", "accelerator": "tcg", "cpu": "max"}), \
                patch.object(host.shutil, "disk_usage", return_value=type("Usage", (), {"free": 40 * 1024**3})()), \
                patch.object(host, "available_memory_mib", return_value=20000), \
                patch.object(host, "bounded", return_value=b"/nix/store/" + b"a" * 32 + b"-nixos-system-fixture"), \
                patch.object(host.subprocess, "Popen") as start:
            with self.assertRaises(FileExistsError):
                host.workflow(501, self.out, "daily", self.out)
            start.assert_not_called()
        self.assertEqual(marker.read_text(), "keep")

    def test_checker_constants_match_the_accepted_f0_contract(self):
        contract = json.loads((SCRIPTS.parent / "docs/contracts/f0-inputs.json").read_text())
        self.assertEqual(bundle.VERSIONS, contract["versions"])
        self.assertTrue(all(contract["f1_limits"][k] == v for k, v in bundle.LIMITS.items()))

    def test_input_tamper_and_deadline_drift_rejected(self):
        # Small fake sealed inputs exercise the verifier without a VM/image download.
        for name in bundle.FILES:
            (self.out / name).write_text("content")
        inputs = {"limits": dict(bundle.LIMITS), "versions": dict(bundle.VERSIONS),
                  "system": "aarch64-linux", "airgap_sha256": "pinned",
                  "manifest_sha256": bundle.sha(self.out / "fixtures.json"),
                  "compat_elf": {name: {"source": "/nix/store/fixed-coreutils/bin/" + name,
                                         "destination": "/usr/bin/" + name, "sha256": "0" * 64}
                                 for name in ["true", "false"]}}
        (self.out / "inputs.json").write_text(json.dumps(inputs))
        record = {"schema_version": 1, "compat_elf_source_verified": True, "airgap_sha256": "pinned", "files": {
            name: {"sha256": bundle.sha(self.out / name), "bytes": (self.out / name).stat().st_size}
            for name in bundle.FILES}}
        (self.out / "bundle.json").write_text(json.dumps(record))
        self.assertEqual(bundle.check(self.out)[0], inputs)
        (self.out / "Image").write_text("changed")
        with self.assertRaisesRegex(ValueError, "input differs"):
            bundle.check(self.out)
        (self.out / "Image").write_text("content")
        inputs["limits"]["node_ready_seconds"] = 301
        (self.out / "inputs.json").write_text(json.dumps(inputs))
        record["files"]["inputs.json"] = {"sha256": bundle.sha(self.out / "inputs.json"),
                                           "bytes": (self.out / "inputs.json").stat().st_size}
        (self.out / "bundle.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "deadline"):
            bundle.check(self.out)
        inputs["limits"]["node_ready_seconds"] = 300
        inputs["versions"]["python"] = "3.14.8"
        (self.out / "inputs.json").write_text(json.dumps(inputs))
        record["files"]["inputs.json"] = {"sha256": bundle.sha(self.out / "inputs.json"),
                                           "bytes": (self.out / "inputs.json").stat().st_size}
        (self.out / "bundle.json").write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "versions"):
            bundle.check(self.out)


if __name__ == "__main__":
    unittest.main()
