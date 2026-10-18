//! Run a prepared image without Nix or Cargo; acceptance adds a fresh boot.
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

static CANCELLED: AtomicBool = AtomicBool::new(false);
extern "C" fn cancel(_: libc::c_int) {
    CANCELLED.store(true, Ordering::Relaxed);
}

fn bytes(path: &Path) -> io::Result<Vec<u8>> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.len() > 1024 * 1024 {
        return Err(io::Error::other("not a bounded regular report"));
    }
    let mut data = vec![];
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut data)?;
    if data.len() > 1024 * 1024 {
        return Err(io::Error::other("report grew beyond limit"));
    }
    Ok(data)
}
fn report(path: &Path) -> io::Result<Value> {
    serde_json::from_slice(&bytes(path)?).map_err(io::Error::other)
}
fn save(path: &Path, value: &Value) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    serde_json::to_writer_pretty(&mut file, value)?;
    file.write_all(b"\n")?;
    file.sync_all()
}

fn execute(launcher: &Path, argument: &str, log: &Path, fixture: bool) -> io::Result<i32> {
    if CANCELLED.load(Ordering::Relaxed) {
        return Ok(3);
    }
    let output = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(log)?;
    // The fixed launcher owns its watchdog and forwards termination to it.
    // No extra process group would hide that watchdog from the lifecycle.
    let mut command = Command::new(launcher);
    if fixture {
        command.arg("--watchdog-fixture");
    }
    let mut child = command
        .arg(argument)
        .stdin(Stdio::null())
        .stdout(output.try_clone()?)
        .stderr(output)
        .spawn()?;
    let start = Instant::now();
    let mut requested = None;
    loop {
        if let Some(status) = child.try_wait()? {
            return Ok(if requested.is_some() {
                3
            } else {
                status.code().unwrap_or(3)
            });
        }
        if requested.is_none()
            && (CANCELLED.load(Ordering::Relaxed) || start.elapsed() > Duration::from_secs(900))
        {
            let code = unsafe { libc::kill(child.id() as libc::pid_t, libc::SIGTERM) };
            if code != 0 && io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH) {
                return Err(io::Error::last_os_error());
            }
            requested = Some(Instant::now());
        }
        if requested.is_some_and(|time| time.elapsed() > Duration::from_secs(10)) {
            // QEMU has its own 670s watchdog even if the wrapper cannot exit.
            child.kill()?;
            child.wait()?;
            return Ok(3);
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

struct Verified {
    boot: String,
    inputs_hash: String,
    cases: Vec<Value>,
}

fn verified(root: &Path, manifest: &Value) -> io::Result<Verified> {
    if report(&root.join("vm-manifest.json"))? != *manifest {
        return Err(io::Error::other("prepared image manifest changed"));
    }
    let result = report(&root.join("netns-batch/result.json"))?;
    let cases = result["cases"]
        .as_array()
        .ok_or_else(|| io::Error::other("batch cases missing"))?;
    let expected = [
        ("normal-001", 0),
        ("route-fault-001", 1),
        ("normal-002", 0),
        ("tc-ingress-001", 0),
        ("tc-drop-disabled-001", 1),
        ("tc-wrong-hook-001", 3),
        ("tc-receiver-stopped-001", 3),
        ("bpf-test-run-001", 0),
        ("bpf-load-failure-001", 3),
        ("batch-cancel-001", 0),
        ("tc-after-cancel-001", 0),
        ("batch-timeout-001", 0),
        ("tc-restored-001", 0),
        ("tc-netem-loss-001", 0),
        ("tc-netem-unconfigured-001", 3),
        ("tc-after-netem-001", 0),
    ];
    if result["schema_version"] != 7
        || result["scenario"] != "netns-batch"
        || result["status"] != "passed"
        || result["exit_code"] != 0
        || result["vm_boots"] != 1
        || cases.len() != 16
        || !cases.iter().zip(expected).all(|(case, (id, code))| {
            case["id"] == id
                && case["status"] == "passed"
                && case["expected_exit_code"] == code
                && case["execution"]["exit_code"] == code
        })
    {
        return Err(io::Error::other("batch pass evidence incomplete"));
    }
    let stop = report(&root.join("vm-stop.json"))?;
    if stop["qemu_exit_code"] != 0
        || stop["escalated"] != false
        || stop["evidence_synced"] != true
        || stop["evidence_synced_before_stop"] != true
        || stop["reason"] != "service_completed"
        || String::from_utf8_lossy(&bytes(&root.join("service-result.txt"))?).trim() != "success"
    {
        return Err(io::Error::other("VM lifecycle evidence incomplete"));
    }
    let boot = String::from_utf8(bytes(&root.join("guest-boot-id.txt"))?)
        .map_err(io::Error::other)?
        .trim()
        .to_owned();
    if boot.len() != 36 || !boot.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
        return Err(io::Error::other("invalid boot identity"));
    }
    let inputs = bytes(&root.join("boot-inputs.sha256"))?;
    if inputs.is_empty() {
        return Err(io::Error::other("boot input hashes missing"));
    }
    Ok(Verified {
        boot,
        inputs_hash: format!("{:x}", Sha256::digest(inputs)),
        cases: cases
            .iter()
            .map(|case| {
                json!({
                    "id":case["id"], "status":case["status"],
                    "expected_exit_code":case["expected_exit_code"],
                    "exit_code":case["execution"]["exit_code"], "report":case["report"]
                })
            })
            .collect(),
    })
}

fn verified_watchdog(root: &Path, manifest: &Value) -> io::Result<Verified> {
    let stop = report(&root.join("vm-stop.json"))?;
    let ready = report(&root.join("watchdog-ready.json"))?;
    let progress = report(&root.join("netns-batch/batch-progress.json"))?;
    let checked = report(&root.join("watchdog-verification.json"))?;
    if report(&root.join("vm-manifest.json"))? != *manifest
        || manifest["watchdog_fixture_supported"] != true
        || checked["status"] != "passed"
        || checked["scenario"] != "netns-watchdog"
        || stop["schema_version"] != 2
        || stop["reason"] != "watchdog"
        || stop["status"] != "environment_error"
        || stop["lifecycle_exit_code"] != 3
        || stop["qemu_reaped"] != true
        || stop["qemu_exit_code"] != 0
        || stop["escalated"] != false
        || stop["evidence_synced_before_stop"] != true
        || stop["evidence_synced"] != true
        || !stop["sync_error"].is_null()
        || stop["watchdog_fixture"] != true
        || stop["checkpoint_seen"] != true
        || ready["state"] != "T"
        || progress["status"] != "incomplete"
        || ready["batch_pid"] != progress["runner_pid"]
        || root.join("service-result.txt").exists()
        || root.join("netns-batch/result.json").exists()
    {
        return Err(io::Error::other(
            "watchdog expected failure evidence incomplete",
        ));
    }
    let boot = String::from_utf8(bytes(&root.join("guest-boot-id.txt"))?)
        .map_err(io::Error::other)?
        .trim()
        .to_owned();
    if boot.len() != 36 || !boot.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
        return Err(io::Error::other("invalid boot identity"));
    }
    let inputs = bytes(&root.join("boot-inputs.sha256"))?;
    if inputs.is_empty() {
        return Err(io::Error::other("missing boot input hashes"));
    }
    Ok(Verified {
        boot,
        inputs_hash: format!("{:x}", Sha256::digest(inputs)),
        cases: progress["cases"]
            .as_array()
            .ok_or_else(|| io::Error::other("partial cases missing"))?
            .clone(),
    })
}

