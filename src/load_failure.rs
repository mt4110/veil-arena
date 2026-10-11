//! Kernel verifier rejection is an environment error, never a traffic verdict.
use super::{save, tc};
use aya::maps::{Array, IterableMap, PerCpuArray};
use aya::programs::{ProgramError, SchedClassifier, loaded_programs};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::io::{self, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::Path;

fn programs() -> io::Result<Vec<u32>> {
    let mut ids = loaded_programs()
        .map(|p| p.map(|p| p.id()))
        .collect::<Result<Vec<_>, _>>()
        .map_err(io::Error::other)?;
    ids.sort_unstable();
    Ok(ids)
}
fn write_new(path: &Path, bytes: &[u8]) -> io::Result<()> {
    let mut file = std::fs::OpenOptions::new()
        .create_new(true)
        .write(true)
        .mode(0o600)
        .open(path)?;
    file.write_all(bytes)?;
    file.sync_all()
}
fn diagnostic(errno: Option<i32>, log: &str) -> bool {
    errno == Some(libc::EINVAL)
        && log
            .lines()
            .any(|line| line == "invalid func unknown#999999")
}
pub fn run(out: &Path) -> io::Result<i32> {
    std::fs::DirBuilder::new().mode(0o700).create(out)?;
    let object = std::env::var_os("ARENA_TC_REJECT_OBJECT")
        .ok_or_else(|| io::Error::other("reject object missing"))?;
    let meta = std::fs::symlink_metadata(&object)?;
    if !meta.is_file() || meta.len() > 1024 * 1024 {
        return Err(io::Error::other("invalid reject object"));
    }
    let bytes = std::fs::read(object)?;
    write_new(&out.join("fixture.o"), &bytes)?;
    let programs_before = programs()?;
    let mut bpf = aya::Ebpf::load(&bytes).map_err(io::Error::other)?;
    let ids = [
        Array::<_, u32>::try_from(
            bpf.map("MODE")
                .ok_or_else(|| io::Error::other("MODE missing"))?,
        )
        .map_err(io::Error::other)?
        .map()
        .info()
        .map_err(io::Error::other)?
        .id(),
        PerCpuArray::<_, u64>::try_from(
            bpf.map("STATS")
                .ok_or_else(|| io::Error::other("STATS missing"))?,
        )
        .map_err(io::Error::other)?
        .map()
        .info()
        .map_err(io::Error::other)?
        .id(),
    ];
    let before = tc::stats(&bpf)?;
    let (outcome, errno, log, program_id, program_fd_obtained) = {
        let p: &mut SchedClassifier = bpf
            .program_mut("arena_rejected")
            .ok_or_else(|| io::Error::other("reject program missing"))?
            .try_into()
            .map_err(io::Error::other)?;
        let loaded = p.load();
        let fd = p.fd().is_ok();
        match loaded {
            Err(ProgramError::LoadError {
                io_error,
                verifier_log,
            }) => (
                "verifier_rejected",
                io_error.raw_os_error(),
                verifier_log.to_string(),
                None,
                fd,
            ),
            Err(error) => ("unexpected_error", None, error.to_string(), None, fd),
            Ok(()) => (
                "unexpected_success",
                None,
                String::new(),
                Some(p.info().map_err(io::Error::other)?.id()),
                fd,
            ),
        }
    };
    let after = tc::stats(&bpf)?;
    drop(bpf);
    let (program_unloaded, maps_unloaded) = tc::released(program_id, ids)?;
    let programs_after = programs()?;
    if log.len() > 65536 {
        return Err(io::Error::other("verifier diagnostic too large"));
    }
    write_new(&out.join("verifier.log"), log.as_bytes())?;
    let expected = outcome == "verifier_rejected"
        && diagnostic(errno, &log)
        && !program_fd_obtained
        && program_id.is_none()
        && before == json!({"mode":0,"counters":vec![0;6]})
        && after == before
        && program_unloaded
        && maps_unloaded
        && programs_after == programs_before;
    let report = json!({"schema_version":1,"scenario":"bpf-load-failure","status":"environment_error","exit_code":3,
        "stage":"bpf_prog_load","elf_parsed":true,"load_outcome":outcome,"expected_rejection":expected,
        "errno":errno,"verifier_log":log,"diagnostic_file":"verifier.log","fixture":"fixture.o",
        "object_sha256":format!("{:x}",Sha256::digest(&bytes)),"program_name":"arena_rejected","map_ids":ids,
        "program_id":program_id,"program_fd_obtained":program_fd_obtained,"program_ids_before":programs_before,"program_ids_after":programs_after,
        "before":before,"after":after,"attached":false,"traffic_started":false,
        "cleanup":{"program_unloaded":program_unloaded,"maps_unloaded":maps_unloaded,"pinned":false}});
    save(&out.join("result.json"), &report)?;
    Ok(3)
}
pub fn matches_rejection(r: &Value) -> bool {
    r["schema_version"] == 1
        && r["scenario"] == "bpf-load-failure"
        && r["status"] == "environment_error"
        && r["exit_code"] == 3
        && r["stage"] == "bpf_prog_load"
        && r["elf_parsed"] == true
        && r["load_outcome"] == "verifier_rejected"
        && r["expected_rejection"] == true
        && r["errno"] == libc::EINVAL
        && r["verifier_log"]
            .as_str()
            .is_some_and(|s| diagnostic(Some(libc::EINVAL), s))
        && r["program_id"].is_null()
        && r["program_fd_obtained"] == false
        && r["program_name"] == "arena_rejected"
        && r["attached"] == false
        && r["traffic_started"] == false
        && r["before"] == json!({"mode":0,"counters":vec![0;6]})
        && r["after"] == r["before"]
        && r["program_ids_before"].is_array()
        && r["program_ids_after"] == r["program_ids_before"]
        && r["cleanup"] == json!({"program_unloaded":true,"maps_unloaded":true,"pinned":false})
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn expected_rejection_requires_kernel_diagnostic_and_errno() {
        assert!(diagnostic(
            Some(libc::EINVAL),
            "0: (85) call unknown#999999\ninvalid func unknown#999999\n"
        ));
        assert!(!diagnostic(
            Some(libc::EPERM),
            "invalid func unknown#999999\n"
        ));
        assert!(!diagnostic(
            Some(libc::EINVAL),
            "unrelated verifier failure"
        ));
        assert!(!diagnostic(None, "invalid func unknown#999999\n"));
    }
}
