#!/usr/bin/env bash
# Run inside the pinned Nix devShell. Does not start VMs or fetch Cargo dependencies.
set -euo pipefail
cargo fmt --all -- --check
cargo fmt --manifest-path tools/vm-controller/Cargo.toml --all -- --check
cargo clippy --offline --locked --all-targets -- -D warnings
cargo clippy --offline --locked --manifest-path tools/vm-controller/Cargo.toml --all-targets -- -D warnings
cargo test --offline --locked
cargo test --offline --locked --manifest-path tools/vm-controller/Cargo.toml
python3 -B -m unittest discover -s tests -p 'test_*.py'
nix flake check --all-systems --no-build --no-update-lock-file
