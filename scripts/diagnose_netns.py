#!/usr/bin/env python3
"""Read completed netns workflow evidence without changing its verdict or files."""
import argparse
import json
import pathlib
import stat
import sys


LIMIT = 1024 * 1024


class Evidence:
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workflow root must be a directory")
        self.missing = []

    def path(self, relative):
        relative = pathlib.Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("evidence path escapes workflow root")
        path = self.root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise ValueError("symlink in evidence path")
        return path

    def reference(self, relative):
        path = self.path(relative)
        if not path.exists():
            self.missing.append(str(relative))
            return None
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError("evidence must be a regular file")
        return str(path)

    def report(self, relative, required=False):
        path = self.reference(relative)
        if path is None:
            if required:
                raise ValueError(f"required evidence missing: {relative}")
            return None
        with open(path, "rb") as source:
            data = source.read(LIMIT + 1)
        if len(data) > LIMIT:
            raise ValueError("JSON evidence exceeds 1 MiB")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError("JSON report must be an object")
        return value


def milliseconds(value):
    return value if type(value) is int and value >= 0 else None


def watchdog_lifecycle_issues(stop):
    # These are the stop/recovery fields required by verified_watchdog().
    # A diagnostic override must fail closed on absent or mistyped evidence.
    required = {
        "schema_version": 2, "status": "environment_error", "reason": "watchdog",
        "lifecycle_exit_code": 3, "qemu_reaped": True, "qemu_exit_code": 0,
        "escalated": False, "evidence_synced": True, "evidence_synced_before_stop": True,
        "sync_error": None, "watchdog_fixture": True, "checkpoint_seen": True,
    }
    return [key for key, expected in required.items()
            if key not in stop or type(stop[key]) is not type(expected) or stop[key] != expected]


