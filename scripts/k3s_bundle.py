#!/usr/bin/env python3
"""Hash-seal F1 boot inputs; prepare once, verify before every run."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile

KEEPER_INPUTS = {
    "tools/m0_exec_check.py": "108372f8dbe35f354c2fae7dc8708353a02324e186ae611b59afc639ef760eed",
    "experiments/apparmor/m0-exec-check.profile": "e56379b741145cf8caa07f2e2bff9e47a8dbd00f2c781e95af1af53e006d3cc6",
}

FILES = ["store.img", "Image", "initrd", "kernel-params", "inputs.json", "fixtures.json", "toplevel", "run-base.qcow2"]
LIMITS = {"vcpus": 4, "memory_mib": 16384, "writable_run_disk_virtual_gib": 32,
          "node_ready_seconds": 300, "pod_ready_seconds": 120, "io_probe_seconds": 5,
          "workflow_seconds": 600, "host_watchdog_seconds": 660}

VERSIONS = {"rust": "1.98.1", "cargo_binary": "1.98.0", "python": "3.14.7",
            "kernel": "6.12.112", "qemu": "11.1.1", "k3s": "1.36.4+k3s1",
            "containerd": "2.3.4-k3s1.36", "flannel": "v0.28.4", "apparmor": "5.0.2", "coreutils": "9.11"}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def bounded(path, limit=1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"invalid file: {path.name}")
    return path.read_bytes()


def check(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("bundle must be a real directory")
    record = json.loads(bounded(root / "bundle.json"))
    if record.get("schema_version") != 1 or set(record.get("files", {})) != set(FILES):
        raise ValueError("bundle file contract mismatch")
    for name, row in record["files"].items():
        path = root / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size != row["bytes"]
                or not 0 < path.stat().st_size <= 8 * 1024**3 or sha(path) != row["sha256"]):
            raise ValueError(f"boot input differs: {name}")
    inputs = json.loads(bounded(root / "inputs.json"))
    expected = dict(LIMITS)
    if inputs.get("scenario") == "k3s-f2":
        if inputs.get("role") not in {"server", "agent"}:
            raise ValueError("invalid F2 node role")
        if inputs["role"] == "agent":
            expected.update(vcpus=2, memory_mib=4096)
    if inputs.get("scenario") == "k3s-f3":
        keeper = inputs.get("keeper", {})
        if (keeper.get("input_sha256") != KEEPER_INPUTS
                or keeper.get("profile_name") != "veil_keeper_m0_exec_check_20261016"
                or keeper.get("child_uid") != 501 or keeper.get("child_gid") != 1000
                or keeper.get("command_timeout_seconds") != 20):
            raise ValueError("F0 Keeper fixed inputs differ")
    if any(inputs.get("limits", {}).get(k) != v for k, v in expected.items()):
        raise ValueError("F0 resource or deadline contract mismatch")
    if inputs.get("versions") != VERSIONS:
        raise ValueError("fixed F0 versions differ")
    if sha(root / "fixtures.json") != inputs.get("manifest_sha256"):
        raise ValueError("fixture manifest differs")
    compat = inputs.get("compat_elf", {})
    if (set(compat) != {"true", "false"} or record.get("compat_elf_source_verified") is not True
            or any(row.get("destination") != f"/usr/bin/{name}" or len(row.get("sha256", "")) != 64
                   or not row.get("source", "").startswith("/nix/store/") for name, row in compat.items())):
        raise ValueError("F0 compatibility ELF preparation missing/invalid")
    if inputs["system"] not in {"aarch64-linux", "x86_64-linux"}:
        raise ValueError("unsupported guest architecture")
    if record["airgap_sha256"] != inputs["airgap_sha256"]:
        raise ValueError("airgap hash mismatch")
    return inputs, record


def seal(root, airgap, fixture):
    inputs = json.loads(bounded(root / "inputs.json"))
    for key in ["compat_python", "compat_aa_exec", "apparmor_parser"]:
        path = Path(inputs[key])
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError(f"fixed executable missing: {key}")
    for row in inputs["compat_elf"].values():
        source = Path(row["source"])
        with source.open("rb") as stream:
            magic = stream.read(4)
        if magic != b"\x7fELF" or sha(source) != row["sha256"]:
            raise ValueError("fixed compatibility ELF source differs")
    if sha(airgap) != inputs["airgap_sha256"]:
        raise ValueError("airgap archive differs from F0")
    with tarfile.open(fixture) as archive:
        manifest = json.load(archive.extractfile("manifest.json"))
        if len(manifest) != 1 or manifest[0]["RepoTags"] != [inputs["imageName"].removeprefix("docker.io/")]:
            raise ValueError("fixture image tag mismatch")
        config_bytes = archive.extractfile(manifest[0]["Config"]).read()
        config = json.loads(config_bytes)
        if config["config"]["Cmd"] != inputs["fixture_command"]:
            raise ValueError("fixture command mismatch")
        descriptors = []
        for name in manifest[0]["Layers"]:
            member = archive.getmember(name)
            with archive.extractfile(member) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            descriptors.append({"mediaType": "application/vnd.oci.image.layer.v1.tar",
                                "size": member.size, "digest": "sha256:" + digest})
    config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    oci = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
           "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "size": len(config_bytes),
                      "digest": config_digest}, "layers": descriptors}
    oci_bytes = json.dumps(oci, sort_keys=True, separators=(",", ":")).encode()
    record = {"schema_version": 1, "compat_elf_source_verified": True, "files": {name: {"sha256": sha(root / name), "bytes": (root / name).stat().st_size}
                                             for name in FILES},
              "airgap_sha256": sha(airgap), "fixture_archive_sha256": sha(fixture),
              "fixture_config_digest": config_digest,
              "fixture_oci_manifest": oci,
              "fixture_oci_manifest_digest": "sha256:" + hashlib.sha256(oci_bytes).hexdigest()}
    with (root / "bundle.json").open("x") as dest:
        dest.write(json.dumps(record, indent=2) + "\n")
    check(root)


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "seal":
        seal(*map(Path, sys.argv[2:]))
    elif len(sys.argv) == 3 and sys.argv[1] == "check":
        check(Path(sys.argv[2]))
    else:
        raise SystemExit("usage: k3s_bundle.py check BUNDLE | seal BUNDLE AIRGAP FIXTURE")
