#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit offline-VM entry point; preparing/evaluating the flake never runs it."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from k3s_execution import execution, route as host_route
from k3s_host import available_memory_mib
from warden_host import sha, stop_controller, install_signal_handlers
from warden_preflight import read, verify

FILES = ('store.img', 'Image', 'initrd', 'kernel-params', 'toplevel', 'scenario', 'guest-system')


def check_bundle(bundle):
    if any(c in str(bundle) for c in ',\n') or bundle.is_symlink():
        raise ValueError('unsafe bundle path')
    hashes = {name: sha(bundle / name) for name in FILES
              if not (bundle / name).is_symlink() and (bundle / name).is_file()}
    expected = ''.join(hashes.get(name, '') + '  ' + name + '\n' for name in FILES)
    if len(hashes) != len(FILES) or read(bundle / 'SHA256SUMS') != expected:
        raise ValueError('bundle seal mismatch')
    if read(bundle / 'scenario') != 'warden-nonloading-v1\n':
        raise ValueError('wrong scenario; policy bundle is forbidden')
    if not re.fullmatch(r'/nix/store/[0-9a-z]{32}-nixos-system-arena-warden-preflight-[^/\n]+\n', read(bundle / 'toplevel')):
        raise ValueError('unexpected guest system')
    return hashes


def verify_stop(root):
    stop = json.loads(read(root / 'vm-stop.json'))
    if (not isinstance(stop, dict) or stop.get('status') != 'passed'
            or stop.get('qemu_reaped') is not True or stop.get('evidence_synced') is not True
            or stop.get('escalated') is not False or type(stop.get('qemu_exit_code')) is not int
            or stop['qemu_exit_code'] != 0):
        raise ValueError('VM stop evidence failed')


def verify_terminal(root):
    terminal = json.loads(read(root / 'terminal.json'))
    if (not isinstance(terminal, dict)
            or terminal.get('schema') != 'arena-warden-nonloading-terminal-v1'
            or terminal.get('service_result') != 'success'
            or terminal.get('diagnostic_directory_present') is not True
            or type(terminal.get('writer_uid')) is not int
            or type(terminal.get('output_owner_uid')) is not int
            or terminal['writer_uid'] != terminal['output_owner_uid']
            or terminal['writer_uid'] != root.stat().st_uid
            or read(root / 'service-result.txt') != 'success\n'
            or read(root / 'nonloading/service-result.txt') != 'success\n'):
        raise ValueError('incomplete or mismatched terminal evidence')


def workflow(uid, bundle, output):
    if uid <= 0 or os.getuid() != uid:
        raise ValueError('matching ordinary host UID required')
    expected = check_bundle(bundle)
    if output.exists() or output.is_symlink() or any(c in str(output) for c in ',\n'):
        raise ValueError('new evidence path required')
    if available_memory_mib() < 5120:
        raise ValueError('need 5 GiB available/inactive memory')
    # Calls KVM API only on an explicitly requested run, never during preparation.
    route = host_route()
    if read(bundle / 'guest-system').strip() != ('aarch64-linux' if route['arch'] == 'aarch64' else 'x86_64-linux'):
        raise ValueError("guest architecture differs from host execution route")
    route = execution()
    output.mkdir(mode=0o700)
    report = {'schema': 'arena-warden-nonloading-host-v1', 'status': 'running', 'boot_inputs': expected,
              'execution': route, 'runtime_protection_verified': False}
    code = 3
    try:
        console = 'ttyAMA0' if route['arch'] == 'aarch64' else 'ttyS0'
        args = ['veil-arena-vm-controller', str(output), f"qemu-system-{route['arch']}",
                '-machine', 'virt' if route['arch'] == 'aarch64' else 'q35',
                '-cpu', route['cpu'], '-accel', route['accelerator'], '-m', '4096', '-smp', '2',
                '-nographic', '-monitor', 'none', '-no-reboot', '-nic', 'none',
                '-kernel', str(bundle / 'Image'), '-initrd', str(bundle / 'initrd'),
                '-append', read(bundle / 'kernel-params').strip() + ' init=' + read(bundle / 'toplevel').strip() + f'/init console={console}',
                '-drive', f'file={bundle}/store.img,if=virtio,format=raw,readonly=on',
                '-virtfs', f'local,path={output},security_model=none,mount_tag=out']
        (output / 'qemu-argv.json').write_text(json.dumps(args, indent=2) + '\n')
        process = subprocess.Popen(args, env=dict(os.environ, ARENA_VM_OUT=str(output)), start_new_session=True)
        try:
            native = process.wait(timeout=150)
        except BaseException:
            report['cleanup_escalated'] = stop_controller(process)
            raise
        if native != 0:
            raise ValueError('VM controller failed')
        verify_stop(output)
        verify_terminal(output)
        result = verify(output / 'nonloading')
        if check_bundle(bundle) != expected:
            raise ValueError('bundle changed during execution')
        report['boot_id'] = result['boot_id']
        report['status'] = 'passed-nonloading'
        code = 0
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as error:
        report['status'] = 'failed'
        report['error'] = str(error)
    except KeyboardInterrupt:
        report['status'] = 'cancelled'
        code = 130
    finally:
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    return code


if __name__ == '__main__':
    install_signal_handlers()
    if len(sys.argv) != 4:
        raise SystemExit('usage: arena-warden-preflight HOST_UID BUNDLE NEW_OUTPUT')
    raise SystemExit(workflow(int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])))
