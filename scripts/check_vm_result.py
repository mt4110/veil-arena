"""Check bounded guest evidence; a clean QEMU exit alone is not a passing test."""
import json
import hashlib
import errno
import struct
import pathlib
import sys

NETWORK_LABELS = ["parent-links-before", "parent-routes-before", "ip-version", "nsenter-version", "kernel",
                  "app-veth", "app-peer", "server-veth", "server-peer"]
for role, interfaces in (("app", ("app0",)), ("router", ("rt-app", "rt-server")), ("server", ("server0",))):
    NETWORK_LABELS.append(f"{role}-loopback")
    for interface in interfaces:
        NETWORK_LABELS.extend((f"{role}-{interface}-address", f"{role}-{interface}-up"))
NETWORK_LABELS.extend(("app-route", "server-route", "router-forwarding-on"))
for role in ("app", "router", "server"):
    NETWORK_LABELS.extend((f"{role}-addresses", f"{role}-routes"))
NETWORK_LABELS.extend(("app-gateway", "server-gateway", "app-server-ping", "server-app-ping"))
for phase in ("initial", "recovery"):
    if phase == "recovery":
        NETWORK_LABELS.extend(("router-forwarding-off", "forwarding-off-app-server", "forwarding-off-server-app",
                               "router-forwarding-restore", "recovered-ping"))
    NETWORK_LABELS.extend(f"{phase}-{direction}-{protocol}" for direction in ("app-server", "server-app")
                         for protocol in ("tcp", "udp"))
NETWORK_LABELS.extend(("app-neighbors", "router-neighbors", "server-neighbors", "parent-links-after", "parent-routes-after"))
TC_LABELS = ["tc-filters-before", "tc-plain-send", "tc-filters-attached", "tc-pass-send",
             "tc-count-send", "tc-drop-send", "tc-filters-detached", "tc-detached-send"]

BATCH_CASES = (("normal-001", 0), ("route-fault-001", 1), ("normal-002", 0), ("tc-ingress-001", 0),
               ("tc-drop-disabled-001", 1), ("tc-wrong-hook-001", 3), ("tc-receiver-stopped-001", 3), ("bpf-test-run-001", 0), ("bpf-load-failure-001", 3), ("batch-cancel-001", 0), ("tc-after-cancel-001", 0), ("batch-timeout-001", 0), ("tc-restored-001", 0))
FAULTS = {"tc-drop-disabled-001":"drop-disabled", "tc-wrong-hook-001":"wrong-hook", "tc-receiver-stopped-001":"receiver-stopped"}


def check(root: pathlib.Path, scenario: str = "blackhole-offline") -> int:
    if scenario not in {"blackhole-offline", "netns-baseline", "netns-batch", "netns-watchdog"}:
        return 3
    if scenario == "netns-watchdog":
        return check_watchdog(root)
    if scenario == "netns-batch":
        return check_batch(root)
    reports = ([root / "netns-results/result.json"] if scenario == "netns-baseline"
               else list((root / "results").glob("blackhole-offline-*/result.json")))
    if len(reports) != 1:
        print("VM result missing or ambiguous", file=sys.stderr)
        return 3
    path = reports[0]
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        print("VM result is not a bounded regular file", file=sys.stderr)
        return 3
    service_path = root / "service-result.txt"
    if (service_path.is_symlink() or not service_path.is_file()
            or service_path.stat().st_size > 1024):
        print("Guest service evidence missing or invalid", file=sys.stderr)
        return 3
    try:
        result = json.loads(path.read_text())
        service = service_path.read_text().strip()
    except (OSError, ValueError):
        print("VM evidence cannot be read", file=sys.stderr)
        return 3
    if not isinstance(result, dict):
        print("VM result must be an object", file=sys.stderr)
        return 3
    if scenario == "netns-baseline":
        return check_netns(result, service)
    if result.get("status") == "failed":
        print("Guest suite detected a regression", file=sys.stderr)
        return 1
    if service != "success":
        print(f"Guest service failed: {service}", file=sys.stderr)
        return 3
    if result.get("status") != "passed":
        print(f"Guest suite: {result.get('status')}", file=sys.stderr)
        return 3
    steps = result.get("steps", [])
    if (not isinstance(steps, list) or len(steps) != 18
            or any(not isinstance(step, dict) or step.get("status") != "passed" for step in steps)
            or result.get("source_unchanged") is not True
            or result.get("binary_unchanged") is not True):
        print("Guest pass evidence incomplete", file=sys.stderr)
        return 3
    print(f"Guest offline suite passed: {path}")
    return 0


