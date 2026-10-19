//! Supervise an actual nested batch; preserve its interruption as interruption.
use super::{process, read_report, save, tc};
use serde_json::{Value, json};
use std::io;
use std::os::unix::fs::DirBuilderExt;
use std::path::Path;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

pub fn run(out: &Path, reason: &str) -> io::Result<i32> {
    std::fs::DirBuilder::new().mode(0o700).create(out)?;
    let cancelled = Arc::new(AtomicBool::new(false));
    let flag = cancelled.clone();
    ctrlc::set_handler(move || flag.store(true, Ordering::SeqCst)).map_err(io::Error::other)?;
    let nested = out.join("interrupted-batch");
    let checkpoint = nested.join("tc-ingress-001/tc-interruption-ready.json");
    let executable = std::env::current_exe()?.to_string_lossy().into_owned();
    let execution = process::execute_controlled(
        "interrupted-batch",
        &executable,
        &[
            "--batch-interruption".into(),
            reason.into(),
            nested.to_string_lossy().into_owned(),
        ],
        Path::new("/work"),
        out,
        process::Control {
            deadline: Instant::now() + Duration::from_secs(180),
            cancelled: &cancelled,
            graceful: Some(process::GracefulStop {
                request_path: nested.join("supervisor-request.json"),
                readiness_path: Some(checkpoint.clone()),
                timeout_after_ready: None,
                cancel_after_ready: reason == "cancelled",
                grace: Duration::from_secs(45),
            }),
        },
    )?;
    let batch = read_report(&nested.join("result.json")).unwrap_or(Value::Null);
    let network = read_report(&nested.join("tc-ingress-001/result.json")).unwrap_or(Value::Null);
    let tc_result =
        read_report(&nested.join("tc-ingress-001/tc-result.json")).unwrap_or(Value::Null);
    let ready = read_report(&checkpoint).unwrap_or(Value::Null);
    let clean = matches!(reason, "cancelled" | "timeout")
        && batch["status"] == reason
        && batch["exit_code"] == if reason == "cancelled" { 130 } else { 4 }
        && execution.result["exit_code"] == batch["exit_code"]
        && execution.result["signal"].is_null()
        && execution.result["termination_escalated"] == false
        && execution.result["output_complete"] == true
        && execution.result["log_truncated"] == false
        && network["status"] == reason
        && network["cleanup"]["helpers_reaped"] == true
        && network["cleanup"]["parent_network_unchanged"] == true
        && network["cleanup"]["errors"] == json!([])
        && network["cleanup"]["owned_namespace_references"] == json!([])
        && tc::matches_interruption(&tc_result, reason)
        && ready["attachment"] == tc_result["attachment"]
        && ready["filters"] == tc_result["injection"]["filters"]
        && valid_interrupted_cases(&batch["cases"]);
    let report = json!({"schema_version":1,"scenario":"batch-interruption","status":if clean {"passed"} else {"environment_error"},
        "reason":reason,"execution":execution.result,"batch_report":"interrupted-batch/result.json","checkpoint":"tc-ingress-001/tc-interruption-ready.json",
        "cleanup_verified":clean,"scope":"attached checkpoint in guest; graceful interrupt only; not panic or power loss"});
    save(&out.join("result.json"), &report)?;
    Ok(if clean { 0 } else { 3 })
}
pub fn matches_report(r: &Value, reason: &str) -> bool {
    r["schema_version"] == 1
        && r["scenario"] == "batch-interruption"
        && r["status"] == "passed"
        && r["reason"] == reason
        && r["cleanup_verified"] == true
        && r["execution"]["output_complete"] == true
        && r["execution"]["termination_escalated"] == false
        && r["execution"]["exit_code"] == if reason == "cancelled" { 130 } else { 4 }
}

fn valid_interrupted_cases(value: &Value) -> bool {
    let expected: Vec<_> = super::BATCH_CASES
        .iter()
        .filter(|(id, _)| !id.starts_with("batch-") && *id != "tc-after-cancel-001")
        .map(|(id, _)| *id)
        .collect();
    value.as_array().is_some_and(|cases| {
        cases.len() == expected.len()
            && cases
                .iter()
                .zip(expected)
                .enumerate()
                .all(|(i, (case, id))| {
                    case["id"] == id
                        && case["status"]
                            == if i < 3 {
                                "passed"
                            } else if i == 3 {
                                "failed"
                            } else {
                                "not_run"
                            }
                })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn interrupted_batch_requires_the_full_current_tail() {
        let mut cases: Vec<_> = super::super::BATCH_CASES.iter()
            .filter(|(id,_)| !id.starts_with("batch-") && *id != "tc-after-cancel-001")
            .enumerate().map(|(i,(id,_))| json!({"id":id,"status":if i<3 {"passed"} else if i==3 {"failed"} else {"not_run"}})).collect();
        assert_eq!(cases.len(), 15);
        assert!(valid_interrupted_cases(&json!(cases)));
        let last = cases.pop().unwrap();
        assert!(!valid_interrupted_cases(&json!(cases)));
        cases.push(last);
        cases[14]["id"] = json!("unrelated");
        assert!(!valid_interrupted_cases(&json!(cases)));
    }
}
