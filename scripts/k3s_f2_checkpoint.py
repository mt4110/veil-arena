"""F2 server stop after two Ready nodes, before Pods/traffic; peer remains owned."""
import hashlib
import json
import os
from pathlib import Path
import re
import time

from k3s_bundle import bounded
from k3s_checkpoint import observe_stop, publish

CHECKPOINT = 'f2-nodes-ready-before-pods'
CAPABILITY = {'schema_version': 1, 'checkpoint': CHECKPOINT}


def capability(inputs):
    for key, name in [('f2_checkpoint_sha256', 'k3s_f2_checkpoint.py'),
                      ('f2_observer_sha256', 'k3s_checkpoint.py')]:
        if inputs.get(key) != hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest():
            raise ValueError('matching F2 checkpoint-capable guest required')


def configuration(out):
    p = out / 'lifecycle-control.json'
    if not os.path.lexists(p):
        return None
    row = json.loads(bounded(p))
    if not isinstance(row, dict) or type(row.get('schema_version')) is not int or row not in [dict(CAPABILITY, action=a) for a in ['cancel', 'timeout']]:
        raise ValueError('invalid F2 lifecycle control')
    return row


def agent_ready(out, result):
    publish(out / 'agent-ready.json', {'schema_version': 1, 'boot_id': result['boot_id']})


def peer(run, inputs):
    """The atomic marker follows the saved agent image/ready proof."""
    out = run / 'agent'
    if not os.path.lexists(out / 'agent-ready.json'):
        return None
    gate = json.loads(bounded(out / 'agent-ready.json'))
    row = json.loads(bounded(out / 'result.json'))
    if not isinstance(row, dict):
        raise ValueError('invalid peer result shape')
    boot = row.get('boot_id', '')
    if (gate != {'schema_version': 1, 'boot_id': boot}
            or not re.fullmatch(r'[0-9a-f-]{36}', boot)
            or row.get('scenario') != 'k3s-f2' or row.get('role') != 'agent'
            or row.get('schema_version') != 1 or row.get('inputs') != inputs['agent']
            or row.get('status') != 'environment_error' or row.get('exit_code') != 3
            or row.get('checks') != [{'stage': 'agent_ready', 'status': 'passed',
                                     'image_config_digest': inputs['agent']['fixture_config_digest']}]
            or os.path.lexists(out / 'service-result.txt')
            or os.path.lexists(run / 'private-control/finish')):
        raise ValueError('peer agent readiness/identity differs')
    return boot


def checkpoint(out, control, result, inputs, end, save):
    capability(inputs)
    result.update(stage=CHECKPOINT, lifecycle_fixture=configuration(out))
    save()
    # Reuse the original Node deadline. No new timeout allowance.
    while not os.path.lexists(control / 'peer-ready.json'):
        if time.monotonic() >= end:
            raise ValueError('peer not ready within original Node deadline')
        time.sleep(.025)
    peer_gate = json.loads(bounded(control / 'peer-ready.json'))
    if (not isinstance(peer_gate, dict) or set(peer_gate) != {'schema_version', 'boot_id'}
            or peer_gate['schema_version'] != 1
            or not isinstance(peer_gate['boot_id'], str)
            or not re.fullmatch(r'[0-9a-f-]{36}', peer_gate['boot_id'])):
        raise ValueError('invalid peer gate')
    observe_stop(out, result, CHECKPOINT,
                 {'traffic_not_started': True, 'peer_boot_id': peer_gate['boot_id']})


