#!/usr/bin/env python3
"""Own exactly two QEMU controllers on a private UNIX-stream LAN; retain evidence."""
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
import uuid

from k3s_bundle import bounded, check, sha
from k3s_host import available_memory_mib, recovered
from k3s_f2_guest import STAGES, placed, policy

ROLES = {"server": (4, 16384), "agent": (2, 4096)}


class GuestFailure(ValueError):
    def __init__(self, role, result):
        code = result.get("exit_code")
        if type(code) is not int or code not in {1, 3, 4, 130}:
            raise ValueError(role + ": invalid guest failure exit")
        self.code = code
        self.role = role
        self.stage = result.get("stage")
        self.detail = result.get("detail")
        self.status = {1: "failed", 3: "environment_error", 4: "timeout", 130: "cancelled"}[code]
        super().__init__(f"{role}: {self.stage}: {self.detail}")


def record_guest_failure(item, error):
    fields = {"status": error.status, "exit_code": error.code,
              "stage": error.stage, "detail": error.detail}
    item.update(fields, failed_node=error.role)
    item["guest_failure"] = {"role": error.role, **fields}
    item["nodes"][error.role] = fields.copy()


def verify_cluster(root, inputs):
    results = {}
    for role in ROLES:
        out = root / role
        stop = json.loads(bounded(out / "vm-stop.json"))
        # Lifecycle evidence always takes priority over partial guest reports.
        if (stop.get("status") != "passed" or not recovered(stop)
                or stop.get("lifecycle_exit_code") != 0 or stop.get("evidence_synced_before_stop") is not True):
            raise ValueError(f"{role}: abnormal VM lifecycle/evidence")
        row = json.loads(bounded(out / "result.json"))
        if (row.get("schema_version") != 1 or row.get("scenario") != "k3s-f2"
                or row.get("role") != role or row.get("inputs") != inputs[role]
                or not re.fullmatch(r"[0-9a-f-]{36}", row.get("boot_id", ""))):
            raise ValueError(f"{role}: guest identity/completion proof invalid")
        if row.get("status") != "passed" or row.get("exit_code") != 0:
            raise GuestFailure(role, row)
        if bounded(out / "service-result.txt").strip() != b"success":
            raise ValueError(f"{role}: service completion marker differs")
        results[role] = row
    server = results["server"]
    if [r.get("stage") for r in server.get("checks", [])] != STAGES or any(r.get("status") != "passed" for r in server["checks"]):
        raise ValueError("missing/failed server stages")
    if server["checks"][0].get("names") != ["arena-k3s-agent", "arena-k3s-server"]:
        raise ValueError("two Ready node identities missing")
    if server["checks"][1].get("placement") != {"server":"arena-k3s-server", "client":"arena-k3s-agent", "control":"arena-k3s-agent"}:
        raise ValueError("cross-node Pod placement missing")
    pods = json.loads(bounded(root / "server" / "arena-f2-pods.json"))
    if not placed(pods):
        raise ValueError("actual Pod placement differs")
    server_ip = next(p["status"]["podIP"] for p in pods if p["metadata"]["name"] == "server")
    svc = json.loads(bounded(root / "server" / "service-state.json"))["spec"]["clusterIP"]
    dns = "echo.arena-f2.svc.cluster.local"
    policy_rules = [bounded(root/"server"/f"policy-rules-{phase}.txt") for phase in ["before","after","removed"]]
    if any(not rules.strip() for rules in policy_rules):
        raise ValueError("empty policy dataplane evidence")
    seen = set()
    for stage in server["checks"][2:]:
        expected = ([("client", p, a, "allow") for a in [server_ip, dns] for p in ["tcp", "udp"]]
                    if stage["stage"] != "deny" else
                    [("client", "tcp", a, "deny") for a in [server_ip, dns]] +
                    [("control","tcp",dns,"allow"),("client","udp",dns,"allow")])
        rows = stage.get("probes", [])
        if [(r.get("client"),r.get("protocol"),r.get("destination"),r.get("expected")) for r in rows] != expected:
            raise ValueError("missing target/control/direct/Service probe")
        for row in rows:
            p = row["probe"]
            deny = row["expected"] == "deny"
            payload = p.get("payload", "")
            if (payload in seen or not re.fullmatch(rf"arena-f2:{row['protocol']}:[0-9a-f]{{32}}", payload)
                    or p.get("addresses") != [svc if row["destination"] == dns else server_ip]
                    or p.get("host") != row["destination"] or p.get("protocol") != row["protocol"]
                    or row.get("receiver_count") != (0 if deny else 1)):
                raise ValueError("probe identity/receiver/DNS evidence differs")
            seen.add(payload)
            if deny:
                if p.get("outcome") != "denied" or p.get("errno") not in {110, 111, 113, 13}:
                    raise ValueError("deny is not bounded network refusal/timeout")
            elif p.get("received") != payload or p.get("count") != 1:
                raise ValueError("echo/count mismatch")
    actual = json.loads(bounded(root/"server"/"policy-state.json"))
    if (actual.get("spec") != policy()["spec"] or not actual.get("metadata",{}).get("uid")
            or server["checks"][3].get("policy_uid") != actual["metadata"]["uid"]
            or b"arena-selective" not in bounded(root/"server"/"policy-rules-before.txt")
            or b"arena-selective" not in bounded(root/"server"/"policy-rules-after.txt")
            or b"arena-selective" in bounded(root/"server"/"policy-rules-removed.txt")):
        raise ValueError("policy configuration/publication/removal evidence differs")
    agent = results["agent"]
    if (agent.get("checks") != [{"stage":"agent_ready","status":"passed","image_config_digest":inputs["agent"]["fixture_config_digest"]}]
            or json.loads(bounded(root/"agent"/"fixture-image-state.json")).get("id") != inputs["agent"]["fixture_config_digest"]):
        raise ValueError("agent runtime image proof missing")
    if results["server"]["boot_id"] == results["agent"]["boot_id"]:
        raise ValueError("nodes reused boot identity")
    return {role: row["boot_id"] for role,row in results.items()}


