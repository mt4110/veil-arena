"""Batch local Blackhole checks through the existing Rust runner; retain evidence."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import tempfile
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
CODES = {"passed": 0, "failed": 1, "environment_error": 3, "timeout": 4, "cancelled": 130}
EXPECTED_STAGES = (
    "rust-version", "cargo-version", "python-version", "git-root", "git-head", "git-status",
    "build", "rust-tests", "python-tests", "replay-ipv4", "replay-ipv6", "replay-dns",
    "reject-dns-short-header", "replay-bpf", "checksum-ipv4", "checksum-ipv6",
    "checksum-ipv6-options", "reject-udp-length", "reject-udp-checksum",
)


def valid_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def write_json(path, value):
    with path.open("x", encoding="utf-8") as file:
        os.chmod(path, 0o600)
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


def directory(path, projects):
    path = Path(os.path.abspath(path))
    for component in [path, *path.parents]:
        if component.is_symlink():
            raise ValueError("workflow output/cache paths must not contain symlinks")
    if any(path.is_relative_to(project) for project in projects):
        raise ValueError("workflow output/cache must be outside Blackhole checkouts")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path.resolve()


def stop(process):
    # Only the new session created by this workflow is targeted.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)


def command(args, cwd, env, timeout, logs, name):
    started = time.monotonic()
    result = {"args": list(map(str, args)), "cwd": str(cwd)}
    with (logs / f"{name}.stdout.log").open("xb") as out, (logs / f"{name}.stderr.log").open("xb") as err:
        os.chmod(out.name, 0o600)
        os.chmod(err.name, 0o600)
        try:
            process = subprocess.Popen(args, cwd=cwd, env=env, stdout=out, stderr=err,
                                       start_new_session=True)
        except OSError as error:
            result.update(status="environment_error", detail=str(error), exit_code=None)
        else:
            try:
                code = process.wait(timeout=timeout)
                result.update(status="completed", exit_code=code)
            except subprocess.TimeoutExpired:
                stop(process)
                result.update(status="timeout", exit_code=process.returncode)
            except KeyboardInterrupt:
                stop(process)
                result.update(status="cancelled", exit_code=process.returncode)
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return result


def assess(report, exit_code, failure_stage=None):
    """Validate evidence before interpreting a nonzero code as expected failure."""
    if not isinstance(report, dict):
        raise ValueError("report must be an object")
    status = report.get("status")
    if status not in CODES or CODES[status] != exit_code:
        raise ValueError("report status and process exit disagree")
    if report.get("schema_version") != 1 or report.get("suite") != "blackhole-offline":
        raise ValueError("unknown report contract")
    if status not in {"passed", "failed"}:
        return status
    steps = report.get("steps")
    if (not isinstance(steps, list) or len(steps) != len(EXPECTED_STAGES)
            or report.get("source_unchanged") is not True
            or report.get("binary_unchanged") is not True):
        raise ValueError("incomplete evidence or source/binary changed")
    if any(not isinstance(step, dict) for step in steps):
        raise ValueError("stage must be an object")
    ids = [step.get("id") for step in steps]
    if tuple(ids) != EXPECTED_STAGES:
        raise ValueError("stage order or identifiers differ from the nineteen-stage contract")
    source = report.get("source_after")
    if (not isinstance(source, dict) or not isinstance(source.get("files"), dict)
            or not source["files"] or not valid_digest(source.get("sha256"))
            or not valid_digest(report.get("binary_sha256"))):
        raise ValueError("missing or invalid source/binary identity")
    if failure_stage is None:
        if status == "passed" and all(step.get("status") == "passed" for step in steps):
            return "passed"
        return status if status != "passed" else "environment_error"
    if status != "failed":
        return "failed" if status == "passed" else status
    failures = [index for index, step in enumerate(steps) if step.get("status") == "failed"]
    if len(failures) != 1:
        return "failed"
    index = failures[0]
    if (steps[index]["id"] == failure_stage
            and all(step.get("status") == "passed" for step in steps[:index])
            and all(step.get("status") == "not_run" for step in steps[index + 1:])):
        return "passed"
    return "failed"


def diagnostic(report, report_path):
    """Describe observed failure evidence, without guessing its root cause."""
    steps = report.get("steps", [])
    if not isinstance(steps, list):
        return None
    step = next((step for step in steps if isinstance(step, dict)
                 and step.get("status") not in {"passed", "not_run"}), None)
    if step is None:
        return None
    result = {"stage": step.get("id", "unknown"),
              "reason": step.get("detail") or "段階が正常終了しませんでした",
              "exit_code": step.get("exit_code"),
              "expected_exit_code": step.get("expected_exit_code"),
              "logs": {}, "observations": []}
    for stream in ("stdout", "stderr"):
        name = step.get(f"{stream}_log")
        if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
            continue
        path = report_path.parent / name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
                    continue
                data = file.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    continue
            result["logs"][stream] = str(path)
            for line in data.decode("utf-8", errors="replace").splitlines():
                text = line.strip()
                if ((text.startswith("test ") and text.endswith(" ... FAILED"))
                        or text.startswith(("FAIL:", "ERROR:", "error:", "left:", "right:"))):
                    if len(result["observations"]) < 4:
                        result["observations"].append(text[:240])
        except OSError:
            continue
    return result


def console_json(value):
    # Keep Japanese readable, but escape C1, bidi controls and surrogate codepoints too.
    rendered = json.dumps(value, ensure_ascii=False)
    return "".join(json.dumps(char, ensure_ascii=True)[1:-1]
                   if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char
                   for char in rendered)


def display_row(row, run):
    labels = {"passed": "成功", "failed": "失敗", "environment_error": "環境エラー",
              "timeout": "期限超過", "cancelled": "中断"}
    expected = row.get("expected_failure_stage") and row["status"] == "passed"
    label = "想定した失敗を確認（試験自体は失敗）" if expected else labels.get(row["status"], row["status"])
    name = row["name"]
    if name.startswith("healthy-"):
        name = f"通常試験{name.removeprefix('healthy-')}回目"
    elif name == "before":
        name = "変更前の試験"
    elif name == "preparation":
        name = "準備"
    print(f"[{label}] {name}", flush=True)
    if row["status"] == "passed":
        return
    detail = row.get("diagnostic")
    if detail:
        print(f"  段階: {console_json(detail['stage'])}", flush=True)
        if detail["exit_code"] is not None:
            print(f"  終了コード: {json.dumps(detail['exit_code'])}（期待: {json.dumps(detail['expected_exit_code'])}）", flush=True)
        reason = row.get("detail") or detail["reason"]
        if reason == "exit code or output did not match the recorded expectation; inspect logs":
            reason = "終了コードまたは出力が期待値と一致しません。下のログ抜粋を確認してください。"
        print(f"  理由: {console_json(reason)}", flush=True)
        for observation in detail["observations"]:
            print(f"  ログ抜粋: {console_json(observation)}", flush=True)
        for stream, path in detail["logs"].items():
            print(f"  {stream}ログ: {console_json(path)}", flush=True)
    else:
        print(f"  理由: {console_json(row.get('detail') or '段階の詳細を取得できませんでした。実行ログを確認してください。')}", flush=True)
    if row.get("report"):
        print(f"  詳細報告: {console_json(str(run / row['report']))}", flush=True)
    elif not detail or not detail["logs"]:
        for stream in ("stdout", "stderr"):
            print(f"  実行{stream}ログ: {console_json(str(run / (row['name'] + '.' + stream + '.log')))}", flush=True)


def suite(binary, project, cache, env, timeout, run, name, failure_stage=None, failure_text=None):
    output = run / name
    output.mkdir(mode=0o700)
    execution = command([str(binary), "blackhole-offline", "--project", str(project),
                         "--artifacts", str(output), "--timeout-seconds", str(timeout)],
                        cache, env, timeout + 5, run, name)
    row = {"name": name, "project": str(project), "expected_failure_stage": failure_stage,
           "execution": execution, "status": execution["status"]}
    if execution["status"] != "completed":
        return row
    reports = list(output.glob("blackhole-offline-*/result.json"))
    try:
        if len(reports) != 1 or not stat.S_ISREG(reports[0].lstat().st_mode) or reports[0].stat().st_size > 2 * 1024 * 1024:
            raise ValueError("expected one regular, bounded Rust result")
        report = json.loads(reports[0].read_text())
        verdict = assess(report, execution["exit_code"], failure_stage)
        if failure_stage is not None and verdict == "passed":
            failed = next(step for step in report["steps"] if step["status"] == "failed")
            # unittest writes failure names and tracebacks to stderr by default.
            log_field = "stderr_log" if failure_stage == "python-tests" else "stdout_log"
            log_name = failed.get(log_field)
            if not isinstance(log_name, str) or Path(log_name).name != log_name:
                raise ValueError("invalid failure evidence path")
            evidence = reports[0].parent / log_name
            if not stat.S_ISREG(evidence.lstat().st_mode) or evidence.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("failure evidence must be regular and bounded")
            if not failure_text or failure_text not in evidence.read_text():
                verdict = "failed"
                row["detail"] = "expected regression failure text was not present"
        row.update(status=verdict,
                   report=str(reports[0].relative_to(run)), runner_status=report["status"],
                   duration_ms=report.get("duration_ms"), source=report.get("source_after"),
                   binary_sha256=report.get("binary_sha256"))
        if verdict != "passed":
            row["diagnostic"] = diagnostic(report, reports[0])
        if verdict == "passed" and (not isinstance(row["source"], dict)
                or not isinstance(row["binary_sha256"], str)):
            raise ValueError("successful assessment requires source and binary identity")

    except (ValueError, OSError, KeyError, TypeError) as error:
        row.update(status="environment_error", detail=str(error))
    return row


def batch(options):
    projects = [options.project.resolve(strict=True)]
    if options.before:
        projects.append(options.before.resolve(strict=True))
    cache = directory(options.cache, projects)
    artifacts = directory(options.artifacts, projects)
    # Keep lock files and evidence; never remove previous runs or caches.
    fd = os.open(cache / "workflow.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+") as lock:
        if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
            raise ValueError("cache lock is not a regular file")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = Path(tempfile.mkdtemp(prefix="blackhole-workflow-", dir=artifacts))
        started = time.monotonic()
        summary = {"schema_version": 1, "status": "environment_error", "runs": [],
                   "cache": str(cache), "repeat": options.repeat,
                   "manual_baseline": None, "time_saving_claim": False}
        env = os.environ.copy()
        build_env = env | {"CARGO_HOME": str(options.arena_cargo_home.resolve()),
                           "CARGO_TARGET_DIR": str(cache / "runner")}
        test_env = env | {"CARGO_HOME": str(options.project_cargo_home.resolve())}
        write_json(run / "manifest.json", {"project": str(projects[0]),
                   "before": str(projects[1]) if options.before else None,
                   "before_failure": options.before_failure, "before_contains": options.before_contains, "timeout_seconds": options.timeout_seconds,
                   "arena_cargo_home": build_env["CARGO_HOME"], "project_cargo_home": test_env["CARGO_HOME"]})
        with (run / "events.jsonl").open("x", encoding="utf-8") as journal:
            os.chmod(journal.name, 0o600)
            def event(row):
                journal.write(json.dumps(row) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
            event({"event": "preparation_started"})
            preparation = command(["cargo", "build", "--offline", "--locked", "--bin", "veil-arena"],
                                  ROOT, build_env, options.timeout_seconds, run, "preparation")
            summary["preparation"] = preparation
            event({"event": "preparation_finished", **preparation})
            if preparation["status"] == "completed" and preparation["exit_code"] == 0:
                binary = cache / "runner/debug/veil-arena"
                scenarios = []
                if options.before:
                    scenarios.append(("before", projects[1], options.before_failure))
                scenarios += [(f"healthy-{index + 1}", projects[0], None) for index in range(options.repeat)]
                summary["status"] = "passed"
                reference = None
                for name, project, expected in scenarios:
                    event({"event": "suite_started", "name": name})
                    row = suite(binary, project, cache, test_env, options.timeout_seconds, run, name, expected, options.before_contains if expected else None)
                    if expected is None and row["status"] == "passed":
                        identity = (row["source"], row["binary_sha256"])
                        if reference is not None and reference != identity:
                            row.update(status="environment_error", detail="healthy repeats used different source/binary")
                        reference = identity
                    summary["runs"].append(row)
                    event({"event": "suite_finished", **row})
                    display_row(row, run)
                    if row["status"] != "passed":
                        summary["status"] = row["status"]
                        summary["not_run"] = [scenario[0] for scenario in scenarios[len(summary["runs"]):]]
                        print(f"  未実行: {', '.join(summary['not_run']) or 'なし'}", flush=True)
                        break
            else:
                summary["status"] = preparation["status"] if preparation["status"] != "completed" else "environment_error"
                display_row({"name": "preparation", "status": summary["status"],
                             "diagnostic": {"stage": "preparation", "exit_code": preparation.get("exit_code"),
                                            "expected_exit_code": 0,
                                            "reason": preparation.get("detail") or "Arenaの準備が完了しませんでした",
                                            "observations": [],
                                            "logs": {stream: str(run / f"preparation.{stream}.log")
                                                     for stream in ("stdout", "stderr")}}}, run)
            summary["duration_ms"] = round((time.monotonic() - started) * 1000)
            summary["automated_command_count"] = 1 + len(summary["runs"])
            write_json(run / "summary.json", summary)
            event({"event": "finished", "status": summary["status"]})
        print(f"全体結果: {summary['status']}", flush=True)
        print(f"summary: {run / 'summary.json'}", flush=True)
        return CODES.get(summary["status"], 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--before", type=Path)
    parser.add_argument("--before-failure", choices=["rust-tests", "python-tests", "reject-dns-short-header"])
    parser.add_argument("--before-contains", help="exact text required in the failed stage log (stderr for python-tests, stdout otherwise)")
    parser.add_argument("--cache", type=Path, default=ROOT / "target/blackhole-workflow")
    parser.add_argument("--artifacts", type=Path, default=ROOT / "artifacts")
    home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    parser.add_argument("--arena-cargo-home", type=Path, default=home)
    parser.add_argument("--project-cargo-home", type=Path, default=home)
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=2)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    options = parser.parse_args()
    if (bool(options.before) != bool(options.before_failure)
            or bool(options.before) != bool(options.before_contains)
            or not 1 <= options.timeout_seconds <= 3600):
        parser.error("before, before-failure and before-contains must be paired; timeout must be 1..3600")
    if os.geteuid() == 0:
        parser.error("run as an ordinary user")
    def cancel(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancel)
    try:
        return batch(options)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as error:
        print(f"workflow environment error: {error}", flush=True)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
