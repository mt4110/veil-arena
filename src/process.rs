use serde_json::{Value, json};
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::process::{CommandExt, ExitStatusExt};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

pub const LOG_LIMIT: usize = 2 * 1024 * 1024;

pub struct Execution {
    pub result: Value,
    pub stdout: Vec<u8>,
}

struct OwnedChild {
    child: Child,
    reaped: bool,
    finished: bool,
}

impl OwnedChild {
    fn stop(&mut self) -> io::Result<()> {
        let pid = i32::try_from(self.child.id()).map_err(io::Error::other)?;
        // SAFETY: pid is a positive PID returned by spawn. process_group(0)
        // created a new group with that PID; we never signal the host group.
        let result = unsafe { libc::kill(-pid, libc::SIGKILL) };
        if result == 0 {
            Ok(())
        } else {
            let error = io::Error::last_os_error();
            if error.raw_os_error() == Some(libc::ESRCH) {
                Ok(())
            } else {
                Err(error)
            }
        }
    }
}

impl Drop for OwnedChild {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.stop();
            // Best effort only; never block indefinitely while unwinding.
            let _ = self.child.try_wait();
        }
    }
}

fn nonblocking(fd: RawFd) -> io::Result<()> {
    // SAFETY: fd comes from a live ChildStdout/ChildStderr. Neither call
    // changes ownership; existing file status flags are preserved.
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

struct Capture<R> {
    pipe: R,
    file: File,
    bytes: Vec<u8>,
    eof: bool,
    truncated: bool,
}

impl<R: Read + AsRawFd> Capture<R> {
    fn new(pipe: R, file: File) -> io::Result<Self> {
        nonblocking(pipe.as_raw_fd())?;
        Ok(Self {
            pipe,
            file,
            bytes: Vec::new(),
            eof: false,
            truncated: false,
        })
    }

    fn pump(&mut self) -> io::Result<()> {
        // Bound each polling turn so a continuous producer cannot prevent
        // checking cancellation, the deadline, or the other output stream.
        let mut buffer = [0_u8; 8192];
        for _ in 0..8 {
            match self.pipe.read(&mut buffer) {
                Ok(0) => {
                    self.eof = true;
                    break;
                }
                Ok(count) => {
                    let retained = count.min(LOG_LIMIT - self.bytes.len());
                    self.file.write_all(&buffer[..retained])?;
                    self.bytes.extend_from_slice(&buffer[..retained]);
                    self.truncated |= retained < count;
                }
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }
}

pub struct GracefulStop {
    pub request_path: std::path::PathBuf,
    pub readiness_path: Option<std::path::PathBuf>,
    pub timeout_after_ready: Option<Duration>,
    pub cancel_after_ready: bool,
    pub grace: Duration,
}
pub struct Control<'a> {
    pub deadline: Instant,
    pub cancelled: &'a AtomicBool,
    pub graceful: Option<GracefulStop>,
}

pub fn execute(
    id: &str,
    program: &str,
    args: &[String],
    cwd: &Path,
    run_dir: &Path,
    deadline: Instant,
    cancelled: &AtomicBool,
) -> io::Result<Execution> {
    execute_controlled(
        id,
        program,
        args,
        cwd,
        run_dir,
        Control {
            deadline,
            cancelled,
            graceful: None,
        },
    )
}

pub fn execute_controlled(
    id: &str,
    program: &str,
    args: &[String],
    cwd: &Path,
    run_dir: &Path,
    control: Control<'_>,
) -> io::Result<Execution> {
    let Control {
        mut deadline,
        cancelled,
        graceful,
    } = control;
    let start = Instant::now();
    let stdout_name = format!("{id}.stdout.log");
    let stderr_name = format!("{id}.stderr.log");
    let stdout_file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(run_dir.join(&stdout_name))?;
    let stderr_file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(run_dir.join(&stderr_name))?;
    let mut result = json!({
        "id": id, "program": program, "args": args, "cwd": cwd,
        "stdout_log": stdout_name, "stderr_log": stderr_name,
        "exit_code": null, "signal": null, "status": "environment_error",
        "output_complete": false, "log_truncated": false,
    });
    if cancelled.load(Ordering::SeqCst) || Instant::now() >= deadline {
        result["status"] = json!(if cancelled.load(Ordering::SeqCst) {
            "cancelled"
        } else {
            "timeout"
        });
        result["detail"] = json!("command was not started");
        return Ok(Execution {
            result,
            stdout: vec![],
        });
    }
    let mut command = Command::new(program);
    command
        .args(args)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("CARGO_NET_OFFLINE", "true")
        .env("CARGO_TERM_COLOR", "never")
        .process_group(0);
    let child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            result["detail"] = json!(format!("spawn failed: {error}"));
            result["duration_ms"] = json!(start.elapsed().as_millis());
            return Ok(Execution {
                result,
                stdout: vec![],
            });
        }
    };
    result["child_pid"] = json!(child.id());
    let mut owned = OwnedChild {
        child,
        reaped: false,
        finished: false,
    };
    let mut stdout = Capture::new(
        owned.child.stdout.take().expect("piped stdout"),
        stdout_file,
    )?;
    let mut stderr = Capture::new(
        owned.child.stderr.take().expect("piped stderr"),
        stderr_file,
    )?;
    let mut exit: Option<ExitStatus> = None;
    let mut stopped_at = None;
    let mut forced_status = None;
    let mut gate_seen = false;
    let mut escalated = false;
    let mut killed_at = None;
    loop {
        stdout.pump()?;
        stderr.pump()?;
        let now = Instant::now();
        if !gate_seen
            && graceful.as_ref().is_some_and(|policy| {
                policy
                    .readiness_path
                    .as_ref()
                    .is_some_and(|path| path.is_file())
            })
        {
            gate_seen = true;
            result["readiness_observed"] = json!(true);
            if let Some(duration) = graceful
                .as_ref()
                .and_then(|policy| policy.timeout_after_ready)
            {
                deadline = deadline.min(now + duration);
            }
        }
        if stopped_at.is_none() {
            forced_status = if cancelled.load(Ordering::SeqCst)
                || (gate_seen
                    && graceful
                        .as_ref()
                        .is_some_and(|policy| policy.cancel_after_ready))
            {
                Some("cancelled")
            } else if stdout.truncated || stderr.truncated {
                Some("output_limit")
            } else if now >= deadline {
                Some("timeout")
            } else {
                None
            };
            if forced_status.is_some() {
                if let Some(policy) = &graceful {
                    let request = json!({"reason":forced_status,"signal":if owned.reaped { None } else { Some(libc::SIGINT) },"child_pid":owned.child.id()});
                    let pending = policy.request_path.with_extension("json.pending");
                    let mut file = OpenOptions::new()
                        .write(true)
                        .create_new(true)
                        .open(&pending)?;
                    serde_json::to_writer(&mut file, &request)?;
                    file.write_all(b"\n")?;
                    file.sync_all()?;
                    drop(file);
                    std::fs::hard_link(&pending, &policy.request_path)?;
                    // This child is still held and unreaped; never signal a guessed PID/group.
                    if !owned.reaped
                        && unsafe { libc::kill(owned.child.id() as i32, libc::SIGINT) } != 0
                        && io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
                    {
                        return Err(io::Error::last_os_error());
                    }
                    result["stop_request"] = request;
                } else {
                    owned.stop()?;
                    killed_at = Some(now);
                }
                stopped_at = Some(now);
            }
        }
        if exit.is_none() {
            exit = owned.child.try_wait()?;
            owned.reaped = exit.is_some();
        }
        if exit.is_some() && stdout.eof && stderr.eof {
            break;
        }
        if graceful
            .as_ref()
            .is_some_and(|policy| stopped_at.is_some_and(|time| time.elapsed() >= policy.grace))
            && killed_at.is_none()
        {
            owned.stop()?;
            escalated = true;
            killed_at = Some(now);
        }
        if killed_at.is_some_and(|time| time.elapsed() >= Duration::from_secs(2)) {
            result["detail"] =
                json!("termination/output collection did not complete within 2 seconds");
            break;
        }
        thread::sleep(Duration::from_millis(10));
    }
    stdout.file.sync_all()?;
    stderr.file.sync_all()?;
    result["termination_escalated"] = json!(escalated);
    result["duration_ms"] = json!(start.elapsed().as_millis());
    result["output_complete"] = json!(stdout.eof && stderr.eof);
    result["log_truncated"] = json!(stdout.truncated || stderr.truncated);
    if let Some(status) = exit {
        result["exit_code"] = json!(status.code());
        result["signal"] = json!(status.signal());
    }
    result["status"] = json!(forced_status.unwrap_or_else(|| {
        if exit.is_some_and(|status| status.success()) {
            "passed"
        } else {
            "failed"
        }
    }));
    owned.finished = owned.reaped && stdout.eof && stderr.eof;
    Ok(Execution {
        result,
        stdout: stdout.bytes,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicU64;
    use std::time::{SystemTime, UNIX_EPOCH};

    static NEXT_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    fn directory() -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "veil-arena-process-{}-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir(&path).unwrap();
        path
    }

    fn shell(script: &str, timeout: Duration, cancelled: &AtomicBool) -> Execution {
        let dir = directory();
        execute(
            "test",
            "sh",
            &["-c".into(), script.into()],
            &dir,
            &dir,
            Instant::now() + timeout,
            cancelled,
        )
        .unwrap()
    }

    #[test]
    fn graceful_stop_preserves_signal_cleanup_for_cancel_and_timeout() {
        for reason in ["cancelled", "timeout"] {
            let root = std::env::temp_dir().join(format!(
                "arena-grace-{}-{}",
                std::process::id(),
                NEXT_DIRECTORY.fetch_add(1, Ordering::Relaxed)
            ));
            std::fs::create_dir(&root).unwrap();
            let ready = root.join("ready");
            let code = if reason == "cancelled" { 130 } else { 4 };
            let script = format!(
                "trap 'printf cleanup; exit {code}' INT; printf partial; : > '{}'; while :; do sleep 0.05; done",
                ready.display()
            );
            let cancelled = AtomicBool::new(false);
            let result = execute_controlled(
                "grace",
                "sh",
                &["-c".into(), script],
                &root,
                &root,
                Control {
                    deadline: Instant::now() + Duration::from_secs(5),
                    cancelled: &cancelled,
                    graceful: Some(GracefulStop {
                        request_path: root.join("request.json"),
                        readiness_path: Some(ready),
                        timeout_after_ready: if reason == "timeout" {
                            Some(Duration::from_millis(100))
                        } else {
                            None
                        },
                        cancel_after_ready: reason == "cancelled",
                        grace: Duration::from_secs(2),
                    }),
                },
            )
            .unwrap();
            assert_eq!(result.result["status"], reason);
            assert_eq!(result.result["exit_code"], code);
            assert_eq!(result.result["termination_escalated"], false);
            assert_eq!(result.result["output_complete"], true);
            assert_eq!(result.stdout, b"partialcleanup");
            assert_eq!(result.result["stop_request"]["reason"], reason);
        }
    }

    #[test]
    fn failure_and_signal_are_not_success() {
        let result = shell(
            "printf evidence; exit 7",
            Duration::from_secs(2),
            &AtomicBool::new(false),
        );
        assert_eq!(result.result["status"], "failed");
        assert_eq!(result.result["exit_code"], 7);
        assert_eq!(result.stdout, b"evidence");
        let result = shell(
            "kill -TERM $$",
            Duration::from_secs(2),
            &AtomicBool::new(false),
        );
        assert_eq!(result.result["status"], "failed");
        assert_eq!(result.result["signal"], libc::SIGTERM);
    }

    #[test]
    fn spawn_error_and_pre_cancel_are_explicit() {
        let dir = directory();
        let result = execute(
            "missing",
            "/no/such/veil-arena-program",
            &[],
            &dir,
            &dir,
            Instant::now() + Duration::from_secs(2),
            &AtomicBool::new(false),
        )
        .unwrap();
        assert_eq!(result.result["status"], "environment_error");
        let result = shell("exit 0", Duration::from_secs(2), &AtomicBool::new(true));
        assert_eq!(result.result["status"], "cancelled");
        assert!(result.result["exit_code"].is_null());
    }

    #[test]
    fn deadline_covers_descendants_holding_pipes_after_parent_exit() {
        let start = Instant::now();
        let result = shell(
            "sleep 30 & exit 0",
            Duration::from_millis(100),
            &AtomicBool::new(false),
        );
        assert_eq!(result.result["status"], "timeout");
        assert_eq!(result.result["output_complete"], true);
        assert!(start.elapsed() < Duration::from_secs(3));
    }

    #[test]
    fn excessive_output_is_bounded_and_fails() {
        let result = shell(
            "yes bounded-output",
            Duration::from_secs(3),
            &AtomicBool::new(false),
        );
        assert_eq!(result.result["status"], "output_limit");
        assert_eq!(result.stdout.len(), LOG_LIMIT);
        assert_eq!(result.result["log_truncated"], true);
    }

    #[test]
    fn cancellation_after_spawn_preserves_partial_output() {
        let cancelled = AtomicBool::new(false);
        thread::scope(|scope| {
            scope.spawn(|| {
                thread::sleep(Duration::from_millis(100));
                cancelled.store(true, Ordering::SeqCst);
            });
            let result = shell(
                "printf before-cancel; sleep 30",
                Duration::from_secs(3),
                &cancelled,
            );
            assert_eq!(result.result["status"], "cancelled");
            assert_eq!(result.stdout, b"before-cancel");
            assert_eq!(result.result["output_complete"], true);
        });
    }
}
