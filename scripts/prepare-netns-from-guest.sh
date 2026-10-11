#!/usr/bin/env bash
# Prepare native host tools around a trusted, exported guest; never starts a VM.
set -euo pipefail
if [ "$#" -ne 1 ]; then echo "usage: prepare-netns-from-guest.sh GUEST_BUNDLE" >&2; exit 2; fi
export ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_GUEST_BUNDLE="$(cd "$1" && pwd -P)"
export ARENA_HOST_UID="$(id -u)"
export ARENA_HOST_SYSTEM="$(nix eval --impure --raw --expr builtins.currentSystem)"
if [ "$ARENA_HOST_UID" -eq 0 ]; then echo "run as an ordinary user" >&2; exit 3; fi
expression='
  let f = builtins.getFlake (builtins.getEnv "ARENA_FLAKE");
  in f.lib.mkNetnsBundleVm {
    hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM";
    hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID");
    guestBundle = builtins.toPath (builtins.getEnv "ARENA_GUEST_BUNDLE");
  }
'
output=$(nix eval --impure --raw --expr "($expression).outPath")
case "$output" in /nix/store/*-arena-netns-prepared) ;; *) exit 3;; esac
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
