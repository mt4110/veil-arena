#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Observe only, synthetic TCP echo, inside one explicitly booted offline guest."""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback

SCOPE = '/sys/fs/cgroup/warden.slice/warden-test.slice'
CLIENT = r'''
import json,os,socket,sys,threading
from pathlib import Path
scope=Path('/proc/self/cgroup').read_text().strip().split(':',2)[2]
r={'pid':os.getpid(),'scope':scope,'cgroup_id':os.stat('/sys/fs/cgroup'+scope).st_ino,
   'family':int(sys.argv[1]),'address':sys.argv[2],'port':int(sys.argv[3]),'errno':None}
def work():
 r['tid']=threading.get_native_id()
 with socket.socket(r['family']) as s:
  s.settimeout(3)
  try:
   s.connect((r['address'],r['port']));s.sendall(b'arena-observe');assert s.recv(64)==b'arena-observe';r['errno']=0
  except OSError as e:r['errno']=e.errno
thread=threading.Thread(target=work);thread.start();thread.join()
print(json.dumps(r),flush=True)
'''


def run(*args):
    return subprocess.check_output(args, text=True, timeout=10).strip()


def bpf(*args):
    return json.loads(run('bpftool', '-j', *map(str, args)))


def one(value):
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    if not isinstance(value, dict):
        raise ValueError('one bpftool object required')
    return value


def attachments():
    return bpf('cgroup', 'show', SCOPE)