def qemu_args(arch, bundle, out, control, socket, role, inputs):
    cpu, memory = ROLES[role]
    top = bounded(bundle / "toplevel").decode().strip()
    if not re.fullmatch(r"/nix/store/[0-9a-z]+-nixos-system-[A-Za-z0-9._+-]+", top):
        raise ValueError("invalid trusted toplevel")
    return [f"qemu-system-{arch}", "-machine", "virt" if arch=="aarch64" else "q35", "-cpu","max",
            "-accel","tcg","-m",str(memory),"-smp",str(cpu),"-nographic","-monitor","none","-no-reboot",
            "-nodefaults", "-serial", "stdio",
            "-netdev",f"stream,id=lan,server={'on' if role=='server' else 'off'},addr.type=unix,addr.path={socket}",
            "-device",f"virtio-net-pci,netdev=lan,mac=52:54:00:77:10:{'01' if role=='server' else '02'}",
            "-kernel",str(bundle/"Image"),"-initrd",str(bundle/"initrd"),"-append",
            bounded(bundle/"kernel-params").decode().strip()+f" init={top}/init console="+("ttyAMA0" if arch=="aarch64" else "ttyS0"),
            "-drive",f"file={bundle/'store.img'},if=virtio,format=raw,readonly=on",
            "-drive",f"file={out/'run.qcow2'},if=none,format=qcow2,id=runstate",
            "-device","virtio-blk-pci,drive=runstate,serial=arena-run-state",
            "-virtfs",f"local,path={out},security_model=none,mount_tag=out",
            "-virtfs",f"local,path={control},security_model=none,mount_tag=control,readonly=on"]


