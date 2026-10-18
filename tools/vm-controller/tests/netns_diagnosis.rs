#[test]
fn automatic_diagnosis_preserves_workflow_and_owned_resources() {
    let cases = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/netns_auto_diagnosis_cases.py");
    let status = std::process::Command::new("python3")
        .arg("-B")
        .arg(cases)
        .env(
            "ARENA_TEST_WORKFLOW",
            env!("CARGO_BIN_EXE_veil-arena-netns-workflow"),
        )
        .status()
        .expect("pinned devShell Python must be available");
    assert!(status.success());
}
