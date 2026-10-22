"""Synthetic Warden acceptance in an offline Arena guest; no exploit payloads."""
import errno
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
import traceback


# One socket for both exchanges; report its identity before and after rules.
PERSISTENT_CLIENT = r'''
import json,os,socket,sys,time
from pathlib import Path
with socket.socket() as s:
    s.settimeout(3)
    s.connect(('127.0.0.1',int(sys.argv[1])))
    connected_ns=time.monotonic_ns()
    identity={'pid':os.getpid(),'scope':Path('/proc/self/cgroup').read_text().strip().split(':',2)[2],
              'errno':0,'family':2,'address':'127.0.0.1','port':int(sys.argv[1]),'protocol':'tcp',
              'local_address':s.getsockname()[0],'local_port':s.getsockname()[1],
              'connected_ns':connected_ns}
    for sequence,line in enumerate(sys.stdin,1):
        s.sendall(b'warden-m3');assert s.recv(64)==b'warden-m3'
        print(json.dumps(dict(identity,sequence=sequence,exchanged_ns=time.monotonic_ns())),flush=True)
'''


def test(binary, inputs, out):
    sys.path.insert(0, str(inputs))
    from connect_monitor import Monitor, links, attached, signatures
    from connect_policy import Server, client, control, assert_deny
    if os.geteuid() != 0 or 'arena.warden=1' not in Path('/proc/cmdline').read_text().split():
        raise ValueError('dedicated Arena guest required')
    if Path('/etc/hostname').read_text().strip() != 'veil-warden-sandbox':
        raise ValueError('Warden sandbox hostname required')
    record = {'schema_version': 3, 'scenario': 'warden-policy', 'status': 'running',
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'kernel': os.uname().release, 'steps': [], 'clients': [], 'monitors': [],
              'limits': ['new TCP only', 'exact destination denylist', 'no payload inspection',
                         'not a PHP/CVE test', 'not production acceptance']}
    def save():
        (out / 'warden-result.json').write_text(json.dumps(record, indent=2) + '\n')
    def step(name, **proof):
        record['steps'].append({'id': name, **proof}); save()
    def attempt(server, **kwargs):
        c = client(server.family, server.address, server.port, **kwargs)
        c['protocol'] = kwargs.get('kind', 'tcp')
        record['clients'].append(c)
        return c
    def eventually(predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= deadline: raise AssertionError('receiver evidence timeout')
            time.sleep(.02)
    monitors, servers, persistent = [], [], None
    def monitor(*extra):
        m = Monitor(str(binary), str(inputs / 'veil-warden-ebpf'), extra=list(extra))
        monitors.append(m)
        return m
    def stop(m, how):
        m.process.send_signal(signal.SIGTERM if how == 'term' else signal.SIGKILL)
        code = m.process.wait(timeout=10); m.reader.join(timeout=2)
        assert code == (0 if how == 'term' else -signal.SIGKILL)
        assert {x['id'] for x in links()} == {x['id'] for x in m.before}
        assert signatures(attached()) == signatures(m.baseline)
        record['monitors'].append({'exit': how, 'returncode': code,
                                   'links_restored': True, 'lines': m.lines,
                                   'stderr': m.process.stderr.read()})
        absent = subprocess.run([str(binary), 'policy', 'list'], capture_output=True, text=True, timeout=5)
        assert absent.returncode != 0
        return code
    try:
        s4 = Server(socket.AF_INET, '127.0.0.1')
        servers.append(s4)
        s6 = Server(socket.AF_INET6, '::1'); servers.append(s6)
        udp = Server(socket.AF_INET, '127.0.0.1', socket.SOCK_DGRAM, s4.port); servers.append(udp)
        baseline = []
        for s in [s4, s6]:
            before = len(s.received)
            c = attempt(s); assert c['errno'] == 0
            eventually(lambda: len(s.received) == before + 1)
            baseline.append(c)
        step('baseline', families=[4, 6], errno=0, clients=baseline, receiver_delta=[1, 1])
        m = monitor()
        c = attempt(s4); assert c['errno'] == 0
        m.wait(lambda: any(int(e['tgid']) == c['pid'] for e in m.events()))
        event = next(e for e in m.events() if int(e['tgid']) == c['pid'])
        assert event['decision'] == 'allow'
        control(str(binary), 'add', s4.address, s4.port, ok=False)
        step('observe', client=c, errno=c['errno'], event=event, mutations_rejected=True)
        stop(m, 'term')
        m = monitor('--enforce')
        persistent = subprocess.Popen(['systemd-run', '--quiet', '--wait', '--pipe', '--collect',
            '--unit=arena-warden-persistent', '--slice=warden-test.slice',
            'python3', '-u', '-c', PERSISTENT_CLIENT, str(s4.port)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def exchange():
            persistent.stdin.write('send\n'); persistent.stdin.flush()
            assert select.select([persistent.stdout], [], [], 3)[0]
            return json.loads(persistent.stdout.readline())
        initial = exchange()
        for s in [s4, s6]: control(str(binary), 'add', s.address, s.port)
        rules_added_ns = time.monotonic_ns()
        denied = []
        for s in [s4, s6]:
            before = len(s.received)
            c = attempt(s); event = assert_deny(m, c)
            time.sleep(.1); assert len(s.received) == before
            denied.append({'client': c, 'event': event, 'receiver_delta': 0})
        step('deny', proofs=denied)
        before = len(s4.received); ongoing = exchange()
        eventually(lambda: len(s4.received) == before + 1)
        step('existing-connection-bypasses-deny', receiver_delta=1, before=initial, after=ongoing, rules_added_ns=rules_added_ns)
        c = attempt(udp, kind='udp'); assert c['errno'] == 0
        eventually(lambda: udp.received == ['warden-m3'])
        step('udp-not-blocked', client=c, receiver_delta=1)
        c = attempt(s4, inside=False); assert c['errno'] == 0
        step('outside-cgroup-not-blocked', client=c)
        other = Server(socket.AF_INET, '127.0.0.2', port=s4.port); servers.append(other)
        c = attempt(other); assert c['errno'] == 0
        eventually(lambda: other.received == ['warden-m3'])
        step('unlisted-destination-not-blocked', client=c, receiver_delta=1)
        restored = []
        for s in [s4, s6]:
            control(str(binary), 'remove', s.address, s.port)
            c = attempt(s); assert c['errno'] == 0
            restored.append(c)
        step('remove-restores', errno=0, families=[4, 6], clients=restored)
        persistent.stdin.close(); assert persistent.wait(timeout=5) == 0
        for how in ['term', 'kill']:
            for s in [s4, s6]:
                control(str(binary), 'add', s.address, s.port)
                assert_deny(m, attempt(s))
            code = stop(m, how)
            restored = [attempt(s) for s in [s4, s6]]
            assert all(c['errno'] == 0 for c in restored)
            step('stop-' + how + '-restores', returncode=code, clients=restored,
                 links_restored=True, control_endpoint_closed=True)
            if how == 'term': m = monitor('--enforce')
        assert all(not s.errors for s in servers)
        record['status'] = 'passed'
    except Exception as error:
        record['status'] = 'failed'; record['error'] = str(error)
        (out / 'failure.txt').write_text(traceback.format_exc())
        raise
    finally:
        if persistent is not None and persistent.poll() is None:
            persistent.terminate(); persistent.wait(timeout=5)
        for m in monitors:
            m.cleanup()
        for s in servers: s.close()
        record['cleanup_complete'] = all(m.process.poll() is not None for m in monitors)
        save()


if __name__ == '__main__':
    test(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
