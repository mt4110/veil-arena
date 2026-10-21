#!/usr/bin/env bash
# Preparation only; snapshots selected Keeper bytes without modifying that checkout.
set -euo pipefail
export ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_HOST_UID="$(id -u)"
export ARENA_HOST_SYSTEM="$(nix eval --impure --raw --expr builtins.currentSystem)"
if [ "$ARENA_HOST_UID" -eq 0 ]; then echo 'use an ordinary preparation user' >&2; exit 3; fi
umask 077
case "${1:-}" in
  guest)
    if [ "$#" -ne 2 ]; then echo 'usage: prepare-keeper.sh guest KEEPER_SOURCE' >&2; exit 2; fi
    case "$ARENA_HOST_SYSTEM" in aarch64-linux|x86_64-linux) ;; *) echo 'prepare guest on matching Linux builder' >&2; exit 3;; esac
    mkdir -p "$ARENA_FLAKE/artifacts/keeper-inputs"
    arena_snapshot="$ARENA_FLAKE/artifacts/keeper-inputs/$(nix develop --command python3 -c 'import uuid;print(uuid.uuid4().hex)' | tail -1)"
    export ARENA_KEEPER_INPUTS
    ARENA_KEEPER_INPUTS=$(nix develop --command python3 -B "$ARENA_FLAKE/scripts/keeper_inputs.py" "$2" "$arena_snapshot" | tail -1)
    expression='let f = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (builtins.path { path = builtins.toPath (builtins.getEnv "ARENA_FLAKE"); filter = path: type: !(builtins.elem (builtins.baseNameOf path) [ ".git" "artifacts" "target" "__pycache__" ]); }))); in f.lib.mkK3sGuest { hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); keeperSource = builtins.path { path = builtins.toPath (builtins.getEnv "ARENA_KEEPER_INPUTS"); name = "arena-keeper-inputs"; }; }'
    ;;
  runner)
    if [ "$#" -ne 1 ]; then echo 'usage: prepare-keeper.sh runner' >&2; exit 2; fi
    expression='let f = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (builtins.path { path = builtins.toPath (builtins.getEnv "ARENA_FLAKE"); filter = path: type: !(builtins.elem (builtins.baseNameOf path) [ ".git" "artifacts" "target" "__pycache__" ]); }))); in f.lib.mkKeeperRunner { hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM"; hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); }'
    ;;
  *) echo 'usage: prepare-keeper.sh guest KEEPER_SOURCE | runner' >&2; exit 2;;
esac
output=$(nix eval --impure --raw --expr "($expression).outPath")
mkdir -p "$ARENA_FLAKE/artifacts/prepared-k3s"
root="$ARENA_FLAKE/artifacts/prepared-k3s/$(basename "$output")"
if [ -e "$root" ] || [ -L "$root" ]; then
  if [ ! -L "$root" ] || [ "$(readlink "$root")" != "$output" ]; then echo 'prepared root differs' >&2; exit 3; fi
  nix build --impure --no-link --print-out-paths --expr "$expression"
else
  nix build --impure --out-link "$root" --print-out-paths --expr "$expression"
fi
