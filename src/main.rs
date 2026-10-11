#[cfg(not(unix))]
compile_error!("This offline runner currently supports Unix hosts only.");

mod process;

use process::{Execution, execute};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

const HELP: &str =
    "veil-arena blackhole-offline --project PATH [--artifacts PATH] [--timeout-seconds N]

ブラックホールの既存Rust/Python回帰試験と人工fixture再生を実行します。
既定: artifacts/、全実行の期限300秒（1〜3600秒）。依存取得は行いません。
対象は信頼するローカルのveil-blackholeチェックアウトです。OS sandboxではありません。
Live取得、sudo、VM、netns、削除処理は呼び出しません。
終了コード: 0=成功、1=検証不一致、2=引数不正、3=環境/保存エラー、4=期限超過、130=中断。
";

const FIXTURES: &[&str] = &[
    "query-a.hex",
    "query-v6.hex",
    "dns-query-a.hex",
    "bpf-two-records.hex",
    "query-v6-options.hex",
    "bad-udp-length.hex",
    "bad-udp-checksum.hex",
];

struct Options {
    project: PathBuf,
    artifacts: PathBuf,
    timeout: Duration,
}

fn parse(args: &[String]) -> Result<Option<Options>, String> {
    if args.is_empty()
        || args == ["--help"]
        || args == ["-h"]
        || args == ["blackhole-offline", "--help"]
    {
        return Ok(None);
    }
    if args.first().map(String::as_str) != Some("blackhole-offline") {
        return Err("command must be blackhole-offline".into());
    }
    let mut project = None;
    let mut artifacts = None;
    let mut timeout = None;
    let mut index = 1;
    while index < args.len() {
        let value = args.get(index + 1).ok_or("option value is missing")?;
        match args[index].as_str() {
            "--project" if project.is_none() => project = Some(PathBuf::from(value)),
            "--artifacts" if artifacts.is_none() => artifacts = Some(PathBuf::from(value)),
            "--timeout-seconds" if timeout.is_none() => {
                let seconds: u64 = value.parse().map_err(|_| "timeout must be an integer")?;
                if !(1..=3600).contains(&seconds) {
                    return Err("timeout must be 1..=3600 seconds".into());
                }
                timeout = Some(Duration::from_secs(seconds));
            }
            _ => return Err("unknown or duplicate option".into()),
        }
        index += 2;
    }
    Ok(Some(Options {
        project: project.ok_or("--project is required")?,
        artifacts: artifacts.unwrap_or_else(|| PathBuf::from("artifacts")),
        timeout: timeout.unwrap_or(Duration::from_secs(300)),
    }))
}

fn create_file(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
}

fn write_json(path: &Path, value: &Value) -> io::Result<()> {
    let mut file = create_file(path)?;
    serde_json::to_writer_pretty(&mut file, value)?;
    file.write_all(b"\n")?;
    file.sync_all()
}

