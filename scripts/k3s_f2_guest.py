#!/usr/bin/env python3
"""F2 two-node guest proof. No credential API objects or join tokens in evidence."""
import errno
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from k3s_guest import Failure, image_matches, ready, request_timeout

NAMESPACE = "arena-f2"
NODES = {"arena-k3s-server", "arena-k3s-agent"}
STAGES = ["nodes", "pods", "baseline", "deny", "recovery"]


def placed(pods):
    return ({p["metadata"]["name"]: p["spec"].get("nodeName") for p in pods}
            == {"server": "arena-k3s-server", "client": "arena-k3s-agent", "control": "arena-k3s-agent"})


def policy():
    # Only server ingress: UDP remains open, TCP only from the control Pod.
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
            "metadata": {"name": "arena-selective", "namespace": NAMESPACE},
            "spec": {"podSelector": {"matchLabels": {"app": "echo"}}, "policyTypes": ["Ingress"],
                     "ingress": [{"ports": [{"protocol": "UDP", "port": 18080}]},
                                 {"from": [{"podSelector": {"matchLabels": {"app": "control"}}}],
                                  "ports": [{"protocol": "TCP", "port": 18080}]}]}}


def probe_output(cp, stage, *, action, protocol, address, nonce):
    # kubectl's exit alone cannot prove that the container fixture executed.
    try:
        row = json.loads(cp.stdout)
    except (ValueError, TypeError):
        raise Failure(stage, "exec/fixture evidence missing or malformed", 3) from None
    if not isinstance(row, dict):
        raise Failure(stage, "exec/fixture evidence is not an object", 3)
    if cp.returncode:
        if (cp.returncode == 1 and row.get("schema_version") == 1
                and row.get("kind") == "arena-f2-probe-mismatch"
                and row.get("action") == action and row.get("protocol") == protocol
                and row.get("host") == address and row.get("nonce") == nonce
                and isinstance(row.get("detail"), str) and 0 < len(row["detail"]) <= 1024):
            raise Failure(stage, row["detail"], 1)
        raise Failure(stage, "exec failed without matching fixture-originated evidence", 3)
    if row.get("kind") == "arena-f2-probe-mismatch":
        raise Failure(stage, "fixture mismatch contradicts successful exec status", 3)
    return row


def traffic_probe(kube, log_rows, fixture_command, stage, deadline, client, protocol, address, deny=False):
    probe_end = min(deadline, time.monotonic() + 5)
    nonce = uuid.uuid4().hex
    action = "deny" if deny else "probe"
    cp = kube(["-n", NAMESPACE, "exec", client, "--", *fixture_command,
               action, protocol, address, nonce], probe_end, required=False)
    row = probe_output(cp, stage, action=action, protocol=protocol, address=address, nonce=nonce)
    expected = f"arena-f2:{protocol}:{nonce}"
    ipaddress.IPv4Address(row["addresses"][0])
    if (row.get("protocol") != protocol or row.get("host") != address or len(row["addresses"]) != 1
            or row.get("payload") != expected):
        raise Failure(stage, "probe identity/address mismatch", 1)
    if deny:
        if row.get("outcome") != "denied" or row.get("errno") not in {errno.ETIMEDOUT, errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.EACCES}:
            raise Failure(stage, "target TCP was not refused/timed out", 1)
    elif row.get("received") != expected or row.get("count") != 1:
        raise Failure(stage, "echo mismatch", 1)
    if time.monotonic() >= probe_end:
        raise Failure(stage, "five-second probe deadline expired", 4 if time.monotonic() >= deadline else 3)
    receiver_count = sum(r == {"protocol": protocol, "payload": expected} for r in log_rows(probe_end))
    if time.monotonic() >= probe_end:
        raise Failure(stage, "five-second probe deadline expired", 4 if time.monotonic() >= deadline else 3)
    if receiver_count != (0 if deny else 1):
        raise Failure(stage, "receiver nonce count mismatch", 1)
    return {"client": client, "protocol": protocol, "destination": address,
            "expected": "deny" if deny else "allow", "probe": row, "receiver_count": receiver_count}



