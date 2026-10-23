#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Seal the previously built CLI/BPF pair after checking its source snapshot."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(repo, build, dest):
    if dest.exists() or dest.is_symlink():
        raise ValueError('new input directory required')
    expected = json.loads((build/'source-hashes.json').read_text())
    selected = sorted(n for n in expected if n in ['Cargo.toml','Cargo.lock']
                      or (n.startswith('crates/') and (n.endswith('.rs') or n.endswith('/Cargo.toml'))))
    with tarfile.open(build/'source.tar') as archive:
        for name in selected:
            path = repo/name
            if (path.is_symlink() or sha(path) != expected[name]
                    or archive.extractfile('source/'+name).read() != path.read_bytes()):
                raise ValueError('runtime source differs: '+name)
    actual = sorted([p.relative_to(repo).as_posix() for p in repo.glob('crates/**/*.rs')]
                    + [p.relative_to(repo).as_posix() for p in repo.glob('crates/**/Cargo.toml')]
                    + ['Cargo.toml','Cargo.lock'])
    if actual != selected:
        raise ValueError('runtime source inventory differs')
    spec = importlib.util.spec_from_file_location('verify_package',repo/'scripts/verify-package.py')
    verifier = importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    manifest = verifier.verify(build/'package')
    if manifest['system'] != 'aarch64-linux':
        raise ValueError('ARM64 artifact required')
    dest.mkdir(mode=0o700)
    files={}
    for name,source in [('veil-warden',build/'package/bin/veil-warden'),('veil-warden-ebpf',build/'package/lib/veil-warden/veil-warden-ebpf')]:
        shutil.copyfile(source,dest/name);(dest/name).chmod(0o400);files[name]=sha(dest/name)
    meta={'schema_version':1,'scenario':'warden-observe-json-v1','guest_system':'aarch64-linux',
          'warden_revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
          'archive_sha256':sha(build/'source.tar'),'sources':{n:expected[n] for n in selected},
          'files':files,'package_manifest':manifest}
    (dest/'inputs.json').write_text(json.dumps(meta,indent=2)+'\n')
    return meta


if __name__=='__main__':
    snapshot(*(Path(p).resolve() for p in sys.argv[1:]))
