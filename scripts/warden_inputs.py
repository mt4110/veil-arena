"""Snapshot owned Warden artifacts only after checking their archived source."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot(repo, build, dest):
    if dest.exists() or dest.is_symlink():
        raise ValueError('input snapshot already exists')
    sources = sorted([*repo.glob('crates/**/*.rs'), *repo.glob('crates/**/Cargo.toml'),
                      repo / 'Cargo.toml', repo / 'Cargo.lock',
                      repo / 'tests/vm/cases/connect_monitor.py',
                      repo / 'tests/vm/cases/connect_policy.py'])
    with tarfile.open(build / 'source.tar.gz', 'r:gz') as archive:
        archived = {m.name for m in archive.getmembers() if m.isfile() and
                    ((m.name.startswith('crates/') and (m.name.endswith('.rs') or m.name.endswith('/Cargo.toml')))
                     or m.name in {'Cargo.toml', 'Cargo.lock', 'tests/vm/cases/connect_monitor.py',
                                   'tests/vm/cases/connect_policy.py'})}
        if archived != {p.relative_to(repo).as_posix() for p in sources}:
            raise ValueError('archived source file inventory differs from checkout')
        for path in sources:
            relative = path.relative_to(repo).as_posix()
            if path.is_symlink() or not path.is_file():
                raise ValueError('source must be a regular file: ' + relative)
            member = archive.getmember(relative)
            if not member.isfile() or member.size > 2**20:
                raise ValueError('invalid archived source: ' + relative)
            if archive.extractfile(member).read() != path.read_bytes():
                raise ValueError('artifact archive differs from checkout: ' + relative)
    selected = {'veil-warden': build / 'veil-warden',
                'veil-warden-ebpf': build / 'veil-warden-ebpf',
                'connect_monitor.py': repo / 'tests/vm/cases/connect_monitor.py',
                'connect_policy.py': repo / 'tests/vm/cases/connect_policy.py'}
    for name, path in selected.items():
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 32 * 2**20:
            raise ValueError('invalid selected artifact: ' + name)
    header = selected['veil-warden'].read_bytes()[:20]
    if header[:6] != b'\x7fELF\x02\x01' or int.from_bytes(header[18:20], 'little') != 183:
        raise ValueError('this initial adapter requires an owned aarch64 Linux ELF')
    dest.mkdir(mode=0o700)
    for name, path in selected.items():
        shutil.copyfile(path, dest / name)
        (dest / name).chmod(0o400)
    record = {'schema_version': 1, 'scenario': 'warden-policy', 'guest_system': 'aarch64-linux',
              'warden_revision': subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip(),
              'archive_sha256': sha(build / 'source.tar.gz'),
              'sources': {p.relative_to(repo).as_posix(): sha(p) for p in sources},
              'files': {name: sha(dest / name) for name in selected}}
    (dest / 'inputs.json').write_text(json.dumps(record, indent=2) + '\n')
    (dest / 'inputs.json').chmod(0o400)
    return record


if __name__ == '__main__':
    snapshot(*(Path(p).resolve() for p in sys.argv[1:]))
