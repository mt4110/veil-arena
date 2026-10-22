#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Publish the fixed VM-controller marker last, including startup failures."""
import json
import os
from pathlib import Path
import re
import sys


def durable_new(path, text):
    with path.open('x') as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def finish(root, result):
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,63}', result):
        raise ValueError('invalid systemd service result')
    if root.is_symlink() or not root.is_dir():
        raise ValueError('dedicated evidence directory required')
    owner = root.stat().st_uid
    writer = os.geteuid()
    if writer != owner:
        raise ValueError('evidence owner differs from writer UID')
    child = root / 'nonloading'
    if child.is_symlink():
        raise ValueError('diagnostic directory must not be a symlink')
    present = child.is_dir()
    if present:
        durable_new(child / 'service-result.txt', result + '\n')
    durable_new(root / 'terminal.json', json.dumps({
        'schema': 'arena-warden-nonloading-terminal-v1',
        'service_result': result, 'diagnostic_directory_present': present,
        'writer_uid': writer, 'output_owner_uid': owner,
    }, indent=2) + '\n')
    # vm-controller observes this fixed location. Publish only after the
    # nested marker and terminal record have been closed and synced.
    durable_new(root / 'service-result.txt', result + '\n')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: warden_preflight_terminal.py EVIDENCE_ROOT')
    result = os.environ.get('SERVICE_RESULT', '')
    print('nonloading terminal: ' + result, file=sys.stderr, flush=True)
    finish(Path(sys.argv[1]), result)
