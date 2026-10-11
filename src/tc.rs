//! Guest-only TC reference and bounded UDP evidence. No pins or host interfaces.
use super::{APP, Owned, Run, SERVER, parent_guard, read_report, save, spawn};
use aya::maps::{Array, IterableMap, PerCpuArray};
use aya::programs::tc::TcAttachOptions;
use aya::programs::{SchedClassifier, TcAttachType, loaded_programs, tc};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, BufRead, Write};
use std::net::UdpSocket;
use std::os::unix::process::ExitStatusExt;
use std::path::Path;
use std::time::{Duration, Instant};

const PHASES: [&str; 5] = ["plain", "pass", "count", "drop", "detached"];
const N: usize = 4;
const SOURCE_PORT: u16 = 39100;
const PORTS: [u16; 2] = [39101, 39102];

pub(super) fn stats(bpf: &aya::Ebpf) -> io::Result<Value> {
    let mode = Array::<_, u32>::try_from(bpf.map("MODE").unwrap()).map_err(io::Error::other)?;
    let counters =
        PerCpuArray::<_, u64>::try_from(bpf.map("STATS").unwrap()).map_err(io::Error::other)?;
    let values: Vec<u64> = (0..6)
        .map(|i| counters.get(&i, 0).map(|v| v.iter().sum()))
        .collect::<Result<_, _>>()
        .map_err(io::Error::other)?;
    Ok(json!({"mode":mode.get(&0,0).map_err(io::Error::other)?,"counters":values}))
}

pub fn agent(out: &Path, fault: &str) -> io::Result<()> {
    parent_guard()?;
    let object = std::env::var_os("ARENA_TC_OBJECT")
        .ok_or_else(|| io::Error::other("fixed TC object missing"))?;
    let bytes = std::fs::read(&object)?;
    if bytes.len() > 1024 * 1024 {
        return Err(io::Error::other("TC object too large"));
    }
    let mut bpf = aya::Ebpf::load(&bytes).map_err(io::Error::other)?;
    let mode_id = Array::<_, u32>::try_from(bpf.map("MODE").unwrap())
        .map_err(io::Error::other)?
        .map()
        .info()
        .map_err(io::Error::other)?
        .id();
    let stats_id = PerCpuArray::<_, u64>::try_from(bpf.map("STATS").unwrap())
        .map_err(io::Error::other)?
        .map()
        .info()
        .map_err(io::Error::other)?
        .id();
    tc::qdisc_add_clsact("rt-app").map_err(io::Error::other)?;
    let program: &mut SchedClassifier = bpf
        .program_mut("arena_ingress")
        .unwrap()
        .try_into()
        .map_err(io::Error::other)?;
    program.load().map_err(io::Error::other)?;
    let id = program.info().map_err(io::Error::other)?.id();
    let link = program
        .attach_with_options(
            "rt-app",
            if fault == "wrong-hook" {
                TcAttachType::Egress
            } else {
                TcAttachType::Ingress
            },
            TcAttachOptions::Netlink(tc::NlOptions {
                priority: 49152,
                handle: 1,
            }),
        )
        .map_err(io::Error::other)?;
    save(
        &out.join("tc-ready.json"),
        &json!({"schema_version":1,"aya":"0.13.1","program_id":id,
        "map_ids":[mode_id,stats_id],"object_sha256":format!("{:x}",Sha256::digest(&bytes)),
        "interface":"rt-app","hook":if fault == "wrong-hook" {"egress"} else {"ingress"},"attach":"netlink-clsact","priority":49152,"handle":1,"pinned":false}),
    )?;
    let attempt = (|| -> io::Result<()> {
        for line in io::stdin().lock().lines() {
            let line = line?;
            if line == "stop" {
                break;
            }
            let (phase, when) = line
                .split_once(':')
                .ok_or_else(|| io::Error::other("invalid TC control"))?;
            let mode = match phase {
                "pass" => 0_u32,
                "count" => 1,
                "drop" if fault == "drop-disabled" => 1,
                "drop" => 2,
                _ => return Err(io::Error::other("invalid TC mode")),
            };
            if !matches!(when, "before" | "after") {
                return Err(io::Error::other("invalid TC snapshot"));
            }
            if when == "before" {
                Array::<_, u32>::try_from(bpf.map_mut("MODE").unwrap())
                    .map_err(io::Error::other)?
                    .set(0, mode, 0)
                    .map_err(io::Error::other)?;
            }
            save(&out.join(format!("tc-{phase}-{when}.json")), &stats(&bpf)?)?;
        }
        Ok(())
    })();
    let program: &mut SchedClassifier = bpf
        .program_mut("arena_ingress")
        .unwrap()
        .try_into()
        .map_err(io::Error::other)?;
    program.detach(link).map_err(io::Error::other)?;
    drop(bpf);
    let (unloaded, maps_unloaded) = released(Some(id), [mode_id, stats_id])?;
    save(
        &out.join("tc-cleanup.json"),
        &json!({"detached":true,"program_unloaded":unloaded,"maps_unloaded":maps_unloaded,"pinned":false}),
    )?;
    if !unloaded || !maps_unloaded {
        return Err(io::Error::other("owned BPF objects still loaded"));
    }
    attempt
}

