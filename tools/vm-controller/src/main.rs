//! Host-owned lifecycle for a disposable, read-only guest. Evidence precedes stop.
use std::ffi::OsString;
use std::fs::{self, OpenOptions};
use std::io::{self, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::ExitStatusExt;
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

static CANCELLED: AtomicBool = AtomicBool::new(false);

extern "C" fn cancel(_: libc::c_int) {
    CANCELLED.store(true, Ordering::Relaxed);
}

fn completed(root: &Path) -> io::Result<bool> {
    let path = root.join("service-result.txt");
    let meta = match fs::symlink_metadata(&path) {
        Ok(meta) => meta,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if !meta.is_file() || meta.len() > 1024 {
        return Err(io::Error::other("invalid guest service marker"));
    }
    Ok(!fs::read_to_string(path)?.trim().is_empty())
}

fn sync_tree(path: &Path, remaining: &mut usize) -> io::Result<()> {
    if *remaining == 0 {
        return Err(io::Error::other("too many evidence entries"));
    }
    *remaining -= 1;
    let meta = fs::symlink_metadata(path)?;
    if meta.is_dir() {
        for entry in fs::read_dir(path)? {
            sync_tree(&entry?.path(), remaining)?;
        }
    } else if !meta.is_file() {
        return Err(io::Error::other("evidence contains a non-regular entry"));
    }
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)?
        .sync_all()
}

fn stop(child: &mut Child) -> io::Result<(ExitStatus, bool)> {
    if let Some(status) = child.try_wait()? {
        return Ok((status, false));
    }
    // Until this child is reaped its PID cannot identify another process.
    let code = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGTERM) };
    if code != 0 && io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH) {
        return Err(io::Error::last_os_error());
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok((status, false));
        }
        if Instant::now() >= deadline {
            child.kill()?;
            return Ok((child.wait()?, true));
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

#[cfg(test)]
fn run(root: &Path, argv: &[OsString], timeout: Duration) -> io::Result<i32> {
    run_policy(root, argv, timeout, false)
}

fn checkpoint(root: &Path) -> io::Result<bool> {
    let path = root.join("watchdog-ready.json");
    let meta = match fs::symlink_metadata(&path) {
        Ok(meta) => meta,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if !meta.is_file() || meta.len() > 65536 {
        return Err(io::Error::other("invalid watchdog checkpoint"));
    }
    let value: serde_json::Value =
        serde_json::from_slice(&fs::read(path)?).map_err(io::Error::other)?;
    if value["schema_version"] != 1
        || value["scenario"] != "guest-runner-stopped"
        || value["state"] != "T"
        || value["signal"] != 19
        || !value["batch_pid"]
            .as_u64()
            .is_some_and(|pid| pid > 1 && pid <= i32::MAX as u64)
    {
        return Err(io::Error::other("watchdog stopped-runner proof missing"));
    }
    Ok(true)
}

fn run_policy(root: &Path, argv: &[OsString], timeout: Duration, fixture: bool) -> io::Result<i32> {
    let serial = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(root.join("serial.log"))?;
    let started = Instant::now();
    let mut child = Command::new(&argv[0])
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(serial.try_clone()?)
        .stderr(serial)
        // Inherit the outer watchdog's process group so its final kill also
        // reaches QEMU if this controller is stuck in filesystem I/O.
        .spawn()?;
    let qemu_pid = child.id();
    let mut gate = None;
    let mut evidence_synced = false;
    let mut sync_error = None;
    let fixture_delay = Duration::from_secs(2);
    let reason = loop {
        if CANCELLED.load(Ordering::Relaxed) {
            break "cancelled";
        }
        if fixture && gate.is_none() {
            match checkpoint(root) {
                Ok(true) => gate = Some(Instant::now()),
                Ok(false) => (),
                Err(_) => break "evidence_error",
            }
        }
        if gate.is_some_and(|time| time.elapsed() >= fixture_delay)
            || started.elapsed()
                >= if fixture {
                    timeout.min(Duration::from_secs(120))
                } else {
                    timeout
                }
        {
            break "watchdog";
        }
        match child.try_wait() {
            Ok(Some(_)) => break "guest_exit",
            Ok(None) => (),
            Err(_) => break "process_error",
        }
        match completed(root) {
            Ok(true) => {
                if sync_tree(root, &mut 4096).is_err() {
                    break "evidence_error";
                }
                evidence_synced = true;
                break "service_completed";
            }
            Ok(false) => (),
            Err(_) => break "evidence_error",
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    // The stopped guest cannot finish its service. Preserve available evidence before stopping QEMU too.
    if !evidence_synced {
        match sync_tree(root, &mut 4096) {
            Ok(()) => evidence_synced = true,
            Err(error) => sync_error = Some(error.to_string()),
        }
    }
    let evidence_synced_before_stop = evidence_synced;
    let (status, escalated) = stop(&mut child)?;
    let service_completed = completed(root).unwrap_or(false);
    if !evidence_synced && reason == "guest_exit" && service_completed {
        evidence_synced = sync_tree(root, &mut 4096).is_ok();
    }
    let code = status.code().map_or("null".into(), |code| code.to_string());
    let mut record = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(root.join("vm-stop.json"))?;
    let lifecycle_exit_code = if matches!(reason, "service_completed" | "guest_exit")
        && service_completed
        && evidence_synced
        && !escalated
        && status.success()
    {
        0
    } else {
        3
    };
    serde_json::to_writer_pretty(
        &mut record,
        &serde_json::json!({"schema_version":2,"reason":reason,
        "status":if lifecycle_exit_code == 0 {"passed"} else {"environment_error"},"lifecycle_exit_code":lifecycle_exit_code,
        "qemu_pid":qemu_pid,"controller_pid":std::process::id(),"qemu_reaped":true,
        "qemu_exit_code":status.code(),"qemu_signal":status.signal(),"escalated":escalated,
        "evidence_synced":evidence_synced,"evidence_synced_before_stop":evidence_synced_before_stop,"sync_error":sync_error,
        "watchdog_fixture":fixture,"checkpoint_seen":gate.is_some(),"timeout_after_checkpoint_ms":if fixture {Some(2000)} else {None},
        "checkpoint_elapsed_ms":gate.map(|time|time.elapsed().as_millis()),"startup_timeout_ms":if fixture {timeout.min(Duration::from_secs(120)).as_millis()} else {timeout.as_millis()},
        "duration_ms":started.elapsed().as_millis()}),
    )?;
    writeln!(record)?;
    record.sync_all()?;
    let mut exit = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(root.join("qemu-exit-code.txt"))?;
    writeln!(exit, "{code}")?;
    exit.sync_all()?;
    sync_tree(root, &mut 4096)?;
    Ok(lifecycle_exit_code)
}

fn main() {
    let args: Vec<OsString> = std::env::args_os().skip(1).collect();
    let (fixture, args) = if args.first().is_some_and(|arg| arg == "--watchdog-fixture") {
        (true, &args[1..])
    } else {
        (false, &args[..])
    };
    if args.len() < 2 {
        eprintln!("usage: veil-arena-vm-controller OUTPUT_DIRECTORY QEMU [ARGS...]");
        std::process::exit(2);
    }
    for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
        if unsafe { libc::signal(signal, cancel as *const () as libc::sighandler_t) }
            == libc::SIG_ERR
        {
            eprintln!("cannot install cancellation handler");
            std::process::exit(3);
        }
    }
    let code = match run_policy(
        Path::new(&args[0]),
        &args[1..],
        Duration::from_secs(660),
        fixture,
    ) {
        Ok(code) => code,
        Err(error) => {
            eprintln!("VM lifecycle error: {error}");
            3
        }
    };
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::atomic::AtomicUsize;
    static NEXT: AtomicUsize = AtomicUsize::new(0);

    fn root() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "arena-vm-controller-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        path
    }
    fn shell(root: &Path, script: &str) -> Vec<OsString> {
        vec![
            "sh".into(),
            "-c".into(),
            script.into(),
            "fixture".into(),
            root.as_os_str().into(),
        ]
    }
    #[test]
    fn completion_is_synced_before_stopping_a_guest_that_stays_running() {
        let out = root();
        let args = shell(
            &out,
            "trap 'exit 0' TERM; printf success > \"$1/service-result.txt\"; printf ready; while :; do sleep 0.02; done",
        );
        assert_eq!(run(&out, &args, Duration::from_secs(3)).unwrap(), 0);
        let record = fs::read_to_string(out.join("vm-stop.json")).unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&record).unwrap()["reason"],
            "service_completed"
        );
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&record).unwrap()["evidence_synced_before_stop"],
            true
        );
        assert!(
            fs::read_to_string(out.join("serial.log"))
                .unwrap()
                .contains("ready")
        );
    }
    #[test]
    fn clean_exit_without_service_completion_is_an_environment_error() {
        let out = root();
        assert_eq!(
            run(
                &out,
                &shell(&out, "printf premature"),
                Duration::from_secs(3)
            )
            .unwrap(),
            3
        );
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(
                &fs::read_to_string(out.join("vm-stop.json")).unwrap()
            )
            .unwrap()["reason"],
            "guest_exit"
        );
    }
    #[test]
    fn watchdog_cannot_become_a_pass() {
        let out = root();
        assert_eq!(
            run(
                &out,
                &shell(&out, "exec sleep 10"),
                Duration::from_millis(50)
            )
            .unwrap(),
            3
        );
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(
                &fs::read_to_string(out.join("vm-stop.json")).unwrap()
            )
            .unwrap()["reason"],
            "watchdog"
        );
    }
    #[test]
    fn stopped_runner_fixture_preserves_partial_evidence_before_owned_stop() {
        let out = root();
        let args = shell(
            &out,
            "trap 'exit 0' TERM; printf partial > \"$1/partial.log\"; printf '%s' '{\"schema_version\":1,\"scenario\":\"guest-runner-stopped\",\"state\":\"T\",\"signal\":19,\"batch_pid\":42}' > \"$1/watchdog-ready.json\"; while :; do sleep 0.02; done",
        );
        assert_eq!(
            run_policy(&out, &args, Duration::from_secs(3), true).unwrap(),
            3
        );
        let record: serde_json::Value =
            serde_json::from_slice(&fs::read(out.join("vm-stop.json")).unwrap()).unwrap();
        assert_eq!(record["reason"], "watchdog");
        assert_eq!(record["status"], "environment_error");
        assert_eq!(record["checkpoint_seen"], true);
        assert_eq!(record["evidence_synced_before_stop"], true);
        assert_eq!(record["qemu_reaped"], true);
        assert_eq!(record["escalated"], false);
        assert!(record["checkpoint_elapsed_ms"].as_u64().unwrap() >= 2000);
        assert_eq!(fs::read(out.join("partial.log")).unwrap(), b"partial");
        assert!(!out.join("service-result.txt").exists());
    }
    #[test]
    fn evidence_symlink_is_rejected_without_touching_its_target() {
        let out = root();
        let outside = root().join("untouched");
        fs::write(&outside, "original").unwrap();
        std::os::unix::fs::symlink(&outside, out.join("link")).unwrap();
        let args = shell(
            &out,
            "trap 'exit 0' TERM; printf success > \"$1/service-result.txt\"; while :; do sleep 0.02; done",
        );
        assert!(run(&out, &args, Duration::from_secs(3)).is_err());
        assert_eq!(fs::read_to_string(outside).unwrap(), "original");
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(
                &fs::read_to_string(out.join("vm-stop.json")).unwrap()
            )
            .unwrap()["reason"],
            "evidence_error"
        );
    }
}