fn workflow(launcher: &Path, mode: &str, root: &Path) -> io::Result<i32> {
    fs::DirBuilder::new().mode(0o700).create(root)?;
    let started = Instant::now();
    let mut runs = vec![];
    let mut final_code = 3;
    let mut detail = None;
    let attempt = (|| -> io::Result<()> {
        if execute(
            launcher,
            "--describe",
            &root.join("prepared-manifest.json"),
            false,
        )? != 0
        {
            return Err(io::Error::other("prepared launcher description failed"));
        }
        let manifest = report(&root.join("prepared-manifest.json"))?;
        if manifest["scenario"] != "netns-batch" {
            return Err(io::Error::other("requires a prepared netns-batch image"));
        }
        let mut previous = None;
        let names = match mode {
            "watchdog-acceptance" => vec![
                ("daily-watchdog", true),
                ("daily", false),
                ("fresh-watchdog", true),
                ("fresh", false),
            ],
            "watchdog-daily" => vec![("daily-watchdog", true), ("daily", false)],
            "acceptance" => vec![("daily", false), ("fresh", false)],
            _ => vec![("daily", false)],
        };
        let mut boots = vec![];
        for (name, fixture) in names {
            let output = root.join(name);
            let exit = execute(
                launcher,
                &output.to_string_lossy(),
                &root.join(format!("{name}.launcher.log")),
                fixture,
            )?;
            runs.push(json!({"id":name,"exit_code":exit,"expected_exit_code":if fixture {3} else {0},"watchdog_fixture":fixture,"evidence_verification":"not_started","status":"unverified","output":name}));
            if exit != if fixture { 3 } else { 0 } {
                final_code = if exit == 1 { 1 } else { 3 };
                runs.last_mut().unwrap()["status"] = json!("failed");
                return Err(io::Error::other(format!(
                    "{name} failed; inspect {name}.launcher.log"
                )));
            }
            runs.last_mut().unwrap()["evidence_verification"] = json!("pending");
            let Verified {
                boot,
                inputs_hash: hash,
                cases,
            } = if fixture {
                verified_watchdog(&output, &manifest).map_err(|error| {
                    runs.last_mut().unwrap()["status"] = json!("failed");
                    io::Error::other(format!(
                        "{name} watchdog evidence verification failed: {error}"
                    ))
                })?
            } else {
                verified(&output, &manifest)?
            };
            // Evidence validation and cross-run acceptance are separate gates.
            runs.last_mut().unwrap()["evidence_verification"] = json!("passed");
            if previous
                .as_ref()
                .is_some_and(|(old_boot, old_hash)| *old_boot == boot || *old_hash != hash)
            {
                return Err(io::Error::other(
                    "acceptance requires a different boot with identical inputs",
                ));
            }
            if boots.contains(&boot) {
                return Err(io::Error::other("VM boot identity repeated"));
            }
            boots.push(boot.clone());
            previous = Some((boot.clone(), hash.clone()));
            *runs.last_mut().unwrap() = json!({"id":name,"exit_code":exit,"expected_exit_code":if fixture {3} else {0},"watchdog_fixture":fixture,"evidence_verification":"passed","status":"passed","output":name,"boot_id":boot,"boot_inputs_sha256":hash,"cases":cases});
        }
        final_code = 0;
        Ok(())
    })();
    if let Err(error) = attempt {
        detail = Some(error.to_string());
    }
    save(
        &root.join("summary.json"),
        &json!({"schema_version":1,"scenario":"netns-workflow","mode":mode,
        "status":if final_code==0 {"passed"} else if final_code==1 {"failed"} else {"environment_error"},
        "exit_code":final_code,"runs":runs,"requested_vm_boots":match mode {"watchdog-acceptance"=>4,"watchdog-daily"|"acceptance"=>2,_=>1},
        "duration_ms":started.elapsed().as_millis(),"detail":detail,
        "build_policy":"prepared immutable launcher only; no build or dependency fetch during tests"}),
    )?;
    println!(
        "{}: {} — {}",
        mode,
        if final_code == 0 { "passed" } else { "failed" },
        root.join("summary.json").display()
    );
    Ok(final_code)
}