def diagnose(root):
    evidence = Evidence(root)
    workflow = evidence.report("summary.json", required=True)
    if workflow.get("scenario") != "netns-workflow" or not isinstance(workflow.get("runs"), list):
        raise ValueError("expected netns-workflow summary with runs")
    result = {
        "schema_version": 1,
        "reported_status": workflow.get("status"),
        "workflow_detail": workflow.get("detail"),
        "first_problem": None,
        "root_cause": "undetermined" if workflow.get("status") != "passed" else "not_applicable",
        "missing_evidence": evidence.missing,
        "scope": "diagnostic only; original checker verdict and deadlines are unchanged",
    }
    for run in workflow["runs"]:
        if run.get("status") == "passed":
            continue
        run_path = pathlib.Path(run["output"])
        batch_path = run_path / "netns-batch"
        problem = {"run": run.get("id"), "case": None, "kind": "incomplete_evidence", "logs": []}
        result["first_problem"] = problem
        stop_path = run_path / "vm-stop.json"
        stop = evidence.report(stop_path)
        if stop:
            problem["logs"].append(str(evidence.path(stop_path)))
            problem["vm_stop_reason"] = stop.get("reason")
            if stop.get("status") != "passed" or stop.get("reason") != "service_completed":
                problem.update({"kind": "vm_lifecycle_failure", "detail": stop.get("reason")})
        for relative in [pathlib.Path(str(run_path) + ".launcher.log"), run_path / "serial.log"]:
            reference = evidence.reference(relative)
            if reference:
                problem["logs"].append(reference)
        watchdog_lifecycle_valid = False
        if run.get("watchdog_fixture") is True:
            if stop is None:
                problem.update({"kind": "incomplete_evidence", "detail": "watchdog lifecycle evidence missing"})
                break
            issues = watchdog_lifecycle_issues(stop)
            if issues:
                problem.update({"kind": "vm_lifecycle_failure",
                                "detail": f"watchdog lifecycle evidence invalid ({stop.get('reason')}): " + ", ".join(issues),
                                "invalid_lifecycle_fields": issues})
                break
            watchdog_lifecycle_valid = True
        if run.get("evidence_verification") == "passed":
            problem.update({"kind": "acceptance_invariant_failure",
                            "detail": workflow.get("detail") or "cross-run acceptance check failed"})
            references = [run_path / "guest-boot-id.txt", run_path / "boot-inputs.sha256"]
            if run.get("watchdog_fixture") is True:
                references.append(run_path / "watchdog-verification.json")
            for relative in references:
                reference = evidence.reference(relative)
                if reference:
                    problem["logs"].append(reference)
            break
        if (run.get("watchdog_fixture") is True
                and run.get("exit_code") == run.get("expected_exit_code") == 3
                and watchdog_lifecycle_valid):
            # This stop is intentional. The workflow failed while checking its
            # evidence, not because an ordinary VM unexpectedly hit a watchdog.
            # Do not require a completed batch result from the stopped fixture.
            problem.update({"kind": "watchdog_evidence_verification_failure",
                            "detail": workflow.get("detail") or "watchdog evidence verification failed"})
            for relative in [run_path / "watchdog-verification.json", run_path / "watchdog-ready.json",
                             batch_path / "batch-progress.json"]:
                reference = evidence.reference(relative)
                if reference:
                    problem["logs"].append(reference)
            break
        batch = evidence.report(batch_path / "result.json")
        if batch is None:
            break
        if batch.get("scenario") != "netns-batch" or not isinstance(batch.get("cases"), list):
            raise ValueError("expected netns-batch report with cases")
        if problem["kind"] == "incomplete_evidence":
            problem["kind"] = "workflow_failure_without_case_failure"
        elapsed = 0
        for case in batch["cases"]:
            execution = case.get("execution", {})
            duration = milliseconds(execution.get("duration_ms"))
            if case.get("status") != "failed":
                elapsed += duration or 0
                continue
            case_path = batch_path / case["id"]
            report_path = batch_path / case["report"]
            detail = evidence.report(report_path)
            steps = detail.get("steps", []) if detail else []
            problem.update({
                "case": case["id"],
                "kind": "batch_supervisor_timeout" if execution.get("status") == "timeout" else "case_failed",
                "execution_status": execution.get("status"),
                "exit_code": execution.get("exit_code"),
                "expected_exit_code": case.get("expected_exit_code"),
                "duration_ms": duration,
                "preceding_case_duration_ms": elapsed,
                "recorded_step_duration_ms": sum(milliseconds(step.get("duration_ms")) or 0 for step in steps),
                "detail": detail.get("detail") if detail else case.get("detail"),
            })
            references = [report_path]
            for key in ["stdout_log", "stderr_log"]:
                if execution.get(key):
                    references.append(batch_path / execution[key])
            for step in steps:
                if step.get("status") != "passed":
                    for key in ["stdout_log", "stderr_log"]:
                        if step.get(key):
                            references.append(case_path / step[key])
                    break
            for relative in references:
                reference = evidence.reference(relative)
                if reference and reference not in problem["logs"]:
                    problem["logs"].append(reference)
            break
        break
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", help="completed workflow directory containing summary.json")
    parser.add_argument("--json", action="store_true", help="print machine-readable diagnosis")
    args = parser.parse_args()
    try:
        report = diagnose(args.workflow)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        print(f"diagnosis unavailable: {error}", file=sys.stderr)
        return 3
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Recorded workflow: {report['reported_status']}")
        problem = report["first_problem"]
        if problem:
            print(f"First problem: {problem['run']} / {problem['case'] or 'case report unavailable'}")
            print(f"Kind: {problem['kind']}")
            if "exit_code" in problem:
                print(f"Exit: {problem['exit_code']} / expected {problem['expected_exit_code']}; duration {problem['duration_ms']} ms")
            if "detail" in problem:
                print(f"Detail: {problem['detail'] or 'not recorded'}")
            for path in problem["logs"]:
                print(f"Evidence: {path}")
        if report["workflow_detail"]:
            print(f"Workflow detail: {report['workflow_detail']}")
        if report["missing_evidence"]:
            print("Missing evidence: " + ", ".join(report["missing_evidence"]))
        if report["root_cause"] == "undetermined":
            print("Root cause: undetermined; this report does not attribute delay to host I/O or runner code.")
    return 0  # Successful diagnosis is not a passed test verdict.


if __name__ == "__main__":
    sys.exit(main())
