#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Export root-owned guest results as the evidence owner's UID; marker last."""
import json
import os
from pathlib import Path
import re
import sys
from warden_preflight_terminal import durable_new


def finish(source, out, result):
    if not re.fullmatch('[a-z][a-z0-9-]{0,63}', result):
        raise ValueError('invalid service result')
    if out.is_symlink() or not out.is_dir() or os.geteuid() != out.stat().st_uid:
        raise ValueError('matching evidence owner required')
    present = source.is_dir() and not source.is_symlink()
    hashes = {}
    if present:
        import hashlib
        dest = out / 'observe'
        dest.mkdir(mode=0o700)
        for path in sorted(source.iterdir()):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2**20:
                raise ValueError('unexpected proof file')
            data = path.read_bytes()
            with (dest / path.name).open('xb') as target:
                target.write(data); target.flush(); os.fsync(target.fileno())
            hashes[path.name] = hashlib.sha256(data).hexdigest()
    durable_new(out / 'terminal.json', json.dumps({'schema':'arena-warden-observe-terminal-v1',
        'service_result':result, 'proof_directory_present':present, 'files':hashes,
        'writer_uid':os.geteuid(), 'output_owner_uid':out.stat().st_uid}, indent=2) + '\n')
    durable_new(out / 'service-result.txt', result + '\n')


if __name__ == '__main__':
    finish(Path(sys.argv[1]), Path(sys.argv[2]), os.environ.get('SERVICE_RESULT', ''))
