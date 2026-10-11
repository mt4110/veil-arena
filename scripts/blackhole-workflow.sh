#!/usr/bin/env bash
# Pinned cached tools; no VM boot, Cargo fetch, or cache removal.
set -euo pipefail
arena_root="$(cd "$(dirname "$0")/.." && pwd -P)"
exec nix --offline --extra-experimental-features 'nix-command flakes' develop "$arena_root" \
  --no-update-lock-file --command python3 -B "$arena_root/scripts/blackhole_workflow.py" "$@"
