#!/usr/bin/env bash
# Build only. Does not start a VM, configure builders, modify networks, or delete files.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "usage: bash scripts/build-offline-vm.sh /path/to/veil-blackhole" >&2
  exit 2
fi
export ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_BLACKHOLE="$(cd "$1" && pwd -P)"
export ARENA_HOST_UID="$(id -u)"
export ARENA_HOST_SYSTEM="$(nix eval --impure --raw --expr builtins.currentSystem)"
export ARENA_VM_ACCELERATOR="${ARENA_VM_ACCELERATOR:-tcg}"
if [ "$ARENA_HOST_UID" -eq 0 ]; then echo "run as an ordinary user" >&2; exit 3; fi
nix build --impure --no-link --print-out-paths --expr '
  let f = builtins.getFlake (builtins.getEnv "ARENA_FLAKE");
  in f.lib.mkOfflineVm {
    hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM";
    hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID");
    accelerator = builtins.getEnv "ARENA_VM_ACCELERATOR";
    blackholeSource = builtins.toPath (builtins.getEnv "ARENA_BLACKHOLE");
  }
'
