#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One-VM observe acceptance. Never selects the policy guest or enables deny."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from k3s_execution import route as host_route, execution
from k3s_host import available_memory_mib
from warden_host import check_bundle as sealed_bundle, stop_controller, install_signal_handlers
from warden_preflight_host import verify_stop

SCENARIO = 'warden-observe-json-v1'
SCOPE = '/warden.slice/warden-test.slice'


def read(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2**20:
        raise ValueError('invalid bounded evidence: ' + str(path))
    return path.read_text()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def check_bundle(bundle):
    require(not any(c in str(bundle) for c in ',\n'), 'safe bundle path required')
    hashes = sealed_bundle(bundle)
    meta = json.loads(read(bundle / 'inputs.json'))
    require(meta.get('scenario') == SCENARIO and meta.get('guest_system') == 'aarch64-linux', 'observe-only bundle required')
    for digest in [meta.get('runtime_cli_sha256'), meta.get('files', {}).get('veil-warden-ebpf')]:
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'missing runtime hashes')
    return hashes, meta


def client(value, inside=True):
    require(isinstance(value, dict), 'client must be an object')
    require(type(value.get('errno')) is int and value['errno'] == 0
            and type(value.get('receiver_delta')) is int and value['receiver_delta'] == 1, 'missing TCP echo proof')
    for key in ['pid', 'tid', 'cgroup_id', 'port']:
        require(type(value.get(key)) is int and value[key] > 0, 'invalid client identity')
    require(value['port'] <= 65535 and value['pid'] != value['tid'], 'invalid threaded client')
    require(type(value.get('family')) is int and value['family'] in [2, 10], 'invalid family')
    require(value.get('address') == ('127.0.0.1' if value['family'] == 2 else '::1'), 'non-loopback client')
    require(isinstance(value.get('scope'), str)
            and value['scope'].startswith(SCOPE + '/') == inside, 'client cgroup differs')


def match_event(c, e):
    return (e.get('type') == 'event' and type(e.get('pid')) is int and e['pid'] == c['pid']
            and type(e.get('tid')) is int and e['tid'] == c['tid']
            and e.get('cgroup_id') == str(c['cgroup_id'])
            and type(e.get('family')) is int and e['family'] == (4 if c['family'] == 2 else 6)
            and e.get('destination_ip') == c['address'] and type(e.get('destination_port')) is int
            and e['destination_port'] == c['port'] and e.get('protocol') == 'tcp'
            and e.get('observation') == 'connect_attempt' and e.get('decision') == 'allow'
            and type(e.get('policy_id')) is int and e['policy_id'] == 0
            and e.get('connection_result') == 'unknown')


