#!/usr/bin/env python3
"""Select a mounted-volume copy of the immutable, prepared guest inputs."""
import json
import os
import pathlib
import sys

import guest_bundle


def absolute(value):
    path = pathlib.Path(value)
    if not path.is_absolute() or any(c in str(path) for c in (",", "\n", "\r")):
        raise ValueError("use an absolute path without commas or newlines")
    return path


def select(metadata, trusted, candidate, volume, output=None):
    volume = absolute(volume)
    if volume == pathlib.Path("/") or not os.path.ismount(volume):
        raise ValueError("guest volume is not mounted")
    volume = volume.resolve(strict=True)
    candidate = absolute(candidate)
    if candidate.is_symlink() or not candidate.is_dir():
        raise ValueError("guest bundle must be an existing directory, not a symlink")
    candidate = candidate.resolve(strict=True)
    device = volume.stat().st_dev
    if not candidate.is_relative_to(volume) or candidate.stat().st_dev != device:
        raise ValueError("guest bundle is outside the selected volume")
    if output is not None:
        output = absolute(output)
        if os.path.lexists(output):
            raise ValueError("output path already exists")
        parent = output.parent.resolve(strict=True)
        if not parent.is_relative_to(volume) or parent.stat().st_dev != device:
            raise ValueError("output path is outside the selected volume")
        # Refuse placing evidence inside the boot bundle itself.
        if parent.is_relative_to(candidate):
            raise ValueError("output path is inside the guest bundle")
    trusted = pathlib.Path(trusted)
    for name in ("bundle.json", "original-vm-manifest.json", "kernel-params"):
        if guest_bundle.bounded(trusted / name) != guest_bundle.bounded(candidate / name):
            raise ValueError(f"guest input differs from prepared bundle: {name}")
    guest_bundle.check(candidate)
    # A removal during hashing must not become permission to create output on
    # the directory left behind at a disconnected mount point.
    if not os.path.ismount(volume) or volume.stat().st_dev != device:
        raise ValueError("guest volume changed during verification")
    result = json.loads(guest_bundle.bounded(pathlib.Path(metadata)))
    if (result.get("scenario") != "netns-batch" or result.get("store_image") != str(trusted)
            or result.get("network") != "no NIC"):
        raise ValueError("prepared launcher does not support this guest bundle")
    result.update(store_image=str(candidate), boot_input_storage="verified mounted volume",
                  boot_volume=str(volume))
    return result


if __name__ == "__main__":
    try:
        if len(sys.argv) not in (5, 6):
            raise ValueError("usage: volume_guest.py METADATA TRUSTED_BUNDLE BUNDLE VOLUME [NEW_OUTPUT]")
        print(json.dumps(select(*sys.argv[1:]), sort_keys=True))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(f"Guest volume: {error}", file=sys.stderr)
        sys.exit(3)
