"""Offline Warden workflow using Arena's existing owned-QEMU controller."""
import errno
import hashlib
import ipaddress
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


def positive_policy_id(value):
    if isinstance(value, str) and re.fullmatch('[0-9]{1,10}', value):
        value = int(value)
    return type(value) is int and 0 < value <= 0xffffffff


def stop_controller(process):
    # Popen owns a new session/group; until reaped, its PID cannot be reused.
    # Repeated terminal signals must not interrupt this bounded cleanup.
    handlers = {s: signal.signal(s, signal.SIG_IGN) for s in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]}
    try:
        if process.poll() is not None: return False
        process.terminate()
        try:
            process.wait(timeout=15)
            return False
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            return True
    finally:
        for signum, handler in handlers.items(): signal.signal(signum, handler)


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


def evidence_object(value):
    if not isinstance(value, dict): raise ValueError('evidence object required')
    return value


def evidence_objects(value):
    if not isinstance(value, list): raise ValueError('evidence array required')
    for item in value: evidence_object(item)
    return value


def client_endpoint(client, protocol='tcp'):
    pid, port = client.get('pid'), client.get('port')
    if (not positive_policy_id(pid) or type(pid) is not int
            or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(client.get('address'), str) or client.get('protocol') != protocol):
        return None
    try:
        address = ipaddress.ip_address(client['address'])
    except ValueError:
        return None
    family = 2 if address.version == 4 else 10
    if type(client.get('family')) is not int or client['family'] != family: return None
    destination = f'{address}:{port}' if address.version == 4 else f'[{address}]:{port}'
    return family, destination


def event_matches_client(client, event):
    endpoint = client_endpoint(client)
    return bool(endpoint and event.get('family') == ('4' if endpoint[0] == 2 else '6')
                and event.get('tgid') == str(client['pid']) and event.get('destination') == endpoint[1]
                and event.get('protocol') == 'tcp')


def recovery_matches(clients, proofs):
    return (len(clients) == 2 and all(integer_equal(c.get('errno'), 0) for c in clients)
            and {client_endpoint(c) for c in clients} == {client_endpoint(p['client']) for p in proofs}
            and all(isinstance(c.get('scope'), str)
                    and c['scope'].startswith('/warden.slice/warden-test.slice/') for c in clients))


def recovery_connection(before, after, rules_added_ns, endpoints):
    # A single socket connected before the policy and exchanged again afterward.
    identity = ['pid', 'scope', 'family', 'address', 'port', 'protocol',
                'local_address', 'local_port', 'connected_ns']
    return (before.get('family') == 2 and client_endpoint(before) in endpoints
            and client_endpoint(before) == client_endpoint(after)
            and integer_equal(before.get('errno'), 0) and integer_equal(after.get('errno'), 0)
            and isinstance(before.get('scope'), str) and before['scope'].startswith('/warden.slice/warden-test.slice/')
            and all(type(before.get(k)) is type(after.get(k)) and before.get(k) == after.get(k) for k in identity)
            and before.get('local_address') == '127.0.0.1'
            and type(before.get('local_port')) is int and 1 <= before['local_port'] <= 65535
            and integer_equal(before.get('sequence'), 1) and integer_equal(after.get('sequence'), 2)
            and all(type(v) is int and v > 0 for v in [before.get('connected_ns'),
                before.get('exchanged_ns'), rules_added_ns, after.get('exchanged_ns')])
            and before['connected_ns'] <= before['exchanged_ns'] < rules_added_ns < after['exchanged_ns'])


def integer_equal(value, expected):
    return type(value) is int and value == expected