def verify_case(case):
    require(isinstance(case, dict), 'case object required')
    how = case.get('signal')
    require(how in ['term', 'kill'] and type(case.get('returncode')) is int
            and case['returncode'] == (0 if how == 'term' else -9), 'signal exit differs')
    argv = case.get('argv')
    require(isinstance(argv, list) and len(argv) == 8 and argv[1:2] == ['connect']
            and argv[2] == '--object' and argv[4:] == ['--log-format', 'json', '--interval-ms', '100'], 'observe argv differs')
    for key in ['links_before','links_live','links_after','attached_before','attached_live','attached_after']:
        require(isinstance(case.get(key), list), 'missing link proof')
    from warden_observe_guest import signatures, same_links, programs_released
    require(signatures(case['attached_before']) == signatures(case['attached_after']), 'baseline cgroup semantics differ')
    old = {row['id'] for row in case['links_before']}
    new = [row for row in case['links_live'] if row['id'] not in old]
    require(len(new) == 2 and {row.get('attach_type') for row in new} == {'cgroup_inet4_connect','cgroup_inet6_connect'}, 'both live links required')
    require(not ({r['id'] for r in new} & {r['id'] for r in case['links_after']})
            and not ({r['prog_id'] for r in new} & {r['id'] for r in case['attached_after']}), 'owned links/programs remain attached')
    require(same_links(case['links_before'], case['links_after']), 'baseline global links differ')
    require(programs_released(case.get('programs_before'), case.get('programs_live'),
                case.get('programs_after'), [r['prog_id'] for r in new]), 'global program release proof missing or owned programs remain loaded')
    prior = {row['id'] for row in case['attached_before']}
    attached = {row['id'] for row in case['attached_live'] if row['id'] not in prior}
    require(attached == {row['prog_id'] for row in new}, 'live cgroup/prog identity differs')
    for key in ['maps', 'maps_before_stop']:
        maps = case.get(key, {})
        require(isinstance(maps, dict) and maps.get('deny') == [] and isinstance(maps.get('mode'), list)
                and len(maps['mode']) == 1 and isinstance(maps['mode'][0], dict) and maps['mode'][0].get('key') == ['0x00'] * 4
                and maps['mode'][0].get('value') == ['0x00'] * 4, 'mode is not observe or deny map populated')
    require(case['maps'] == case['maps_before_stop'], 'mode/map identity changed')
    clients = case.get('clients'); restored = case.get('restored_clients')
    require(isinstance(clients, list) and isinstance(restored, list) and len(clients) == len(restored) == 2, 'both families required')
    for values in [clients, restored]:
        for c in values: client(c)
        require({c['family'] for c in values} == {2, 10}, 'IPv4/IPv6 required')
    require({(c['address'],c['port']) for c in clients} == {(c['address'],c['port']) for c in restored}, 'recovery destination differs')
    client(case.get('outside_client'), inside=False)
    records = case.get('records')
    require(isinstance(records, list) and len(records) >= 5
            and all(isinstance(record, dict) for record in records), 'incomplete or malformed JSON lifecycle')
    require(records[0].get('type') == 'startup' and records[0].get('ready') is False, 'startup absent')
    require(records[1].get('type') == 'ready' and records[1].get('ready') is True
            and type(records[1].get('links')) is int and records[1]['links'] == 2, 'ready absent')
    instance = records[0].get('instance_id')
    require(isinstance(instance, str) and instance, 'instance absent')
    previous_elapsed = 0
    for index, record in enumerate(records):
        require(type(record.get('schema_version')) is int and record['schema_version'] == 1
                and record.get('instance_id') == instance and record.get('mode') == 'observe'
                and record.get('scope') == SCOPE and record.get('sequence') == str(index), 'JSON envelope differs')
        require(index < 2 or record.get('type') not in ['startup','ready'], 'repeated lifecycle record')
        require(isinstance(record.get('elapsed_ns'), str)
                and re.fullmatch('[0-9]+', record['elapsed_ns']), 'monotonic clock absent')
        elapsed = int(record['elapsed_ns'])
        require(elapsed >= previous_elapsed, 'elapsed clock moved backwards')
        previous_elapsed = elapsed
        require(record.get('type') in ['startup','ready','event','stats','shutdown']
                and not any(key in record for key in ['argv','environ','payload','comm']), 'unexpected or sensitive record')
    events = [r for r in records if r['type'] == 'event']
    require(len(events) == 2 and all(sum(match_event(c,e) for e in events) == 1 for c in clients), 'TCP events differ')
    stats = [r for r in records if r['type'] == 'stats']
    require(stats and stats[-1].get('attempted') == '2' and stats[-1].get('emitted') == '2'
            and stats[-1].get('decoded') == '2', 'final observed counts differ')
    for row in stats:
        require(all(row.get(key) == '0' for key in ['denied','ring_dropped','decode_errors','queue_dropped','stats_dropped']), 'denial or missing events')
    final = [r for r in records if r['type'] == 'shutdown']
    if how == 'term':
        require(stats[-1].get('final') is True and len(final) == 1 and records[-1] == final[0]
                and final[0].get('success') is True and final[0].get('ready') is False
                and final[0].get('attachment_state') == 'owned_links_released'
                and final[0].get('final_stats_available') is True, 'graceful final evidence missing')
    else:
        require(not final and not any(r.get('final') is True for r in stats), 'SIGKILL cannot produce graceful completion')
    require(type(case.get('signal_sent_ns')) is int and type(case.get('stopped_ns')) is int
            and 0 < case['signal_sent_ns'] <= case['stopped_ns'], 'stop timing absent')