fn allocate_run(root: &Path, prefix: &str) -> io::Result<(PathBuf, String)> {
    for suffix in 0..1024 {
        let id = format!("{prefix}-{suffix}");
        let directory = root.join(&id);
        match fs::DirBuilder::new().mode(0o700).create(&directory) {
            Ok(()) => return Ok((directory, id)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::other("cannot allocate a fresh run directory"))
}

fn destination(path: &Path) -> io::Result<PathBuf> {
    let mut existing = if path.is_absolute() {
        path.to_owned()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut missing = vec![];
    while !existing.exists() {
        missing.push(
            existing
                .file_name()
                .ok_or_else(|| io::Error::other("invalid artifact destination"))?
                .to_owned(),
        );
        if !existing.pop() {
            return Err(io::Error::other("cannot resolve artifact destination"));
        }
    }
    let mut resolved = fs::canonicalize(existing)?;
    for component in missing.into_iter().rev() {
        resolved.push(component);
    }
    Ok(resolved)
}

fn read_regular(path: &Path, limit: u64) -> io::Result<Vec<u8>> {
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_file() || metadata.len() > limit {
        return Err(io::Error::other(format!(
            "not a bounded regular file: {}",
            path.display()
        )));
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?;
    if !file.metadata()?.is_file() {
        return Err(io::Error::other("opened input is not a regular file"));
    }
    let mut bytes = vec![];
    file.take(limit + 1).read_to_end(&mut bytes)?;
    if bytes.len() as u64 > limit {
        return Err(io::Error::other("input grew beyond its limit"));
    }
    Ok(bytes)
}

fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn source_files(directory: &Path, paths: &mut Vec<PathBuf>) -> io::Result<()> {
    if !directory.exists() {
        return Ok(());
    }
    if !fs::symlink_metadata(directory)?.is_dir() {
        return Err(io::Error::other("source directory must not be a symlink"));
    }
    for entry in fs::read_dir(directory)? {
        let path = entry?.path();
        let kind = fs::symlink_metadata(&path)?.file_type();
        if kind.is_dir() {
            source_files(&path, paths)?;
        } else if matches!(
            path.extension().and_then(|s| s.to_str()),
            Some("rs" | "c" | "h" | "py" | "hex" | "toml")
        ) {
            if !kind.is_file() {
                return Err(io::Error::other(
                    "source inputs must not be symlinks/devices",
                ));
            }
            paths.push(path);
        }
        if paths.len() > 4096 {
            return Err(io::Error::other("too many source inputs"));
        }
    }
    Ok(())
}

fn fingerprint(project: &Path) -> io::Result<Value> {
    let mut paths = vec![];
    for name in [
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "build.rs",
    ] {
        let path = project.join(name);
        if path.exists() {
            paths.push(path);
        }
    }
    for directory in ["src", "tests", "scripts", "examples", ".cargo"] {
        source_files(&project.join(directory), &mut paths)?;
    }
    paths.sort();
    let mut files = BTreeMap::new();
    let mut total = 0_usize;
    for path in paths {
        let bytes = read_regular(&path, 8 * 1024 * 1024)?;
        total += bytes.len();
        if total > 64 * 1024 * 1024 {
            return Err(io::Error::other("source inputs exceed 64 MiB"));
        }
        let relative = path.strip_prefix(project).map_err(io::Error::other)?;
        let name = relative
            .to_str()
            .ok_or_else(|| io::Error::other("non-UTF-8 source path"))?;
        files.insert(
            name.to_owned(),
            json!({"sha256": sha256(&bytes), "bytes": bytes.len()}),
        );
    }
    let encoded = serde_json::to_vec(&files)?;
    Ok(json!({"sha256": sha256(&encoded), "files": files}))
}

struct CacheLock(File);

impl CacheLock {
    fn acquire(cache: &Path) -> io::Result<Self> {
        fs::create_dir_all(cache)?;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(cache.join("arena.lock"))?;
        // SAFETY: the live File owns this fd. flock does not take ownership;
        // closing the File releases the advisory lock without deleting it.
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(io::Error::other(format!(
                "build cache is busy or cannot be locked: {}",
                io::Error::last_os_error()
            )));
        }
        Ok(Self(file))
    }
}

struct Step {
    id: String,
    program: String,
    args: Vec<String>,
    expected_exit: i32,
    summary: Option<String>,
    checksums: Option<usize>,
    stdout_exact: Option<String>,
    stdout_prefix: Option<String>,
    setup: bool,
}

impl Step {
    fn command(id: &str, program: &str, args: &[&str], setup: bool) -> Self {
        Self {
            id: id.into(),
            program: program.into(),
            args: args.iter().map(|s| (*s).into()).collect(),
            expected_exit: 0,
            summary: None,
            checksums: None,
            stdout_exact: None,
            stdout_prefix: None,
            setup,
        }
    }
}

fn steps(binary: &Path, inputs: &Path, cache: &Path) -> Vec<Step> {
    let cache = cache.to_str().expect("validated cache path");
    let mut result = vec![
        Step::command("rust-version", "rustc", &["--version"], true),
        Step::command("cargo-version", "cargo", &["--version"], true),
        Step::command("python-version", "python3", &["--version"], true),
        Step::command("git-root", "git", &["rev-parse", "--show-toplevel"], true),
        Step::command("git-head", "git", &["rev-parse", "HEAD"], true),
        Step::command(
            "git-status",
            "git",
            &["status", "--porcelain=v1", "--untracked-files=no"],
            true,
        ),
        Step::command(
            "build",
            "cargo",
            &[
                "build",
                "--offline",
                "--locked",
                "--bin",
                "veil-blackhole",
                "--target-dir",
                cache,
            ],
            true,
        ),
        Step::command(
            "rust-tests",
            "cargo",
            &["test", "--offline", "--locked", "--target-dir", cache],
            false,
        ),
        Step::command(
            "python-tests",
            "python3",
            &[
                "-B",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-p",
                "test_*.py",
            ],
            false,
        ),
    ];
    for (id, prefix) in [
        ("rust-version", "rustc 1.98.1 "),
        ("cargo-version", "cargo 1.98.0 "),
        ("python-version", "Python 3.14.7"),
    ] {
        let step = result.iter_mut().find(|step| step.id == id).unwrap();
        if id == "python-version" {
            step.stdout_exact = Some(prefix.into());
        } else {
            step.stdout_prefix = Some(prefix.into());
        }
    }
    for (id, fixture, format, verify, count, malformed) in [
        ("replay-ipv4", "query-a.hex", "ethernet", false, 1, 0),
        ("replay-ipv6", "query-v6.hex", "ethernet", false, 1, 0),
        ("replay-dns", "dns-query-a.hex", "dns", false, 1, 0),
        (
            "replay-bpf",
            "bpf-two-records.hex",
            "bpf-darwin",
            false,
            2,
            0,
        ),
        ("checksum-ipv4", "query-a.hex", "ethernet", true, 1, 0),
        ("checksum-ipv6", "query-v6.hex", "ethernet", true, 1, 0),
        (
            "checksum-ipv6-options",
            "query-v6-options.hex",
            "ethernet",
            true,
            1,
            0,
        ),
        (
            "reject-udp-length",
            "bad-udp-length.hex",
            "ethernet",
            false,
            0,
            1,
        ),
        (
            "reject-udp-checksum",
            "bad-udp-checksum.hex",
            "ethernet",
            true,
            0,
            1,
        ),
    ] {
        let fixture = inputs.join(fixture);
        let mut step = Step::command(
            id,
            binary.to_str().expect("validated binary path"),
            &[
                "replay",
                "--fixture",
                fixture.to_str().expect("validated fixture path"),
                "--format",
                format,
            ],
            false,
        );
        if verify {
            step.args.push("--verify-checksums".into());
        }
        step.expected_exit = if malformed == 0 { 0 } else { 2 };
        step.summary = Some(format!(
            "queries={count} unsupported=0 malformed={malformed} truncated=0"
        ));
        step.checksums = verify.then_some(count);
        result.push(step);
    }
    result
}

fn judge(step: &Step, execution: &mut Execution) {
    execution.result["expected_exit_code"] = json!(step.expected_exit);
    execution.result["expected_summary"] = json!(step.summary);
    let status = execution.result["status"]
        .as_str()
        .unwrap_or("environment_error");
    if !matches!(status, "passed" | "failed") {
        return;
    }
    let output = std::str::from_utf8(&execution.stdout);
    let summary_ok = step.summary.as_ref().is_none_or(|summary| {
        output.is_ok_and(|text| {
            let summaries: Vec<_> = text
                .lines()
                .filter(|line| line.starts_with("queries="))
                .collect();
            summaries == [summary.as_str()]
                && text.lines().any(|line| line.starts_with("mode=offline "))
        })
    });
    let checksum_ok = step.checksums.is_none_or(|expected| {
        output.is_ok_and(|text| {
            text.lines().any(|line| {
                line == "mode=offline checksum=verification-enabled direction=unverified"
            }) && text
                .split_whitespace()
                .filter(|word| *word == "udp=valid")
                .count()
                == expected
        })
    });
    let passed = execution.result["exit_code"] == step.expected_exit
        && execution.result["signal"].is_null()
        && execution.result["output_complete"] == true
        && summary_ok
        && checksum_ok
        && step.stdout_exact.as_ref().is_none_or(|expected| {
            output.is_ok_and(|text| text.trim_end_matches('\n') == expected)
        })
        && step.stdout_prefix.as_ref().is_none_or(|expected| {
            output.is_ok_and(|text| text.starts_with(expected) && text.lines().count() == 1)
        });
    execution.result["status"] = json!(if passed {
        "passed"
    } else if step.setup {
        "environment_error"
    } else {
        "failed"
    });
    if !passed {
        execution.result["detail"] =
            json!("exit code or output did not match the recorded expectation; inspect logs");
    }
}

fn append_event(journal: &mut File, value: &Value) -> io::Result<()> {
    serde_json::to_writer(&mut *journal, value)?;
    journal.write_all(b"\n")?;
    journal.sync_all()
}

fn suite(
    options: &Options,
    run_dir: &Path,
    report: &mut Value,
    cancelled: &AtomicBool,
) -> io::Result<()> {
    let deadline = Instant::now() + options.timeout;
    let project = fs::canonicalize(&options.project)?;
    if project.to_str().is_none() || run_dir.to_str().is_none() {
        return Err(io::Error::other("project/artifact paths must be UTF-8"));
    }
    if run_dir.starts_with(&project) {
        return Err(io::Error::other(
            "artifacts must be outside the blackhole checkout",
        ));
    }
    for required in [
        "Cargo.toml",
        "Cargo.lock",
        "src/main.rs",
        "tests/cli.rs",
        "tests/test_live_smoke.py",
    ] {
        read_regular(&project.join(required), 8 * 1024 * 1024)?;
    }
    let manifest = String::from_utf8(read_regular(&project.join("Cargo.toml"), 1024 * 1024)?)
        .map_err(io::Error::other)?;
    if !manifest
        .lines()
        .any(|line| line.trim() == "name = \"veil-blackhole\"")
    {
        return Err(io::Error::other("expected the veil-blackhole package"));
    }
    let before = fingerprint(&project)?;
    let inputs = run_dir.join("inputs");
    fs::create_dir(&inputs)?;
    let mut fixtures = BTreeMap::new();
    for name in FIXTURES {
        let bytes = read_regular(&project.join("tests/fixtures").join(name), 1024 * 1024)?;
        let mut file = create_file(&inputs.join(name))?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        fixtures.insert(
            *name,
            json!({"sha256": sha256(&bytes), "bytes": bytes.len()}),
        );
    }
    // Cargo can retain valid fingerprints for two package roots while both
    // write the same target/debug binary. Separate target directories by
    // canonical checkout path, not merely by package name or revision.
    let cache = std::env::current_dir()?
        .join("target/blackhole-offline")
        .join(sha256(
            project.to_str().expect("validated project path").as_bytes(),
        ));
    let cache = {
        fs::create_dir_all(&cache)?;
        fs::canonicalize(cache)?
    };
    if cache.to_str().is_none() {
        return Err(io::Error::other("cache path must be UTF-8"));
    }
    let lock = CacheLock::acquire(&cache)?;
    let _lock_fd = lock.0.as_raw_fd();
    let binary = cache.join("debug/veil-blackhole");
    let mut plan = steps(&binary, &inputs, &cache);
    plan.iter_mut()
        .find(|step| step.id == "git-root")
        .expect("git-root step")
        .stdout_exact = Some(project.to_str().expect("validated project path").to_owned());
    let manifest = json!({
        "schema_version": 2, "suite": "blackhole-offline", "project": project,
        "required_versions": {"rust": "1.98.1", "cargo": "1.98.0", "python": "3.14.7"},
        "host_os": std::env::consts::OS, "host_arch": std::env::consts::ARCH,
        "arena_version": env!("CARGO_PKG_VERSION"), "timeout_seconds": options.timeout.as_secs(),
        "arena_binary_sha256": sha256(&read_regular(&std::env::current_exe()?, 64 * 1024 * 1024)?),
        "log_limit_bytes_per_stream": process::LOG_LIMIT, "source_before": before,
        "fixtures": fixtures, "build_cache": cache,
        "commands": plan.iter().map(|step| json!({"id": step.id, "program": step.program,
            "args": step.args, "expected_exit_code": step.expected_exit,
            "expected_summary": step.summary, "expected_stdout": step.stdout_exact,
            "expected_stdout_prefix": step.stdout_prefix})).collect::<Vec<_>>(),
    });
    write_json(&run_dir.join("manifest.json"), &manifest)?;
    let mut journal = create_file(&run_dir.join("events.jsonl"))?;
    append_event(
        &mut journal,
        &json!({"event": "started", "suite": "blackhole-offline"}),
    )?;
    report["project"] = json!(project);
    let mut records = vec![];
    let mut overall = "passed".to_owned();
    let mut binary_hash = None;
    for step in &plan {
        if overall != "passed" {
            records.push(json!({"id": step.id, "status": "not_run", "detail": "a previous step did not pass"}));
            continue;
        }
        let _ = writeln!(io::stdout(), "running {}", step.id);
        append_event(
            &mut journal,
            &json!({"event": "step_started", "id": step.id}),
        )?;
        let mut execution = execute(
            &step.id,
            &step.program,
            &step.args,
            &project,
            run_dir,
            deadline,
            cancelled,
        )?;
        judge(step, &mut execution);
        overall = execution.result["status"]
            .as_str()
            .unwrap_or("environment_error")
            .to_owned();
        if step.id == "build" && overall == "passed" {
            binary_hash = Some(sha256(&read_regular(&binary, 64 * 1024 * 1024)?));
        }
        let _ = writeln!(io::stdout(), "{}: {}", step.id, overall);
        append_event(&mut journal, &execution.result)?;
        records.push(execution.result);
        report["steps"] = json!(records);
    }
    report["steps"] = json!(records);
    let after = fingerprint(&project)?;
    let unchanged = before == after;
    let binary_unchanged = match &binary_hash {
        Some(expected) => Some(*expected == sha256(&read_regular(&binary, 64 * 1024 * 1024)?)),
        None => None,
    };
    report["source_after"] = after;
    report["source_unchanged"] = json!(unchanged);
    report["binary_sha256"] = json!(binary_hash);
    report["binary_unchanged"] = json!(binary_unchanged);
    if !unchanged || binary_unchanged == Some(false) {
        overall = "environment_error".into();
        report["detail"] = json!(
            "source or binary changed during the run; results cannot be treated as one revision"
        );
    }
    if cancelled.load(Ordering::SeqCst) && overall == "passed" {
        overall = "cancelled".into();
    } else if Instant::now() >= deadline && overall == "passed" {
        overall = "timeout".into();
    }
    report["status"] = json!(overall);
    append_event(
        &mut journal,
        &json!({"event": "finished", "status": overall}),
    )?;
    Ok(())
}

fn run(options: &Options, cancelled: &AtomicBool) -> io::Result<i32> {
    let requested_root = destination(&options.artifacts)?;
    if fs::canonicalize(&options.project).is_ok_and(|project| requested_root.starts_with(project)) {
        return Err(io::Error::other(
            "artifacts must be outside the blackhole checkout",
        ));
    }
    if fs::symlink_metadata(&options.artifacts)
        .is_ok_and(|metadata| metadata.file_type().is_symlink())
    {
        return Err(io::Error::other("artifact root must not be a symlink"));
    }
    fs::create_dir_all(&options.artifacts)?;
    let root = fs::canonicalize(&options.artifacts)?;
    let started = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(io::Error::other)?;
    let prefix = format!(
        "blackhole-offline-{}-{}",
        started.as_nanos(),
        std::process::id()
    );
    let (run_dir, run_id) = allocate_run(&root, &prefix)?;
    let _ = writeln!(io::stdout(), "artifacts: {}", run_dir.display());
    let begin = Instant::now();
    let mut report = json!({"schema_version": 1, "run_id": run_id,
        "suite": "blackhole-offline", "started_unix_ms": started.as_millis(),
        "status": "environment_error", "steps": [], "project_requested": options.project});
    if let Err(error) = suite(options, &run_dir, &mut report, cancelled) {
        report["status"] = json!("environment_error");
        report["detail"] = json!(error.to_string());
    }
    report["duration_ms"] = json!(begin.elapsed().as_millis());
    write_json(&run_dir.join("result.json"), &report)?;
    let status = report["status"].as_str().unwrap_or("environment_error");
    let _ = writeln!(
        io::stdout(),
        "result: {status}\nreport: {}",
        run_dir.join("result.json").display()
    );
    if let Some(detail) = report["detail"].as_str() {
        eprintln!("{detail}");
    }
    Ok(match status {
        "passed" => 0,
        "failed" => 1,
        "timeout" => 4,
        "cancelled" => 130,
        _ => 3,
    })
}

fn main() {
    let args = match std::env::args_os()
        .skip(1)
        .map(|arg| arg.into_string())
        .collect::<Result<Vec<_>, _>>()
    {
        Ok(args) => args,
        Err(_) => {
            eprintln!("arguments must be UTF-8");
            std::process::exit(2);
        }
    };
    let options = match parse(&args) {
        Ok(Some(options)) => options,
        Ok(None) => {
            print!("{HELP}");
            return;
        }
        Err(error) => {
            eprintln!("{error}\n{HELP}");
            std::process::exit(2);
        }
    };
    // SAFETY: geteuid has no arguments, reads the calling process identity,
    // and neither accesses memory through pointers nor changes credentials.
    if unsafe { libc::geteuid() } == 0 {
        eprintln!("run veil-arena as an ordinary user, without sudo");
        std::process::exit(3);
    }
    let cancelled = Arc::new(AtomicBool::new(false));
    let signal_flag = Arc::clone(&cancelled);
    if let Err(error) = ctrlc::set_handler(move || {
        signal_flag.store(true, Ordering::SeqCst);
    }) {
        eprintln!("cannot install cancellation handler: {error}");
        std::process::exit(3);
    }
    let code = match run(&options, &cancelled) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("environment/artifact error: {error}");
            3
        }
    };
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn toolchain_mismatch_is_an_environment_error() {
        let plan = steps(Path::new("binary"), Path::new("inputs"), Path::new("cache"));
        for (id, output, expected) in [
            ("rust-version", "rustc 1.98.1 (hash date)\n", "passed"),
            (
                "rust-version",
                "rustc 1.99.0 (hash date)\n",
                "environment_error",
            ),
            ("cargo-version", "cargo 1.98.0 (hash date)\n", "passed"),
            (
                "cargo-version",
                "cargo 1.98.10 (hash date)\n",
                "environment_error",
            ),
            ("python-version", "Python 3.14.7\n", "passed"),
            ("python-version", "Python 3.14.70\n", "environment_error"),
        ] {
            let step = plan.iter().find(|step| step.id == id).unwrap();
            let mut execution = Execution {
                result: json!({"status": "passed", "exit_code": 0,
                    "signal": null, "output_complete": true}),
                stdout: output.as_bytes().to_vec(),
            };
            judge(step, &mut execution);
            assert_eq!(execution.result["status"], expected);
        }
    }

    #[test]
    fn cli_rejects_missing_duplicate_and_unbounded_options() {
        for args in [
            vec!["blackhole-offline"],
            vec!["capture"],
            vec![
                "blackhole-offline",
                "--project",
                "x",
                "--timeout-seconds",
                "0",
            ],
            vec![
                "blackhole-offline",
                "--project",
                "x",
                "--timeout-seconds",
                "3601",
            ],
            vec!["blackhole-offline", "--project", "x", "--project", "y"],
        ] {
            assert!(parse(&args.into_iter().map(String::from).collect::<Vec<_>>()).is_err());
        }
    }

    #[test]
    fn expected_rejection_requires_both_code_and_offline_summary() {
        let mut step = Step::command("negative", "blackhole", &[], false);
        step.expected_exit = 2;
        step.summary = Some("queries=0 unsupported=0 malformed=1 truncated=0".into());
        for (code, stdout, expected) in [
            (
                2,
                "mode=offline checksum=unverified direction=unverified\nqueries=0 unsupported=0 malformed=1 truncated=0\n",
                "passed",
            ),
            (2, "", "failed"),
            (
                0,
                "mode=offline checksum=unverified direction=unverified\nqueries=0 unsupported=0 malformed=1 truncated=0\n",
                "failed",
            ),
            (
                2,
                "mode=offline checksum=unverified direction=unverified\nqueries=1 unsupported=0 malformed=0 truncated=0\n",
                "failed",
            ),
        ] {
            let mut execution = Execution {
                result: json!({"status": "failed", "exit_code": code,
                "signal": null, "output_complete": true}),
                stdout: stdout.as_bytes().to_vec(),
            };
            judge(&step, &mut execution);
            assert_eq!(execution.result["status"], expected);
        }
    }

    #[test]
    fn repeated_run_ids_preserve_existing_artifacts() {
        let root =
            std::env::temp_dir().join(format!("veil-arena-allocation-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let (first, _) = allocate_run(&root, "same-clock").unwrap();
        write_json(&first.join("result.json"), &json!({"preserve": true})).unwrap();
        let (second, _) = allocate_run(&root, "same-clock").unwrap();
        assert_ne!(first, second);
        assert_eq!(
            serde_json::from_slice::<Value>(&fs::read(first.join("result.json")).unwrap()).unwrap()
                ["preserve"],
            true
        );
    }
}
