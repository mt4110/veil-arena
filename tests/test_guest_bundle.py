import hashlib
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("bundle", pathlib.Path(__file__).parents[1] / "scripts/guest_bundle.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class BundleTests(unittest.TestCase):
    def fixture(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="arena-guest-bundle-"))
        rows = []
        for name in ["store.img", "Image", "initrd"]:
            data = name.encode()
            (root / name).write_bytes(data)
            rows.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
        params = b"arena.netns=1"
        manifest = json.dumps({"scenario": "netns-batch", "network": "no NIC", "guestSystem": "aarch64-linux",
                               "versions": {"rust": "1.98.1", "cargo": "1.98.0", "python": "3.14.7", "kernel": "6.12.112"}}).encode()
        (root / "kernel-params").write_bytes(params)
        (root / "original-vm-manifest.json").write_bytes(manifest)
        (root / "bundle.json").write_text(json.dumps({"schema_version": 1, "files": rows,
            "kernel_params_sha256": hashlib.sha256(params).hexdigest(),
            "original_manifest_sha256": hashlib.sha256(manifest).hexdigest()}))
        return root

    def test_hashes_and_contract(self):
        root = self.fixture()
        self.assertEqual(module.check(root)["guestSystem"], "aarch64-linux")
        with (root / "Image").open("ab") as file:
            file.write(b"changed")
        with self.assertRaises(ValueError):
            module.check(root)

    def test_metadata_and_parameters_cannot_change(self):
        for name in ["kernel-params", "original-vm-manifest.json"]:
            root = self.fixture()
            with (root / name).open("ab") as file:
                file.write(b" ")
            with self.assertRaises(ValueError):
                module.check(root)

    def test_symlink_and_unknown_input_are_rejected(self):
        root = self.fixture()
        link = root.parent / (root.name + "-link")
        link.symlink_to(root, target_is_directory=True)
        with self.assertRaises(ValueError):
            module.check(link)
        data = json.loads((root / "bundle.json").read_text())
        data["files"][0]["name"] = "../outside"
        (root / "bundle.json").write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            module.check(root)


if __name__ == "__main__":
    unittest.main()