def wait(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError('bounded observation timeout')
        time.sleep(.02)


class Echo:
    def __init__(self, family, address):
        self.socket = socket.socket(family)
        self.socket.settimeout(.1)
        self.socket.bind((address, 0))
        self.socket.listen(8)
        self.family, self.address, self.port = family, address, self.socket.getsockname()[1]
        self.received, self.errors, self.done = [], [], False
        def serve():
            while not self.done:
                try:
                    c, _ = self.socket.accept()
                    with c:
                        c.settimeout(3)
                        data = c.recv(64)
                        if data != b'arena-observe':
                            raise AssertionError('unexpected fixture payload')
                        self.received.append(data.hex())
                        c.sendall(data)
                except socket.timeout:
                    pass
                except Exception as error:
                    if not self.done:
                        self.errors.append(type(error).__name__)
        self.thread = threading.Thread(target=serve, daemon=True)
        self.thread.start()

    def close(self):
        self.done = True
        self.thread.join(timeout=4)
        self.socket.close()


def read_records(path):
    text = path.read_text()
    if not text:
        return []
    # Live writer may have a partial trailing record; it must be whole on exit.
    return [json.loads(line) for line in text[:text.rfind('\n') + 1].splitlines()]


def signatures(items):
    return sorted(json.dumps({k:v for k,v in row.items() if k != 'id'}, sort_keys=True) for row in items)


def mode_maps(new_links):
    ids = set()
    for link in new_links:
        ids.update(one(bpf('prog', 'show', 'id', link['prog_id']))['map_ids'])
    maps = [one(bpf('map', 'show', 'id', number)) for number in ids]
    mode = [m for m in maps if m['name'] == 'MODE']
    deny = [m for m in maps if m['name'].startswith('DENY_DESTINAT')]
    assert len(mode) == len(deny) == 1, maps
    raw = bpf('map', 'dump', 'id', mode[0]['id'])
    empty = bpf('map', 'dump', 'id', deny[0]['id'])
    assert len(raw) == 1 and raw[0]['key'] == ['0x00'] * 4 and raw[0]['value'] == ['0x00'] * 4, raw
    assert empty == [], empty
    return {'mode': raw, 'deny': empty, 'mode_id': mode[0]['id'], 'deny_id': deny[0]['id']}


def test(binary, obj, out):
    if (os.geteuid() != 0 or 'arena.warden-observe=1' not in Path('/proc/cmdline').read_text().split()
            or Path('/etc/hostname').read_text().strip() != 'veil-warden-sandbox'):
        raise ValueError('explicit isolated observe guest required')
    out.mkdir(mode=0o755)
    result = {'schema': 'arena-warden-observe-v1', 'status': 'running',
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'kernel': os.uname().release, 'cases': [], 'drop_applied': False,
              'runtime_cli_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
              'bpf_sha256':hashlib.sha256(obj.read_bytes()).hexdigest()}
    serial = 0
    servers, process = [], None
    def save():
        (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    def attempt(server, inside=True):
        nonlocal serial
        serial += 1
        before = len(server.received)
        value = json.loads(run('systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--unit=arena-observe-client-' + str(serial),
            '--slice=warden-test.slice' if inside else '--slice=system.slice',
            'python3', '-c', CLIENT, str(server.family), server.address, str(server.port)))
        assert value['errno'] == 0, value
        wait(lambda: len(server.received) == before + 1)
        value['receiver_delta'] = len(server.received) - before
        return value
    try:
        servers = [Echo(socket.AF_INET, '127.0.0.1'), Echo(socket.AF_INET6, '::1')]
        result['baseline_clients'] = [attempt(s) for s in servers]
        save()
        for how, signum in [('term', signal.SIGTERM), ('kill', signal.SIGKILL)]:
            before_links, before_attached = bpf('link', 'show'), attachments()
            argv = [str(binary), 'connect', '--object', str(obj), '--log-format', 'json', '--interval-ms', '100']
            log, errors = out / (how + '.jsonl'), out / (how + '.stderr')
            case = {'signal': how, 'argv': argv, 'links_before': before_links,
                    'attached_before': before_attached}
            result['cases'].append(case)
            with log.open('x') as stdout, errors.open('x') as stderr:
                process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
                wait(lambda: process.poll() is not None or any(r['type'] == 'ready' for r in read_records(log)))
                assert process.poll() is None, (process.returncode, log.read_text(), errors.read_text())
                live = bpf('link', 'show')
                new = [x for x in live if x['id'] not in {b['id'] for b in before_links}]
                assert len(new) == 2 and {x['attach_type'] for x in new} == {'cgroup_inet4_connect', 'cgroup_inet6_connect'}, live
                case['links_live'] = live
                case['attached_live'] = attachments()
                case['maps'] = mode_maps(new)
                case['clients'] = [attempt(s) for s in servers]
                case['outside_client'] = attempt(servers[0], inside=False)
                wait(lambda: len([r for r in read_records(log) if r['type'] == 'event']) == 2)
                wait(lambda: any(r['type'] == 'stats' and r['attempted'] == '2' for r in read_records(log)))
                case['maps_before_stop'] = mode_maps(new)
                case['signal_sent_ns'] = time.monotonic_ns()
                process.send_signal(signum)
                case['returncode'] = process.wait(timeout=10)
                case['stopped_ns'] = time.monotonic_ns()
            assert case['returncode'] == (0 if how == 'term' else -9), case
            text = log.read_text()
            case['truncated_tail'] = text[text.rfind('\n') + 1:]
            assert how == 'kill' or not case['truncated_tail'], 'incomplete graceful log record'
            def detached():
                case['links_after'], case['attached_after'] = bpf('link', 'show'), attachments()
                # Capture both tables on every observation, even when one differs.
                save()
                return (not ({r['id'] for r in new} & {r['id'] for r in case['links_after']})
                    and not ({r['prog_id'] for r in new} & {r['id'] for r in case['attached_after']})
                    and signatures(case['attached_after']) == signatures(before_attached))
            wait(detached)
            case['restored_clients'] = [attempt(s) for s in servers]
            case['records'] = read_records(log)
            assert errors.read_text() == '', errors.read_text()
            save()
        assert all(not s.errors for s in servers)
        result['receiver_errors'] = [s.errors for s in servers]
        result['status'] = 'passed'
    except BaseException as error:
        result['status'] = 'failed'
        result['error'] = type(error).__name__ + ': ' + str(error)
        (out / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
        for server in servers:
            server.close()
        result['cleanup_complete'] = process is None or process.poll() is not None
        save()


if __name__ == '__main__':
    test(*(Path(p) for p in sys.argv[1:]))