def collect(children):
    # Signal only controllers started here; their Child ownership protects other VMs.
    for child in children.values():
        if child.poll() is None:
            child.send_signal(signal.SIGTERM)
    errors=[]
    for role,child in children.items():
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Each controller has its own new session; this also reaches only its QEMU.
            os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=5)
            errors.append(role+": controller cleanup exceeded deadline")
    return errors


def workflow(uid, bundles, mode, root):
    os.umask(0o077)
    if mode not in {"daily","acceptance"} or os.getuid() != uid or uid == 0:
        raise ValueError("mode/ordinary host UID invalid")
    inputs, seals = {},{}
    for role,path in bundles.items():
        inputs[role],seals[role] = check(path)
        if inputs[role].get("role")!=role or inputs[role].get("scenario")!="k3s-f2" or inputs[role]["hostUid"]!=uid:
            raise ValueError("F2 bundle role/scenario/UID differs")
    arch = "aarch64" if platform.machine() in {"arm64","aarch64"} else "x86_64"
    if any(i["system"] != arch+"-linux" for i in inputs.values()):
        raise ValueError("guest architecture differs")
    if inputs["server"]["fixture_config_digest"]!=inputs["agent"]["fixture_config_digest"]:
        raise ValueError("node fixture identities differ")
    if (os.cpu_count() or 0)<6 or available_memory_mib()<24576:
        raise ValueError("requires 6 host CPUs and 24 GiB available/inactive RAM")
    count=2 if mode=="acceptance" else 1
    if shutil.disk_usage(root.parent).free<(count*64+2)*1024**3:
        raise ValueError("insufficient disk for retained fresh clusters")
    if any(c in str(root)+''.join(map(str,bundles.values())) for c in [",","\n"]):
        raise ValueError("unsupported path character")
    root.mkdir(mode=0o700)
    summary={"schema_version":1,"scenario":"k3s-f2","mode":mode,"status":"environment_error","runs":[]}
    deadline=time.monotonic()+600
    code=3
    cancelled=False
    def cancel(signum,frame):
        nonlocal cancelled
        cancelled=True
    handlers={s:signal.signal(s,cancel) for s in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]}
    ids=set()
    try:
        for index in range(count):
            run=root/f"fresh-{index+1}"
            run.mkdir(mode=0o700)
            control=run/"private-control"; control.mkdir(mode=0o700)
            (control/"join-token").write_text(secrets.token_hex(32))
            # Short path avoids AF_UNIX platform length limits. Retain directory; QEMU owns its socket.
            lane=Path("/tmp")/("arena-f2-"+uuid.uuid4().hex[:16]);lane.mkdir(mode=0o700)
            socket=lane/"lan.sock"
            item={"output":str(run),"status":"environment_error","nodes":{},"network":"private UNIX stream; no host bridge/NAT/port"}
            summary["runs"].append(item)
            children={}
            try:
                for role,bundle in bundles.items():
                    if cancelled or time.monotonic()>=deadline:
                        code=130 if cancelled else 4
                        raise ValueError("cancelled/workflow deadline before node launch")
                    if check(bundle)!=(inputs[role],seals[role]):
                        raise ValueError("immutable node inputs changed")
                    out=run/role;out.mkdir(mode=0o700)
                    subprocess.run(["qemu-img","create","-f","qcow2","-F","qcow2","-b",str((bundle/"run-base.qcow2").resolve()),str(out/"run.qcow2")],check=True,timeout=10,stdout=subprocess.DEVNULL)
                    args=qemu_args(arch,bundle,out,control,socket,role,inputs[role])
                    (out/"qemu-argv.json").write_text(json.dumps(args,indent=2)+'\n')
                    (out/"vm-manifest.json").write_text(json.dumps({"scenario":"k3s-f2","role":role,"owner_uid":uid,"owner_pid":os.getpid(),"inputs":inputs[role],"boot_inputs":seals[role]["files"],"bundle_sha256":sha(bundle/"bundle.json")},indent=2)+'\n')
                    children[role]=subprocess.Popen(["timeout","--signal=TERM","--kill-after=5","670","veil-arena-vm-controller",str(out),*args],stdin=subprocess.DEVNULL,start_new_session=True)
                    if role=="server":
                        end=min(deadline,time.monotonic()+5)
                        while not socket.exists():
                            if children[role].poll() is not None or cancelled or time.monotonic()>=end:
                                raise ValueError("owned server LAN socket not established")
                            time.sleep(.025)
                while any(c.poll() is None for c in children.values()):
                    if cancelled or time.monotonic()>=deadline:
                        code=130 if cancelled else 4
                        raise ValueError("workflow cancelled" if cancelled else "600s shared workflow deadline")
                    if children["server"].poll() is not None and not (control/"finish").exists():
                        (control/"finish").write_text("server controller completed\n")
                    for role, child in children.items():
                        if child.poll() == 0:
                            completed=json.loads(bounded(run/role/"result.json"))
                            if completed.get("status") != "passed":
                                raise GuestFailure(role, completed)
                    if any(c.poll() not in {None,0} for c in children.values()):
                        raise ValueError("node lifecycle controller failed")
                    time.sleep(.1)
                boot_ids=verify_cluster(run,inputs)
                if ids.intersection(boot_ids.values()):
                    raise ValueError("fresh cluster reused boot identity")
                ids.update(boot_ids.values())
                item.update(status="passed",boot_ids=boot_ids)
            except GuestFailure as error:
                record_guest_failure(item, error)
                raise
            finally:
                errors=collect(children)
                item["controller_exits"]={r:c.returncode for r,c in children.items()}
                item["recovery"]={}
                for role in children:
                    try:
                        stop=json.loads(bounded(run/role/"vm-stop.json"))
                        item["recovery"][role]=stop
                        if (stop.get("qemu_reaped") is not True or stop.get("escalated") is not False
                                or stop.get("evidence_synced") is not True):
                            errors.append(role+": abnormal QEMU/evidence recovery")
                    except (OSError,ValueError,KeyError,TypeError):
                        errors.append(role+": recovery evidence unavailable")
                item["cleanup_errors"]=errors
                if errors:
                    code=3
                    item.update(status="environment_error", exit_code=3, stage="cleanup", detail='; '.join(errors))
                    raise ValueError('; '.join(errors))
        for role,bundle in bundles.items():
            if check(bundle)!=(inputs[role],seals[role]):
                raise ValueError("immutable inputs changed during workflow")
        if time.monotonic()>=deadline:
            code=4
            raise ValueError("600s shared workflow expired during verification")
        code=0;summary["status"]="passed"
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as error:
        if isinstance(error, GuestFailure): code=error.code
        summary.update(detail=str(error),status="cancelled" if code==130 else "timeout" if code==4 else "failed" if code==1 else "environment_error")
    finally:
        for s,h in handlers.items():signal.signal(s,h)
        summary["exit_code"]=code
        (root/"summary.json").write_text(json.dumps(summary,indent=2)+'\n')
    if code:print("Failure: "+summary.get("detail","unknown"),file=sys.stderr)
    print(f"k3s-f2 {mode}: {summary['status']} — {root/'summary.json'}")
    return code


if __name__=="__main__":
    try:
        if len(sys.argv)!=6:
            raise ValueError("usage: arena-k3s-f2 SERVER_BUNDLE AGENT_BUNDLE daily|acceptance NEW_OUTPUT")
        sys.exit(workflow(int(sys.argv[1]),dict(zip(ROLES,map(lambda p:Path(p).absolute(),sys.argv[2:4]),strict=True)),sys.argv[4],Path(sys.argv[5]).absolute()))
    except (OSError,ValueError,KeyError,TypeError) as error:
        print("k3s-f2 preflight: "+str(error),file=sys.stderr);sys.exit(3)