def run(inputs, manifest_path, out=Path("/out"), control=Path("/control")):
    start = time.monotonic()
    deadline = start + 600
    stage = "preflight"
    result = {"schema_version": 1, "scenario": "k3s-f2", "role": inputs["role"], "inputs": inputs,
              "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
              "status": "environment_error", "exit_code": 3, "checks": []}

    def save():
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")

    def cmd(args, end, data=None):
        remaining = min(end, deadline) - time.monotonic()
        if remaining <= 0:
            raise Failure(stage, "absolute deadline expired", 4 if time.monotonic() >= deadline else 3)
        try:
            return subprocess.run(args, input=data, capture_output=True, timeout=remaining)
        except subprocess.TimeoutExpired:
            raise Failure(stage, "command exceeded absolute deadline", 4 if time.monotonic() >= deadline else 3) from None

    def kube(args, end, data=None, required=True):
        cp = cmd(["k3s", "kubectl", "--request-timeout=" + request_timeout(end, deadline, time.monotonic()), *args], end, data)
        if required and cp.returncode:
            # Only public fixture commands; never K3s startup/token arguments.
            (out / "last-api-error.json").write_text(json.dumps({"stage": stage, "args": args,
                    "returncode": cp.returncode, "stderr": cp.stderr[:8192].decode(errors="replace")}) + "\n")
            raise Failure(stage, "fixed API operation failed")
        return cp

    def wait(kind, end, namespace=None):
        while time.monotonic() < end:
            cp = kube((["-n", namespace] if namespace else []) + ["get", kind, "-o", "json"], end, required=False)
            if cp.returncode == 0:
                items = json.loads(cp.stdout)["items"]
                (out / f"{namespace or 'cluster'}-{kind}.json").write_text(json.dumps(items, indent=2) + "\n")
                if ready(items) and (kind != "nodes" or {i["metadata"]["name"] for i in items} == NODES):
                    return items
            time.sleep(min(.25, max(0, end - time.monotonic())))
        raise Failure(stage, f"{kind} not Ready before absolute deadline")

    def log_rows(end):
        cp = kube(["-n", NAMESPACE, "logs", "server", "--limit-bytes=65536"], end)
        return [json.loads(line) for line in cp.stdout.splitlines()]

    def probe(client, protocol, address, deny=False):
        return traffic_probe(kube, log_rows, inputs["fixture_command"], stage, deadline,
                             client, protocol, address, deny)

    def mark(name, **fields):
        result["checks"].append({"stage": name, "status": "passed", **fields})
        save()

    try:
        save()
        if (inputs["scenario"] != "k3s-f2" or inputs["role"] not in {"server", "agent"}
                or Path("/sys/module/apparmor/parameters/enabled").read_text().strip() != "Y"
                or not Path("/sys/fs/cgroup/cgroup.controllers").is_file()):
            raise Failure(stage, "guest contract/cgroup/AppArmor unavailable")
        if cmd(["uname", "-r"], deadline).stdout.strip().decode() != inputs["versions"]["kernel"]:
            raise Failure(stage, "kernel differs")
        end = min(deadline, time.monotonic() + 300)
        if cmd(["systemctl", "start", "--no-block", "k3s"], end).returncode:
            raise Failure(stage, "cannot start K3s")
        if inputs["role"] == "agent":
            stage = "agent_ready"
            while time.monotonic() < end:
                cp = cmd(["k3s", "crictl", "inspecti", inputs["imageName"]], end)
                if cp.returncode == 0:
                    image = json.loads(cp.stdout)["status"]
                    if image.get("id") != inputs["fixture_config_digest"]:
                        raise Failure(stage, "agent fixture image config differs", 1)
                    break
                time.sleep(.25)
            else:
                raise Failure(stage, "agent runtime/image not ready")
            (out / "fixture-image-state.json").write_text(json.dumps({k: image.get(k) for k in ["id", "repoTags", "repoDigests"]}) + "\n")
            mark(stage, image_config_digest=image["id"])
            while not (control / "finish").exists():
                if time.monotonic() >= deadline:
                    raise Failure(stage, "server/host finish not received", 4)
                time.sleep(.1)
        else:
            stage = "nodes"
            nodes = wait("nodes", end)
            for node in nodes:
                info = node["status"]["nodeInfo"]
                if (info.get("kubeletVersion") != "v" + inputs["versions"]["k3s"]
                        or info.get("containerRuntimeVersion") != "containerd://" + inputs["versions"]["containerd"]):
                    raise Failure(stage, "node version mismatch", 1)
            mark(stage, names=sorted(NODES))
            stage = "pods"
            end = min(deadline, time.monotonic()+120)
            manifest = json.loads(manifest_path.read_text())
            kube(["apply", "-f", "-"], end, json.dumps(manifest).encode())
            pods = wait("pods", end, NAMESPACE)
            wait("pods", end, "kube-system")
            if not placed(pods):
                raise Failure(stage, "cross-node placement differs", 1)
            image = json.loads(cmd(["k3s", "crictl", "inspecti", inputs["imageName"]], end).stdout)["status"]
            for pod in pods:
                status = pod["status"].get("containerStatuses", [])
                if (len(status) != 1 or status[0].get("ready") is not True
                        or pod["spec"]["containers"][0]["image"] != inputs["imageName"]
                        or not image_matches(image, inputs["fixture_config_digest"], status[0].get("imageID"))):
                    raise Failure(stage, "fixed fixture image identity differs", 1)
            mark(stage, placement={p["metadata"]["name"]:p["spec"]["nodeName"] for p in pods})
            pod_ip = next(p["status"]["podIP"] for p in pods if p["metadata"]["name"] == "server")
            svc = json.loads(kube(["-n", NAMESPACE, "get", "svc", "echo", "-o", "json"], end).stdout)
            service = "echo.arena-f2.svc.cluster.local"
            (out / "service-state.json").write_text(json.dumps(svc, indent=2)+'\n')
            stage = "baseline"
            rows = [probe("client", protocol, address) for address in [pod_ip, service] for protocol in ["tcp", "udp"]]
            if any(row["probe"]["addresses"] != [svc["spec"]["clusterIP"]] for row in rows if row["destination"] == service):
                raise Failure(stage, "Service DNS does not resolve actual Service IP", 1)
            mark(stage, probes=rows)
            stage = "deny"
            desired = policy()
            kube(["apply", "-f", "-"], deadline, json.dumps(desired).encode())
            actual = json.loads(kube(["-n", NAMESPACE, "get", "networkpolicy", "arena-selective", "-o", "json"], deadline).stdout)
            if actual.get("spec") != desired["spec"]:
                raise Failure(stage, "policy configuration not established")
            (out / "policy-state.json").write_text(json.dumps(actual, indent=2)+'\n')
            # Wait only for rule publication, never retry the actual traffic assertion.
            end = min(deadline, time.monotonic()+5)
            while time.monotonic() < end:
                rules = cmd(["iptables-save", "-c"], end)
                if rules.returncode == 0 and b"arena-selective" in rules.stdout:
                    (out / "policy-rules-before.txt").write_bytes(rules.stdout)
                    break
                time.sleep(.1)
            else:
                raise Failure(stage, "policy dataplane rules not published")
            rows = [probe("client", "tcp", address, deny=True) for address in [pod_ip, service]]
            rows += [probe("control", "tcp", service), probe("client", "udp", service)]
            (out / "policy-rules-after.txt").write_bytes(cmd(["iptables-save", "-c"], deadline).stdout)
            mark(stage, probes=rows, policy_uid=actual["metadata"]["uid"])
            stage = "recovery"
            kube(["-n", NAMESPACE, "delete", "networkpolicy", "arena-selective", "--wait=true"], deadline)
            end = min(deadline, time.monotonic()+5)
            while time.monotonic() < end:
                rules = cmd(["iptables-save", "-c"], end)
                if rules.returncode == 0 and b"arena-selective" not in rules.stdout:
                    (out / "policy-rules-removed.txt").write_bytes(rules.stdout)
                    break
                time.sleep(.1)
            else:
                raise Failure(stage, "policy dataplane rules not removed")
            mark(stage, probes=[probe("client", p, a) for a in [pod_ip,service] for p in ["tcp","udp"]])
        result.update(status="passed", exit_code=0)
    except Failure as error:
        result.update(stage=error.stage, detail=error.detail, exit_code=error.code,
                      status="failed" if error.code == 1 else "timeout" if error.code == 4 else "environment_error")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        result.update(stage=stage, detail=f"{type(error).__name__}: {error}", exit_code=3)
    finally:
        result["duration_seconds"] = time.monotonic()-start
        save()
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])))