def verify(root):
    def read(name):
        p = root / name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 2**20: raise ValueError('invalid evidence: ' + name)
        return p.read_text()
    r = evidence_object(json.loads(read('warden-result.json')))
    stop = evidence_object(json.loads(read('vm-stop.json')))
    # Check accessed containers before semantic checks: malformed JSON evidence
    # must become a reported verification failure, never an uncaught traceback.
    steps = evidence_objects(r.get('steps'))
    monitors = evidence_objects(r.get('monitors'))
    if not isinstance(r.get('boot_id'), str): raise ValueError('invalid boot identity')
    if [s.get('id') for s in steps] != STEPS: raise ValueError('incomplete Warden phases')
    evidence_objects(steps[0].get('clients'))
    for i in [4, 6]: evidence_object(steps[i].get('client'))
    for key in ['before', 'after']: evidence_object(steps[3].get(key))
    evidence_object(steps[1].get('event'))
    evidence_object(steps[1].get('client'))
    evidence_objects(steps[7].get('clients'))
    proofs = evidence_objects(steps[2].get('proofs'))
    for proof in proofs:
        client = evidence_object(proof.get('client'))
        evidence_object(proof.get('event'))
        if not isinstance(client.get('scope'), str): raise ValueError('invalid client scope')
    outside = evidence_object(steps[5].get('client'))
    if not isinstance(outside.get('scope'), str): raise ValueError('invalid outside scope')
    for step in steps[8:]: evidence_objects(step.get('clients'))
    if (not integer_equal(r.get('schema_version'), 3) or r.get('status') != 'passed'
            or r.get('scenario') != 'warden-policy' or r.get('cleanup_complete') is not True
            or read('service-result.txt').strip() != 'success'
            or stop.get('status') != 'passed' or stop.get('qemu_reaped') is not True
            or stop.get('evidence_synced') is not True or stop.get('escalated') is not False
            or not integer_equal(stop.get('qemu_exit_code'), 0)):
        raise ValueError('guest or VM lifecycle failed')
    if (not re.fullmatch('[0-9a-f-]{36}', r.get('boot_id', ''))
            or read('guest-boot-id.txt').strip() != r['boot_id']):
        raise ValueError('boot identity differs')
    if (steps[0].get('receiver_delta') != [1, 1]
            or any(not integer_equal(v, 1) for v in steps[0]['receiver_delta'])
            or steps[1].get('mutations_rejected') is not True
            or steps[1]['event'].get('decision') != 'allow'
            or not integer_equal(steps[1].get('errno'), 0)
            or not integer_equal(steps[1]['client'].get('errno'), 0)
            or not event_matches_client(steps[1]['client'], steps[1]['event'])
            or not isinstance(steps[1]['client'].get('scope'), str)
            or not steps[1]['client']['scope'].startswith('/warden.slice/warden-test.slice/')):
        raise ValueError('baseline/observe proof missing')
    if len(proofs) != 2 or any(not integer_equal(p.get('receiver_delta'), 0)
            or not integer_equal(p['client'].get('errno'), errno.EPERM)
            or not p.get('client', {}).get('scope', '').startswith('/warden.slice/warden-test.slice/')
            or p.get('event', {}).get('decision') != 'deny' or not positive_policy_id(p.get('event', {}).get('policy_id'))
            or not event_matches_client(p['client'], p['event']) for p in proofs):
        raise ValueError('deny proof missing')
    # These are Linux guest AF_INET/AF_INET6 values, not host socket constants
    # (macOS has a different AF_INET6 number). Events encode IP version 4/6.
    families = [(p['client'].get('family'), p['event'].get('family')) for p in proofs]
    if families.count((2, '4')) != 1 or families.count((10, '6')) != 1:
        raise ValueError('deny requires one matching IPv4 and IPv6 proof')
    if client_endpoint(steps[1]['client']) not in {client_endpoint(p['client']) for p in proofs}:
        raise ValueError('observe destination differs')
    if any(not integer_equal(steps[i].get('receiver_delta'), 1) for i in [3, 4, 6]): raise ValueError('limits not demonstrated')
    if (not integer_equal(steps[5]['client'].get('errno'), 0) or not integer_equal(steps[7].get('errno'), 0)
            or steps[5].get('client', {}).get('scope', '').startswith('/warden.slice/warden-test.slice/')):
        raise ValueError('outside scope/remove proof missing')
    if (steps[0].get('families') != [4, 6] or not integer_equal(steps[0].get('errno'), 0)
            or not recovery_matches(steps[0]['clients'], proofs)):
        raise ValueError('baseline clients missing')
    endpoints = {client_endpoint(p['client']) for p in proofs}
    outside = steps[5]['client']
    if client_endpoint(outside) not in endpoints or not outside['scope'].startswith('/'):
        raise ValueError('outside client destination differs')
    udp, unlisted = steps[4]['client'], steps[6]['client']
    ipv4_port = next(p['client']['port'] for p in proofs if p['client']['family'] == 2)
    if (not integer_equal(udp.get('errno'), 0) or client_endpoint(udp, 'udp') != (2, f'127.0.0.1:{ipv4_port}')
            or not isinstance(udp.get('scope'), str) or not udp['scope'].startswith('/warden.slice/warden-test.slice/')):
        raise ValueError('UDP proof differs')
    if (not integer_equal(unlisted.get('errno'), 0) or client_endpoint(unlisted) in endpoints
            or client_endpoint(unlisted) != (2, f"127.0.0.2:{ipv4_port}")
            or not isinstance(unlisted.get('scope'), str) or not unlisted['scope'].startswith('/warden.slice/warden-test.slice/')):
        raise ValueError('unlisted destination proof differs')
    existing = steps[3]; before, after = existing['before'], existing['after']
    if (not recovery_connection(before, after, existing.get('rules_added_ns'), endpoints)):
        raise ValueError('established connection identity missing')
    if steps[7].get('families') != [4, 6] or not recovery_matches(steps[7]['clients'], proofs):
        raise ValueError('remove recovery proof missing')
    for s, code in zip(steps[8:], [0, -signal.SIGKILL], strict=True):
        if (not integer_equal(s.get('returncode'), code) or s.get('links_restored') is not True
                or s.get('control_endpoint_closed') is not True
                or not recovery_matches(s['clients'], proofs)):
            raise ValueError('stop recovery proof missing')
    if len(monitors) != 3 or any(m.get('links_restored') is not True for m in monitors):
        raise ValueError('monitor lifecycle proof missing')
    return r


