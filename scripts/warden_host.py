"""Offline Warden workflow using Arena's existing owned-QEMU controller."""
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from k3s_execution import execution
from k3s_host import available_memory_mib

STEPS = ['baseline', 'observe', 'deny', 'existing-connection-bypasses-deny',
         'udp-not-blocked', 'outside-cgroup-not-blocked', 'unlisted-destination-not-blocked',
         'remove-restores', 'stop-term-restores', 'stop-kill-restores']
FILES = ['store.img', 'Image', 'initrd', 'kernel-params', 'toplevel', 'inputs.json']


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check_bundle(bundle):
    if bundle.is_symlink() or not bundle.is_dir(): raise ValueError('regular bundle directory required')
    sums = bundle / 'SHA256SUMS'
    if sums.is_symlink() or not sums.is_file() or sums.stat().st_size > 4096:
        raise ValueError('invalid hash manifest')
    lines = sums.read_text().splitlines()
    if len(lines) != len(FILES): raise ValueError('unexpected boot inputs')
    result = {}
    for name, line in zip(FILES, lines, strict=True):
        digest, filename = line.split()
        path = bundle / name
        if filename != name or not re.fullmatch('[0-9a-f]{64}', digest): raise ValueError('invalid hash row')
        limit = 2 * 1024**3 if name in ['store.img', 'Image', 'initrd'] else 2**20
        if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
            raise ValueError('invalid boot input: ' + name)
        if sha(path) != digest: raise ValueError('input hash differs: ' + name)
        result[name] = digest
    if not re.fullmatch('/nix/store/[0-9a-z]+-nixos-system-veil-warden-sandbox-[^/\n]+', (bundle / 'toplevel').read_text().strip()):
        raise ValueError('unexpected guest system')
    return result


def verify(root):
    def read(name):
        p = root / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 2**20: raise ValueError('invalid evidence: ' + name)
        return p.read_text()
    r = json.loads(read('warden-result.json')); stop = json.loads(read('vm-stop.json'))
    if (r.get('schema_version') != 1 or r.get('status') != 'passed'
            or r.get('scenario') != 'warden-policy' or r.get('cleanup_complete') is not True
            or read('service-result.txt').strip() != 'success'
            or stop.get('status') != 'passed' or stop.get('qemu_reaped') is not True
            or stop.get('evidence_synced') is not True or stop.get('escalated') is not False
            or stop.get('qemu_exit_code') != 0):
        raise ValueError('guest or VM lifecycle failed')
    if (not re.fullmatch('[0-9a-f-]{36}', r.get('boot_id', ''))
            or read('guest-boot-id.txt').strip() != r['boot_id']):
        raise ValueError('boot identity differs')
    steps = r.get('steps', [])
    if [s.get('id') for s in steps] != STEPS: raise ValueError('incomplete Warden phases')
    if (steps[0].get('receiver_delta') != [1, 1] or steps[1].get('mutations_rejected') is not True
            or steps[1].get('event', {}).get('decision') != 'allow'):
        raise ValueError('baseline/observe proof missing')
    proofs = steps[2].get('proofs', [])
    if len(proofs) != 2 or any(p.get('receiver_delta') != 0 or p.get('client', {}).get('errno') != errno.EPERM
            or not p.get('client', {}).get('scope', '').startswith('/warden.slice/warden-test.slice/')
            or p.get('event', {}).get('decision') != 'deny' or int(p.get('event', {}).get('policy_id', 0)) <= 0 for p in proofs):
        raise ValueError('deny proof missing')
    if any(steps[i].get('receiver_delta') != 1 for i in [3, 4, 6]): raise ValueError('limits not demonstrated')
    if (steps[5].get('client', {}).get('errno') != 0 or steps[7].get('errno') != 0
            or steps[5].get('client', {}).get('scope', '').startswith('/warden.slice/warden-test.slice/')):
        raise ValueError('outside scope/remove proof missing')
    for s, code in zip(steps[8:], [0, -signal.SIGKILL], strict=True):
        if (s.get('returncode') != code or s.get('links_restored') is not True
                or s.get('control_endpoint_closed') is not True or len(s.get('clients', [])) != 2
                or any(c.get('errno') != 0 for c in s['clients'])):
            raise ValueError('stop recovery proof missing')
    if len(r.get('monitors', [])) != 3 or any(m.get('links_restored') is not True for m in r['monitors']):
        raise ValueError('monitor lifecycle proof missing')
    return r


