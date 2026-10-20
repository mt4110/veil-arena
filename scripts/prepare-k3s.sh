#!/usr/bin/env bash
# Preparation only. Uses the pinned store and keeps immutable roots per result.
set -euo pipefail
if [ "$#" -ne 1 ]; then echo 'usage: bash scripts/prepare-k3s.sh guest|runner|f2-server|f2-agent|f2-runner' >&2; exit 2; fi
export ARENA_FLAKE="$(cd "$(dirname "$0")/.." && pwd -P)"
export ARENA_HOST_UID="$(id -u)"
export ARENA_HOST_SYSTEM="$(nix eval --impure --raw --expr builtins.currentSystem)"
if [ "$ARENA_HOST_UID" -eq 0 ]; then echo 'run as an ordinary user' >&2; exit 3; fi
case "$1" in
  guest|f2-server|f2-agent)
    case "$ARENA_HOST_SYSTEM" in aarch64-linux|x86_64-linux) ;; *) echo 'prepare guest on a matching Linux builder; run on Mac using the copied bundle' >&2; exit 3;; esac
    expression='let f = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (builtins.path { path = builtins.toPath (builtins.getEnv "ARENA_FLAKE"); filter = path: type: !(builtins.elem (builtins.baseNameOf path) [ ".git" "artifacts" "target" "__pycache__" ]); }))); in f.lib.mkK3sGuest { hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); }'
    if [ "$1" != guest ]; then
      export ARENA_F2_ROLE="${1#f2-}"
      expression="${expression%\}} f2Role = builtins.getEnv \"ARENA_F2_ROLE\"; }"
    fi
    ;;
  runner|f2-runner)
    expression='let f = builtins.getFlake (builtins.unsafeDiscardStringContext (toString (builtins.path { path = builtins.toPath (builtins.getEnv "ARENA_FLAKE"); filter = path: type: !(builtins.elem (builtins.baseNameOf path) [ ".git" "artifacts" "target" "__pycache__" ]); }))); in f.lib.mkK3sRunner { hostSystem = builtins.getEnv "ARENA_HOST_SYSTEM"; hostUid = builtins.fromJSON (builtins.getEnv "ARENA_HOST_UID"); }'
    if [ "$1" = f2-runner ]; then expression="${expression/mkK3sRunner/mkK3sF2Runner}"; fi
    ;;
  *) echo 'use guest or runner' >&2; exit 2;;
esac
output=$(nix eval --impure --raw --expr "($expression).outPath")
umask 077
cache="$ARENA_FLAKE/artifacts/prepared-k3s"
mkdir -p "$cache"
root="$cache/$(basename "$output")"
if [ -e "$root" ] || [ -L "$root" ]; then
  if [ ! -L "$root" ] || [ "$(readlink "$root")" != "$output" ]; then
    echo 'prepared root mismatch' >&2; exit 3
  fi
  nix build --impure --no-link --print-out-paths --expr "$expression"
else
  nix build --impure --out-link "$root" --print-out-paths --expr "$expression"
fi