def workflow(uid, bundle, mode, output):
    if uid <= 0 or os.getuid() != uid: raise ValueError('ordinary matching host UID required')
    if mode not in ['daily', 'acceptance']: raise ValueError('use daily or acceptance')
    if any(c in str(bundle) for c in ',\n'): raise ValueError('bundle path without comma/newline required')
    route = execution()
    expected = check_bundle(bundle)
    meta = evidence_object(json.loads((bundle / 'inputs.json').read_text()))
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
            p = subprocess.Popen(args, env=env, start_new_session=True)
            try:
                result = p.wait(timeout=660)
            except BaseException:
                escalated = stop_controller(p)
                report['interruption_cleanup'] = {'controller_reaped': True, 'escalated': escalated}
                if escalated:
                    raise ValueError('controller cleanup timed out; owned process group killed; evidence may be incomplete')
                raise
            if result != 0: raise ValueError('VM controller failed: ' + str(result))
            r = verify(root)
            if r['boot_id'] in [x['boot_id'] for x in report['runs']]: raise ValueError('boot ID reused')
            report['runs'].append({'path': root.name, 'boot_id': r['boot_id'], 'status': 'passed'})
        if check_bundle(bundle) != expected: raise ValueError('bundle changed during final run')
        report['status'] = 'passed'; code = 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report['status'] = 'failed'; report['error'] = str(error)
    except KeyboardInterrupt:
        report['status'] = 'cancelled'; code = 130
    finally:
        (output / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f"warden-policy {mode}: {report['status']} — {output}")
    return code


def install_signal_handlers():
    def cancelled(_signum, _frame):
        raise KeyboardInterrupt
    return {s: signal.signal(s, cancelled) for s in [signal.SIGINT, signal.SIGTERM, signal.SIGHUP]}


if __name__ == '__main__':
    install_signal_handlers()
    try:
        if len(sys.argv) != 5: raise ValueError('usage: arena-warden BUNDLE daily|acceptance NEW_OUTPUT')
        sys.exit(workflow(int(sys.argv[1]), Path(sys.argv[2]).absolute(), sys.argv[3], Path(sys.argv[4]).absolute()))
    except (OSError, ValueError, KeyError) as error:
        print('Warden preflight: ' + str(error), file=sys.stderr); sys.exit(3)
