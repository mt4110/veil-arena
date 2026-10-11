#!/usr/bin/env bash
# Preparation only. Reuses the Nix store; never starts a VM or removes cache.
set -euo pipefail
if [ "$#" -ne 0 ]; then echo "usage: bash scripts/prepare-netns.sh" >&2; exit 2; fi
export ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_HOST_UID="$(id -u)"
export ARENA_HOST_SYSTEM="$(nix eval --impure --raw --expr builtins.currentSystem)"
export ARENA_VM_ACCELERATOR="${ARENA_VM_ACCELERATOR:-tcg}"
if [ "$ARENA_HOST_UID" -eq 0 ]; then echo "run as an ordinary user" >&2; exit 3; fi
expression='
 let f = builtins.getFlake (builtins.getEnv "ARENA_FLAKE");
 in f.lib.mkNetnsBatchVm {
   hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM";
   hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID");
   accelerator = builtins.getEnv "ARENA_VM_ACCELERATOR";
 }
'
# A separate immutable GC root per prepared output keeps its closure reusable.
# Existing roots must already identify that exact output; never repoint them.
output=$(nix eval --impure --raw --expr "($expression).outPath")
case "$output" in /nix/store/*-arena-netns-prepared) ;; *) exit 3 ;; esac
umask 077
cache="$ARENA_FLAKE/artifacts/prepared-netns"
mkdir -p "$cache"
root="$cache/$(basename "$output")"
if [ -e "$root" ] || [ -L "$root" ]; then
  if [ ! -L "$root" ] || [ "$(readlink "$root")" != "$output" ]; then
    echo "prepared cache root differs from expected output" >&2; exit 3
  fi
  nix build --impure --no-link --print-out-paths --expr "$expression"
else
  nix build --impure --out-link "$root" --print-out-paths --expr "$expression"
fi
