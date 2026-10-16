import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parents[1] / "scripts"))
import volume_guest as module
import test_guest_bundle as fixtures


class VolumeGuestTests(unittest.TestCase):
    def fixture(self):
        volume = pathlib.Path(tempfile.mkdtemp(prefix="arena-volume-"))
        trusted = fixtures.BundleTests().fixture()
        candidate = volume / "guest bundle"
        shutil.copytree(trusted, candidate)
        metadata = volume / "prepared.json"
        metadata.write_text(json.dumps({"scenario": "netns-batch", "network": "no NIC",
                                       "store_image": str(trusted), "host_controller": "/nix/store/controller"}))
        return metadata, trusted, candidate, volume

    def select(self, args, output=None):
        with mock.patch.object(module.os.path, "ismount", return_value=True):
            return module.select(*args, output)

    def test_same_inputs_select_volume_without_writing_or_replacing_lifecycle(self):
        args = self.fixture()
        before = {p.name: p.read_bytes() for p in args[2].iterdir()}
        result = self.select(args, args[3] / "new output")
        self.assertEqual(result["store_image"], str(args[2].resolve()))
        self.assertEqual(result["boot_volume"], str(args[3].resolve()))
        self.assertEqual(result["host_controller"], "/nix/store/controller")
        self.assertEqual(before, {p.name: p.read_bytes() for p in args[2].iterdir()})
        self.assertFalse((args[3] / "new output").exists())

    def test_missing_and_unmounted_directory_reject_before_hashing(self):
        args = self.fixture()
        for volume in (args[3], args[3] / "disconnected"):
            with mock.patch.object(module.guest_bundle, "check") as check:
                with self.assertRaisesRegex(ValueError, "not mounted"):
                    module.select(*args[:3], volume, volume / "new")
                check.assert_not_called()
            self.assertFalse((volume / "new").exists())

    def test_changed_input_and_kernel_parameters_are_rejected(self):
        for name in ("Image", "kernel-params", "original-vm-manifest.json"):
            args = self.fixture()
            with (args[2] / name).open("ab") as out:
                out.write(b"changed")
            with self.assertRaises(ValueError):
                self.select(args, args[3] / "new")
            self.assertFalse((args[3] / "new").exists())

    def test_self_consistent_replacement_is_not_the_prepared_bundle(self):
        args = self.fixture()
        data = b"a different kernel"
        (args[2] / "Image").write_bytes(data)
        record = json.loads((args[2] / "bundle.json").read_text())
        record["files"][1].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        (args[2] / "bundle.json").write_text(json.dumps(record))
        module.guest_bundle.check(args[2])
        with self.assertRaisesRegex(ValueError, "differs from prepared"):
            self.select(args)

    def test_existing_output_and_dangling_symlink_are_preserved(self):
        args = self.fixture()
        target = args[3] / "existing"
        target.write_bytes(b"preserve this evidence")
        link = args[3] / "dangling"
        link.symlink_to(args[3] / "missing")
        for output in (target, link):
            with self.assertRaisesRegex(ValueError, "already exists"):
                self.select(args, output)
        self.assertEqual(target.read_bytes(), b"preserve this evidence")
        self.assertTrue(link.is_symlink())

    def test_output_and_bundle_must_remain_inside_volume(self):
        args = self.fixture()
        outside = pathlib.Path(tempfile.mkdtemp(prefix="arena-outside-"))
        link = args[3] / "outside-link"
        link.symlink_to(outside, target_is_directory=True)
        for output in (outside / "new", link / "new", args[2] / "new"):
            with self.assertRaises(ValueError):
                self.select(args, output)
            self.assertFalse(output.exists())
        with self.assertRaisesRegex(ValueError, "outside"):
            self.select((args[0], args[1], args[1], args[3]))

    def test_volume_disappearing_during_input_check_is_rejected(self):
        args = self.fixture()
        with mock.patch.object(module.os.path, "ismount", side_effect=[True, False]):
            with self.assertRaisesRegex(ValueError, "changed during verification"):
                module.select(*args, args[3] / "new")
        self.assertFalse((args[3] / "new").exists())

    def test_symlink_bundle_and_unsafe_paths_are_rejected(self):
        args = self.fixture()
        link = args[3] / "bundle-link"
        link.symlink_to(args[2], target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.select((args[0], args[1], link, args[3]))
        for output in ("relative", str(args[3] / "comma,path"), str(args[3] / "new\nline")):
            with self.assertRaisesRegex(ValueError, "absolute path"):
                self.select(args, output)


if __name__ == "__main__":
    unittest.main()
