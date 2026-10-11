//! Guest fixture: stop only our owning batch after a real TC checkpoint.
use super::{parent_guard, read_report, save};
use serde_json::json;
use std::{
    fs, io,
    path::Path,
    time::{Duration, Instant},
};

pub fn observe(batch_pid: u32, out: &Path) -> io::Result<i32> {
    parent_guard()?;
    if unsafe { libc::getppid() } != batch_pid as i32
        || !fs::read_to_string("/proc/cmdline")?
            .split_whitespace()
            .any(|s| s == "arena.watchdog=1")
    {
        return Err(io::Error::other(
            "watchdog fixture requires its live owning batch and boot opt-in",
        ));
    }
    let until = Instant::now() + Duration::from_secs(90);
    let path = out.join("tc-ingress-001/tc-interruption-ready.json");
    let ready = loop {
        if path.exists() {
            break read_report(&path)?;
        }
        if Instant::now() >= until {
            return Err(io::Error::other("watchdog TC checkpoint missing"));
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let case_pid = ready["runner_pid"]
        .as_u64()
        .filter(|p| *p > 1 && *p <= i32::MAX as u64)
        .ok_or_else(|| io::Error::other("invalid checkpoint PID"))?;
    let case_status = fs::read_to_string(format!("/proc/{case_pid}/status"))?;
    if !case_status
        .lines()
        .any(|s| s == format!("PPid:\t{batch_pid}"))
        || unsafe { libc::getppid() } != batch_pid as i32
    {
        return Err(io::Error::other("checkpoint case is not owned by batch"));
    }
    // Our parent remains unreaped while this helper exists; this is not a guessed PID.
    if unsafe { libc::kill(batch_pid as i32, libc::SIGSTOP) } != 0 {
        return Err(io::Error::last_os_error());
    }
    let status = loop {
        let status = fs::read_to_string(format!("/proc/{batch_pid}/status"))?;
        if status.lines().any(|s| s.starts_with("State:\tT")) {
            break status;
        }
        if Instant::now() >= until {
            return Err(io::Error::other("batch did not enter stopped state"));
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    let root = out
        .parent()
        .ok_or_else(|| io::Error::other("missing output parent"))?;
    save(
        &root.join("watchdog-runner-status.json"),
        &json!({"batch_pid":batch_pid,"proc_status":status,"case_pid":case_pid,"case_proc_status":case_status}),
    )?;
    save(
        &root.join("watchdog-ready.json"),
        &json!({"schema_version":1,"scenario":"guest-runner-stopped","signal":libc::SIGSTOP,
        "batch_pid":batch_pid,"observer_pid":std::process::id(),"case_pid":case_pid,"state":"T","checkpoint":ready,
        "scope":"owning batch stopped; guest kernel remains responsive; no graceful guest cleanup"}),
    )?;
    // Hold the parent-bound helper; the host watchdog terminates this disposable VM.
    let mut byte = [0_u8; 1];
    use std::io::Read;
    std::io::stdin().read(&mut byte).map(|_| 0)
}
