#!/bin/sh
# Test fixture only: mirror save()'s pending-file / hard-link publication.
set -eu
sleep 3.2
printf ready > "$1.pending"
if [ "${2:-}" = gated ]; then
    printf 'staged\n'
    read -r publish
fi
ln "$1.pending" "$1"
# Owned::stop closes stdin to request a clean fixture shutdown.
read -r stop || :