pub(super) fn released(id: Option<u32>, map_ids: [u32; 2]) -> io::Result<(bool, bool)> {
    // Kernel reference release can be deferred after netlink detach/FD close.
    // Observe actual disappearance; a delay alone never counts as cleanup.
    let until = Instant::now() + Duration::from_secs(1);
    let (unloaded, maps_unloaded) = loop {
        let live: Vec<_> = loaded_programs()
            .collect::<Result<_, _>>()
            .map_err(io::Error::other)?;
        let unloaded = !id.is_some_and(|id| live.iter().any(|p| p.id() == id));
        let maps_unloaded = map_ids.iter().all(|map| {
            matches!(aya::maps::MapInfo::from_id(*map), Err(aya::maps::MapError::SyscallError(e)) if e.io_error.raw_os_error()==Some(libc::ENOENT))
        });
        if (unloaded && maps_unloaded) || Instant::now() >= until {
            break (unloaded, maps_unloaded);
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    Ok((unloaded, maps_unloaded))
}

fn payload(phase: &str, kind: usize, seq: usize) -> Vec<u8> {
    format!("VTC1:{phase}:{kind}:{seq}").into_bytes()
}

pub fn sender(phase: &str) -> io::Result<()> {
    parent_guard()?;
    if !PHASES.contains(&phase) {
        return Err(io::Error::other("unknown TC phase"));
    }
    let socket = UdpSocket::bind((APP, SOURCE_PORT))?;
    let mut sent = vec![];
    for (kind, port) in PORTS.iter().enumerate() {
        for seq in 0..N {
            let body = payload(phase, kind, seq);
            if socket.send_to(&body, (SERVER, *port))? != body.len() {
                return Err(io::Error::other("partial UDP send"));
            }
            sent.push(
                json!({"port":port,"sequence":seq,"payload":String::from_utf8(body).unwrap()}),
            );
        }
    }
    println!(
        "{}",
        json!({"phase":phase,"source":format!("{APP}:{SOURCE_PORT}"),"destination":SERVER,"packets":sent})
    );
    Ok(())
}

pub fn receiver(phase: &str, out: &Path) -> io::Result<()> {
    parent_guard()?;
    if !PHASES.contains(&phase) {
        return Err(io::Error::other("unknown TC phase"));
    }
    let sockets: Vec<_> = PORTS
        .iter()
        .map(|p| UdpSocket::bind((SERVER, *p)))
        .collect::<Result<_, _>>()?;
    for s in &sockets {
        s.set_nonblocking(true)?;
    }
    save(
        &out.join(format!("tc-{phase}-receiver-ready.json")),
        &json!({"ports":PORTS,"address":SERVER}),
    )?;
    let mut line = String::new();
    io::stdin().read_line(&mut line)?;
    if line != "collect\n" {
        return Err(io::Error::other("receiver control ended early"));
    }
    let until = Instant::now() + Duration::from_millis(500);
    let mut received: [Vec<usize>; 2] = [vec![], vec![]];
    while Instant::now() < until {
        for (kind, s) in sockets.iter().enumerate() {
            let mut bytes = [0_u8; 128];
            match s.recv_from(&mut bytes) {
                Ok((size, peer)) => {
                    if peer.to_string() != format!("{APP}:{SOURCE_PORT}") {
                        return Err(io::Error::other("unexpected UDP peer"));
                    }
                    let seq = (0..N)
                        .find(|n| payload(phase, kind, *n) == bytes[..size])
                        .ok_or_else(|| io::Error::other("unexpected UDP payload"))?;
                    if received[kind].contains(&seq) {
                        return Err(io::Error::other("duplicate UDP fixture"));
                    }
                    received[kind].push(seq);
                }
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => (),
                Err(e) => return Err(e),
            }
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    for v in &mut received {
        v.sort_unstable();
    }
    save(
        &out.join(format!("tc-{phase}-received.json")),
        &json!({"phase":phase,"target_sequences":received[0],"control_sequences":received[1],"window_ms":500,"payloads_verified":true}),
    )
}

fn request(run: &Run, agent: &mut Owned, out: &Path, phase: &str, when: &str) -> io::Result<Value> {
    writeln!(
        agent
            .child
            .stdin
            .as_mut()
            .ok_or_else(|| io::Error::other("TC agent closed"))?,
        "{phase}:{when}"
    )?;
    let path = out.join(format!("tc-{phase}-{when}.json"));
    run.ready(agent, &path)?;
    read_report(&path)
}

pub fn comparison(
    run: &mut Run,
    app: u32,
    router: u32,
    server: u32,
    exe: &str,
    out: &Path,
    fault: &str,
) -> io::Result<()> {
    let mut injection = Value::Null;
    let mut agent: Option<Owned> = None;
    let mut phases = vec![];
    let mut ready = Value::Null;
    let mut assertion_failed = false;
    let attempt = (|| -> io::Result<()> {
        let empty: Value = serde_json::from_slice(&run.command(
            "tc-filters-before",
            Some(router),
            &["tc", "-j", "filter", "show", "dev", "rt-app", "ingress"],
            0,
        )?)?;
        if empty != json!([]) {
            return Err(io::Error::other("unexpected existing TC filters"));
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
                run.ready(agent.as_mut().unwrap(), &out.join("tc-ready.json"))?;
                ready = read_report(&out.join("tc-ready.json"))?;
                let filters: Value = serde_json::from_slice(&run.command(
                    "tc-filters-attached",
                    Some(router),
                    &["tc", "-j", "filter", "show", "dev", "rt-app", "ingress"],
                    0,
                )?)?;
                if fault == "wrong-hook" {
                    let egress: Value = serde_json::from_slice(&run.command(
                        "tc-fault-egress",
                        Some(router),
                        &["tc", "-j", "filter", "show", "dev", "rt-app", "egress"],
                        0,
                    )?)?;
                    injection = json!({"ingress_filters":filters,"egress_filters":egress});
                }
                if !filters.as_array().is_some_and(|fs| {
                    fs.iter()
                        .filter(|f| {
                            f["options"]["prog"]["id"] == ready["program_id"]
                                && f["options"]["direct-action"] == true
                                && f["options"]["handle"] == "0x1"
                        })
                        .count()
                        == 1
                        && fs.iter().all(|f| {
                            f["kind"] == "bpf"
                                && f["pref"] == 49152
                                && (f["options"]["prog"]["id"].is_null()
                                    || f["options"]["prog"]["id"] == ready["program_id"])
                        })
                }) {
                    return Err(io::Error::other("TC attachment identity differs"));
                }
                if fault == "interrupt-hold" {
                    injection = json!({"checkpoint":"pass-attached-before-send","filters":filters});
                    save(
                        &out.join("tc-interruption-ready.json"),
                        &json!({"schema_version":1,"checkpoint":"pass-attached-before-send","runner_pid":std::process::id(),"attachment":ready,"filters":filters}),
                    )?;
                    while !run.cancelled.load(std::sync::atomic::Ordering::SeqCst)
                        && Instant::now() < run.deadline
                    {
                        std::thread::sleep(Duration::from_millis(10));
                    }
                    return Err(io::Error::other(
                        "TC comparison interrupted at attached checkpoint",
                    ));
                }
            }
            if phase == "detached" {
                let a = agent.as_mut().unwrap();
                writeln!(a.child.stdin.as_mut().unwrap(), "stop")?;
                a.stop()?;
                let filters: Value = serde_json::from_slice(&run.command(
                    "tc-filters-detached",
                    Some(router),
                    &["tc", "-j", "filter", "show", "dev", "rt-app", "ingress"],
                    0,
                )?)?;
                if filters != json!([]) {
                    return Err(io::Error::other("TC filter survived detach"));
                }
            }
            let before = if matches!(phase, "pass" | "count" | "drop") {
                request(run, agent.as_mut().unwrap(), out, phase, "before")?
            } else {
                json!({"mode":null,"counters":vec![0;6]})
            };
            let mut r = spawn(
                "nsenter",
                &[
                    "--target".into(),
                    server.to_string(),
                    "--net".into(),
                    "--".into(),
                    exe.into(),
                    "--tc-receiver".into(),
                    phase.into(),
                    out.to_string_lossy().into_owned(),
                ],
                &out.join(format!("tc-{phase}-receiver.log")),
                false,
            )?;
            run.ready(&mut r, &out.join(format!("tc-{phase}-receiver-ready.json")))?;
            if fault == "receiver-stopped" && phase == "drop" {
                r.child.kill()?;
                r.stop()?;
                let status = r
                    .child
                    .try_wait()?
                    .ok_or_else(|| io::Error::other("receiver not reaped"))?;
                injection = json!({"phase":"drop","ready":read_report(&out.join("tc-drop-receiver-ready.json"))?,
                    "reaped":r.stopped,"signal":status.signal(),"received_report_present":out.join("tc-drop-received.json").exists(),
                    "sender_started":false});
            }
            // A readiness file can outlive its helper. This check applies to every run.
            if r.child.try_wait()?.is_some() {
                return Err(io::Error::other("TC receiver stopped before send"));
            }
            let sent: Value = serde_json::from_slice(&run.command(
                &format!("tc-{phase}-send"),
                Some(app),
                &[exe, "--tc-send", phase],
                0,
            )?)?;
            writeln!(r.child.stdin.as_mut().unwrap(), "collect")?;
            let path = out.join(format!("tc-{phase}-received.json"));
            // Receiver may exit immediately after saving; poll evidence and then reap it.
            let until = run.deadline.min(Instant::now() + Duration::from_secs(3));
            while !path.is_file()
                && Instant::now() < until
                && !run.cancelled.load(std::sync::atomic::Ordering::SeqCst)
            {
                std::thread::sleep(Duration::from_millis(10));
            }
            r.stop()?;
            if r.child.try_wait()?.is_none_or(|s| !s.success()) {
                return Err(io::Error::other("TC receiver failed"));
            }
            let received = read_report(&path)?;
            let after = if matches!(phase, "pass" | "count" | "drop") {
                request(run, agent.as_mut().unwrap(), out, phase, "after")?
            } else {
                json!({"mode":null,"counters":vec![0;6]})
            };
            let b: Vec<u64> = serde_json::from_value(before["counters"].clone())?;
            let a: Vec<u64> = serde_json::from_value(after["counters"].clone())?;
            let delta: Vec<u64> = a
                .iter()
                .zip(&b)
                .map(|(a, b)| {
                    a.checked_sub(*b)
                        .ok_or_else(|| io::Error::other("counter moved backwards"))
                })
                .collect::<Result<_, _>>()?;
            let item = json!({"phase":phase,"sent":sent,"received":received,"before":before,"after":after,"counter_delta":delta});
            let matches = valid_phase(&item);
            phases.push(item);
            if !matches {
                assertion_failed = true;
                return Err(io::Error::other(format!(
                    "TC {phase} delivery/count assertion failed"
                )));
            }
        }
        Ok(())
    })();
    let stopped = agent.as_mut().is_none_or(|a| a.stop().is_ok());
    let cleanup = read_report(&out.join("tc-cleanup.json")).unwrap_or(Value::Null);
    let clean = stopped
        && cleanup
            == json!({"detached":true,"program_unloaded":true,"maps_unloaded":true,"pinned":false});
    let interruption = if run.cancelled.load(std::sync::atomic::Ordering::SeqCst) {
        if read_report(&out.join("interrupt-request.json")).is_ok_and(|r| r["reason"] == "timeout")
        {
            Some("timeout")
        } else {
            Some("cancelled")
        }
    } else {
        None
    };
    let status = if !clean {
        "environment_error"
    } else if let Some(reason) = interruption {
        reason
    } else if attempt.is_ok() {
        "passed"
    } else if assertion_failed {
        "failed"
    } else {
        "environment_error"
    };
    let result = json!({"schema_version":1,"scenario":"tc-ingress","status":status,
        "detail":attempt.as_ref().err().map(|e|e.to_string()),"fault":fault,"injection":injection,"attachment":ready,"phases":phases,"cleanup":cleanup});
    save(&out.join("tc-result.json"), &result)?;
    if !clean {
        return Err(io::Error::other("TC cleanup evidence incomplete"));
    }
    attempt
}

pub fn matches_fault(result: &Value, fault: &str) -> bool {
    if result["fault"] != fault
        || result["cleanup"]
            != json!({"detached":true,"program_unloaded":true,"maps_unloaded":true,"pinned":false})
    {
        return false;
    }
    let Some(phases) = result["phases"].as_array() else {
        return false;
    };
    let prefix = if fault == "wrong-hook" { 1 } else { 3 };
    if phases.len() != prefix + usize::from(fault == "drop-disabled")
        || !phases[..prefix]
            .iter()
            .zip(PHASES)
            .all(|(p, name)| p["phase"] == name && valid_phase(p))
    {
        return false;
    }
    match fault {
        "drop-disabled" => {
            let p = &phases[3];
            result["status"] == "failed"
                && result["detail"] == "TC drop delivery/count assertion failed"
                && p["phase"] == "drop"
                && p["before"]["mode"] == 1
                && p["after"]["mode"] == 1
                && p["counter_delta"] == json!([4, 4, 0, 4, 4, 0])
                && p["received"]["target_sequences"] == json!([0, 1, 2, 3])
                && p["received"]["control_sequences"] == json!([0, 1, 2, 3])
                && !valid_phase(p)
        }
        "wrong-hook" => {
            result["status"] == "environment_error"
                && result["detail"] == "TC attachment identity differs"
                && result["attachment"]["hook"] == "egress"
                && result["injection"]["ingress_filters"] == json!([])
                && result["injection"]["egress_filters"]
                    .as_array()
                    .is_some_and(|fs| {
                        fs.iter().any(|f| {
                            f["options"]["prog"]["id"] == result["attachment"]["program_id"]
                                && f["options"]["direct-action"] == true
                        })
                    })
        }
        "receiver-stopped" => {
            result["status"] == "environment_error"
                && result["detail"] == "TC receiver stopped before send"
                && result["injection"]
                    == json!({"phase":"drop","ready":{"ports":PORTS,"address":SERVER},"reaped":true,
                "signal":9,"received_report_present":false,"sender_started":false})
        }
        _ => false,
    }
}

fn valid_phase(item: &Value) -> bool {
    let phase = item["phase"].as_str().unwrap_or("");
    let (mode, delta) = match phase {
        "plain" | "detached" => (Value::Null, vec![0; 6]),
        "pass" => (json!(0), vec![0; 6]),
        "count" => (json!(1), vec![4, 4, 0, 4, 4, 0]),
        "drop" => (json!(2), vec![4, 0, 4, 4, 4, 0]),
        _ => return false,
    };
    let expected = if phase == "drop" {
        json!([])
    } else {
        json!([0, 1, 2, 3])
    };
    item["before"]["mode"] == mode
        && item["after"]["mode"] == mode
        && item["counter_delta"] == json!(delta)
        && item["received"]["target_sequences"] == expected
        && item["received"]["control_sequences"] == json!([0, 1, 2, 3])
        && item["received"]["payloads_verified"] == true
        && item["sent"]["packets"]
            .as_array()
            .is_some_and(|a| a.len() == 8)
}

pub(super) fn matches_interruption(r: &Value, reason: &str) -> bool {
    r["status"] == reason
        && r["fault"] == "interrupt-hold"
        && r["injection"]["checkpoint"] == "pass-attached-before-send"
        && r["attachment"]["hook"] == "ingress"
        && r["attachment"]["interface"] == "rt-app"
        && r["phases"]
            .as_array()
            .is_some_and(|p| p.len() == 1 && p[0]["phase"] == "plain" && valid_phase(&p[0]))
        && r["cleanup"]
            == json!({"detached":true,"program_unloaded":true,"maps_unloaded":true,"pinned":false})
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn drop_requires_both_exact_delivery_and_kernel_counts() {
        let mut item = json!({"phase":"drop","before":{"mode":2},"after":{"mode":2},"counter_delta":[4,0,4,4,4,0],
            "received":{"target_sequences":[],"control_sequences":[0,1,2,3],"payloads_verified":true},"sent":{"packets":vec![0;8]}});
        assert!(valid_phase(&item));
        item["counter_delta"] = json!(vec![0; 6]);
        assert!(!valid_phase(&item));
        item["counter_delta"] = json!([4, 0, 4, 4, 4, 0]);
        item["received"]["control_sequences"] = json!([]);
        assert!(!valid_phase(&item));
        item["received"]["control_sequences"] = json!([0, 1, 2, 3]);
        item["received"]["target_sequences"] = json!([0]);
        assert!(!valid_phase(&item));
    }
}
