//! Guest-only baseline. Anonymous namespaces are owned by child lifetimes.
#[path = "interruption.rs"]
mod interruption;
#[path = "load_failure.rs"]
mod load_failure;
#[path = "netem.rs"]
mod netem;
#[path = "process.rs"]
mod process;
#[path = "tc.rs"]
mod tc;
#[path = "tcp_syn.rs"]
mod tcp_syn;
#[path = "test_run.rs"]
mod test_run;
#[path = "watchdog.rs"]
mod watchdog;

use serde_json::{Value, json};
use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream, UdpSocket};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

const APP: &str = "10.77.1.10";
const SERVER: &str = "10.77.2.10";
const TIMEOUT: Duration = Duration::from_secs(90);
const BATCH_CASES: [(&str, i32); 18] = [
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
    ("tc-tcp-drop-disabled-001", 1),
    ("tc-tcp-syn-001", 0),
];

fn save(path: &Path, value: &Value) -> io::Result<()> {
    // Readers poll the final name. Publish only after the complete JSON is synced.
    // hard_link is atomic and fails if the destination exists; retain the pending
    // file as evidence rather than deleting or replacing an existing report.
    let mut pending = path.as_os_str().to_os_string();
    pending.push(".pending");
    let mut out = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(Path::new(&pending))?;
    serde_json::to_writer_pretty(&mut out, value)?;
    out.write_all(b"\n")?;
    out.sync_all()?;
    drop(out);
    fs::hard_link(Path::new(&pending), path)
}

fn ns(pid: u32) -> io::Result<PathBuf> {
    fs::read_link(format!("/proc/{pid}/ns/net"))
}

fn parent_guard() -> io::Result<()> {
    // Helpers are bounded and also die if the owning runner disappears.
    let parent = unsafe { libc::getppid() };
    if unsafe { libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) } != 0
        || parent <= 1
        || unsafe { libc::getppid() } != parent
    {
        return Err(io::Error::other("cannot bind helper lifetime to parent"));
    }
    Ok(())
}

fn vm_guard() -> io::Result<()> {
    // All helper entry points share the same guard, so they cannot accidentally
    // bind the synthetic addresses on an ordinary host.
    if unsafe { libc::geteuid() } != 0
        || !fs::read_to_string("/proc/cmdline")?
            .split_whitespace()
            .any(|value| value == "arena.netns=1")
    {
        return Err(io::Error::other("requires root inside the arena netns VM"));
    }
    Ok(())
}

struct Owned {
    child: Child,
    stopped: bool,
}

impl Owned {
    fn stop(&mut self) -> io::Result<()> {
        self.stop_with_grace(Duration::from_secs(2))
    }

    fn stop_with_grace(&mut self, grace: Duration) -> io::Result<()> {
        if self.stopped {
            return Ok(());
        }
        // Closing the hold helper's pipe lets it exit normally.
        self.child.stdin.take();
        let until = Instant::now() + grace;
        loop {
            if self.child.try_wait()?.is_some() {
                self.stopped = true;
                return Ok(());
            }
            if Instant::now() >= until {
                break;
            }
            thread::sleep(Duration::from_millis(10));
        }
        self.child.kill()?;
        let until = Instant::now() + Duration::from_secs(2);
        while Instant::now() < until {
            if self.child.try_wait()?.is_some() {
                self.stopped = true;
                return Ok(());
            }
            thread::sleep(Duration::from_millis(10));
        }
        Err(io::Error::other("owned helper did not stop"))
    }
}

