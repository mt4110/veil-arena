#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Non-loading guest scenario. Never starts/stops a target or loads BPF.

Run only inside the separately constrained offline guest, not on a real host.
The package verifier is supplied from the matching Warden source snapshot.
"""
import importlib.util
import json
from pathlib import Path
import re
import platform
import subprocess
import sys

CASES = (
    ('running', ['--service', 'arena-fixture.service', '--lang', 'en'], 1, 'candidate'),
    ('missing', ['--service', 'arena-missing.service', '--lang', 'en'], 1, 'unknown'),
    ('protected', ['--service', 'ssh.service'], 2, None),
    ('mixed', ['--service', 'arena-fixture.service', '--cgroup', '/system.slice/arena-fixture.service'], 2, None),
)
LIMIT = 1024 * 1024


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > LIMIT:
        raise ValueError('missing/oversize/nonregular evidence')
    return path.read_text()


def verify_case(record, stdout, trace):
    expected = next((c for c in CASES if c[0] == record.get('id')), None)
    if expected is None or type(record.get('returncode')) is not int or record['returncode'] != expected[2]:
        raise ValueError('unexpected native exit')
    if not re.search(r'(?:^|\n)(?:[0-9]+\s+)?\+\+\+ exited with ' + str(expected[2]) + r' \+\+\+\s*$', trace) or re.search(r'\bbpf\(', trace) or any(family != 'AF_UNIX' for family in re.findall(r'\bAF_[A-Z0-9_]+\b', trace)):
        raise ValueError('missing trace or forbidden BPF/network operation')
    if expected[3] is None:
        if stdout:
            raise ValueError('invalid input reached diagnostic')
    else:
        lines = stdout.splitlines()
        for line in ['schema=veil-warden-preflight-v1', 'read_only=true', 'bpf_loaded=false',
                     'apply_supported=false', 'deployment_ready=false', f'service_identity={expected[3]}']:
            if lines.count(line) != 1:
                raise ValueError('diagnostic contract mismatch')
        if expected[3] == 'candidate' and 'canonical_unit=arena-fixture.service' not in lines:
            raise ValueError('wrong service identity')


def verify(root):
    result = json.loads(read(root / 'preflight-result.json'))
    if (not isinstance(result, dict) or result.get('schema') != 'arena-warden-nonloading-v1'
            or result.get('status') != 'passed-nonloading'
            or result.get('runtime_protection_verified') is not False
            or not isinstance(result.get('boot_id'), str)
            or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', result['boot_id'])
            or result.get('boot_id') != read(root / 'guest-boot-id.txt').strip()
            or not isinstance(result.get('cases'), list)
            or [c.get('id') for c in result['cases'] if isinstance(c, dict)] != [c[0] for c in CASES]
            or len(result['cases']) != len(CASES)):
        raise ValueError('invalid nonloading result')
    manifest = result.get('package_manifest')
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'veil-warden-package-v1'
            or manifest.get('system') not in ('aarch64-linux', 'x86_64-linux')
            or manifest.get('deployment_ready') is not False
            or not isinstance(manifest.get('sha256'), dict)
            or set(manifest['sha256']) != {'bin/veil-warden', 'lib/veil-warden/veil-warden-ebpf'}
            or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in manifest['sha256'].values())):
        raise ValueError('missing package identity')
    for case in result['cases']:
        name = case['id']
        verify_case(case, read(root / f'{name}.stdout'), read(root / f'{name}.trace'))
        read(root / f'{name}.stderr')
    if read(root / 'service-result.txt').strip() != 'success':
        raise ValueError('guest service did not finish successfully')
    return result


def run(package, verifier, output):
    if Path('/etc/hostname').read_text().strip() != 'arena-warden-preflight':
        raise ValueError('dedicated nonloading guest required')
    # Existing directories/evidence are never overwritten.
    if not output.is_dir() or any(output.iterdir()):
        raise ValueError('empty dedicated evidence directory required')
    spec = importlib.util.spec_from_file_location('warden_package_verifier', verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manifest = module.verify(package)
    if manifest["system"] != {"aarch64": "aarch64-linux", "x86_64": "x86_64-linux"}.get(platform.machine()):
        raise ValueError("package architecture differs from guest")
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    (output / 'guest-boot-id.txt').write_text(boot + '\n')
    result = {'schema': 'arena-warden-nonloading-v1', 'status': 'running', 'boot_id': boot,
              'runtime_protection_verified': False, 'package_manifest': manifest, 'cases': []}
    try:
        binary = package / 'bin/veil-warden'
        for name, args, _, _ in CASES:
            trace = output / f'{name}.trace'
            with (output / f'{name}.stdout').open('x') as out, (output / f'{name}.stderr').open('x') as err:
                native = subprocess.run(['strace', '-f', '-q', '-s', '256', '-e', 'trace=bpf,network',
                                         '-o', str(trace), str(binary), 'diagnose', *args],
                                        stdout=out, stderr=err, timeout=15, check=False)
            case = {'id': name, 'returncode': native.returncode}
            result['cases'].append(case)
            verify_case(case, read(output / f'{name}.stdout'), read(trace))
        # Recheck both immutable ELF artifacts after all cases.
        if module.verify(package) != manifest:
            raise ValueError('package changed')
        result['status'] = 'passed-nonloading'
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        result['status'] = 'failed'
        result['error'] = str(error)
    finally:
        (output / 'preflight-result.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['status'] == 'passed-nonloading' else 3


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'verify':
        verify(Path(sys.argv[2]))
    elif len(sys.argv) == 5 and sys.argv[1] == 'guest':
        raise SystemExit(run(*map(Path, sys.argv[2:])))
    else:
        raise SystemExit('usage: warden_preflight.py verify EVIDENCE | guest PACKAGE VERIFIER EMPTY_OUTPUT')
