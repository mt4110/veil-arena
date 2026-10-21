#!/usr/bin/env python3
"""Run the existing F2 and F3 fresh acceptance workflows sequentially."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys

from k3s_bundle import bounded, check
from k3s_execution import execution
from k3s_host import available_memory_mib, workflow as single_workflow
from k3s_f2_host import workflow as cluster_workflow, verify_cluster
from k3s_f3_host import verify_run


def preflight(uid, bundles, root):
    if uid == 0 or os.getuid() != uid:
        raise ValueError("prepared ordinary host UID required")
    selected = execution()
    inputs = {}
    for role, path in bundles.items():
        row, _ = check(path)
        expected = "k3s-f3" if role == "keeper" else "k3s-f2"
        if (row.get("scenario") != expected or row.get("hostUid") != uid
                or row.get("system") != selected["arch"] + "-linux"
                or (role != "keeper" and row.get("role") != role)):
            raise ValueError(f"{role}: bundle scenario/role/UID/architecture differs")
        if any(c in str(path) for c in [",", "\n"]):
            raise ValueError("unsupported bundle path character")
        inputs[role] = row
    if inputs["server"]["fixture_config_digest"] != inputs["agent"]["fixture_config_digest"]:
        raise ValueError("F2 node fixture identities differ")
    if os.path.lexists(root):
        raise ValueError("output already exists; retain it and choose a new directory")
    if any(c in str(root) for c in [",", "\n"]):
        raise ValueError("unsupported output path character")
    if (os.cpu_count() or 0) < 6 or available_memory_mib() < 24576:
        raise ValueError("requires 6 host CPUs and 24 GiB available/inactive RAM")
    # Four F2 disks and two F3 disks; retain all evidence and overlays.
    if shutil.disk_usage(root.parent).free < 194 * 1024**3:
        raise ValueError("requires 194 GiB free for retained F2 + F3 acceptance")
    return selected, inputs


def verify_component(stage, out, inputs):
    report = json.loads(bounded(out / "summary.json"))
    if (not isinstance(report, dict) or report.get("schema_version") != 1
            or report.get("scenario") != "k3s-" + stage
            or report.get("mode") != "acceptance" or report.get("status") != "passed"
            or type(report.get("exit_code")) is not int or report["exit_code"] != 0
            or not isinstance(report.get("runs"), list) or len(report["runs"]) != 2):
        raise ValueError(f"{stage}: acceptance summary invalid")
    ids = []
    for index, run in enumerate(report["runs"], 1):
        if not isinstance(run, dict) or run.get("status") != "passed":
            raise ValueError(f"{stage}: fresh run incomplete")
        fresh = out / f"fresh-{index}"
        if stage == "f2":
            actual = verify_cluster(fresh, {r: inputs[r] for r in ("server", "agent")})
            if run.get("boot_ids") != actual:
                raise ValueError("F2 summary boot identities differ from raw evidence")
            ids.extend(actual.values())
        else:
            code, result = verify_run(fresh, inputs["keeper"])
            if code or run.get("boot_id") != result["boot_id"] or run.get("exit_code") != 0:
                raise ValueError("F3 summary differs from raw evidence")
            ids.append(result["boot_id"])
    if len(set(ids)) != len(ids):
        raise ValueError(f"{stage}: repeated fresh boot identity")
    return ids


def workflow(uid, bundles, root):
    os.umask(0o077)
    selected, inputs = preflight(uid, bundles, root)
    snapshots = {role: check(path) for role, path in bundles.items()}
    root.mkdir(mode=0o700)
    summary = {"schema_version": 1, "scenario": "k3s-f4", "scope": "f2-f3-acceptance",
               "execution": selected, "status": "environment_error", "exit_code": 3,
               "components": [], "component_workflow_seconds": 600}
    cancelled = False

    def cancel(signum, frame):
        nonlocal cancelled
        cancelled = True

    handlers = {s: signal.signal(s, cancel) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    code = 3
    stage = "f2"
    all_ids = []
    try:
        for stage in ("f2", "f3"):
            if cancelled:
                code = 130
                raise ValueError("cancelled between acceptance components")
            # Recheck immutable bundles, including before the second component.
            if any(check(path) != snapshots[role] for role, path in bundles.items()):
                raise ValueError("immutable input changed before component")
            out = root / stage
            item = {"stage": stage, "summary": str(out / "summary.json"), "status": "environment_error"}
            summary["components"].append(item)
            if stage == "f2":
                code = cluster_workflow(uid, {r: bundles[r] for r in ("server", "agent")}, "acceptance", out)
            else:
                code = single_workflow(uid, bundles["keeper"], "acceptance", out,
                                       scenario="k3s-f3", verifier=verify_run)
            if type(code) is not int or code not in {0, 1, 3, 4, 130}:
                code = 3
                raise ValueError("component returned an invalid exit code")
            item["exit_code"] = code
            if code:
                child = json.loads(bounded(out / "summary.json"))
                if not isinstance(child, dict):
                    raise ValueError("component failure report is not an object")
                item["detail"] = child.get("detail", "component failed; inspect its evidence")
                item["status"] = {1: "failed", 3: "environment_error", 4: "timeout", 130: "cancelled"}[code]
                raise ValueError(item["detail"])
            code = 3  # Independent verification failures are environment errors.
            ids = verify_component(stage, out, inputs)
            if set(all_ids).intersection(ids):
                raise ValueError("F2/F3 reused a boot identity")
            all_ids.extend(ids)
            item.update(status="passed", exit_code=0, boot_ids=ids)
        stage = "final_verification"
        if cancelled:
            code = 130
            raise ValueError("cancelled during final verification")
        if any(check(path) != snapshots[role] for role, path in bundles.items()):
            raise ValueError("immutable input changed during acceptance")
        code = 0
        summary["status"] = "passed"
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        summary.update(stage=stage, detail=str(error), status={1: "failed", 4: "timeout", 130: "cancelled"}.get(code, "environment_error"))
        if summary["components"] and summary["components"][-1]["status"] != "passed":
            summary["components"][-1].update(exit_code=code, detail=str(error))
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
        summary["exit_code"] = code
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    if code:
        print(f"Failure: {stage}: {summary.get('detail')}", file=sys.stderr)
    print(f"k3s-f4: {summary['status']} — {root / 'summary.json'}")
    return code


if __name__ == "__main__":
    try:
        if len(sys.argv) != 6:
            raise ValueError("usage: arena-k3s-f4 SERVER_BUNDLE AGENT_BUNDLE KEEPER_BUNDLE NEW_OUTPUT")
        bundles = dict(zip(("server", "agent", "keeper"), (Path(p).absolute() for p in sys.argv[2:5]), strict=True))
        sys.exit(workflow(int(sys.argv[1]), bundles, Path(sys.argv[5]).absolute()))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print("F4 preflight: " + str(error), file=sys.stderr)
        sys.exit(3)
