#!/usr/bin/env python3
"""One-shot F1 guest verifier. Never emits kubeconfig, tokens or Secrets."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


class Failure(Exception):
    def __init__(self, stage, detail, code=3):
        self.stage, self.detail, self.code = stage, detail, code
        super().__init__(detail)


def request_timeout(end, workflow_end, now):
    # API operations use their remaining stage budget; echo exec still has only 5 s.
    return f"{max(1, int((min(end, workflow_end) - now) * 1000))}ms"


def executable_entry(path, expected):
    # Matching unresolved paths must not make a dangling compatibility link pass.
    try:
        return (path.is_symlink() and expected.is_file() and os.access(expected, os.X_OK)
                and path.resolve(strict=True) == expected.resolve(strict=True))
    except OSError:
        return False


def image_matches(image, expected_config, pod_reference):
    # CRI's image reference may be a repository manifest digest, not a config ID.
    return (image.get("id") == expected_config
            and pod_reference in {expected_config, *image.get("repoDigests", [])})


def ready(items):
    return bool(items) and all(any(c.get("type") == "Ready" and c.get("status") == "True"
                                  for c in item.get("status", {}).get("conditions", [])) for item in items)


def run(out, inputs):
    started = time.monotonic()
    deadline = started + 600
    records = []
    stage = "preflight"
    result = {"schema_version": 1, "scenario": "k3s-f1", "status": "environment_error",
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "inputs": inputs, "checks": records}

    def save():
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    def command(args, end, data=None):
        remaining = min(end, deadline) - time.monotonic()
        if remaining <= 0:
            raise Failure(stage, "absolute stage deadline expired", 4 if time.monotonic() >= deadline else 3)
        try:
            cp = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=remaining, check=False)
        except subprocess.TimeoutExpired:
            raise Failure(stage, "command exceeded absolute deadline", 4 if time.monotonic() >= deadline else 3) from None
        # These commands use only the fixed public fixture/API objects, never Secrets/kubeconfig.
        if cp.returncode:
            (out / "last-command-failure.json").write_text(json.dumps({
                "stage": stage, "argv": args, "exit_code": cp.returncode,
                "stderr": cp.stderr[:8192].decode(errors="replace"),
                "stdout": cp.stdout[:8192].decode(errors="replace"),
            }, indent=2) + "\n")
        return cp

    def kubectl(args, end, data=None):
        return command(["k3s", "kubectl", "--request-timeout=" + request_timeout(end, deadline, time.monotonic()), *args], end, data)

    def wait(kind, end, namespace=None, selector=None, evidence_name=None):
        end = min(deadline, end)
        while time.monotonic() < end:
            args = (["-n", namespace] if namespace else []) + ["get", kind, "-o", "json"]
            if selector:
                args += ["-l", selector]
            cp = kubectl(args, end)
            if cp.returncode == 0:
                items = json.loads(cp.stdout)["items"]
                # Persist only public object state; do not dump credential-bearing API objects.
                (out / (evidence_name or f"{kind}-state.json")).write_text(json.dumps(items, indent=2) + "\n")
                if ready(items) and (kind != "nodes" or len(items) == 1):
                    return items
            time.sleep(min(0.25, max(0, end - time.monotonic())))
        raise Failure(stage, f"{kind} did not become Ready before its absolute deadline")

    try:
        save()
        if os.uname().release != "6.12.112":
            raise Failure(stage, "kernel version mismatch")
        if Path("/sys/module/apparmor/parameters/enabled").read_text().strip() != "Y":
            raise Failure(stage, "AppArmor is not enabled")
        if not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
            raise Failure(stage, "cgroup v2 unavailable")
        lsm = Path("/sys/kernel/security/lsm").read_text().strip().split(",")
        if "apparmor" not in lsm or not Path("/sys/kernel/security/apparmor/profiles").is_file():
            raise Failure(stage, "AppArmor LSM/securityfs unavailable")
        compat = {}
        for name, row in inputs["compat_elf"].items():
            path = Path(row["destination"])
            data = path.read_bytes()
            actual_hash = hashlib.sha256(data).hexdigest()
            if (path.is_symlink() or not data.startswith(b"\x7fELF") or actual_hash != row["sha256"]
                    or path.stat().st_mode & 0o777 != 0o555):
                raise Failure(stage, f"compatibility ELF mismatch: {name}")
            compat[name] = {"path": str(path), "sha256": actual_hash, "regular_elf": True}
        for name, expected in [("python3", inputs["compat_python"]), ("aa-exec", inputs["compat_aa_exec"])]:
            if not executable_entry(Path("/usr/bin", name), Path(expected)):
                raise Failure(stage, f"compatibility entry mismatch: {name}")
        if not os.access(inputs["apparmor_parser"], os.X_OK):
            raise Failure(stage, "fixed parser is not executable")
        (out / "compat-state.json").write_text(json.dumps({
            "elf": compat, "lsm": lsm, "keeper_child_uid": inputs["keeper_child_uid"],
            "keeper_child_gid": inputs["keeper_child_gid"], "host_uid": inputs["hostUid"],
            "parser": inputs["apparmor_parser"], "keeper_profile_loaded_by_f1": False,
        }, indent=2) + "\n")
        # No Keeper profile is loaded by F1.
        stage = "node_ready"
        # Node budget starts before K3s is started, not at the first successful API call.
        end = min(deadline, time.monotonic() + 300)
        cp = command(["systemctl", "start", "--no-block", "k3s"], end)
        if cp.returncode:
            raise Failure(stage, "cannot start K3s")
        nodes = wait("nodes", end)
        records.append({"stage": stage, "status": "passed", "count": len(nodes)})
        save()
        node = nodes[0]
        info = node["status"]["nodeInfo"]
        if (node["metadata"]["name"] != "arena-k3s-server"
                or info.get("kubeletVersion") != "v" + inputs["versions"]["k3s"]
                or info.get("containerRuntimeVersion") != "containerd://" + inputs["versions"]["containerd"]):
            raise Failure(stage, "node identity/version mismatch", 1)
        stage = "pod_ready"
        pod_end = min(deadline, time.monotonic() + 120)
        manifest = json.loads(Path(sys.argv[2]).read_text())
        cp = kubectl(["apply", "-f", "-"], pod_end, json.dumps(manifest).encode())
        if cp.returncode:
            raise Failure(stage, "fixture manifest apply failed")
        pods = wait("pods", pod_end, "arena-f1")
        if {p["metadata"]["name"] for p in pods} != {"server", "client"}:
            raise Failure(stage, "fixture Pod membership mismatch", 1)
        wait("pods", pod_end, "kube-system", "k8s-app=kube-dns", "coredns-state.json")
        inspect = command(["k3s", "crictl", "inspecti", inputs["imageName"]], pod_end)
        if inspect.returncode:
            raise Failure(stage, "fixture runtime image identity unavailable")
        image = json.loads(inspect.stdout)["status"]
        (out / "fixture-image-state.json").write_text(json.dumps({
            key: image.get(key) for key in ["id", "repoTags", "repoDigests"]
        }, indent=2) + "\n")
        expected_id = inputs["fixture_config_digest"]
        if image.get("id") != expected_id:
            raise Failure(stage, "fixture runtime config digest mismatch", 1)
        for pod in pods:
            containers = pod["status"].get("containerStatuses", [])
            if (pod["spec"].get("nodeName") != node["metadata"]["name"]
                    or len(containers) != 1 or containers[0].get("ready") is not True
                    or pod["spec"]["containers"][0]["image"] != inputs["imageName"]
                    or not image_matches(image, expected_id, containers[0].get("imageID", ""))):
                raise Failure(stage, "Pod node/image identity mismatch", 1)
        records.append({"stage": stage, "status": "passed", "count": len(pods),
                        "image_config_digest": expected_id, "node": node["metadata"]["name"]})
        save()
        service = kubectl(["-n", "arena-f1", "get", "svc", "echo", "-o", "json"], deadline)
        if service.returncode:
            raise Failure(stage, "Service evidence unavailable")
        service_ip = json.loads(service.stdout)["spec"]["clusterIP"]
        for protocol in ["tcp", "udp"]:
            stage = f"dns_{protocol}"
            nonce = uuid.uuid4().hex
            cp = kubectl(["-n", "arena-f1", "exec", "client", "--", *inputs["fixture_command"],
                          "probe", protocol, "echo.arena-f1.svc.cluster.local", nonce], time.monotonic() + 5)
            if cp.returncode:
                raise Failure(stage, "bounded DNS/echo command failed", 1)
            probe = json.loads(cp.stdout)
            expected = f"arena-f1:{protocol}:{nonce}"
            if (probe.get("addresses") != [service_ip] or probe.get("payload") != expected
                    or probe.get("received") != expected or probe.get("count") != 1):
                raise Failure(stage, "DNS address or echo mismatch", 1)
            logs = kubectl(["-n", "arena-f1", "logs", "server", "--limit-bytes=8192"], deadline)
            receiver = [json.loads(line) for line in logs.stdout.splitlines()] if logs.returncode == 0 else []
            if sum(r == {"protocol": protocol, "payload": expected} for r in receiver) != 1:
                raise Failure(stage, "receiver count/payload mismatch", 1)
            records.append({"stage": stage, "status": "passed", "probe": probe,
                            "receiver_count": 1})
            save()
        result.update(status="passed", exit_code=0)
    except Failure as error:
        result.update(status="failed" if error.code == 1 else "timeout" if error.code == 4 else "environment_error",
                      stage=error.stage, detail=error.detail, exit_code=error.code)
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(stage=stage, detail=f"{type(error).__name__}: {error}", exit_code=3)
    finally:
        # Diagnostic collection does not retry the trial or extend its readiness deadline.
        if result["status"] != "passed" and deadline - time.monotonic() > 0:
            try:
                cp = subprocess.run(["k3s", "kubectl", "--request-timeout=1s", "-n", "arena-f1",
                                     "get", "events", "-o", "json"], stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=min(2, deadline - time.monotonic()))
                if cp.returncode == 0 and len(cp.stdout) <= 131072:
                    (out / "fixture-events.json").write_text(json.dumps(json.loads(cp.stdout), indent=2) + "\n")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                result["event_diagnostic"] = "unavailable; original failure retained"
        result["duration_seconds"] = time.monotonic() - started
        save()
    return result["exit_code"]


if __name__ == "__main__":
    out = Path("/out")
    sys.exit(run(out, json.loads(Path(sys.argv[1]).read_text())))