def workflow(uid, bundle, mode, output):
    if uid <= 0 or os.getuid() != uid: raise ValueError('ordinary matching host UID required')
    if mode not in ['daily', 'acceptance']: raise ValueError('use daily or acceptance')
    route = execution()
    expected = check_bundle(bundle)
    meta = json.loads((bundle / 'inputs.json').read_text())
    if meta.get('guest_system') != ('aarch64-linux' if route['arch'] == 'aarch64' else 'x86_64-linux'):
        raise ValueError('Warden artifact architecture differs from runtime host')
    # Existing 4 GiB VM plus a conservative 1 GiB host reserve, not a measured minimum.
    if available_memory_mib() < 5120: raise ValueError('need 5 GiB available/inactive memory')
    if output.exists() or output.is_symlink() or any(c in str(output) for c in ',\n'):
        raise ValueError('new output without comma/newline required')
    output.mkdir(mode=0o700)
    report = {'schema_version': 1, 'scenario': 'warden-policy', 'status': 'running',
              'mode': mode, 'boot_inputs': expected, 'execution': route, 'runs': []}
    code = 3
    try:
        for i in range(2 if mode == 'acceptance' else 1):
            if check_bundle(bundle) != expected: raise ValueError('bundle changed between runs')
            root = output / f'fresh-{i+1}'; root.mkdir(mode=0o700)
            env = dict(os.environ, ARENA_VM_OUT=str(root))
            console = 'ttyAMA0' if route['arch'] == 'aarch64' else 'ttyS0'
            args = ['veil-arena-vm-controller', str(root), f"qemu-system-{route['arch']}",
                    '-machine', 'virt' if route['arch'] == 'aarch64' else 'q35',
                    '-cpu', route['cpu'], '-accel', route['accelerator'], '-m', '4096', '-smp', '2',
                    '-nographic', '-monitor', 'none', '-no-reboot', '-nic', 'none',
                    '-kernel', str(bundle / 'Image'), '-initrd', str(bundle / 'initrd'),
                    '-append', (bundle / 'kernel-params').read_text().strip() + ' init=' + (bundle / 'toplevel').read_text().strip() + f'/init console={console}',
                    '-drive', f'file={bundle}/store.img,if=virtio,format=raw,readonly=on',
                    '-virtfs', f'local,path={root},security_model=none,mount_tag=out']
            (root / 'qemu-argv.json').write_text(json.dumps(args, indent=2) + '\n')
            (root / 'vm-manifest.json').write_text(json.dumps({'scenario': 'warden-policy', 'network': 'no NIC',
                'memory_mib': 4096, 'vcpus': 2, 'hostUid': uid, 'boot_inputs': expected, 'route': route}, indent=2) + '\n')
            p = subprocess.Popen(args, env=env)
            try:
                result = p.wait(timeout=660)
            except BaseException:
                if p.poll() is None:
                    p.terminate()
                    p.wait(timeout=15)
                raise
            if result != 0: raise ValueError('VM controller failed: ' + str(result))
            r = verify(root)
            if r['boot_id'] in [x['boot_id'] for x in report['runs']]: raise ValueError('boot ID reused')
            report['runs'].append({'path': root.name, 'boot_id': r['boot_id'], 'status': 'passed'})
        report['status'] = 'passed'; code = 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report['status'] = 'failed'; report['error'] = str(error)
    except KeyboardInterrupt:
        report['status'] = 'cancelled'; code = 130
    finally:
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"warden-policy {mode}: {report['status']} — {output}")
    return code


if __name__ == '__main__':
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, cancelled)
    try:
        if len(sys.argv) != 5: raise ValueError('usage: arena-warden BUNDLE daily|acceptance NEW_OUTPUT')
        sys.exit(workflow(int(sys.argv[1]), Path(sys.argv[2]).absolute(), sys.argv[3], Path(sys.argv[4]).absolute()))
    except (OSError, ValueError, KeyError) as error:
        print('Warden preflight: ' + str(error), file=sys.stderr); sys.exit(3)
