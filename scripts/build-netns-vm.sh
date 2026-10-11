#!/usr/bin/env bash
# Build only; no VM start, network mutation or deletion.
set -euo pipefail
if [ "$#" -ne 0 ]; then echo "usage: bash scripts/build-netns-vm.sh" >&2; exit 2; fi
export ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_HOST_UID="$(id -u)"
export ARENA_HOST_SYSTEM="$(nix eval --impure --raw --expr builtins.currentSystem)"
export ARENA_VM_ACCELERATOR="${ARENA_VM_ACCELERATOR:-tcg}"
if [ "$ARENA_HOST_UID" -eq 0 ]; then echo "run as an ordinary user" >&2; exit 3; fi
nix build --impure --no-link --print-out-paths --expr '
  let f = builtins.getFlake (builtins.getEnv "ARENA_FLAKE");
  in f.lib.mkNetnsVm {
    hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM";
    hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID");
    accelerator = builtins.getEnv "ARENA_VM_ACCELERATOR";
  }
'
