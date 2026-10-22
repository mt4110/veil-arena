#!/usr/bin/env bash
# Preparation only. Does not start a VM, call KVM APIs or load BPF.
set -euo pipefail
export ARENA_FLAKE
ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_HOST_UID="${ARENA_HOST_UID:-$(id -u)}"
export ARENA_HOST_SYSTEM
ARENA_HOST_SYSTEM=$(nix --extra-experimental-features 'nix-command flakes' eval --impure --raw --expr builtins.currentSystem)
case "${1:-}" in
  guest)
    if [ "$#" -ne 2 ]; then echo 'usage: prepare-warden-preflight.sh guest WARDEN_SOURCE' >&2; exit 2; fi
    case "$ARENA_HOST_SYSTEM" in aarch64-linux|x86_64-linux) ;; *) echo 'use a matching Linux builder; no remote builder is started automatically' >&2; exit 3;; esac
    export ARENA_WARDEN_SOURCE
    ARENA_WARDEN_SOURCE=$(cd "$2" && pwd -P)
    # Nix interpolation is intentionally passed literally to Nix.
    # shellcheck disable=SC2016
    expression='let w = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (clean (builtins.getEnv "ARENA_WARDEN_SOURCE")))); in f.lib.mkWardenPreflightGuest { hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); guestSystem = builtins.getEnv "ARENA_HOST_SYSTEM"; wardenPackage = w.packages.${builtins.getEnv "ARENA_HOST_SYSTEM"}.veil-warden; wardenVerifier = (clean (builtins.getEnv "ARENA_WARDEN_SOURCE")) + "/scripts/verify-package.py"; }'
    ;;
  runner)
    if [ "$#" -ne 1 ] || [ "$(id -u)" -eq 0 ]; then echo 'use an ordinary host preparation user' >&2; exit 3; fi
    expression='f.lib.mkWardenPreflightRunner { hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM"; hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); }'
    ;;
  *) echo 'usage: prepare-warden-preflight.sh guest WARDEN_SOURCE | runner' >&2; exit 2;;
esac
prefix='let clean = p: builtins.path { path = builtins.toPath p; filter = path: type: !(builtins.elem (builtins.baseNameOf path) [ ".git" "artifacts" "target" "__pycache__" ]); }; f = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (clean (builtins.getEnv "ARENA_FLAKE")))); in '
nix --extra-experimental-features 'nix-command flakes' build --option builders '' --impure --no-link --print-out-paths --expr "$prefix$expression"
