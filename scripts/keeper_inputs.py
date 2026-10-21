#!/usr/bin/env python3
"""Copy only the two F0 hash-pinned Keeper inputs; never edit the owner checkout."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def snapshot(source, destination, contract):
    if destination.exists() or destination.is_symlink():
        raise ValueError('snapshot destination must be new')
    payloads = {}
    for name, expected in contract['keeper']['input_sha256'].items():
        path = source / name
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Keeper input must be a regular file: ' + name)
            data = stream.read(1024**2 + 1)
        if len(data) > 1024**2 or hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Keeper input hash/size differs: ' + name)
        payloads[name] = data
    destination.mkdir(mode=0o700)
    for name, data in payloads.items():
        target = destination / Path(name).name
        with target.open('xb') as stream:
            stream.write(data)
        target.chmod(0o444)
    destination.chmod(0o555)
    return destination


if __name__ == '__main__':
    try:
        if len(sys.argv) != 3:
            raise ValueError('usage: keeper_inputs.py KEEPER_SOURCE NEW_SNAPSHOT')
        contract = json.loads((Path(__file__).resolve().parents[1] / 'docs/contracts/f0-inputs.json').read_text())
        print(snapshot(Path(sys.argv[1]).absolute(), Path(sys.argv[2]).absolute(), contract))
    except (OSError, ValueError) as error:
        print('Keeper preparation: ' + str(error), file=sys.stderr)
        sys.exit(3)
