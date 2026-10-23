#!/usr/bin/env bash
# Prepare only. Does not start a VM, load BPF, or modify the Warden checkout.
set -euo pipefail
ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_FLAKE
export ARENA_HOST_UID="${ARENA_HOST_UID:-$(id -u)}"
ARENA_HOST_SYSTEM=$(nix --extra-experimental-features 'nix-command flakes' eval --impure --raw --expr builtins.currentSystem)
export ARENA_HOST_SYSTEM
case "${1:-}" in
  guest)
    if [ "$#" -ne 2 ]; then echo 'usage: prepare-warden-observe.sh guest INPUT_SNAPSHOT' >&2; exit 2; fi
    export ARENA_WARDEN_INPUTS
    ARENA_WARDEN_INPUTS=$(cd "$2" && pwd -P)
    if [ "$ARENA_HOST_SYSTEM" != aarch64-linux ]; then echo 'use matching aarch64 Linux builder' >&2; exit 3; fi
    expression='f.lib.mkWardenObserveGuest { hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); wardenInputs = builtins.toPath (builtins.getEnv "ARENA_WARDEN_INPUTS"); }'
    ;;
  runner)
    if [ "$#" -ne 1 ] || [ "$(id -u)" -eq 0 ]; then echo 'use an ordinary host preparation user' >&2; exit 3; fi
    expression='f.lib.mkWardenObserveRunner { hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM"; hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); }'
    ;;
  *) echo 'usage: prepare-warden-observe.sh guest INPUT_SNAPSHOT | runner' >&2; exit 2;;
esac
prefix='let f = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (builtins.path { path = builtins.toPath (builtins.getEnv "ARENA_FLAKE"); filter = path: type: !(builtins.elem (builtins.baseNameOf path) [ ".git" "artifacts" "target" "__pycache__" ]); }))); in '
nix --extra-experimental-features 'nix-command flakes' build --option builders '' --impure --no-link --print-out-paths --expr "$prefix$expression"