fn diagnose(python: &Path, script: &Path, root: &Path) -> io::Result<()> {
    // workflow() has already waited for the launcher and saved its summary.
    // This reader must never participate in VM cleanup or change the verdict.
    let mut child = Command::new(python)
        .arg("-B")
        .arg(script)
        .arg(root)
        .stdin(Stdio::null())
        .spawn()?;
    let started = Instant::now();
    loop {
        if let Some(status) = child.try_wait()? {
            return if status.success() {
                Ok(())
            } else {
                Err(io::Error::other(format!("reader exited with {status}")))
            };
        }
        if started.elapsed() >= Duration::from_secs(5) {
            child.kill()?;
            child.wait()?;
            return Err(io::Error::other("reader exceeded 5s; stopped and reaped"));
        }
        std::thread::sleep(Duration::from_millis(25));
    }
}

fn main() {
    for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGHUP] {
        if unsafe { libc::signal(signal, cancel as *const () as libc::sighandler_t) }
            == libc::SIG_ERR
        {
            std::process::exit(3);
        }
    }
    let args: Vec<_> = std::env::args_os().skip(1).collect();
    if !(args.len() == 3 || (args.len() == 6 && args[3] == "--diagnose"))
        || !matches!(
            args[1].to_str(),
            Some("daily" | "acceptance" | "watchdog-daily" | "watchdog-acceptance")
        )
    {
        eprintln!(
            "usage: prepared-launcher daily|acceptance|watchdog-daily|watchdog-acceptance NEW_OUTPUT_DIRECTORY [--diagnose PYTHON SCRIPT]"
        );
        std::process::exit(2);
    }
    if unsafe { libc::geteuid() } == 0 {
        eprintln!("run as an ordinary host user");
        std::process::exit(3);
    }
    let result = workflow(
        Path::new(&args[0]),
        args[1].to_str().unwrap(),
        Path::new(&args[2]),
    );
    if args.len() == 6 {
        match &result {
            Ok(0) => (),
            Ok(code) => {
                eprintln!("Workflow failed (exit {code}); reading preserved evidence:");
                if let Err(error) = diagnose(
                    Path::new(&args[4]),
                    Path::new(&args[5]),
                    Path::new(&args[2]),
                ) {
                    eprintln!(
                        "diagnosis unavailable: {error}; original workflow exit {code} retained"
                    );
                }
            }
            // Do not inspect an old directory when create-new or saving failed.
            Err(_) => eprintln!(
                "diagnosis unavailable: workflow did not complete; original error retained"
            ),
        }
    }
    std::process::exit(result.unwrap_or_else(|error| {
        eprintln!("workflow: {error}");
        3
    }));
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::atomic::AtomicUsize;
    static NEXT_FIXTURE: AtomicUsize = AtomicUsize::new(0);

    fn fixture(name: &str) -> (std::path::PathBuf, std::path::PathBuf) {
        let root = std::env::temp_dir().join(format!(
            "arena-workflow-{}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos(),
            NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        // A sibling test can spawn while another thread writes a launcher.
        // Its child may inherit that writable FD until exec, causing ETXTBSY.
        // Use fixed executables; only result directories are generated here.
        let launcher = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("src/fixtures")
            .join(name);
        assert!(fs::metadata(&launcher).unwrap().permissions().mode() & 0o111 != 0);
        (root, launcher)
    }
    #[test]
    fn clean_exit_without_evidence_cannot_pass() {
        let (root, launcher) = fixture("no-evidence.sh");
        assert_eq!(workflow(&launcher, "daily", &root.join("run")).unwrap(), 3);
        assert_eq!(
            report(&root.join("run/summary.json")).unwrap()["status"],
            "environment_error"
        );
    }
    #[test]
    fn failed_first_vm_prevents_acceptance_boot_and_preserves_logs() {
        let (root, launcher) = fixture("failed-vm.sh");
        assert_eq!(
            workflow(&launcher, "acceptance", &root.join("run")).unwrap(),
            1
        );
        assert!(!root.join("run/fresh.launcher.log").exists());
        assert_eq!(
            fs::read(root.join("run/daily.launcher.log")).unwrap(),
            b"failure"
        );
    }
    #[test]
    fn arbitrary_environment_error_is_not_an_expected_watchdog() {
        let (root, launcher) = fixture("unrelated-error.sh");
        assert_eq!(
            workflow(&launcher, "watchdog-acceptance", &root.join("run")).unwrap(),
            3
        );
        assert!(!root.join("run/daily.launcher.log").exists());
        assert_eq!(
            report(&root.join("run/summary.json")).unwrap()["status"],
            "environment_error"
        );
    }
    #[test]
    fn existing_output_is_preserved() {
        let (root, launcher) = fixture("no-evidence.sh");
        let output = root.join("existing");
        fs::create_dir(&output).unwrap();
        fs::write(output.join("evidence"), b"keep").unwrap();
        assert!(workflow(&launcher, "daily", &output).is_err());
        assert_eq!(fs::read(output.join("evidence")).unwrap(), b"keep");
    }
}