def verify(root, meta):
    terminal = json.loads(read(root / 'terminal.json'))
    require(terminal.get('schema') == 'arena-warden-observe-terminal-v1'
            and terminal.get('service_result') == 'success' and terminal.get('proof_directory_present') is True
            and type(terminal.get('writer_uid')) is int and type(terminal.get('output_owner_uid')) is int
            and terminal['writer_uid'] == terminal['output_owner_uid'] == root.stat().st_uid
            and read(root / 'service-result.txt') == 'success\n', 'terminal/UID proof differs')
    require(set(terminal.get('files', {})) == {'result.json','term.jsonl','term.stderr','kill.jsonl','kill.stderr'}, 'proof inventory differs')
    for name, digest in terminal['files'].items():
        require('/' not in name and name not in ['.','..'], 'invalid exported name')
        require(hashlib.sha256(read(root / 'observe' / name).encode()).hexdigest() == digest, 'proof hash differs')
    result = json.loads(read(root / 'observe/result.json'))
    require(result.get('schema') == 'arena-warden-observe-v1' and result.get('status') == 'passed'
            and result.get('cleanup_complete') is True and result.get('drop_applied') is False, 'guest incomplete')
    require(isinstance(result.get('boot_id'), str) and re.fullmatch('[0-9a-f-]{36}', result['boot_id']), 'boot identity absent')
    require(result.get('runtime_cli_sha256') == meta['runtime_cli_sha256']
            and result.get('bpf_sha256') == meta['files']['veil-warden-ebpf'], 'runtime artifacts differ')
    require(len(result.get('baseline_clients', [])) == 2, 'baseline absent')
    for c in result['baseline_clients']: client(c)
    require({c['family'] for c in result['baseline_clients']} == {2,10}, 'baseline families differ')
    require([c.get('signal') for c in result.get('cases', [])] == ['term','kill'], 'signal cases incomplete')
    for case in result['cases']:
        verify_case(case)
        text = read(root / 'observe' / (case['signal'] + '.jsonl'))
        boundary = text.rfind('\n') + 1
        require([json.loads(line) for line in text[:boundary].splitlines()] == case['records']
                and text[boundary:] == case.get('truncated_tail', '')
                and (case['signal'] == 'kill' or boundary == len(text)), 'raw JSON differs')
        require(read(root / 'observe' / (case['signal'] + '.stderr')) == '', 'stderr nonempty')
    require(result['cases'][0]['records'][0]['instance_id'] != result['cases'][1]['records'][0]['instance_id'], 'monitor instance reused')
    require(result.get('receiver_errors') == [[],[]], 'receiver failed')
    verify_stop(root)
    return result


def preflight(uid, bundle, output):
    require(uid > 0 and os.getuid() == uid, 'ordinary matching host UID required')
    hashes, meta = check_bundle(bundle)
    require(not output.exists() and not output.is_symlink() and not any(c in str(output) for c in ',\n'), 'fresh safe evidence directory required')
    require(available_memory_mib() >= 5120, '5 GiB available/inactive memory required')
    route = host_route()
    require(route['arch'] == 'aarch64', 'this bundle is ARM64 only')
    return hashes, meta, route


def workflow(uid, bundle, output):
    expected, meta, route = preflight(uid, bundle, output)
    route = execution()
    output.mkdir(mode=0o700)
    report = {'scenario':SCENARIO, 'status':'running', 'boot_inputs':expected, 'execution':route}
    try:
        args = ['veil-arena-vm-controller',str(output),'qemu-system-aarch64','-machine','virt',
                '-cpu',route['cpu'],'-accel',route['accelerator'],'-m','4096','-smp','2','-nographic',
                '-monitor','none','-no-reboot','-nic','none','-kernel',str(bundle/'Image'),
                '-initrd',str(bundle/'initrd'),'-append',read(bundle/'kernel-params').strip()+' init='+read(bundle/'toplevel').strip()+'/init console=ttyAMA0',
                '-drive',f'file={bundle}/store.img,if=virtio,format=raw,readonly=on',
                '-virtfs',f'local,path={output},security_model=none,mount_tag=out']
        (output/'qemu-argv.json').write_text(json.dumps(args,indent=2)+'\n')
        process = subprocess.Popen(args,env=dict(os.environ,ARENA_VM_OUT=str(output)),start_new_session=True)
        try:
            code = process.wait(timeout=180)
        except BaseException:
            report['cleanup_escalated'] = stop_controller(process)
            raise
        require(code == 0, 'VM controller failed')
        result = verify(output, meta)
        require(check_bundle(bundle)[0] == expected, 'bundle changed')
        report.update(status='passed',boot_id=result['boot_id'])
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError, subprocess.SubprocessError) as error:
        report.update(status='failed',error=str(error))
        return 3
    except KeyboardInterrupt:
        report['status'] = 'cancelled'
        return 130
    finally:
        (output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__ == '__main__':
    install_signal_handlers()
    require(len(sys.argv) == 4, 'usage: arena-warden-observe UID BUNDLE NEW_OUTPUT')
    raise SystemExit(workflow(int(sys.argv[1]),Path(sys.argv[2]).absolute(),Path(sys.argv[3]).absolute()))
