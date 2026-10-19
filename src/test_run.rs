//! Direct kernel execution, separate from TC attachment and wire-delivery proof.
use super::{save, tc};
use aya::maps::{Array, IterableMap, PerCpuArray};
use aya::programs::SchedClassifier;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, Write};
use std::os::fd::{AsFd, AsRawFd};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::Path;
use std::time::{Duration, Instant};

// Linux UAPI bpf_attr.test (v6.12.112). All unused fields and tail padding zero.
#[repr(C, align(8))]
#[derive(Default)]
struct TestAttr {
    prog_fd: u32,
    retval: u32,
    data_size_in: u32,
    data_size_out: u32,
    data_in: u64,
    data_out: u64,
    repeat: u32,
    duration: u32,
    ctx_size_in: u32,
    ctx_size_out: u32,
    ctx_in: u64,
    ctx_out: u64,
    flags: u32,
    cpu: u32,
    batch_size: u32,
    padding: u32,
}
struct Fixture {
    id: String,
    bytes: Vec<u8>,
    mode: u32,
    errno: Option<i32>,
    retval: Option<u32>,
    delta: [u64; 6],
}
fn base() -> Vec<u8> {
    let mut p = vec![0; 46];
    p[..6].copy_from_slice(&[2, 0, 0, 0, 0, 2]);
    p[6..12].copy_from_slice(&[2, 0, 0, 0, 0, 1]);
    p[12..14].copy_from_slice(&0x0800u16.to_be_bytes());
    p[14] = 0x45;
    p[16..18].copy_from_slice(&32u16.to_be_bytes());
    p[22] = 64;
    p[23] = 17;
    p[26..30].copy_from_slice(&[10, 77, 1, 10]);
    p[30..34].copy_from_slice(&[10, 77, 2, 10]);
    p[34..36].copy_from_slice(&39100u16.to_be_bytes());
    p[36..38].copy_from_slice(&39101u16.to_be_bytes());
    p[38..40].copy_from_slice(&12u16.to_be_bytes());
    p[42..].copy_from_slice(b"VTC1");
    checksum(&mut p);
    p
}
fn checksum(p: &mut [u8]) {
    p[24] = 0;
    p[25] = 0;
    let len = usize::from(p[14] & 15) * 4;
    let mut sum: u32 = p[14..14 + len]
        .as_chunks::<2>()
        .0
        .iter()
        .map(|b| u32::from(u16::from_be_bytes([b[0], b[1]])))
        .sum();
    while sum >> 16 != 0 {
        sum = (sum & 0xffff) + (sum >> 16);
    }
    p[24..26].copy_from_slice(&(!(sum as u16)).to_be_bytes());
}
fn fixtures() -> Vec<Fixture> {
    let packet = base();
    let mut rows = vec![];
    for size in 0..46 {
        rows.push(Fixture {
            id: format!("prefix-{size:02}"),
            bytes: packet[..size].to_vec(),
            mode: 2,
            errno: if size < 34 { Some(libc::EINVAL) } else { None },
            retval: if size < 34 { None } else { Some(0) },
            delta: [0; 6],
        });
    }
    let mutations: [(&str, usize, &[u8]); 15] = [
        ("ihl-zero", 14, &[0x40]),
        ("ihl-small", 14, &[0x44]),
        ("ihl-options-truncated", 14, &[0x4f]),
        ("ip-total-zero", 16, &[0, 0]),
        ("ip-total-short", 16, &[0, 19]),
        ("ip-total-no-prefix", 16, &[0, 31]),
        ("ip-total-overrun", 16, &[0, 33]),
        ("udp-zero", 38, &[0, 0]),
        ("udp-short-header", 38, &[0, 7]),
        ("udp-short-prefix", 38, &[0, 11]),
        ("udp-overrun", 38, &[0, 13]),
        ("fragment-mf", 20, &[0x20, 0]),
        ("fragment-offset", 20, &[0, 1]),
        ("version-not-v4", 14, &[0x65]),
        ("protocol-not-udp", 23, &[6]),
    ];
    for (id, offset, data) in mutations {
        let mut bytes = packet.clone();
        bytes[offset..offset + data.len()].copy_from_slice(data);
        rows.push(Fixture {
            id: id.into(),
            bytes,
            mode: 2,
            errno: None,
            retval: Some(0),
            delta: [0; 6],
        });
    }
    for size in [14, 33] {
        let mut bytes = packet[..size].to_vec();
        bytes[12..14].copy_from_slice(&0x88b5u16.to_be_bytes());
        rows.push(Fixture {
            id: format!("non-ip-short-{size}"),
            bytes,
            mode: 2,
            errno: None,
            retval: Some(0),
            delta: [0; 6],
        });
    }
    let mut options = packet.clone();
    options.splice(34..34, [1, 1, 1, 1]);
    options[14] = 0x46;
    options[16..18].copy_from_slice(&36u16.to_be_bytes());
    checksum(&mut options);
    rows.push(Fixture {
        id: "valid-ip-options".into(),
        bytes: options,
        mode: 2,
        errno: None,
        retval: Some(2),
        delta: [1, 0, 1, 0, 0, 0],
    });
    for mode in 0..3 {
        for control in [false, true] {
            let mut bytes = packet.clone();
            if control {
                bytes[36..38].copy_from_slice(&39102u16.to_be_bytes());
            }
            let mut delta = [0; 6];
            let b = if control { 3 } else { 0 };
            let drop = mode == 2 && !control;
            if mode != 0 {
                delta[b] = 1;
                delta[b + if drop { 2 } else { 1 }] = 1;
            }
            rows.push(Fixture {
                id: format!(
                    "recovery-{mode}-{}",
                    if control { "control" } else { "target" }
                ),
                bytes,
                mode,
                errno: None,
                retval: Some(if drop { 2 } else { 0 }),
                delta,
            });
        }
    }
    let mut tcp = vec![0u8; 54];
    tcp[..34].copy_from_slice(&packet[..34]);
    tcp[23] = 6;
    tcp[16..18].copy_from_slice(&40u16.to_be_bytes());
    tcp[34..36].copy_from_slice(&40000u16.to_be_bytes());
    tcp[36..38].copy_from_slice(&39201u16.to_be_bytes());
    tcp[46] = 0x50;
    tcp[47] = 2;
    checksum(&mut tcp);
    for size in 34..54 {
        rows.push(Fixture {
            id: format!("tcp-prefix-{size}"),
            bytes: tcp[..size].to_vec(),
            mode: 4,
            errno: None,
            retval: Some(0),
            delta: [0; 6],
        });
    }
    for mode in [0, 3, 4] {
        for control in [false, true] {
            for flags in if mode == 4 {
                vec![2, 0x12, 0x10, 4, 1, 3]
            } else {
                vec![2]
            } {
                let mut bytes = tcp.clone();
                bytes[47] = flags;
                if control {
                    bytes[36..38].copy_from_slice(&39202u16.to_be_bytes());
                }
                let mut delta = [0; 6];
                let drop = mode == 4 && !control && flags == 2;
                if mode != 0 && flags == 2 {
                    let b = if control { 3 } else { 0 };
                    delta[b] = 1;
                    delta[b + if drop { 2 } else { 1 }] = 1;
                }
                rows.push(Fixture {
                    id: format!(
                        "tcp-{mode}-{}-{flags}",
                        if control { "control" } else { "target" }
                    ),
                    bytes,
                    mode,
                    errno: None,
                    retval: Some(if drop { 2 } else { 0 }),
                    delta,
                });
            }
        }
    }
    for (id, offset, data) in [
        ("tcp-doff-small", 46, vec![0x40]),
        ("tcp-doff-overrun", 46, vec![0xf0]),
        ("tcp-total-overrun", 16, vec![0, 41]),
    ] {
        let mut bytes = tcp.clone();
        bytes[offset..offset + data.len()].copy_from_slice(&data);
        rows.push(Fixture {
            id: id.into(),
            bytes,
            mode: 4,
            errno: None,
            retval: Some(0),
            delta: [0; 6],
        });
    }
    rows
}
fn execute(fd: i32, bytes: &[u8]) -> (Option<i32>, Option<u32>, Vec<u8>) {
    let mut output = vec![0u8; 256];
    let mut attr = TestAttr {
        prog_fd: fd as u32,
        data_size_in: bytes.len() as u32,
        data_size_out: output.len() as u32,
        data_in: if bytes.is_empty() {
            0
        } else {
            bytes.as_ptr() as u64
        },
        data_out: output.as_mut_ptr() as u64,
        repeat: 1,
        ..Default::default()
    };
    // The FD and both buffers remain alive and unmoved throughout the syscall.
    let result = unsafe {
        libc::syscall(
            libc::SYS_bpf,
            10u32,
            &mut attr as *mut TestAttr,
            std::mem::size_of::<TestAttr>(),
        )
    };
    if result < 0 {
        (io::Error::last_os_error().raw_os_error(), None, vec![])
    } else {
        output.truncate(attr.data_size_out as usize);
        (None, Some(attr.retval), output)
    }
}
pub fn run(out: &Path) -> io::Result<i32> {
    std::fs::DirBuilder::new().mode(0o700).create(out)?;
    let object =
        std::env::var_os("ARENA_TC_OBJECT").ok_or_else(|| io::Error::other("TC object missing"))?;
    let bytes = std::fs::read(object)?;
    if bytes.len() > 1024 * 1024 {
        return Err(io::Error::other("TC object too large"));
    }
    let mut bpf = aya::Ebpf::load(&bytes).map_err(io::Error::other)?;
    let ids = [
        Array::<_, u32>::try_from(bpf.map("MODE").unwrap())
            .map_err(io::Error::other)?
            .map()
            .info()
            .map_err(io::Error::other)?
            .id(),
        PerCpuArray::<_, u64>::try_from(bpf.map("STATS").unwrap())
            .map_err(io::Error::other)?
            .map()
            .info()
            .map_err(io::Error::other)?
            .id(),
    ];
    let (id, fd) = {
        let p: &mut SchedClassifier = bpf
            .program_mut("arena_ingress")
            .unwrap()
            .try_into()
            .map_err(io::Error::other)?;
        p.load().map_err(io::Error::other)?;
        (
            p.info().map_err(io::Error::other)?.id(),
            p.fd().map_err(io::Error::other)?.as_fd().as_raw_fd(),
        )
    };
    let mut rows = vec![];
    let mut mismatch = false;
    let prepared_at = Instant::now();
    let mut preparation_ms = None;
    let mut execution_started = None;
    let attempt = (|| -> io::Result<()> {
        let fixtures = fixtures();
        // Preserve every input before executing BPF. Durable output transport
        // latency is preparation, not kernel execution; the parent batch still
        // bounds the whole child, with an additional 90s preparation ceiling.
        for fixture in &fixtures {
            if prepared_at.elapsed() >= Duration::from_secs(90) {
                return Err(io::Error::other("direct fixture preparation deadline"));
            }
            let filename = format!("{}.bin", fixture.id);
            let mut file = std::fs::OpenOptions::new()
                .create_new(true)
                .write(true)
                .mode(0o600)
                .open(out.join(&filename))?;
            file.write_all(&fixture.bytes)?;
            file.sync_all()?;
        }
        preparation_ms = Some(prepared_at.elapsed().as_millis());
        if prepared_at.elapsed() >= Duration::from_secs(90) {
            return Err(io::Error::other("direct fixture preparation deadline"));
        }
        let start = Instant::now();
        execution_started = Some(start);
        let until = start + Duration::from_secs(20);
        for fixture in fixtures {
            if Instant::now() >= until {
                return Err(io::Error::other("direct test deadline"));
            }
            let filename = format!("{}.bin", fixture.id);
            Array::<_, u32>::try_from(bpf.map_mut("MODE").unwrap())
                .map_err(io::Error::other)?
                .set(0, fixture.mode, 0)
                .map_err(io::Error::other)?;
            let before = tc::stats(&bpf)?;
            let (errno, retval, output) = execute(fd, &fixture.bytes);
            let after = tc::stats(&bpf)?;
            let b: Vec<u64> = serde_json::from_value(before["counters"].clone())?;
            let a: Vec<u64> = serde_json::from_value(after["counters"].clone())?;
            let delta: Vec<u64> = a
                .iter()
                .zip(&b)
                .map(|(a, b)| {
                    a.checked_sub(*b)
                        .ok_or_else(|| io::Error::other("counter backwards"))
                })
                .collect::<Result<_, _>>()?;
            let matched = errno == fixture.errno
                && retval == fixture.retval
                && delta == fixture.delta
                && before["mode"] == fixture.mode
                && after["mode"] == fixture.mode
                && (errno.is_some() || output == fixture.bytes);
            rows.push(json!({"id":fixture.id,"mode":fixture.mode,"input":filename,"input_hex":hex(&fixture.bytes),"input_sha256":format!("{:x}",Sha256::digest(&fixture.bytes)),
            "errno":errno,"retval":retval,"kernel_executed":errno.is_none(),"output_hex":hex(&output),"before":before,"after":after,"counter_delta":delta,
            "status":if matched {"passed"} else {"failed"}}));
            if errno.is_some() && errno != fixture.errno {
                return Err(io::Error::other(format!(
                    "unexpected test-run errno {errno:?}"
                )));
            }
            if !matched {
                mismatch = true;
                return Err(io::Error::other("direct packet expectation mismatch"));
            }
        }
        Ok(())
    })();
    let execution_ms = execution_started.map(|start| start.elapsed().as_millis());
    drop(bpf);
    let cleanup = tc::released(Some(id), ids)?;
    let clean = cleanup == (true, true);
    let status = if !clean {
        "environment_error"
    } else if attempt.is_ok() {
        "passed"
    } else if mismatch {
        "failed"
    } else {
        "environment_error"
    };
    let report = json!({"schema_version":1,"scenario":"bpf-test-run","status":status,"detail":attempt.err().map(|e|e.to_string()),
        "preparation_duration_ms":preparation_ms,"execution_duration_ms":execution_ms,
        "api":"BPF_PROG_TEST_RUN","repeat":1,"attached":false,"live_frames":false,"program_id":id,"map_ids":ids,
        "object_sha256":format!("{:x}",Sha256::digest(&bytes)),"cases":rows,
        "cleanup":{"program_unloaded":cleanup.0,"maps_unloaded":cleanup.1,"pinned":false},"scope":"direct kernel execution only; not wire delivery"});
    save(&out.join("result.json"), &report)?;
    Ok(if status == "passed" {
        0
    } else if status == "failed" {
        1
    } else {
        3
    })
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}
pub fn valid_report(r: &Value) -> bool {
    r["schema_version"] == 1
        && r["scenario"] == "bpf-test-run"
        && r["status"] == "passed"
        && r["api"] == "BPF_PROG_TEST_RUN"
        && r["repeat"] == 1
        && r["attached"] == false
        && r["live_frames"] == false
        && r["cleanup"] == json!({"program_unloaded":true,"maps_unloaded":true,"pinned":false})
        && r["cases"].as_array().is_some_and(|rows| {
            rows.len() == fixtures().len()
                && rows.iter().zip(fixtures()).all(|(r, f)| {
                    r["id"] == f.id
                        && r["status"] == "passed"
                        && r["mode"] == f.mode
                        && r["input_hex"] == hex(&f.bytes)
                        && r["errno"] == json!(f.errno)
                        && r["retval"] == json!(f.retval)
                        && r["counter_delta"] == json!(f.delta)
                })
        })
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn uapi_layout_and_fixture_boundaries() {
        assert_eq!(std::mem::size_of::<TestAttr>(), 80);
        assert_eq!(std::mem::offset_of!(TestAttr, data_in), 16);
        assert_eq!(std::mem::offset_of!(TestAttr, ctx_in), 48);
        assert_eq!(std::mem::offset_of!(TestAttr, flags), 64);
        let f = fixtures();
        assert_eq!(f.len(), 109);
        assert_eq!(f.iter().filter(|f| f.errno.is_some()).count(), 34);
        assert!(
            f.iter()
                .filter(|f| f.errno.is_some())
                .all(|f| f.retval.is_none() && f.delta == [0; 6])
        );
    }
}
