//! Fixed IPv4 TCP new-connection controls; SYN packet counts are not connection counts.
use super::{APP, Owned, Run, SERVER, parent_guard, read_report, save, spawn, tc};
use serde_json::{Value, json};
use std::io::{self, BufRead, Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::path::Path;
use std::sync::mpsc;
use std::time::{Duration, Instant};

const PHASES: [&str; 5] = ["plain", "pass", "count", "drop", "detached"];
const PORTS: [u16; 2] = [39201, 39202];
fn payload(phase: &str, kind: usize) -> Vec<u8> {
    format!("VTCP1:{phase}:{kind}").into_bytes()
}

pub fn sender(phase: &str) -> io::Result<()> {
    parent_guard()?;
    if !PHASES.contains(&phase) {
        return Err(io::Error::other("invalid TCP phase"));
    }
    let mut attempts = vec![];
    for (kind, port) in PORTS.iter().enumerate() {
        let address: SocketAddr = format!("{SERVER}:{port}")
            .parse()
            .map_err(io::Error::other)?;
        let started = Instant::now();
        match TcpStream::connect_timeout(&address, Duration::from_millis(1500)) {
            Ok(mut stream) => {
                stream.set_read_timeout(Some(Duration::from_secs(1)))?;
                stream.set_write_timeout(Some(Duration::from_secs(1)))?;
                let body = payload(phase, kind);
                stream.write_all(&body)?;
                let mut echoed = vec![0; body.len()];
                stream.read_exact(&mut echoed)?;
                if echoed != body { return Err(io::Error::other("TCP echo payload differs")); }
                attempts.push(json!({"kind":kind,"port":port,"connect":"connected","payload_verified":true,"duration_ms":started.elapsed().as_millis()}));
            }
            Err(error) => attempts.push(json!({"kind":kind,"port":port,"connect":if error.kind()==io::ErrorKind::TimedOut {"timeout"} else {"error"},"payload_verified":false,"os_error":error.raw_os_error(),"duration_ms":started.elapsed().as_millis()})),
        }
    }
    println!(
        "{}",
        json!({"phase":phase,"connection_attempts":attempts,"scope":"one application connection attempt per port; kernel may retransmit SYN"})
    );
    Ok(())
}

pub fn receiver(phase: &str, out: &Path) -> io::Result<()> {
    parent_guard()?;
    if !PHASES.contains(&phase) {
        return Err(io::Error::other("invalid TCP phase"));
    }
    let listeners = PORTS
        .map(|port| TcpListener::bind((SERVER, port)))
        .into_iter()
        .collect::<io::Result<Vec<_>>>()?;
    for listener in &listeners {
        listener.set_nonblocking(true)?;
    }
    save(
        &out.join(format!("tcp-{phase}-receiver-ready.json")),
        &json!({"address":SERVER,"ports":PORTS}),
    )?;
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(io::stdin().lock().lines().next());
    });
    let mut received = [0_u32; 2];
    let until = Instant::now() + Duration::from_secs(6);
    loop {
        for (kind, listener) in listeners.iter().enumerate() {
            match listener.accept() {
                Ok((mut stream, address)) => {
                    if address.ip().to_string() != APP {
                        return Err(io::Error::other("unexpected TCP client"));
                    }
                    stream.set_read_timeout(Some(Duration::from_secs(1)))?;
                    stream.set_write_timeout(Some(Duration::from_secs(1)))?;
                    let body = payload(phase, kind);
                    let mut got = vec![0; body.len()];
                    stream.read_exact(&mut got)?;
                    if got != body {
                        return Err(io::Error::other("TCP request payload differs"));
                    }
                    stream.write_all(&got)?;
                    received[kind] += 1;
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => (),
                Err(e) => return Err(e),
            }
        }
        match rx.try_recv() {
            Ok(Some(Ok(command))) if command == "collect" => break,
            Ok(_) | Err(mpsc::TryRecvError::Disconnected) => {
                return Err(io::Error::other("TCP receiver control closed"));
            }
            Err(mpsc::TryRecvError::Empty) => (),
        }
        if Instant::now() >= until {
            return Err(io::Error::other("TCP receiver deadline"));
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    save(
        &out.join(format!("tcp-{phase}-received.json")),
        &json!({"phase":phase,"accepted_connections":received,"payloads_verified":true}),
    )
}

pub(super) fn valid_phase(item: &Value) -> bool {
    let Some(phase) = item["phase"].as_str() else {
        return false;
    };
    let mode = match phase {
        "plain" | "detached" => Value::Null,
        "pass" => json!(0),
        "count" => json!(3),
        "drop" => json!(4),
        _ => return false,
    };
    let dropped = phase == "drop";
    let Some(attempts) = item["sent"]["connection_attempts"].as_array() else {
        return false;
    };
    if attempts.len() != 2
        || item["sent"]["phase"] != phase
        || item["received"]["phase"] != phase
        || item["received"]["payloads_verified"] != true
        || item["received"]["accepted_connections"] != json!([if dropped { 0 } else { 1 }, 1])
        || item["before"]["mode"] != mode
        || item["after"]["mode"] != mode
    {
        return false;
    }
    for (kind, a) in attempts.iter().enumerate() {
        let blocked = dropped && kind == 0;
        if a["kind"] != kind
            || a["port"] != PORTS[kind]
            || a["connect"] != if blocked { "timeout" } else { "connected" }
            || a["payload_verified"] != !blocked
        {
            return false;
        }
    }
    let Some(delta) = item["counter_delta"].as_array() else {
        return false;
    };
    let Some(before) = item["before"]["counters"].as_array() else {
        return false;
    };
    let Some(after) = item["after"]["counters"].as_array() else {
        return false;
    };
    if delta.len() != 6 || before.len() != 6 || after.len() != 6 {
        return false;
    }
    let mut d = [0u64; 6];
    for i in 0..6 {
        let (Some(b), Some(a), Some(n)) =
            (before[i].as_u64(), after[i].as_u64(), delta[i].as_u64())
        else {
            return false;
        };
        if a.checked_sub(b) != Some(n) {
            return false;
        }
        d[i] = n;
    }
    if matches!(phase, "plain" | "pass" | "detached") {
        return d == [0; 6];
    }
    // Count SYN packets, including retransmissions, independently of the one
    // application connection attempt. No fixed retry count is assumed.
    d[0] > 0
        && d[3] > 0
        && d[4] == d[3]
        && d[5] == 0
        && if dropped {
            d[1] == 0 && d[2] == d[0]
        } else {
            d[1] == d[0] && d[2] == 0
        }
}

pub(super) fn matches_report(r: &Value, fault: &str) -> bool {
    if r["schema_version"] != 1
        || r["scenario"] != "tc-tcp-syn"
        || r["fault"] != fault
        || r["attachment"]["hook"] != "ingress"
        || r["attachment"]["interface"] != "rt-app"
        || r["cleanup"]
            != json!({"detached":true,"program_unloaded":true,"maps_unloaded":true,"pinned":false})
    {
        return false;
    }
    let Some(phases) = r["phases"].as_array() else {
        return false;
    };
    if fault == "tcp-syn" {
        r["status"] == "passed"
            && r["detail"].is_null()
            && phases.len() == 5
            && phases
                .iter()
                .zip(PHASES)
                .all(|(p, name)| p["phase"] == name && valid_phase(p))
    } else if fault == "tcp-drop-disabled" {
        r["status"] == "failed"
            && r["detail"] == "TCP drop delivery/count assertion failed"
            && phases.len() == 4
            && phases[..3]
                .iter()
                .zip(PHASES)
                .all(|(p, name)| p["phase"] == name && valid_phase(p))
            && phases[3]["phase"] == "drop"
            && phases[3]["sent"]["phase"] == "drop"
            && phases[3]["received"]["phase"] == "drop"
            && {
                let mut positive = phases[3].clone();
                positive["phase"] = json!("count");
                positive["sent"]["phase"] = json!("count");
                positive["received"]["phase"] = json!("count");
                valid_phase(&positive)
            }
    } else {
        false
    }
}

pub(super) fn comparison(
    run: &mut Run,
    app: u32,
    router: u32,
    server: u32,
    exe: &str,
    out: &Path,
    fault: &str,
) -> io::Result<()> {
    let mut agent: Option<Owned> = None;
    let mut receiver: Option<Owned> = None;
    let mut phases = vec![];
    let mut ready = Value::Null;
    let mut assertion_failed = false;
    let attempt = (|| -> io::Result<()> {
        let filters: Value = serde_json::from_slice(&run.command(
            "tc-filters-before",
            Some(router),
            &["tc", "-j", "filter", "show", "dev", "rt-app", "ingress"],
            0,
        )?)?;
        if filters != json!([]) {
            return Err(io::Error::other("unexpected TCP TC filters"));
        }
        for phase in PHASES {
            if phase == "pass" {
                agent = Some(spawn(
                    "nsenter",
                    &[
                        "--target".into(),
                        router.to_string(),
                        "--net".into(),
                        "--".into(),
                        exe.into(),
                        "--tc-agent-fault".into(),
                        fault.into(),
                        out.to_string_lossy().into_owned(),
                    ],
                    &out.join("tc-agent.log"),
                    false,
                )?);
                run.ready_within(
                    agent.as_mut().unwrap(),
                    &out.join("tc-ready.json"),
                    Duration::from_secs(8),
                )?;
                ready = read_report(&out.join("tc-ready.json"))?;
                let f: Value = serde_json::from_slice(&run.command(
                    "tc-filters-attached",
                    Some(router),
                    &["tc", "-j", "filter", "show", "dev", "rt-app", "ingress"],
                    0,
                )?)?;
                if !tc::attachment_matches(&f, &ready)
                    || ready["hook"] != "ingress"
                    || ready["interface"] != "rt-app"
                {
                    return Err(io::Error::other("TCP attachment identity differs"));
                }
            }
            if phase == "detached" {
                agent
                    .as_mut()
                    .unwrap()
                    .stop_with_grace(Duration::from_secs(8))?;
                let f: Value = serde_json::from_slice(&run.command(
                    "tc-filters-detached",
                    Some(router),
                    &["tc", "-j", "filter", "show", "dev", "rt-app", "ingress"],
                    0,
                )?)?;
                if f != json!([]) {
                    return Err(io::Error::other("TCP filter survived detach"));
                }
            }
            let before = if matches!(phase, "pass" | "count" | "drop") {
                tc::request(run, agent.as_mut().unwrap(), out, phase, "before")?
            } else {
                json!({"mode":null,"counters":vec![0;6]})
            };
            receiver = Some(spawn(
                "nsenter",
                &[
                    "--target".into(),
                    server.to_string(),
                    "--net".into(),
                    "--".into(),
                    exe.into(),
                    "--tcp-receiver".into(),
                    phase.into(),
                    out.to_string_lossy().into_owned(),
                ],
                &out.join(format!("tcp-{phase}-receiver.log")),
                false,
            )?);
            let child = receiver.as_mut().unwrap();
            run.ready(child, &out.join(format!("tcp-{phase}-receiver-ready.json")))?;
            if child.child.try_wait()?.is_some() {
                return Err(io::Error::other("TCP receiver stopped before send"));
            }
            let sent: Value = serde_json::from_slice(&run.command(
                &format!("tc-{phase}-send"),
                Some(app),
                &[exe, "--tcp-send", phase],
                0,
            )?)?;
            writeln!(child.child.stdin.as_mut().unwrap(), "collect")?;
            let path = out.join(format!("tcp-{phase}-received.json"));
            let until = run.deadline.min(Instant::now() + Duration::from_secs(3));
            while !path.is_file()
                && Instant::now() < until
                && !run.cancelled.load(std::sync::atomic::Ordering::SeqCst)
            {
                std::thread::sleep(Duration::from_millis(10));
            }
            child.stop()?;
            if !child.child.try_wait()?.is_some_and(|s| s.success()) {
                return Err(io::Error::other("TCP receiver failed"));
            }
            let received = read_report(&path)?;
            let after = if matches!(phase, "pass" | "count" | "drop") {
                tc::request(run, agent.as_mut().unwrap(), out, phase, "after")?
            } else {
                json!({"mode":null,"counters":vec![0;6]})
            };
            let b: Vec<u64> = serde_json::from_value(before["counters"].clone())?;
            let a: Vec<u64> = serde_json::from_value(after["counters"].clone())?;
            let delta = a
                .iter()
                .zip(b)
                .map(|(a, b)| {
                    a.checked_sub(b)
                        .ok_or_else(|| io::Error::other("TCP counter moved backwards"))
                })
                .collect::<io::Result<Vec<_>>>()?;
            let item = json!({"phase":phase,"sent":sent,"received":received,"before":before,"after":after,"counter_delta":delta});
            let ok = valid_phase(&item);
            phases.push(item);
            if !ok {
                assertion_failed = true;
                return Err(io::Error::other(format!(
                    "TCP {phase} delivery/count assertion failed"
                )));
            }
        }
        Ok(())
    })();
    let receiver_stopped = receiver.as_mut().is_none_or(|r| r.stop().is_ok());
    let agent_stopped = agent
        .as_mut()
        .is_none_or(|a| a.stop_with_grace(Duration::from_secs(8)).is_ok());
    let cleanup = read_report(&out.join("tc-cleanup.json")).unwrap_or(Value::Null);
    let clean = receiver_stopped
        && agent_stopped
        && cleanup
            == json!({"detached":true,"program_unloaded":true,"maps_unloaded":true,"pinned":false});
    let status = if !clean {
        "environment_error"
    } else if attempt.is_ok() {
        "passed"
    } else if assertion_failed {
        "failed"
    } else {
        "environment_error"
    };
    save(
        &out.join("tc-result.json"),
        &json!({"schema_version":1,"scenario":"tc-tcp-syn","status":status,"detail":attempt.as_ref().err().map(|e|e.to_string()),"fault":fault,"attachment":ready,"phases":phases,"cleanup":cleanup,"scope":"fixed IPv4 initial SYN; packet counters include retransmissions; no external NIC"}),
    )?;
    if clean {
        attempt
    } else {
        Err(io::Error::other("TCP cleanup verification failed"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn phase(drop: bool) -> Value {
        let name = if drop { "drop" } else { "count" };
        let delta = if drop {
            vec![3, 0, 3, 1, 1, 0]
        } else {
            vec![2, 2, 0, 1, 1, 0]
        };
        json!({"phase":name,"before":{"mode":if drop {4}else{3},"counters":[0,0,0,0,0,0]},
            "after":{"mode":if drop {4}else{3},"counters":delta},"counter_delta":delta,
            "sent":{"phase":name,"connection_attempts":[{"kind":0,"port":39201,"connect":if drop {"timeout"}else{"connected"},"payload_verified":!drop},{"kind":1,"port":39202,"connect":"connected","payload_verified":true}]},
            "received":{"phase":name,"payloads_verified":true,"accepted_connections":[if drop {0}else{1},1]}})
    }
    #[test]
    fn tcp_syn_counts_retransmissions_without_confusing_connection_attempts() {
        assert!(valid_phase(&phase(false)));
        assert!(valid_phase(&phase(true)));
        let mut p = phase(true);
        p["sent"]["connection_attempts"][0]["connect"] = json!("error");
        assert!(!valid_phase(&p));
        let mut p = phase(true);
        p["received"]["accepted_connections"] = json!([0, 0]);
        assert!(!valid_phase(&p));
        let mut p = phase(true);
        p["after"]["counters"] = json!(vec![0; 6]);
        assert!(!valid_phase(&p));
    }
}
