#!/usr/bin/env python3
"""Bounded F1 workflow using the existing owned-QEMU lifecycle controller."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid

from k3s_bundle import bounded, check, sha
from k3s_execution import execution


def available_memory_mib():
    if platform.system() == "Linux":
        rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        return int(rows["MemAvailable"].split()[0]) // 1024
    cp = subprocess.run(["vm_stat"], capture_output=True, check=True, timeout=5, text=True)
    page = int(re.search(r"page size of (\d+) bytes", cp.stdout)[1])
    counts = [int(re.search(rf"{name}:\s+(\d+)", cp.stdout)[1]) for name in
              ["Pages free", "Pages inactive", "Pages speculative"]]
    return sum(counts) * page // 1024**2


def recovered(stop):
    return (stop.get("qemu_reaped") is True and stop.get("qemu_exit_code") == 0
            and stop.get("escalated") is False and stop.get("evidence_synced") is True)


def verify_run(out, inputs):
    stop = json.loads(bounded(out / "vm-stop.json"))
    if stop.get("status") != "passed" or not recovered(stop) or stop.get("lifecycle_exit_code") != 0:
        raise ValueError("VM lifecycle/evidence failed")
    result = json.loads(bounded(out / "result.json"))
    if result.get("schema_version") != 1 or result.get("scenario") != "k3s-f1":
        raise ValueError("guest evidence schema/scenario mismatch")
    if result.get("status") != "passed" or result.get("exit_code") != 0:
        code = result.get("exit_code")
        if type(code) is not int or code not in {1, 3, 4, 130}:
            raise ValueError("guest failure exit code missing/invalid")
        return code, result
    if (result.get("inputs") != inputs or not re.fullmatch(r"[0-9a-f-]{36}", result.get("boot_id", ""))
            or [c.get("stage") for c in result.get("checks", [])] != ["node_ready", "pod_ready", "dns_tcp", "dns_udp"]
            or any(c.get("status") != "passed" for c in result["checks"])
            or bounded(out / "service-result.txt").strip() != b"success"):
        raise ValueError("guest completion proof mismatch")
    if inputs.get("compat_elf"):
        compat = json.loads(bounded(out / "compat-state.json"))
        if ("apparmor" not in compat.get("lsm", [])
                or compat.get("keeper_profile_loaded_by_f1") is not False
                or compat.get("keeper_child_uid") != inputs["keeper_child_uid"]
                or compat.get("keeper_child_gid") != inputs["keeper_child_gid"]
                or compat.get("host_uid") != inputs["hostUid"]):
            raise ValueError("compatibility/LSM evidence differs")
        for name, expected in inputs["compat_elf"].items():
            row = compat.get("elf", {}).get(name, {})
            if row.get("path") != expected["destination"] or row.get("sha256") != expected["sha256"] or row.get("regular_elf") is not True:
                raise ValueError("compatibility ELF evidence differs")
    checks = result["checks"]
    if checks[0].get("count") != 1 or checks[1].get("count") != 2:
        raise ValueError("Node/Pod count mismatch")
    for check_row, protocol in zip(checks[2:], ["tcp", "udp"], strict=True):
        probe = check_row.get("probe", {})
        if (probe.get("protocol") != protocol or probe.get("count") != 1
                or check_row.get("receiver_count") != 1
                or not re.fullmatch(rf"arena-f1:{protocol}:[0-9a-f]{{32}}", probe.get("payload", ""))
                or probe.get("payload") != probe.get("received")
                or probe.get("host") != "echo.arena-f1.svc.cluster.local"
                or len(probe.get("addresses", [])) != 1):
            raise ValueError("DNS/echo evidence mismatch")
    return 0, result


def workflow(uid, bundle, mode, root, *, scenario="k3s-f1", verifier=verify_run):
    os.umask(0o077)
    inputs, seal = check(bundle)
    if inputs.get("scenario", scenario) != scenario:
        raise ValueError("guest scenario differs from the selected runner")
    if os.getuid() != uid or uid == 0 or inputs["hostUid"] != uid:
        raise ValueError("ordinary host UID differs from prepared guest")
    selected = execution()
    arch = selected["arch"]
    if inputs["system"] != arch + "-linux":
        raise ValueError("guest architecture differs from host")
    if mode not in {"daily", "acceptance", "lifecycle"}:
        raise ValueError("use daily, acceptance or F3 lifecycle")
    if mode == "lifecycle":
        from k3s_checkpoint import CAPABILITY, verify as verify_checkpoint, proof as checkpoint_proof
        if scenario != "k3s-f3" or inputs.get("lifecycle_checkpoint_sha256") != sha(Path(__file__).with_name("k3s_checkpoint.py")):
            raise ValueError("matching F3 checkpoint-capable guest required")
    actions = ["cancel", None, "timeout", None] if mode == "lifecycle" else [None] * (2 if mode == "acceptance" else 1)
    if any(c in str(root.resolve()) + str(bundle.resolve()) for c in [",", "\n"]):
        raise ValueError("unsupported path character")
    limits = inputs["limits"]
    required_gib = len(actions) * limits["writable_run_disk_virtual_gib"] + 2
    if shutil.disk_usage(root.parent).free < required_gib * 1024**3:
        raise ValueError(f"less than {required_gib} GiB free for retained run state")
    top = bounded(bundle / "toplevel").decode().strip()
    if not re.fullmatch(r"/nix/store/[0-9a-z]+-nixos-system-[A-Za-z0-9._+-]+", top):
        raise ValueError("invalid trusted guest toplevel")
    if (os.cpu_count() or 0) < limits["vcpus"]:
        raise ValueError("insufficient host CPUs for the fixed guest")
    if available_memory_mib() < limits["memory_mib"] + 2048:
        raise ValueError("insufficient free/inactive memory for the fixed guest plus 2 GiB headroom")
    root.mkdir(mode=0o700)  # New-only, preserve failures and run disks.
    summary = {"schema_version": 1, "scenario": scenario, "mode": mode,
               "status": "environment_error", "runs": []}
    code = 3
    deadline = time.monotonic() + 600
    try:
        for index, action in enumerate(actions):
            if index and check(bundle) != (inputs, seal):
                raise ValueError("immutable inputs changed between fresh VMs")
            if time.monotonic() >= deadline:
                code = 4
                raise ValueError("600s workflow deadline expired before next VM")
            out = root / f"fresh-{index + 1}"
            out.mkdir(mode=0o700)
            run = {"output": str(out), "status": "environment_error"}
            summary["runs"].append(run)
            run["fixture"] = action
            if action:
                (out / "lifecycle-control.json").write_text(json.dumps(dict(CAPABILITY, action=action)) + "\n")
            manifest = {"scenario": scenario, "run_id": uuid.uuid4().hex,
                        "owner_uid": uid, "owner_pid": os.getpid(), "inputs": inputs,
                        "bundle_sha256": sha(bundle / "bundle.json"), "boot_inputs": seal["files"],
                        "network": "no NIC", "accelerator": selected["accelerator"]}
            (out / "vm-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            disk = out / "run.qcow2"
            subprocess.run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
                            str((bundle / "run-base.qcow2").resolve()), str(disk)], check=True, timeout=10,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
            cmd = [f"qemu-system-{arch}", "-machine", "virt" if arch == "aarch64" else "q35",
                   "-cpu", selected["cpu"], "-accel", selected["accelerator"], "-m", str(limits["memory_mib"]), "-smp", str(limits["vcpus"]), "-nographic",
                   "-monitor", "none", "-no-reboot", "-nic", "none", "-kernel", str(bundle / "Image"),
                   "-initrd", str(bundle / "initrd"), "-append",
                   bounded(bundle / "kernel-params").decode().strip() + f" init={top}/init console=" + ("ttyAMA0" if arch == "aarch64" else "ttyS0"),
                   "-drive", f"file={bundle / 'store.img'},if=virtio,format=raw,readonly=on",
                   "-drive", f"file={disk},if=none,format=qcow2,id=runstate",
                   "-device", "virtio-blk-pci,drive=runstate,serial=arena-run-state",
                   "-virtfs", f"local,path={out},security_model=none,mount_tag=out"]
            (out / "qemu-argv.json").write_text(json.dumps(cmd, indent=2) + "\n")
            # Controller enforces 660 s and owns/reaps only its Child, including cancellation.
            cancelled = False
            child = None
            cancel_sent = False
            def cancel(signum, frame):
                nonlocal cancelled
                cancelled = True
                if child is not None and child.poll() is None:
                    child.send_signal(signal.SIGTERM)
            handlers = {s: signal.signal(s, cancel) for s in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]}
            try:
                child = subprocess.Popen(["timeout", "--signal=TERM", "--kill-after=5", "670",
                                          "veil-arena-vm-controller", *(["--watchdog-fixture"] if action == "timeout" else []),
                                          str(out), *cmd], stdin=subprocess.DEVNULL, start_new_session=True)
                try:
                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired("600s workflow", 600)
                        if action == "cancel" and not cancel_sent and not cancelled and (out / "watchdog-ready.json").exists():
                            checkpoint_proof(out, inputs)
                            child.send_signal(signal.SIGTERM)
                            cancel_sent = True
                        try:
                            controller_exit = child.wait(timeout=min(.05, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                except subprocess.TimeoutExpired:
                    child.send_signal(signal.SIGTERM)
                    controller_exit = child.wait(timeout=15)
                    stop = json.loads(bounded(out / "vm-stop.json"))
                    run["deadline_recovery_verified"] = recovered(stop)
                    code = 4 if recovered(stop) else 3
                    run["controller_exit"] = controller_exit
                    raise ValueError("600s workflow deadline expired; " +
                                     ("owned QEMU recovered" if recovered(stop) else "abnormal VM recovery"))
            finally:
                # Recover only the controller process group created above, including exceptional proof reads.
                if child is not None and child.poll() is None:
                    child.send_signal(signal.SIGTERM)
                    try:
                        child.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=5)
                        code = 3
                        run["cleanup_error"] = "owned controller group required SIGKILL"
                for signum, handler in handlers.items():
                    signal.signal(signum, handler)
            run["controller_exit"] = controller_exit
            if cancelled:
                stop = json.loads(bounded(out / "vm-stop.json"))
                code = 130 if recovered(stop) else 3
                raise ValueError("workflow cancelled; inspect owned VM recovery evidence")
            if action:
                code = 3
                boot = verify_checkpoint(out, inputs, action, controller_exit, cancel_sent)
                run.update(status="passed", boot_id=boot, exit_code=0, fixture_verified=True,
                           observed_exit_code=130 if action == "cancel" else 3)
                continue
            if controller_exit:
                raise ValueError("owned VM lifecycle failed; inspect vm-stop.json and serial.log")
            code, result = verifier(out, inputs)
            run.update(status=result["status"], boot_id=result["boot_id"], exit_code=code)
            if code:
                raise ValueError(f"guest failed at {result.get('stage')}: {result.get('detail')}")
        if check(bundle) != (inputs, seal):
            raise ValueError("immutable inputs changed during workflow")
        if time.monotonic() >= deadline:
            code = 4
            raise ValueError("600s workflow deadline expired during final verification")
        ids = [r["boot_id"] for r in summary["runs"]]
        if len(set(ids)) != len(ids):
            raise ValueError("fresh VMs reused a boot ID")
        summary.update(status="passed", exit_code=0)
        code = 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        summary.update(detail=str(error), exit_code=code or 3,
                       status="failed" if code == 1 else "timeout" if code == 4 else "cancelled" if code == 130 else "environment_error")
        code = code or 3
    finally:
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if code:
        print(f"Failure: {summary.get('detail', 'missing failure detail')}", file=sys.stderr)
    print(f"{scenario} {mode}: {summary['status']} — {root / 'summary.json'}")
    return code


if __name__ == "__main__":
    try:
        if len(sys.argv) != 5 or sys.argv[3] not in {"daily", "acceptance"}:
            print("usage: arena-k3s BUNDLE daily|acceptance NEW_OUTPUT_DIRECTORY", file=sys.stderr)
            sys.exit(2)
        sys.exit(workflow(int(sys.argv[1]), Path(sys.argv[2]).absolute(), sys.argv[3], Path(sys.argv[4]).absolute()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"k3s-f1 preflight: {error}", file=sys.stderr)
        sys.exit(3)