def proof(run, inputs):
    from k3s_guest import ready
    server = run / 'server'
    control = configuration(server)
    partial = json.loads(bounded(server / 'result.json'))
    state = json.loads(bounded(server / 'checkpoint-state.json'))
    gate = json.loads(bounded(server / 'watchdog-ready.json'))
    nodes = json.loads(bounded(server / 'cluster-nodes.json'))
    if not all(isinstance(x, dict) for x in [partial, state, gate]):
        raise ValueError('invalid F2 checkpoint evidence shape')
    peer_boot = peer(run, inputs)
    if (control is None or peer_boot is None or not isinstance(nodes, list)
            or len(nodes) != 2 or not ready(nodes)
            or {n['metadata']['name'] for n in nodes} != {'arena-k3s-server', 'arena-k3s-agent'}
            or any(n['status']['nodeInfo'].get('kubeletVersion') != 'v'+inputs['server']['versions']['k3s']
                   or n['status']['nodeInfo'].get('containerRuntimeVersion') != 'containerd://'+inputs['server']['versions']['containerd'] for n in nodes)
            or partial.get('schema_version') != 1 or partial.get('scenario') != 'k3s-f2'
            or partial.get('role') != 'server' or partial.get('inputs') != inputs['server']
            or partial.get('lifecycle_fixture') != control or partial.get('stage') != CHECKPOINT
            or partial.get('status') != 'environment_error' or partial.get('exit_code') != 3
            or partial.get('checks') != [{'stage': 'nodes', 'status': 'passed',
                                         'names': ['arena-k3s-agent', 'arena-k3s-server']}]):
        raise ValueError('F2 partial Ready-node proof differs')
    boot = partial.get('boot_id', '')
    parent, observer = gate.get('batch_pid'), gate.get('observer_pid')
    if (not re.fullmatch(r'[0-9a-f-]{36}', boot) or boot == peer_boot
            or any(type(p) is not int or not 1 < p <= 2147483647 for p in (parent, observer)) or parent == observer
            or gate.get('schema_version') != 1 or gate.get('scenario') != 'guest-runner-stopped'
            or gate.get('checkpoint') != CHECKPOINT or gate.get('state') != 'T' or gate.get('signal') != 19
            or state.get('schema_version') != 1 or state.get('checkpoint') != CHECKPOINT
            or gate.get('boot_id') != boot or state.get('boot_id') != boot
            or state.get('runner_pid') != parent or state.get('observer_pid') != observer
            or state.get('traffic_not_started') is not True or state.get('peer_boot_id') != peer_boot
            or not isinstance(state.get('proc_status'), str)
            or f'Pid:\t{parent}' not in state['proc_status'].splitlines()
            or not any(s.startswith('State:\tT') for s in state['proc_status'].splitlines())
            or any(os.path.lexists(server / p) for p in ['checkpoint-error.json', 'service-result.txt',
                    'arena-f2-pods.json', 'service-state.json', 'receiver-log-source.json'])):
        raise ValueError('F2 stopped runner/peer/traffic proof differs')
    return {'server': boot, 'agent': peer_boot}


def recovered(stop):
    return (isinstance(stop, dict) and stop.get('schema_version') == 2
            and stop.get('status') == 'environment_error' and stop.get('lifecycle_exit_code') == 3
            and stop.get('qemu_reaped') is True and stop.get('qemu_exit_code') == 0
            and stop.get('escalated') is False and stop.get('evidence_synced') is True
            and stop.get('evidence_synced_before_stop') is True and stop.get('sync_error') is None)


def verify(run, inputs, action, exits, cancel_sent):
    for role in ['server', 'agent']:
        stop = json.loads(bounded(run / role / 'vm-stop.json'))
        if not isinstance(stop, dict):
            raise ValueError('invalid F2 lifecycle evidence shape')
        if not recovered(stop) or exits.get(role) != 3:
            raise ValueError(role+': F2 fixture lifecycle recovery failed')
        expected = 'watchdog' if role == 'server' and action == 'timeout' else 'cancelled'
        if stop.get('reason') != expected:
            raise ValueError(role+': unexpected stop reason')
        if role == 'server' and action == 'timeout':
            if (cancel_sent or stop.get('watchdog_fixture') is not True or stop.get('checkpoint_seen') is not True
                    or stop.get('startup_timeout_ms') != 120000 or stop.get('timeout_after_checkpoint_ms') != 2000
                    or type(stop.get('checkpoint_elapsed_ms')) is not int or stop['checkpoint_elapsed_ms'] < 2000):
                raise ValueError('F2 watchdog deadline not proven')
        elif stop.get('watchdog_fixture') is not False:
            raise ValueError('unexpected watchdog fixture on cancelled peer')
    if action not in ['cancel', 'timeout'] or (action == 'cancel' and not cancel_sent):
        raise ValueError('F2 cancellation/action not proven')
    if configuration(run/'server')['action'] != action:
        raise ValueError('F2 action differs')
    return proof(run, inputs)