impl Drop for Owned {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

fn spawn(program: &str, args: &[String], log: &Path, new_net: bool) -> io::Result<Owned> {
    let stderr = OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(log)?;
    let mut command = Command::new(program);
    command
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(stderr)
        .process_group(0);
    // Only async-signal-safe syscalls before exec. New namespaces are created
    // inside these owned children; no veth is ever created in the parent netns.
    unsafe {
        command.pre_exec(move || {
            let parent = libc::getppid();
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0
                || parent <= 1
                || libc::getppid() != parent
            {
                return Err(io::Error::last_os_error());
            }
            if new_net && libc::unshare(libc::CLONE_NEWNET) != 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    Ok(Owned {
        child: command.spawn()?,
        stopped: false,
    })
}

struct Run {
    out: PathBuf,
    deadline: Instant,
    cancelled: Arc<AtomicBool>,
    steps: Vec<Value>,
    check_index: usize,
}

impl Run {
    fn command(
        &mut self,
        label: &str,
        pid: Option<u32>,
        args: &[&str],
        expected: i32,
    ) -> io::Result<Vec<u8>> {
        self.check_index += 1;
        let id = format!("{:03}-{label}", self.check_index);
        let (program, argv) = if let Some(pid) = pid {
            (
                "nsenter",
                [
                    vec![
                        "--target".into(),
                        pid.to_string(),
                        "--net".into(),
                        "--".into(),
                    ],
                    args.iter().map(|v| (*v).to_owned()).collect(),
                ]
                .concat(),
            )
        } else {
            (args[0], args[1..].iter().map(|v| (*v).to_owned()).collect())
        };
        let execution = process::execute(
            &id,
            program,
            &argv,
            Path::new("/work"),
            &self.out,
            self.deadline.min(Instant::now() + Duration::from_secs(8)),
            &self.cancelled,
        )?;
        let mut result = execution.result;
        let acceptable = result["exit_code"] == expected
            && result["signal"].is_null()
            && result["output_complete"] == true
            && result["log_truncated"] == false
            && matches!(result["status"].as_str(), Some("passed" | "failed"));
        result["expected_exit_code"] = json!(expected);
        if acceptable {
            result["status"] = json!("passed");
        } else if result["status"] == "passed" {
            result["status"] = json!("failed");
        }
        self.steps.push(result);
        if !acceptable {
            return Err(io::Error::other(format!(
                "{id} did not match expected exit {expected}"
            )));
        }
        Ok(execution.stdout)
    }

    fn ip(&mut self, label: &str, pid: u32, args: &[&str]) -> io::Result<()> {
        self.command(label, Some(pid), &[&["ip"][..], args].concat(), 0)
            .map(|_| ())
    }

    fn ready(&self, child: &mut Owned, path: &Path) -> io::Result<()> {
        self.ready_within(child, path, Duration::from_secs(3))
    }

    fn ready_within(&self, child: &mut Owned, path: &Path, budget: Duration) -> io::Result<()> {
        let until = self.deadline.min(Instant::now() + budget);
        while Instant::now() < until && !self.cancelled.load(Ordering::SeqCst) {
            if child.child.try_wait()?.is_some() {
                return Err(io::Error::other("echo helper exited before readiness"));
            }
            if path.is_file() {
                return Ok(());
            }
            thread::sleep(Duration::from_millis(10));
        }
        Err(io::Error::other(
            "echo helper readiness timeout/cancellation",
        ))
    }
}

fn client(protocol: &str, destination: &str, nonce: &str) -> io::Result<()> {
    let port = if protocol == "tcp" { 38080 } else { 38081 };
    let address: SocketAddr = format!("{destination}:{port}")
        .parse()
        .map_err(io::Error::other)?;
    let payload = format!("arena:{nonce}:{protocol}\n");
    let mut received = vec![0_u8; payload.len() + 1];
    let size = match protocol {
        "tcp" => {
            let mut socket = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
            socket.set_read_timeout(Some(Duration::from_secs(2)))?;
            socket.set_write_timeout(Some(Duration::from_secs(2)))?;
            socket.write_all(payload.as_bytes())?;
            socket.shutdown(std::net::Shutdown::Write)?;
            received.clear();
            socket.take(257).read_to_end(&mut received)?
        }
        "udp" => {
            let socket = UdpSocket::bind("0.0.0.0:0")?;
            socket.connect(address)?;
            socket.set_read_timeout(Some(Duration::from_secs(2)))?;
            socket.send(payload.as_bytes())?;
            socket.recv(&mut received)?
        }
        _ => return Err(io::Error::other("unknown protocol")),
    };
    if received[..size] != *payload.as_bytes() {
        return Err(io::Error::other("echo payload mismatch"));
    }
    println!(
        "{}",
        json!({"protocol":protocol, "destination":destination, "payload":payload, "bytes":size})
    );
    Ok(())
}

// Server accepts only the bounded synthetic payload and echoes TCP/UDP on
// dedicated guest-only addresses. No executable input or external endpoint.
fn server(address: &str, ready: &Path) -> io::Result<()> {
    let tcp = TcpListener::bind(format!("{address}:38080"))?;
    let udp = UdpSocket::bind(format!("{address}:38081"))?;
    tcp.set_nonblocking(true)?;
    udp.set_nonblocking(true)?;
    save(ready, &json!({"tcp":38080,"udp":38081,"address":address}))?;
    let until = Instant::now() + TIMEOUT;
    let mut count = 0;
    while Instant::now() < until && count < 4 {
        match tcp.accept() {
            Ok((mut stream, _)) => {
                stream.set_read_timeout(Some(Duration::from_secs(2)))?;
                stream.set_write_timeout(Some(Duration::from_secs(2)))?;
                let mut payload = vec![];
                Read::by_ref(&mut stream)
                    .take(257)
                    .read_to_end(&mut payload)?;
                if payload.len() > 256 || !payload.starts_with(b"arena:") {
                    return Err(io::Error::other("invalid echo input"));
                }
                stream.write_all(&payload)?;
                count += 1;
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(e),
        }
        let mut payload = [0_u8; 257];
        match udp.recv_from(&mut payload) {
            Ok((length, peer)) => {
                if length > 256 || !payload[..length].starts_with(b"arena:") {
                    return Err(io::Error::other("invalid echo input"));
                }
                udp.send_to(&payload[..length], peer)?;
                count += 1;
            }
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => {}
            Err(e) => return Err(e),
        }
        thread::sleep(Duration::from_millis(5));
    }
    if count != 4 {
        return Err(io::Error::other(
            "echo server did not receive four requests",
        ));
    }
    Ok(())
}

fn baseline(
    out: PathBuf,
    omit_server_route: bool,
    tc_case: bool,
    tc_fault: &str,
) -> io::Result<i32> {
    // This explicit boot contract prevents accidental host invocation. It is
    // a misuse guard, not containment of hostile root code.
    vm_guard()?;
    let omit_server_route = omit_server_route
        || fs::read_to_string("/proc/cmdline")?
            .split_whitespace()
            .any(|value| value == "arena.omit_server_route=1");
    fs::DirBuilder::new().mode(0o700).create(&out)?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let flag = cancelled.clone();
    ctrlc::set_handler(move || flag.store(true, Ordering::SeqCst)).map_err(io::Error::other)?;
    let mut run = Run {
        out: out.clone(),
        deadline: Instant::now() + TIMEOUT,
        cancelled,
        steps: vec![],
        check_index: 0,
    };
    let mut namespaces = Vec::new();
    let mut echoes = Vec::new();
    let mut namespace_ids = Vec::new();
    let parent_ns = ns(std::process::id())?;
    let exe = std::env::current_exe()?.to_string_lossy().into_owned();
    let mut before = None;
    let mut testing = false;
    let attempt = (|| -> io::Result<()> {
        let links = run.command("parent-links-before", None, &["ip", "-j", "link"], 0)?;
        let decoded: Value = serde_json::from_slice(&links)?;
        if decoded
            .as_array()
            .is_none_or(|items| items.len() != 1 || items[0]["ifname"] != "lo")
        {
            return Err(io::Error::other("parent namespace must have loopback only"));
        }
        let routes = run.command("parent-routes-before", None, &["ip", "-j", "route"], 0)?;
        if serde_json::from_slice::<Value>(&routes)? != json!([]) {
            return Err(io::Error::other("parent has unexpected IPv4 routes"));
        }
        before = Some((decoded, serde_json::from_slice::<Value>(&routes)?));
        run.command("ip-version", None, &["ip", "-Version"], 0)?;
        run.command("nsenter-version", None, &["nsenter", "--version"], 0)?;
        if String::from_utf8_lossy(&run.command("kernel", None, &["uname", "-r"], 0)?).trim()
            != "6.12.112"
        {
            return Err(io::Error::other(
                "guest kernel version differs from contract",
            ));
        }
        for role in ["app", "router", "server"] {
            let child = spawn(
                &exe,
                &["--hold".into()],
                &out.join(format!("{role}-helper.log")),
                true,
            )?;
            let id = ns(child.child.id())?;
            if id == parent_ns || namespace_ids.contains(&id) {
                return Err(io::Error::other("namespace isolation failed"));
            }
            namespace_ids.push(id);
            namespaces.push(child);
        }
        let [app, router, server_pid] =
            std::array::from_fn::<_, 3, _>(|i| namespaces[i].child.id());
        save(
            &out.join("topology.json"),
            &json!({"schema_version":1,"parent_namespace":parent_ns,
            "namespaces":[{"role":"app","pid":app,"namespace":namespace_ids[0],"address":"10.77.1.10/24","gateway":"10.77.1.1"},
            {"role":"router","pid":router,"namespace":namespace_ids[1],"addresses":["10.77.1.1/24","10.77.2.1/24"]},
            {"role":"server","pid":server_pid,"namespace":namespace_ids[2],"address":"10.77.2.10/24","gateway":"10.77.2.1"}],
            "external_nic":false,"nat":false,"default_routes":false,"omit_server_route":omit_server_route}),
        )?;
        run.ip(
            "app-veth",
            app,
            &[
                "link", "add", "app0", "type", "veth", "peer", "name", "rt-app",
            ],
        )?;
        run.ip(
            "app-peer",
            app,
            &["link", "set", "rt-app", "netns", &router.to_string()],
        )?;
        run.ip(
            "server-veth",
            router,
            &[
                "link",
                "add",
                "rt-server",
                "type",
                "veth",
                "peer",
                "name",
                "server0",
            ],
        )?;
        run.ip(
            "server-peer",
            router,
            &["link", "set", "server0", "netns", &server_pid.to_string()],
        )?;
        for (role, pid, interfaces) in [
            ("app", app, vec![("app0", "10.77.1.10/24")]),
            (
                "router",
                router,
                vec![("rt-app", "10.77.1.1/24"), ("rt-server", "10.77.2.1/24")],
            ),
            ("server", server_pid, vec![("server0", "10.77.2.10/24")]),
        ] {
            run.ip(
                &format!("{role}-loopback"),
                pid,
                &["link", "set", "lo", "up"],
            )?;
            for (interface, address) in interfaces {
                run.ip(
                    &format!("{role}-{interface}-address"),
                    pid,
                    &["address", "add", address, "dev", interface],
                )?;
                run.ip(
                    &format!("{role}-{interface}-up"),
                    pid,
                    &["link", "set", interface, "up"],
                )?;
            }
        }
        run.ip(
            "app-route",
            app,
            &["route", "add", "10.77.2.0/24", "via", "10.77.1.1"],
        )?;
        if !omit_server_route {
            run.ip(
                "server-route",
                server_pid,
                &["route", "add", "10.77.1.0/24", "via", "10.77.2.1"],
            )?;
        }
        run.command(
            "router-forwarding-on",
            Some(router),
            &["sysctl", "-w", "net.ipv4.ip_forward=1"],
            0,
        )?;
        for (role, pid) in [("app", app), ("router", router), ("server", server_pid)] {
            run.command(
                &format!("{role}-addresses"),
                Some(pid),
                &["ip", "-j", "address"],
                0,
            )?;
            run.command(
                &format!("{role}-routes"),
                Some(pid),
                &["ip", "-j", "route"],
                0,
            )?;
        }
        testing = true;
        run.command(
            "app-gateway",
            Some(app),
            &["ping", "-n", "-c", "2", "-W", "1", "10.77.1.1"],
            0,
        )?;
        run.command(
            "server-gateway",
            Some(server_pid),
            &["ping", "-n", "-c", "2", "-W", "1", "10.77.2.1"],
            0,
        )?;
        run.command(
            "app-server-ping",
            Some(app),
            &["ping", "-n", "-c", "2", "-W", "1", SERVER],
            0,
        )?;
        run.command(
            "server-app-ping",
            Some(server_pid),
            &["ping", "-n", "-c", "2", "-W", "1", APP],
            0,
        )?;
        for (role, pid, address) in [("app", app, APP), ("server", server_pid, SERVER)] {
            let ready = out.join(format!("{role}-echo-ready.json"));
            let mut child = spawn(
                "nsenter",
                &[
                    "--target".into(),
                    pid.to_string(),
                    "--net".into(),
                    "--".into(),
                    exe.clone(),
                    "--echo-server".into(),
                    address.into(),
                    ready.to_string_lossy().into_owned(),
                ],
                &out.join(format!("{role}-echo.log")),
                false,
            )?;
            run.ready(&mut child, &ready)?;
            echoes.push(child);
        }
        for phase in ["initial", "recovery"] {
            if phase == "recovery" {
                run.command(
                    "router-forwarding-off",
                    Some(router),
                    &["sysctl", "-w", "net.ipv4.ip_forward=0"],
                    0,
                )?;
                run.command(
                    "forwarding-off-app-server",
                    Some(app),
                    &["ping", "-n", "-c", "1", "-W", "1", SERVER],
                    1,
                )?;
                run.command(
                    "forwarding-off-server-app",
                    Some(server_pid),
                    &["ping", "-n", "-c", "1", "-W", "1", APP],
                    1,
                )?;
                run.command(
                    "router-forwarding-restore",
                    Some(router),
                    &["sysctl", "-w", "net.ipv4.ip_forward=1"],
                    0,
                )?;
                run.command(
                    "recovered-ping",
                    Some(app),
                    &["ping", "-n", "-c", "2", "-W", "1", SERVER],
                    0,
                )?;
            }
            for (direction, pid, destination) in
                [("app-server", app, SERVER), ("server-app", server_pid, APP)]
            {
                for protocol in ["tcp", "udp"] {
                    let nonce = format!("{}-{phase}-{direction}", std::process::id());
                    run.command(
                        &format!("{phase}-{direction}-{protocol}"),
                        Some(pid),
                        &[&exe, "--client", protocol, destination, &nonce],
                        0,
                    )?;
                }
            }
        }
        if tc_case {
            if tc_fault.starts_with("tcp-") {
                tcp_syn::comparison(&mut run, app, router, server_pid, &exe, &out, tc_fault)?;
            } else {
                tc::comparison(&mut run, app, router, server_pid, &exe, &out, tc_fault)?;
            }
        }
        for (role, pid) in [("app", app), ("router", router), ("server", server_pid)] {
            run.command(
                &format!("{role}-neighbors"),
                Some(pid),
                &["ip", "-j", "neighbor"],
                0,
            )?;
        }
        Ok(())
    })();
    let mut cleanup_errors = vec![];
    for child in echoes.iter_mut().chain(namespaces.iter_mut()) {
        if let Err(error) = child.stop() {
            cleanup_errors.push(error.to_string());
        }
    }
    let live: Vec<_> = fs::read_dir("/proc")?
        .filter_map(Result::ok)
        .filter_map(|e| {
            e.file_name()
                .to_str()?
                .parse::<u32>()
                .ok()
                .and_then(|pid| ns(pid).ok().map(|id| (pid, id)))
        })
        .filter(|(_, id)| namespace_ids.contains(id))
        .collect();
    if !live.is_empty() {
        cleanup_errors.push(format!("owned namespace still referenced: {live:?}"));
    }
    // Cleanup verification has its own small bounded budget even after cancel.
    let cancellation = run.cancelled.clone();
    run.cancelled = Arc::new(AtomicBool::new(false));
    run.deadline = Instant::now() + Duration::from_secs(8);
    let after = (|| -> io::Result<bool> {
        let links: Value = serde_json::from_slice(&run.command(
            "parent-links-after",
            None,
            &["ip", "-j", "link"],
            0,
        )?)?;
        let routes: Value = serde_json::from_slice(&run.command(
            "parent-routes-after",
            None,
            &["ip", "-j", "route"],
            0,
        )?)?;
        Ok(before.as_ref().is_some_and(|b| *b == (links, routes))
            && ns(std::process::id())? == parent_ns)
    })();
    let parent_unchanged = after.unwrap_or(false);
    if !parent_unchanged {
        cleanup_errors.push("parent namespace verification failed".into());
    }
    let clean = cleanup_errors.is_empty();
    let interrupted = run.steps.iter().any(|step| {
        matches!(
            step["status"].as_str(),
            Some("timeout" | "cancelled" | "environment_error" | "output_limit")
        )
    });
    let interrupted_reason = read_report(&out.join("interrupt-request.json"))
        .ok()
        .and_then(|r| r["reason"].as_str().map(str::to_owned))
        .unwrap_or_else(|| "cancelled".into());
    let status = if !clean || (tc_case && !read_report(&out.join("tc-result.json")).is_ok_and(|r| r["cleanup"] == json!({"detached":true,"program_unloaded":true,"maps_unloaded":true,"pinned":false}))) {
        "environment_error"
    } else if cancellation.load(Ordering::SeqCst) {
        if interrupted_reason == "timeout" { "timeout" } else { "cancelled" }
    } else if !clean
        || interrupted
        || (!testing && attempt.is_err())
        || (tc_case
            && !read_report(&out.join("tc-result.json"))
                .is_ok_and(|r| r["status"] != "environment_error"))
    {
        "environment_error"
    } else if attempt.is_ok() {
        "passed"
    } else {
        "failed"
    };
    save(
        &out.join("result.json"),
        &json!({"schema_version":1,"scenario":"netns-baseline",
        "status":status,"detail":attempt.err().map(|e|e.to_string()),"steps":run.steps,
        "cleanup":{"helpers_reaped":echoes.iter().chain(namespaces.iter()).all(|p|p.stopped),
            "owned_namespace_references":live,"parent_network_unchanged":parent_unchanged,"errors":cleanup_errors},
        "tc_reference":tc_case,"tc_fault":tc_fault,
        "scope":if tc_fault.starts_with("tcp-") {"IPv4 baseline plus fixed initial TCP SYN TC reference; no external NIC"} else if tc_case {"IPv4 baseline plus fixed UDP TC ingress reference; no external NIC"} else {"IPv4 ICMP/TCP/UDP only; no eBPF; no external NIC"}}),
    )?;
    Ok(match status {
        "passed" => 0,
        "failed" => 1,
        "cancelled" => 130,
        "timeout" => 4,
        _ => 3,
    })
}

fn batch(out: PathBuf, interruption_mode: Option<&str>) -> io::Result<i32> {
    fs::DirBuilder::new().mode(0o700).create(&out)?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let flag = cancelled.clone();
    ctrlc::set_handler(move || flag.store(true, Ordering::SeqCst)).map_err(io::Error::other)?;
    let exe = std::env::current_exe()?.to_string_lossy().into_owned();
    let _observer = if interruption_mode == Some("watchdog") {
        Some(spawn(
            &exe,
            &[
                "--watchdog-observer".into(),
                std::process::id().to_string(),
                out.to_string_lossy().into_owned(),
            ],
            &out.join("watchdog-observer.log"),
            false,
        )?)
    } else {
        None
    };
    let mut cases = vec![];
    let mut exit_code = 0;
    // Eighteen cases include two nested interruption batches. Keep the guest
    // 600s service and 670s host watchdog above this bounded suite budget.
    let deadline = Instant::now() + Duration::from_secs(540);
    for (id, expected) in BATCH_CASES {
        if interruption_mode.is_some() && (id.starts_with("batch-") || id == "tc-after-cancel-001")
        {
            continue;
        }
        if exit_code != 0 {
            cases.push(json!({"id":id,"expected_exit_code":expected,"status":"not_run"}));
            continue;
        }
        let mut args = vec![
            "--artifacts".into(),
            out.join(id).to_string_lossy().into_owned(),
        ];
        if id.starts_with("batch-") {
            args = vec![
                "--interruption-case".into(),
                if id == "batch-cancel-001" {
                    "cancelled".into()
                } else {
                    "timeout".into()
                },
                out.join(id).to_string_lossy().into_owned(),
            ];
        }
        if id == "bpf-test-run-001" {
            args[0] = "--bpf-test-run".into();
        }
        if id == "bpf-load-failure-001" {
            args[0] = "--bpf-load-failure".into();
        }
        if id == "route-fault-001" {
            args.push("--omit-server-route".into());
        }
        let fault = if interruption_mode.is_some() && id == "tc-ingress-001" {
            "interrupt-hold"
        } else {
            match id {
                "tc-tcp-drop-disabled-001" => "tcp-drop-disabled",
                "tc-tcp-syn-001" => "tcp-syn",
                "tc-drop-disabled-001" => "drop-disabled",
                "tc-wrong-hook-001" => "wrong-hook",
                "tc-receiver-stopped-001" => "receiver-stopped",
                "tc-netem-loss-001" => "netem-loss",
                "tc-netem-unconfigured-001" => "netem-unconfigured",
                _ => "none",
            }
        };
        if id.starts_with("tc-") {
            args.push("--tc-reference".into());
            if fault != "none" {
                args.push(fault.into());
            }
        }
        if interruption_mode == Some("watchdog") && id == "tc-ingress-001" {
            save(
                &out.join("batch-progress.json"),
                &json!({"schema_version":1,"scenario":"netns-batch-progress","status":"incomplete","runner_pid":std::process::id(),"cases":cases,"active_case":id}),
            )?;
        }
        let execution = process::execute_controlled(
            id,
            &exe,
            &args,
            Path::new("/work"),
            &out,
            process::Control {
                deadline,
                cancelled: &cancelled,
                graceful: Some(process::GracefulStop {
                    request_path: out.join(id).join("interrupt-request.json"),
                    readiness_path: if interruption_mode == Some("timeout")
                        && id == "tc-ingress-001"
                    {
                        Some(out.join(id).join("tc-interruption-ready.json"))
                    } else {
                        None
                    },
                    timeout_after_ready: if interruption_mode == Some("timeout")
                        && id == "tc-ingress-001"
                    {
                        Some(Duration::from_millis(500))
                    } else {
                        None
                    },
                    cancel_after_ready: false,
                    grace: Duration::from_secs(25),
                }),
            },
        )?;
        let report_path = out.join(id).join("result.json");
        let result = read_report(&report_path);
        let matched = result.as_ref().is_ok_and(|r| {
            if id.starts_with("batch-") {
                interruption::matches_report(
                    r,
                    if id == "batch-cancel-001" {
                        "cancelled"
                    } else {
                        "timeout"
                    },
                )
            } else if id == "bpf-test-run-001" {
                test_run::valid_report(r)
            } else if id == "bpf-load-failure-001" {
                load_failure::matches_rejection(r)
            } else if id.starts_with("tc-") {
                r["status"]
                    == (if expected == 0 {
                        "passed"
                    } else if expected == 1 {
                        "failed"
                    } else {
                        "environment_error"
                    })
                    && r["tc_fault"] == fault
                    && r["tc_reference"] == true
                    && read_report(&out.join(id).join("tc-result.json")).is_ok_and(|t| {
                        if fault.starts_with("tcp-") {
                            tcp_syn::matches_report(&t, fault)
                        } else if matches!(fault, "netem-loss" | "netem-unconfigured") {
                            tc::matches_netem(&t, fault)
                        } else if fault == "none" {
                            t["status"] == "passed"
                        } else {
                            tc::matches_fault(&t, fault)
                        }
                    })
                    && r["cleanup"]["errors"] == json!([])
                    && r["cleanup"]["helpers_reaped"] == true
                    && r["cleanup"]["parent_network_unchanged"] == true
                    && r["cleanup"]["owned_namespace_references"] == json!([])
            } else {
                matches_case(r, expected)
            }
        }) && execution.result["exit_code"] == expected
            && execution.result["signal"].is_null()
            && execution.result["output_complete"] == true
            && execution.result["log_truncated"] == false
            && matches!(
                execution.result["status"].as_str(),
                Some("passed" | "failed")
            );
        if !matched {
            let reason = execution.result["status"].as_str().unwrap_or("");
            let clean_interruption = matches!(reason, "cancelled" | "timeout")
                && result.as_ref().is_ok_and(|r| {
                    r["status"] == reason
                        && r["cleanup"]["helpers_reaped"] == true
                        && r["cleanup"]["errors"] == json!([])
                        && r["cleanup"]["owned_namespace_references"] == json!([])
                        && r["cleanup"]["parent_network_unchanged"] == true
                })
                && read_report(&out.join(id).join("tc-result.json"))
                    .is_ok_and(|r| tc::matches_interruption(&r, reason))
                && execution.result["output_complete"] == true
                && execution.result["termination_escalated"] == false
                && execution.result["signal"].is_null()
                && execution.result["exit_code"] == if reason == "cancelled" { 130 } else { 4 };
            exit_code = if clean_interruption {
                if reason == "cancelled" { 130 } else { 4 }
            } else if result.as_ref().is_ok_and(|r| r["status"] == "failed")
                && execution.result["exit_code"] == 1
            {
                1
            } else {
                3
            };
        }
        cases.push(
            json!({"id":id,"expected_exit_code":expected,"execution":execution.result,
            "status":if matched {"passed"} else {"failed"},"report":format!("{id}/result.json"),
            "detail":result.err().map(|e|e.to_string())}),
        );
    }
    save(
        &out.join("result.json"),
        &json!({"schema_version":8,"scenario":"netns-batch",
        "status":match exit_code {0=>"passed",1=>"failed",130=>"cancelled",4=>"timeout",_=>"environment_error"},"interruption_mode":interruption_mode,
        "cases":cases,"exit_code":exit_code,"vm_boots":1,
        "namespace_policy":"fresh owned namespaces for communication cases; verified cleanup before next case"}),
    )?;
    Ok(exit_code)
}

fn read_report(path: &Path) -> io::Result<Value> {
    let meta = fs::symlink_metadata(path)?;
    if !meta.is_file() || meta.len() > 1024 * 1024 {
        return Err(io::Error::other("invalid case report"));
    }
    let mut bytes = vec![];
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 1024 * 1024 {
        return Err(io::Error::other("case report grew beyond bound"));
    }
    serde_json::from_slice(&bytes).map_err(io::Error::other)
}

fn matches_case(result: &Value, expected: i32) -> bool {
    let cleanup = &result["cleanup"];
    if result["scenario"] != "netns-baseline"
        || cleanup["helpers_reaped"] != true
        || cleanup["parent_network_unchanged"] != true
        || cleanup["owned_namespace_references"] != json!([])
        || cleanup["errors"] != json!([])
    {
        return false;
    }
    let Some(steps) = result["steps"].as_array() else {
        return false;
    };
    if expected == 0 {
        result["status"] == "passed"
            && steps.len() == 51
            && steps.iter().all(|s| s["status"] == "passed")
    } else {
        let failures: Vec<_> = steps.iter().filter(|s| s["status"] != "passed").collect();
        result["status"] == "failed"
            && failures.len() == 1
            && failures[0]["id"] == "031-app-server-ping"
            && failures[0]["exit_code"] == 1
            && ["029-app-gateway", "030-server-gateway"].iter().all(|id| {
                steps
                    .iter()
                    .any(|s| s["id"] == *id && s["status"] == "passed")
            })
    }
}

pub fn main() -> i32 {
    if let Err(error) = vm_guard() {
        eprintln!("netns: {error}");
        return 3;
    }
    let args: Vec<_> = std::env::args().skip(1).collect();
    let result = match args.as_slice() {
        [mode, path] if mode == "--bpf-load-failure" => load_failure::run(Path::new(path)),
        [mode, path] if mode == "--bpf-test-run" => test_run::run(Path::new(path)),
        [mode, path] if mode == "--batch" => batch(PathBuf::from(path), None),
        [mode, path] if mode == "--batch-watchdog" => {
            fs::read_to_string("/proc/cmdline").and_then(|cmdline| {
                if !cmdline.split_whitespace().any(|s| s == "arena.watchdog=1") {
                    return Err(io::Error::other("watchdog boot opt-in missing"));
                }
                batch(PathBuf::from(path), Some("watchdog"))
            })
        }
        [mode, pid, path] if mode == "--watchdog-observer" => pid
            .parse()
            .map_err(io::Error::other)
            .and_then(|pid| watchdog::observe(pid, Path::new(path))),
        [mode, reason, path]
            if mode == "--batch-interruption"
                && ["cancelled", "timeout"].contains(&reason.as_str()) =>
        {
            batch(PathBuf::from(path), Some(reason))
        }
        [mode, reason, path]
            if mode == "--interruption-case"
                && ["cancelled", "timeout"].contains(&reason.as_str()) =>
        {
            interruption::run(Path::new(path), reason)
        }
        [hold] if hold == "--hold" => parent_guard().and_then(|()| {
            let mut buffer = [0_u8; 1];
            io::stdin().read(&mut buffer).map(|_| 0)
        }),
        [mode, address, path]
            if mode == "--echo-server" && [APP, SERVER].contains(&address.as_str()) =>
        {
            parent_guard()
                .and_then(|()| server(address, Path::new(path)))
                .map(|()| 0)
        }
        [mode, protocol, address, nonce]
            if mode == "--client"
                && [APP, SERVER].contains(&address.as_str())
                && nonce.len() <= 128 =>
        {
            client(protocol, address, nonce).map(|()| 0)
        }
        [mode, path] if mode == "--tc-agent" => tc::agent(Path::new(path), "none").map(|()| 0),
        [mode, fault, path]
            if mode == "--tc-agent-fault"
                && [
                    "none",
                    "drop-disabled",
                    "wrong-hook",
                    "receiver-stopped",
                    "interrupt-hold",
                    "netem-loss",
                    "netem-unconfigured",
                    "tcp-syn",
                    "tcp-drop-disabled",
                ]
                .contains(&fault.as_str()) =>
        {
            tc::agent(Path::new(path), fault).map(|()| 0)
        }
        [mode, phase] if mode == "--tcp-send" => tcp_syn::sender(phase).map(|()| 0),
        [mode, phase, path] if mode == "--tcp-receiver" => {
            tcp_syn::receiver(phase, Path::new(path)).map(|()| 0)
        }
        [mode, phase] if mode == "--tc-send" => tc::sender(phase).map(|()| 0),
        [mode, phase, path] if mode == "--tc-receiver" => {
            tc::receiver(phase, Path::new(path)).map(|()| 0)
        }
        [mode, path] if mode == "--artifacts" => {
            baseline(PathBuf::from(path), false, false, "none")
        }
        [mode, path, fault] if mode == "--artifacts" && fault == "--omit-server-route" => {
            baseline(PathBuf::from(path), true, false, "none")
        }
        [mode, path, tc] if mode == "--artifacts" && tc == "--tc-reference" => {
            baseline(PathBuf::from(path), false, true, "none")
        }
        [mode, path, tc, fault]
            if mode == "--artifacts"
                && tc == "--tc-reference"
                && [
                    "drop-disabled",
                    "wrong-hook",
                    "receiver-stopped",
                    "interrupt-hold",
                    "netem-loss",
                    "netem-unconfigured",
                    "tcp-syn",
                    "tcp-drop-disabled",
                ]
                .contains(&fault.as_str()) =>
        {
            baseline(PathBuf::from(path), false, true, fault)
        }
        _ => {
            eprintln!("usage: veil-arena-netns --artifacts NEW_DIRECTORY [--omit-server-route]");
            return 2;
        }
    };
    result.unwrap_or_else(|error| {
        eprintln!("netns: {error}");
        3
    })
}

#[cfg(test)]
mod publication_tests {
    use super::*;
    #[test]
    fn tc_cleanup_grace_preserves_delayed_publication() {
        let child = Command::new("sh")
            .args(["-c", "read line; sleep 3; printf complete"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let mut owned = Owned {
            child,
            stopped: false,
        };
        writeln!(owned.child.stdin.as_mut().unwrap(), "stop").unwrap();
        owned.stop_with_grace(Duration::from_secs(8)).unwrap();
        assert!(owned.child.try_wait().unwrap().unwrap().success());
        let mut output = String::new();
        std::io::Read::read_to_string(owned.child.stdout.as_mut().unwrap(), &mut output).unwrap();
        assert_eq!(output, "complete");
    }

    #[test]
    fn tc_snapshot_wait_accepts_delayed_publication_but_respects_case_deadline() {
        let directory = std::env::temp_dir().join(format!(
            "arena-ready-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&directory).unwrap();
        let path = directory.join("ready");
        let child = Command::new("sh")
            .args([
                "-c",
                include_str!("readiness-publication.sh"),
                "arena-ready",
            ])
            .arg(&path)
            .stdin(Stdio::piped())
            .spawn()
            .unwrap();
        let mut owned = Owned {
            child,
            stopped: false,
        };
        let mut run = Run {
            out: directory,
            deadline: Instant::now() + Duration::from_secs(8),
            cancelled: Arc::new(AtomicBool::new(false)),
            steps: vec![],
            check_index: 0,
        };
        run.ready_within(&mut owned, &path, Duration::from_secs(8))
            .unwrap();
        assert_eq!(fs::read_to_string(&path).unwrap(), "ready");
        run.deadline = Instant::now();
        assert!(
            run.ready_within(&mut owned, &path, Duration::from_secs(8))
                .is_err()
        );
        owned.stop().unwrap();
        assert!(owned.child.try_wait().unwrap().unwrap().success());
    }

    #[test]
    fn visible_reports_are_complete_and_existing_reports_are_preserved() {
        let directory = std::env::temp_dir().join(format!(
            "arena-publication-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::DirBuilder::new()
            .mode(0o700)
            .create(&directory)
            .unwrap();
        let value = json!({"payload":"x".repeat(128*1024),"complete":true});
        for i in 0..20 {
            let path = directory.join(format!("{i}.json"));
            let destination = path.clone();
            let expected = value.clone();
            let writer = thread::spawn(move || save(&destination, &expected));
            let until = Instant::now() + Duration::from_secs(5);
            while !path.is_file() && Instant::now() < until {
                thread::yield_now();
            }
            assert_eq!(read_report(&path).unwrap(), value);
            writer.join().unwrap().unwrap();
        }
        let path = directory.join("existing.json");
        fs::write(&path, b"original").unwrap();
        assert_eq!(
            save(&path, &value).unwrap_err().kind(),
            io::ErrorKind::AlreadyExists
        );
        assert_eq!(fs::read(&path).unwrap(), b"original");
    }
}