def read_bounded(path: pathlib.Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise ValueError("invalid bounded report")
    result = json.loads(path.read_text())
    if not isinstance(result, dict):
        raise ValueError("report must be an object")
    return result


def check_batch(root: pathlib.Path) -> int:
    batch_root = root / "netns-batch"
    try:
        if batch_root.is_symlink() or not batch_root.is_dir():
            return 3
        summary = read_bounded(batch_root / "result.json")
        service = root / "service-result.txt"
        if service.is_symlink() or not service.is_file() or service.stat().st_size > 1024:
            return 3
        if summary.get("status") != "passed":
            return 1 if summary.get("status") == "failed" else 3
        cases = summary.get("cases")
        if (summary.get("schema_version") != 6 or summary.get("scenario") != "netns-batch"
                or summary.get("exit_code") != 0 or summary.get("vm_boots") != 1
                or service.read_text().strip() != "success" or not isinstance(cases, list) or len(cases) != len(BATCH_CASES)):
            return 3
        for case, (case_id, expected) in zip(cases, BATCH_CASES):
            if not isinstance(case, dict) or case.get("id") != case_id or case.get("status") != "passed":
                return 3
            execution = case.get("execution")
            if (case.get("report") != f"{case_id}/result.json" or case.get("expected_exit_code") != expected
                    or not isinstance(execution, dict) or execution.get("exit_code") != expected
                    or execution.get("signal") is not None or execution.get("output_complete") is not True
                    or execution.get("log_truncated") is not False
                    or execution.get("status") not in {"passed", "failed"}):
                return 3
            directory = batch_root / case_id
            if directory.is_symlink() or not directory.is_dir():
                return 3
            result = read_bounded(directory / "result.json")
            tc_case = case_id.startswith("tc-")
            fault = FAULTS.get(case_id)
            if case_id == "bpf-test-run-001":
                if check_test_run(result, directory) != 0:
                    return 3
            elif case_id == "bpf-load-failure-001":
                if check_load_failure(result, directory) != 0:
                    return 3
            elif case_id.startswith("batch-"):
                if check_interruption(result, directory, "cancelled" if case_id == "batch-cancel-001" else "timeout") != 0:
                    return 3
            elif fault:
                if check_tc_fault(result, read_bounded(directory / "tc-result.json"), fault) != 0:
                    return 3
            else:
                if check_netns(result, "success" if expected == 0 else "exit-code", tc_case) != expected:
                    return 3
                if tc_case and check_tc(read_bounded(directory / "tc-result.json")) != 0:
                    return 3
            if case_id == "route-fault-001":
                steps = result.get("steps")
                if not isinstance(steps, list) or len(steps) != 33 or any(not isinstance(s, dict) for s in steps):
                    return 3
                failures = [s for s in steps if s.get("status") != "passed"]
                if (len(failures) != 1 or failures[0].get("id") != "031-app-server-ping"
                        or failures[0].get("exit_code") != 1 or failures[0].get("expected_exit_code") != 0):
                    return 3
                if not all(any(s.get("id") == label and s.get("status") == "passed" for s in steps)
                           for label in ("029-app-gateway", "030-server-gateway", "032-parent-links-after", "033-parent-routes-after")):
                    return 3
                topology = read_bounded(directory / "topology.json")
                if topology.get("omit_server_route") is not True:
                    return 3
        print("Batch passed: thirteen cases including cancellation, timeout and restored TC; one VM boot")
        return 0
    except (OSError, ValueError, TypeError):
        print("Batch evidence missing or malformed", file=sys.stderr)
        return 3


def check_netns(result: dict, service: str, tc_case: bool = False) -> int:
    labels_contract = NETWORK_LABELS[:-5] + TC_LABELS + NETWORK_LABELS[-5:] if tc_case else NETWORK_LABELS
    cleanup = result.get("cleanup")
    if (result.get("schema_version") != 1 or result.get("scenario") != "netns-baseline"
            or not isinstance(cleanup, dict)
            or cleanup.get("helpers_reaped") is not True
            or cleanup.get("owned_namespace_references") != []
            or cleanup.get("parent_network_unchanged") is not True
            or cleanup.get("errors") != []):
        print("Guest network cleanup evidence incomplete", file=sys.stderr)
        return 3
    if result.get("status") == "failed":
        print("Guest network baseline detected a failure", file=sys.stderr)
        return 1
    steps = result.get("steps")
    if (service != "success" or result.get("status") != "passed"
            or (tc_case and result.get("tc_reference") is not True)
            or not isinstance(steps, list) or len(steps) != len(labels_contract)
            or any(not isinstance(step, dict) or step.get("status") != "passed"
                   or not isinstance(step.get("id"), str)
                   or type(step.get("exit_code")) is not int
                   or type(step.get("expected_exit_code")) is not int
                   or step.get("exit_code") != step.get("expected_exit_code")
                   or step.get("signal") is not None
                   or step.get("output_complete") is not True
                   or step.get("log_truncated") is not False for step in steps)):
        print("Guest network pass evidence incomplete", file=sys.stderr)
        return 3
    labels = [step.get("id", "")[4:] for step in steps]
    if ([step["id"] for step in steps]
            != [f"{i+1:03}-{label}" for i, label in enumerate(labels_contract)]):
        print("Guest network required checks missing", file=sys.stderr)
        return 3
    if any(step["expected_exit_code"] != (1 if label.startswith("forwarding-off-") else 0)
           for label, step in zip(labels, steps)):
        print("Guest network expectations differ from contract", file=sys.stderr)
        return 3
    print("Guest IPv4 ICMP/TCP/UDP baseline and lifetime cleanup passed")
    return 0


def check_tc(result: dict) -> int:
    return _validate_tc(result, None)


def check_tc_fault(network: dict, result: dict, fault: str) -> int:
    """Only the exact injected cause and complete cleanup make an expected failure acceptable."""
    expected = 1 if fault == "drop-disabled" else 3
    status = "failed" if expected == 1 else "environment_error"
    try:
        cleanup = network["cleanup"]
        prefix = {"drop-disabled":TC_LABELS[:6], "wrong-hook":TC_LABELS[:3]+["tc-fault-egress"],
                  "receiver-stopped":TC_LABELS[:5]}[fault]
        labels = NETWORK_LABELS[:-5] + prefix + NETWORK_LABELS[-2:]
        steps = network["steps"]
        if (network.get("schema_version") != 1 or network.get("scenario") != "netns-baseline"
                or network.get("status") != status or network.get("tc_reference") is not True
                or network.get("tc_fault") != fault or network.get("detail") != result.get("detail")
                or cleanup != {"helpers_reaped":True,"owned_namespace_references":[],"parent_network_unchanged":True,"errors":[]}
                or len(steps) != len(labels)):
            return 3
        for i, (step, label) in enumerate(zip(steps, labels)):
            code = 1 if label.startswith("forwarding-off-") else 0
            if (step.get("id") != f"{i+1:03}-{label}" or step.get("status") != "passed"
                    or step.get("exit_code") != code or step.get("expected_exit_code") != code
                    or step.get("signal") is not None or step.get("output_complete") is not True
                    or step.get("log_truncated") is not False):
                return 3
        return _validate_tc(result, fault)
    except (KeyError, TypeError, ValueError, AttributeError):
        return 3


def _validate_tc(result: dict, fault: str | None) -> int:
    """A missing positive control or missing kernel drop count cannot prove drop."""
    try:
        attachment = result["attachment"]
        if (result.get("schema_version") != 1 or result.get("scenario") != "tc-ingress"
                or result.get("status") != (result.get("status") if fault=="interrupt-hold" else "failed" if fault=="drop-disabled" else "environment_error" if fault else "passed")
                or result.get("cleanup") != {"detached": True, "program_unloaded": True, "maps_unloaded": True, "pinned": False}
                or attachment.get("interface") != "rt-app" or attachment.get("hook") != ("egress" if fault=="wrong-hook" else "ingress")
                or attachment.get("attach") != "netlink-clsact" or attachment.get("aya") != "0.13.1"
                or attachment.get("priority") != 49152 or attachment.get("handle") != 1
                or attachment.get("pinned") is not False
                or type(attachment.get("program_id")) is not int or attachment["program_id"] <= 0
                or not isinstance(attachment.get("map_ids"), list) or len(attachment["map_ids"]) != 2
                or any(type(i) is not int or i <= 0 for i in attachment["map_ids"])
                or len(attachment["object_sha256"]) != 64 or any(c not in "0123456789abcdef" for c in attachment["object_sha256"])):
            return 3
        if not fault and (result.get("fault") != "none" or result.get("injection") is not None):
            return 3
        phases = result["phases"]
        contract = [("plain", None, [0]*6), ("pass", 0, [0]*6), ("count", 1, [4,4,0,4,4,0]),
                    ("drop", 2, [4,0,4,4,4,0]), ("detached", None, [0]*6)]
        if fault:
            if result.get("fault") != fault:
                return 3
            if fault == "drop-disabled":
                if result.get("detail") != "TC drop delivery/count assertion failed" or result.get("injection") is not None:
                    return 3
                contract = contract[:3] + [("drop", 1, [4,4,0,4,4,0])]
            elif fault == "wrong-hook":
                injection = result["injection"]
                filters = injection["egress_filters"]
                if (result.get("detail") != "TC attachment identity differs" or injection.get("ingress_filters") != []
                        or not isinstance(filters,list) or not filters
                        or sum(f.get("options",{}).get("prog",{}).get("id") == attachment["program_id"]
                               and f.get("options",{}).get("direct-action") is True and f.get("options",{}).get("handle") == "0x1" for f in filters) != 1
                        or any(f.get("kind") != "bpf" or f.get("pref") != 49152
                               or f.get("options",{}).get("prog",{}).get("id") not in (None,attachment["program_id"]) for f in filters)):
                    return 3
                contract = contract[:1]
            elif fault == "receiver-stopped":
                if (result.get("detail") != "TC receiver stopped before send"
                        or result.get("injection") != {"phase":"drop","ready":{"ports":[39101,39102],"address":"10.77.2.10"},
                            "reaped":True,"signal":9,"received_report_present":False,"sender_started":False}):
                    return 3
                contract = contract[:3]
            elif fault == "interrupt-hold":
                injection = result["injection"]
                filters = injection["filters"]
                if (result.get("status") not in {"cancelled", "timeout"}
                        or result.get("detail") != "TC comparison interrupted at attached checkpoint"
                        or injection.get("checkpoint") != "pass-attached-before-send"
                        or not isinstance(filters, list) or not filters
                        or sum(f.get("options",{}).get("prog",{}).get("id") == attachment["program_id"]
                               and f.get("options",{}).get("direct-action") is True and f.get("options",{}).get("handle") == "0x1" for f in filters) != 1
                        or any(f.get("kind") != "bpf" or f.get("pref") != 49152
                               or f.get("options",{}).get("prog",{}).get("id") not in (None,attachment["program_id"]) for f in filters)):
                    return 3
                contract = contract[:1]
            else:
                return 3
        if not isinstance(phases, list) or len(phases) != len(contract):
            return 3
        previous = [0]*6
        for item, (phase, mode, delta) in zip(phases, contract):
            received = item["received"]
            before, after = item["before"], item["after"]
            packets = [{"port": port, "sequence": seq, "payload": f"VTC1:{phase}:{kind}:{seq}"}
                       for kind, port in enumerate((39101, 39102)) for seq in range(4)]
            if (item["phase"] != phase or item["sent"] != {"phase":phase, "source":"10.77.1.10:39100", "destination":"10.77.2.10", "packets":packets}
                    or received.get("phase") != phase or received.get("payloads_verified") is not True
                    or received.get("window_ms") != 500
                    or received.get("target_sequences") != ([] if phase == "drop" and fault != "drop-disabled" else [0,1,2,3])
                    or received.get("control_sequences") != [0,1,2,3]
                    or before["mode"] != mode or after["mode"] != mode
                    or len(before["counters"]) != 6 or len(after["counters"]) != 6
                    or any(type(n) is not int or n < 0 for n in before["counters"]+after["counters"])
                    or item["counter_delta"] != delta
                    or [a-b for a,b in zip(after["counters"], before["counters"])] != delta):
                return 3
            if phase in {"pass", "count", "drop"}:
                if before["counters"] != previous:
                    return 3
                previous = after["counters"]
        return 0
    except (KeyError, TypeError, ValueError, AttributeError):
        return 3



def direct_fixtures():
    """Independent byte and result contract for the pinned reference classifier."""
    packet = bytearray.fromhex("02000000000202000000000108004500002000000000401100000a4d010a0a4d020a98bc98bd000c000056544331")
    def checksum(p):
        p[24:26] = bytes(2)
        header = p[14:14 + (p[14] & 15) * 4]
        total = sum(int.from_bytes(header[i:i+2], "big") for i in range(0, len(header), 2))
        while total >> 16:
            total = (total & 65535) + (total >> 16)
        p[24:26] = (65535 - total).to_bytes(2, "big")
    checksum(packet)
    rows = [(f"prefix-{n:02}", bytes(packet[:n]), 2, errno.EINVAL if n < 34 else None, None if n < 34 else 0, [0]*6) for n in range(46)]
    mutations = (("ihl-zero",14,"40"),("ihl-small",14,"44"),("ihl-options-truncated",14,"4f"),
        ("ip-total-zero",16,"0000"),("ip-total-short",16,"0013"),("ip-total-no-prefix",16,"001f"),("ip-total-overrun",16,"0021"),
        ("udp-zero",38,"0000"),("udp-short-header",38,"0007"),("udp-short-prefix",38,"000b"),("udp-overrun",38,"000d"),
        ("fragment-mf",20,"2000"),("fragment-offset",20,"0001"),("version-not-v4",14,"65"),("protocol-not-udp",23,"06"))
    for name, offset, hexdata in mutations:
        p = packet.copy(); data = bytes.fromhex(hexdata); p[offset:offset+len(data)] = data
        rows.append((name, bytes(p), 2, None, 0, [0]*6))
    for size in (14,33):
        p = packet[:size]; p[12:14] = bytes.fromhex("88b5")
        rows.append((f"non-ip-short-{size}", bytes(p), 2, None, 0, [0]*6))
    p = packet.copy(); p[34:34] = bytes([1]*4); p[14] = 0x46; p[16:18] = bytes.fromhex("0024"); checksum(p)
    rows.append(("valid-ip-options", bytes(p), 2, None, 2, [1,0,1,0,0,0]))
    for mode in range(3):
        for control in (False,True):
            p = packet.copy()
            if control: p[36:38] = (39102).to_bytes(2,"big")
            drop = mode == 2 and not control; delta = [0]*6; base = 3 if control else 0
            if mode: delta[base] = 1; delta[base+(2 if drop else 1)] = 1
            rows.append((f"recovery-{mode}-{'control' if control else 'target'}",bytes(p),mode,None,2 if drop else 0,delta))
    return rows


def check_test_run(result, directory):
    try:
        if (result.get("schema_version") != 1 or result.get("scenario") != "bpf-test-run" or result.get("status") != "passed"
            or result.get("api") != "BPF_PROG_TEST_RUN" or result.get("repeat") != 1
            or result.get("attached") is not False or result.get("live_frames") is not False
            or result.get("cleanup") != {"program_unloaded":True,"maps_unloaded":True,"pinned":False}
            or type(result.get("program_id")) is not int or result["program_id"] <= 0
            or not isinstance(result.get("map_ids"),list) or len(result["map_ids"]) != 2
            or any(type(i) is not int or i <= 0 for i in result["map_ids"]) or len(set(result["map_ids"])) != 2
            or not isinstance(result.get("object_sha256"),str) or len(bytes.fromhex(result["object_sha256"])) != 32):
            return 3
        rows = result.get("cases"); fixtures = direct_fixtures(); previous = [0]*6
        if not isinstance(rows,list) or len(rows) != len(fixtures): return 3
        for row,(name,data,mode,error,retval,delta) in zip(rows,fixtures):
            filename = f"{name}.bin"; source = directory / filename
            if source.is_symlink() or not source.is_file() or source.stat().st_size > 256 or source.read_bytes() != data: return 3
            after = [b+d for b,d in zip(previous,delta)]
            if (row.get("id") != name or row.get("status") != "passed" or row.get("mode") != mode
                or row.get("input") != filename or row.get("input_hex") != data.hex()
                or row.get("input_sha256") != hashlib.sha256(data).hexdigest()
                or row.get("errno") != error or row.get("retval") != retval
                or row.get("kernel_executed") is not (error is None)
                or row.get("output_hex") != (data.hex() if error is None else "")
                or row.get("before") != {"mode":mode,"counters":previous}
                or row.get("after") != {"mode":mode,"counters":after} or row.get("counter_delta") != delta): return 3
            previous = after
        return 0
    except (OSError,ValueError,TypeError,KeyError):
        return 3

def reject_elf(data):
    """Check an ELF64 little-endian BPF object containing the fixed helper call."""
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<HH",data,16) != (1,247): return False
    start = struct.unpack_from("<Q",data,40)[0]
    width,count,strings = struct.unpack_from("<HHH",data,58)
    if width != 64 or count < 3 or strings >= count or start < 64 or start + width*count > len(data): return False
    def section(index):
        offset,size = struct.unpack_from("<QQ",data,start + width*index + 24)
        if offset + size > len(data): raise ValueError("section overrun")
        return data[offset:offset+size]
    names = section(strings)
    for index in range(count):
        name = struct.unpack_from("<I",data,start+width*index)[0]
        if name >= len(names): return False
        end = names.find(b"\0",name)
        if end < 0: return False
        if names[name:end] == b"classifier":
            instructions = section(index)
            return len(instructions) % 8 == 0 and struct.pack("<BBhi",0x85,0,0,999999) in [instructions[n:n+8] for n in range(0,len(instructions),8)]
    return False


def check_load_failure(result, directory):
    try:
        if (result.get("schema_version") != 1 or result.get("scenario") != "bpf-load-failure"
            or result.get("status") != "environment_error" or result.get("exit_code") != 3
            or result.get("stage") != "bpf_prog_load" or result.get("elf_parsed") is not True
            or result.get("load_outcome") != "verifier_rejected" or result.get("expected_rejection") is not True
            or result.get("errno") != errno.EINVAL or not isinstance(result.get("verifier_log"),str)
            or "invalid func unknown#999999" not in result["verifier_log"].splitlines()
            or result.get("diagnostic_file") != "verifier.log" or result.get("fixture") != "fixture.o"
            or result.get("program_id") is not None or result.get("program_fd_obtained") is not False
            or result.get("program_name") != "arena_rejected" or result.get("attached") is not False or result.get("traffic_started") is not False
            or result.get("before") != {"mode":0,"counters":[0]*6} or result.get("after") != result["before"]
            or result.get("cleanup") != {"program_unloaded":True,"maps_unloaded":True,"pinned":False}): return 3
        ids = result.get("map_ids"); before = result.get("program_ids_before"); after = result.get("program_ids_after")
        if (not isinstance(ids,list) or len(ids)!=2 or len(set(ids))!=2 or any(type(i) is not int or i<=0 for i in ids)
            or not isinstance(before,list) or any(type(i) is not int or i<=0 for i in before)
            or before != sorted(set(before)) or after != before): return 3
        log = directory/"verifier.log"; fixture = directory/"fixture.o"
        if (log.is_symlink() or not log.is_file() or log.stat().st_size > 65536
            or fixture.is_symlink() or not fixture.is_file() or fixture.stat().st_size > 1024*1024): return 3
        data = fixture.read_bytes()
        if not reject_elf(data) or hashlib.sha256(data).hexdigest() != result.get("object_sha256") or log.read_text() != result["verifier_log"]: return 3
        return 0
    except (OSError,ValueError,TypeError,KeyError,struct.error):
        return 3





def check_interruption(result, directory, reason):
    """An accepted interruption still preserves cancelled/timeout inner results."""
    try:
        code = 130 if reason == "cancelled" else 4
        if (reason not in {"cancelled", "timeout"} or result.get("schema_version") != 1
                or result.get("scenario") != "batch-interruption" or result.get("status") != "passed"
                or result.get("reason") != reason or result.get("cleanup_verified") is not True
                or result.get("batch_report") != "interrupted-batch/result.json"
                or result.get("checkpoint") != "tc-ingress-001/tc-interruption-ready.json"):
            return 3
        nested = directory / "interrupted-batch"
        batch = read_bounded(nested / "result.json")
        inner_contract = [row for row in BATCH_CASES if not row[0].startswith("batch-") and row[0] != "tc-after-cancel-001"]
        cases = batch["cases"]
        if (batch.get("schema_version") != 6 or batch.get("scenario") != "netns-batch"
                or batch.get("status") != reason or batch.get("exit_code") != code
                or batch.get("interruption_mode") != reason or batch.get("vm_boots") != 1
                or len(cases) != len(inner_contract)):
            return 3
        for index, (case, (case_id, expected)) in enumerate(zip(cases, inner_contract)):
            if case.get("id") != case_id or case.get("expected_exit_code") != expected:
                return 3
            if index < 3:
                execution = case["execution"]
                normal = read_bounded(nested / case_id / "result.json")
                if (case.get("status") != "passed" or case.get("report") != f"{case_id}/result.json"
                        or execution.get("exit_code") != expected or execution.get("signal") is not None
                        or execution.get("output_complete") is not True or execution.get("log_truncated") is not False
                        or check_netns(normal, "success" if not expected else "exit-code") != expected):
                    return 3
                if expected:
                    failures = [s for s in normal["steps"] if s.get("status") != "passed"]
                    if (len(normal["steps"]) != 33 or len(failures) != 1 or failures[0].get("id") != "031-app-server-ping"
                            or failures[0].get("exit_code") != 1 or failures[0].get("expected_exit_code") != 0
                            or read_bounded(nested / case_id / "topology.json").get("omit_server_route") is not True):
                        return 3
            elif index == 3:
                if case.get("status") != "failed" or case.get("report") != f"{case_id}/result.json": return 3
            elif (case.get("status") != "not_run" or "execution" in case or "report" in case
                  or (nested / case_id).exists()):
                return 3
        active = nested / "tc-ingress-001"
        network = read_bounded(active / "result.json")
        tc = read_bounded(active / "tc-result.json")
        ready = read_bounded(active / "tc-interruption-ready.json")
        execution = cases[3]["execution"]
        outer = result["execution"]
        for step in (execution, outer):
            if (step.get("exit_code") != code or step.get("signal") is not None
                    or step.get("output_complete") is not True or step.get("log_truncated") is not False
                    or step.get("termination_escalated") is not False
                    or type(step.get("child_pid")) is not int or step["child_pid"] <= 0): return 3
        if execution.get("status") != reason or outer.get("status") != ("cancelled" if reason == "cancelled" else "failed"): return 3
        request = {"reason":reason,"signal":2,"child_pid":execution["child_pid"]}
        if execution.get("stop_request") != request or read_bounded(active / "interrupt-request.json") != request: return 3
        if outer.get("readiness_observed") is not True: return 3
        if reason == "cancelled":
            request = {"reason":reason,"signal":2,"child_pid":outer["child_pid"]}
            if outer.get("stop_request") != request or read_bounded(nested / "supervisor-request.json") != request: return 3
        elif execution.get("readiness_observed") is not True or "stop_request" in outer: return 3
        labels = NETWORK_LABELS[:-5] + TC_LABELS[:3] + NETWORK_LABELS[-2:]
        if (network.get("status") != reason or network.get("scenario") != "netns-baseline" or network.get("schema_version") != 1
                or network.get("tc_reference") is not True or network.get("tc_fault") != "interrupt-hold"
                or network.get("detail") != tc.get("detail") or tc.get("status") != reason
                or network.get("cleanup") != {"helpers_reaped":True,"owned_namespace_references":[],"parent_network_unchanged":True,"errors":[]}
                or len(network["steps"]) != len(labels) or _validate_tc(tc, "interrupt-hold") != 0): return 3
        for i, (step, label) in enumerate(zip(network["steps"], labels)):
            expected = 1 if label.startswith("forwarding-off-") else 0
            if (step.get("id") != f"{i+1:03}-{label}" or step.get("status") != "passed" or step.get("exit_code") != expected
                    or step.get("expected_exit_code") != expected or step.get("signal") is not None
                    or step.get("output_complete") is not True or step.get("log_truncated") is not False): return 3
        if (ready.get("schema_version") != 1 or ready.get("checkpoint") != "pass-attached-before-send"
                or ready.get("runner_pid") != execution["child_pid"] or ready.get("attachment") != tc["attachment"]
                or ready.get("filters") != tc["injection"]["filters"]): return 3
        for label, expected in (("tc-filters-before", []), ("tc-filters-attached", ready["filters"])):
            path = active / f"{labels.index(label)+1:03}-{label}.stdout.log"
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 1048576 or json.loads(path.read_text()) != expected: return 3
        if read_bounded(active / "tc-plain-received.json") != tc["phases"][0]["received"]: return 3
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 3

def check_watchdog(root):
    """Stopped guest runner, host deadline, retained partial controls and owned QEMU exit."""
    try:
        stop = read_bounded(root / "vm-stop.json")
        ready = read_bounded(root / "watchdog-ready.json")
        state = read_bounded(root / "watchdog-runner-status.json")
        manifest = read_bounded(root / "vm-manifest.json")
        directory = root / "netns-batch"
        progress = read_bounded(directory / "batch-progress.json")
        if (stop.get("schema_version") != 2 or stop.get("reason") != "watchdog" or stop.get("status") != "environment_error"
                or stop.get("lifecycle_exit_code") != 3 or stop.get("qemu_reaped") is not True or stop.get("qemu_exit_code") != 0
                or stop.get("qemu_signal") is not None or stop.get("escalated") is not False or stop.get("sync_error") is not None
                or stop.get("evidence_synced") is not True or stop.get("evidence_synced_before_stop") is not True
                or stop.get("watchdog_fixture") is not True or stop.get("checkpoint_seen") is not True
                or stop.get("timeout_after_checkpoint_ms") != 2000 or type(stop.get("checkpoint_elapsed_ms")) is not int
                or stop["checkpoint_elapsed_ms"] < 2000 or stop.get("startup_timeout_ms") != 120000
                or any(type(stop.get(key)) is not int or stop[key] <= 1 for key in ("qemu_pid", "controller_pid"))
                or stop["qemu_pid"] == stop["controller_pid"] or manifest.get("watchdog_fixture_supported") is not True
                or manifest.get("scenario") != "netns-batch" or manifest.get("network") != "no NIC"
                or (root / "service-result.txt").exists() or (directory / "result.json").exists()
                or (root / "qemu-exit-code.txt").read_text().strip() != "0"):
            return 3
        cmdline = (root / "guest-cmdline.txt").read_text().split()
        if "arena.watchdog=1" not in cmdline or "arena.netns=1" not in cmdline: return 3
        if (ready.get("schema_version") != 1 or ready.get("scenario") != "guest-runner-stopped" or ready.get("state") != "T"
                or ready.get("signal") != 19 or progress.get("schema_version") != 1 or progress.get("scenario") != "netns-batch-progress"
                or progress.get("status") != "incomplete" or progress.get("active_case") != "tc-ingress-001"
                or ready.get("batch_pid") != progress.get("runner_pid") or state.get("batch_pid") != ready.get("batch_pid")
                or state.get("case_pid") != ready.get("case_pid")
                or any(type(ready.get(key)) is not int or ready[key] <= 1 for key in ("batch_pid", "case_pid", "observer_pid"))
                or len({ready["batch_pid"], ready["case_pid"], ready["observer_pid"]}) != 3
                or f"Pid:\t{ready['batch_pid']}" not in state["proc_status"].splitlines()
                or not any(line.startswith("State:\tT") for line in state["proc_status"].splitlines())
                or f"Pid:\t{ready['case_pid']}" not in state["case_proc_status"].splitlines()
                or f"PPid:\t{ready['batch_pid']}" not in state["case_proc_status"].splitlines()): return 3
        cases = progress["cases"]
        if len(cases) != 3: return 3
        for case, (case_id, expected) in zip(cases, BATCH_CASES[:3]):
            execution = case["execution"];network = read_bounded(directory / case_id / "result.json")
            if (case.get("id") != case_id or case.get("status") != "passed" or case.get("expected_exit_code") != expected
                    or case.get("report") != f"{case_id}/result.json" or execution.get("exit_code") != expected
                    or execution.get("output_complete") is not True or execution.get("signal") is not None
                    or execution.get("log_truncated") is not False or check_netns(network, "success" if not expected else "exit-code") != expected): return 3
            if expected:
                failures = [step for step in network["steps"] if step.get("status") != "passed"]
                if len(network["steps"]) != 33 or len(failures) != 1 or failures[0].get("id") != "031-app-server-ping" or failures[0].get("exit_code") != 1 or read_bounded(directory / case_id / "topology.json").get("omit_server_route") is not True: return 3
        active = directory / "tc-ingress-001";checkpoint = read_bounded(active / "tc-interruption-ready.json")
        if ready.get("checkpoint") != checkpoint or checkpoint.get("runner_pid") != ready["case_pid"] or checkpoint.get("checkpoint") != "pass-attached-before-send": return 3
        attachment = checkpoint["attachment"]
        if (attachment.get("hook") != "ingress" or attachment.get("interface") != "rt-app" or attachment.get("attach") != "netlink-clsact"
                or attachment.get("pinned") is not False or attachment.get("priority") != 49152 or attachment.get("handle") != 1
                or type(attachment.get("program_id")) is not int or attachment["program_id"] <= 0
                or not isinstance(attachment.get("map_ids"),list) or len(set(attachment["map_ids"])) != 2
                or any(type(i) is not int or i <= 0 for i in attachment["map_ids"])
                or len(bytes.fromhex(attachment["object_sha256"])) != 32): return 3
        def log_json(name):
            path=active/name
            if path.is_symlink() or not path.is_file() or path.stat().st_size>1048576: raise ValueError("invalid partial log")
            return json.loads(path.read_text())
        if log_json("047-tc-filters-before.stdout.log") != [] or log_json("049-tc-filters-attached.stdout.log") != checkpoint["filters"]: return 3
        filters = checkpoint["filters"]
        if (not isinstance(filters,list) or not filters
                or sum(f.get("options",{}).get("prog",{}).get("id") == attachment["program_id"] and f.get("options",{}).get("direct-action") is True and f.get("options",{}).get("handle") == "0x1" for f in filters) != 1
                or any(f.get("kind") != "bpf" or f.get("pref") != 49152 or f.get("options",{}).get("prog",{}).get("id") not in (None,attachment["program_id"]) for f in filters)): return 3
        packets=[{"port":port,"sequence":seq,"payload":f"VTC1:plain:{kind}:{seq}"} for kind,port in enumerate((39101,39102)) for seq in range(4)]
        if (log_json("048-tc-plain-send.stdout.log") != {"phase":"plain","source":"10.77.1.10:39100","destination":"10.77.2.10","packets":packets}
                or read_bounded(active / "tc-plain-received.json") != {"phase":"plain","window_ms":500,"payloads_verified":True,"target_sequences":[0,1,2,3],"control_sequences":[0,1,2,3]}
                or (active / "result.json").exists() or (active / "tc-result.json").exists() or (active / "tc-cleanup.json").exists()): return 3
        print("Watchdog expected environment error verified; partial evidence retained; owned QEMU reaped")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 3


if __name__ == "__main__":
    scenario=sys.argv[2] if len(sys.argv)==3 else "blackhole-offline"
    root=pathlib.Path(sys.argv[1]);code=check(root,scenario)
    if code == 0 and scenario == "netns-watchdog":
        try:
            with (root / "watchdog-verification.json").open("x") as output:
                json.dump({"schema_version":1,"scenario":"netns-watchdog","status":"passed","lifecycle_status":"environment_error","expected_exit_code":3,"guest_result":"incomplete"},output)
                output.flush()
                import os
                os.fsync(output.fileno())
        except OSError:
            code=3
    sys.exit(code)
