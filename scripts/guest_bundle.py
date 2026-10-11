#!/usr/bin/env python3
"""Export/check a trusted Nix guest. Hashes identify inputs, not their safety."""
import hashlib
import json
import pathlib
import re
import shutil
import sys


def bounded(path, limit=1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"invalid bounded file: {path.name}")
    return path.read_bytes()


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def check(bundle):
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("bundle must be a directory")
    record = json.loads(bounded(bundle / "bundle.json"))
    manifest_bytes = bounded(bundle / "original-vm-manifest.json")
    manifest = json.loads(manifest_bytes)
    if (manifest.get("scenario") != "netns-batch" or manifest.get("network") != "no NIC"
            or manifest.get("versions") != {"rust": "1.98.1", "cargo": "1.98.0", "python": "3.14.7", "kernel": "6.12.112"}
            or manifest.get("guestSystem") not in {"aarch64-linux", "x86_64-linux"}
            or record.get("schema_version") != 1):
        raise ValueError("unsupported guest contract")
    rows = record.get("files")
    if not isinstance(rows, list) or [row.get("name") for row in rows] != ["store.img", "Image", "initrd"]:
        raise ValueError("boot inputs differ")
    for row in rows:
        path = bundle / row["name"]
        if (path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 2 * 1024**3
                or row.get("bytes") != path.stat().st_size or digest(path) != row.get("sha256")):
            raise ValueError(f"boot input hash/size differs: {row['name']}")
    if (hashlib.sha256(bounded(bundle / "kernel-params", 65536)).hexdigest() != record.get("kernel_params_sha256")
            or hashlib.sha256(manifest_bytes).hexdigest() != record.get("original_manifest_sha256")):
        raise ValueError("guest metadata differs")
    return manifest


def export(vm, bundle):
    # Require a complete normal VM result before exporting its boot inputs.
    from check_vm_result import check as check_vm
    if check_vm(vm, "netns-batch") != 0:
        raise ValueError("source VM did not pass")
    manifest_bytes = bounded(vm / "vm-manifest.json")
    manifest = json.loads(manifest_bytes)
    lines = bounded(vm / "boot-inputs.sha256", 4096).decode().splitlines()
    if len(lines) != 3:
        raise ValueError("expected three boot inputs")
    sources = []
    for name, line in zip(["store.img", "Image", "initrd"], lines, strict=True):
        expected, path = line.split(maxsplit=1)
        path = pathlib.Path(path.strip())
        if (not re.fullmatch(r"[0-9a-f]{64}", expected) or not str(path).startswith("/nix/store/")
                or not str(path.resolve()).startswith("/nix/store/") or not path.is_file()
                or not 0 < path.stat().st_size <= 2 * 1024**3):
            raise ValueError("invalid trusted store input")
        sources.append((name, expected, path))
    params_path = pathlib.Path(manifest["guest_system"]) / "kernel-params"
    if not str(params_path.resolve()).startswith("/nix/store/"):
        raise ValueError("kernel parameters outside Nix store")
    params = bounded(params_path, 65536)
    bundle.mkdir(mode=0o700)  # Preserve existing output; retain partial failures.
    rows = []
    for name, expected, source in sources:
        dest = bundle / name
        shutil.copyfile(source, dest)
        if digest(dest) != expected:
            raise ValueError(f"source hash changed: {name}")
        dest.chmod(0o400)
        rows.append({"name": name, "sha256": expected, "source": str(source), "bytes": dest.stat().st_size})
    (bundle / "kernel-params").write_bytes(params)
    (bundle / "original-vm-manifest.json").write_bytes(manifest_bytes)
    (bundle / "bundle.json").write_text(json.dumps({
        "schema_version": 1, "files": rows,
        "kernel_params_sha256": hashlib.sha256(params).hexdigest(),
        "original_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }, indent=2) + "\n")
    check(bundle)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "check":
            check(pathlib.Path(sys.argv[2]))
        elif len(sys.argv) == 4 and sys.argv[1] == "export":
            export(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))
        else:
            raise ValueError("usage: guest_bundle.py check BUNDLE | export PASSED_VM NEW_BUNDLE")
        print("Guest bundle SHA-256 and contract verified")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(f"Guest bundle error: {error}", file=sys.stderr)
        sys.exit(3)
