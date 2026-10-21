"""F3 Node Ready fault fixture; proofs are published only after SIGSTOP is observed."""
import json
import os
from pathlib import Path
import signal
import time

from k3s_bundle import bounded

CHECKPOINT = "f3-node-ready-before-keeper"
CAPABILITY = {"schema_version": 1, "checkpoint": CHECKPOINT}


def publish(path, value):
    pending = path.with_name(path.name + ".pending")
    with pending.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    # Publish a complete file without replacing any previous evidence.
    os.link(pending, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def configuration(out):
    path = out / "lifecycle-control.json"
    if not os.path.lexists(path):
        return None
    row = json.loads(bounded(path))
    if row not in [dict(CAPABILITY, action=action) for action in ("cancel", "timeout")]:
        raise ValueError("invalid lifecycle fixture control")
    return row


def stop_at_checkpoint(out, result):
    from keeper_trial import inventory, target
    profiles = inventory()
    if target(profiles):
        raise ValueError("Keeper target profile exists before checkpoint")
    parent = os.getpid()
    observer = os.fork()
    if observer == 0:
        code = 3
        try:
            if os.getppid() != parent:
                raise ValueError("observer lost its owned parent")
            os.kill(parent, signal.SIGSTOP)
            end = time.monotonic() + 5
            while time.monotonic() < end:
                status = Path(f"/proc/{parent}/status").read_text()
                if any(line.startswith("State:\tT") for line in status.splitlines()):
                    break
                time.sleep(.01)
            else:
                raise ValueError("runner did not enter stopped state")
            publish(out / "checkpoint-state.json", {"schema_version": 1, "checkpoint": CHECKPOINT,
                    "boot_id": result["boot_id"], "runner_pid": parent, "observer_pid": os.getpid(),
                    "proc_status": status, "profiles": profiles, "keeper_not_started": True})
            publish(out / "watchdog-ready.json", {"schema_version": 1, "scenario": "guest-runner-stopped",
                    "checkpoint": CHECKPOINT, "boot_id": result["boot_id"], "batch_pid": parent,
                    "observer_pid": os.getpid(), "state": "T", "signal": int(signal.SIGSTOP)})
            code = 0
        except (OSError, ValueError) as error:
            try:
                publish(out / "checkpoint-error.json", {"detail": str(error)})
            finally:
                os.kill(parent, signal.SIGCONT)
        finally:
            os._exit(code)
    _, status = os.waitpid(observer, 0)
    # A successful fixture remains stopped until its owned VM is terminated.
    # Reaching this line means either injection failed or someone resumed it.
    raise ValueError(f"stopped checkpoint unexpectedly resumed; observer status {status}")


def proof(out, inputs):
    from k3s_guest import ready
    from keeper_trial import target
    control = configuration(out)
    partial = json.loads(bounded(out / "result.json"))
    state = json.loads(bounded(out / "checkpoint-state.json"))
    gate = json.loads(bounded(out / "watchdog-ready.json"))
    nodes = json.loads(bounded(out / "cluster-nodes.json"))
    if (control is None or not isinstance(partial, dict) or not isinstance(state, dict)
            or not isinstance(gate, dict) or not isinstance(nodes, list) or len(nodes) != 1 or not ready(nodes)
            or nodes[0]["metadata"].get("name") != "arena-k3s-server"
            or nodes[0]["status"]["nodeInfo"].get("kubeletVersion") != "v" + inputs["versions"]["k3s"]
            or nodes[0]["status"]["nodeInfo"].get("containerRuntimeVersion") != "containerd://" + inputs["versions"]["containerd"]
            or partial.get("schema_version") != 1 or partial.get("scenario") != "k3s-f3"
            or partial.get("inputs") != inputs or partial.get("lifecycle_fixture") != control
            or partial.get("stage") != CHECKPOINT or partial.get("status") != "environment_error"
            or partial.get("exit_code") != 3
            or partial.get("checks") != [{"stage": "node_ready", "status": "passed", "count": 1}]):
        raise ValueError("partial Node Ready evidence differs")
    boot = partial.get("boot_id", "")
    import re
    if not re.fullmatch(r"[0-9a-f-]{36}", boot):
        raise ValueError("checkpoint boot identity invalid")
    parent, observer = gate.get("batch_pid"), gate.get("observer_pid")
    profiles = state.get("profiles")
    if (gate.get("schema_version") != 1 or gate.get("scenario") != "guest-runner-stopped"
            or gate.get("checkpoint") != CHECKPOINT or gate.get("state") != "T" or gate.get("signal") != 19
            or any(type(p) is not int or not 1 < p <= 2147483647 for p in (parent, observer)) or parent == observer
            or state.get("schema_version") != 1 or state.get("checkpoint") != CHECKPOINT
            or state.get("runner_pid") != parent or state.get("observer_pid") != observer
            or gate.get("boot_id") != boot or state.get("boot_id") != boot
            or state.get("keeper_not_started") is not True
            or not isinstance(profiles, list) or any(not isinstance(p, str) for p in profiles) or target(profiles)
            or not isinstance(state.get("proc_status"), str)
            or f"Pid:\t{parent}" not in state["proc_status"].splitlines()
            or not any(line.startswith("State:\tT") for line in state["proc_status"].splitlines())
            or os.path.lexists(out / "checkpoint-error.json")
            or os.path.lexists(out / "keeper-result.json") or os.path.lexists(out / "service-result.txt")):
        raise ValueError("stopped-runner identity/profile/completion proof differs")
    return boot


def verify(out, inputs, action, controller_exit, cancel_sent):
    stop = json.loads(bounded(out / "vm-stop.json"))
    if (not isinstance(stop, dict) or stop.get("schema_version") != 2
            or stop.get("status") != "environment_error" or stop.get("lifecycle_exit_code") != 3
            or controller_exit != 3 or stop.get("qemu_reaped") is not True or stop.get("qemu_exit_code") != 0
            or stop.get("escalated") is not False or stop.get("evidence_synced") is not True
            or stop.get("evidence_synced_before_stop") is not True or stop.get("sync_error") is not None):
        raise ValueError("fixture VM lifecycle/evidence recovery failed")
    boot = proof(out, inputs)
    if configuration(out)["action"] != action:
        raise ValueError("fixture action differs")
    if action == "cancel":
        if not cancel_sent or stop.get("reason") != "cancelled" or stop.get("watchdog_fixture") is not False:
            raise ValueError("checkpoint cancellation not proven")
    elif action == "timeout":
        if (cancel_sent or stop.get("reason") != "watchdog" or stop.get("watchdog_fixture") is not True
                or stop.get("checkpoint_seen") is not True or stop.get("timeout_after_checkpoint_ms") != 2000
                or type(stop.get("checkpoint_elapsed_ms")) is not int or stop["checkpoint_elapsed_ms"] < 2000
                or stop.get("startup_timeout_ms") != 120000):
            raise ValueError("checkpoint watchdog deadline not proven")
    else:
        raise ValueError("unknown fixture action")
    return boot
