"""Failure evidence must survive and must never become an implicit pass."""
import importlib.util
import json
import pathlib
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "vm_result", pathlib.Path(__file__).parents[1] / "scripts/check_vm_result.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


class EvidenceTests(unittest.TestCase):
    def fixture(self, status="passed", service="success"):
        # Keep test evidence. No automatic deletion of files or directories.
        root = pathlib.Path(tempfile.mkdtemp(prefix="arena-vm-evidence-"))
        directory = root / "results/blackhole-offline-test"
        directory.mkdir(parents=True)
        result = {"status": status, "steps": [{"status": "passed"}] * 18,
                  "source_unchanged": True, "binary_unchanged": True}
        (directory / "result.json").write_text(json.dumps(result))
        (root / "service-result.txt").write_text(service)
        return root, directory / "result.json", result

    def test_complete_pass_and_regression(self):
        root, _, _ = self.fixture()
        self.assertEqual(checker.check(root), 0)
        root, _, _ = self.fixture("failed", "exit-code")
        self.assertEqual(checker.check(root), 1)

    def test_missing_incomplete_and_ambiguous_are_environment_errors(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="arena-vm-missing-"))
        self.assertEqual(checker.check(root), 3)
        root, path, result = self.fixture()
        result["steps"] = []
        path.write_text(json.dumps(result))
        self.assertEqual(checker.check(root), 3)
        other = root / "results/blackhole-offline-other"
        other.mkdir()
        (other / "result.json").write_text("{}")
        self.assertEqual(checker.check(root), 3)

    def test_service_failure_and_bad_json_are_environment_errors(self):
        root, _, _ = self.fixture(service="timeout")
        self.assertEqual(checker.check(root), 3)
        root, path, _ = self.fixture()
        path.write_text("not JSON")
        self.assertEqual(checker.check(root), 3)
        path.write_text("[]")
        self.assertEqual(checker.check(root), 3)

    def test_runner_environment_failure_is_not_a_regression(self):
        for status in ("environment_error", "timeout", "cancelled", "unknown"):
            with self.subTest(status=status):
                root, _, _ = self.fixture(status)
                self.assertEqual(checker.check(root), 3)


class NetworkEvidenceTests(unittest.TestCase):
    def result(self):
        labels = checker.NETWORK_LABELS
        steps = [{"id": f"{i+1:03}-{label}", "status": "passed",
                  "exit_code": 1 if label.startswith("forwarding-off-") else 0,
                  "expected_exit_code": 1 if label.startswith("forwarding-off-") else 0,
                  "signal": None, "output_complete": True, "log_truncated": False}
                 for i, label in enumerate(labels)]
        return {"schema_version": 1, "scenario": "netns-baseline", "status": "passed", "steps": steps,
                "cleanup": {"helpers_reaped": True, "owned_namespace_references": [],
                            "parent_network_unchanged": True, "errors": []}}

    def test_network_pass_requires_cleanup_and_complete_traffic_checks(self):
        self.assertEqual(checker.check_netns(self.result(), "success"), 0)
        for field, value in (("helpers_reaped", False), ("owned_namespace_references", [[123, "net:[1]"]]),
                             ("parent_network_unchanged", False), ("errors", ["stop failed"])):
            result = self.result()
            result["cleanup"][field] = value
            self.assertEqual(checker.check_netns(result, "success"), 3)
        result = self.result()
        result["steps"][0]["id"] = "001-unrelated"
        self.assertEqual(checker.check_netns(result, "success"), 3)
        result = self.result()
        negative = next(step for step in result["steps"] if "forwarding-off-app-server" in step["id"])
        negative["expected_exit_code"] = 0
        negative["exit_code"] = 0
        self.assertEqual(checker.check_netns(result, "success"), 3)
        result = self.result()
        result["steps"][0]["id"] = 123
        self.assertEqual(checker.check_netns(result, "success"), 3)

    def test_network_failure_is_regression_only_after_cleanup(self):
        result = self.result()
        result["status"] = "failed"
        self.assertEqual(checker.check_netns(result, "exit-code"), 1)
        result["cleanup"]["helpers_reaped"] = False
        self.assertEqual(checker.check_netns(result, "exit-code"), 3)
        for status in ("environment_error", "cancelled", "timeout", "unknown"):
            result = self.result()
            result["status"] = status
            self.assertEqual(checker.check_netns(result, "exit-code"), 3)

    def test_network_crash_without_result_cannot_pass(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="arena-network-missing-"))
        (root / "service-result.txt").write_text("success")
        self.assertEqual(checker.check(root, "netns-baseline"), 3)


class BatchEvidenceTests(unittest.TestCase):
    def fixture(self, include_interruptions=True):
        root = pathlib.Path(tempfile.mkdtemp(prefix="arena-batch-evidence-"))
        directory = root / "netns-batch"
        directory.mkdir()
        cases = []
        for case_id, expected in checker.BATCH_CASES:
            if not include_interruptions and (case_id.startswith("batch-") or case_id == "tc-after-cancel-001"):
                continue
            target = directory / case_id
            target.mkdir()
            result = NetworkEvidenceTests().result()
            if case_id.startswith("tc-"):
                labels = checker.NETWORK_LABELS[:-5] + checker.TC_LABELS + checker.NETWORK_LABELS[-5:]
                result["steps"] = [{"id":f"{i+1:03}-{label}","status":"passed",
                    "exit_code":1 if label.startswith("forwarding-off-") else 0,
                    "expected_exit_code":1 if label.startswith("forwarding-off-") else 0,
                    "signal":None,"output_complete":True,"log_truncated":False} for i,label in enumerate(labels)]
                result["tc_reference"] = True
                (target / "tc-result.json").write_text(json.dumps(TcEvidenceTests().result()))
            if case_id == "route-fault-001":
                labels = [s for s in checker.NETWORK_LABELS if s != "server-route"]
                labels = labels[:labels.index("app-server-ping")+1] + ["parent-links-after", "parent-routes-after"]
                result["steps"] = [{"id": f"{i+1:03}-{label}", "status": "failed" if label == "app-server-ping" else "passed",
                                    "exit_code": 1 if label == "app-server-ping" else 0, "expected_exit_code": 0}
                                   for i, label in enumerate(labels)]
                result["status"] = "failed"
                (target / "topology.json").write_text(json.dumps({"omit_server_route": True}))
            if case_id in checker.FAULTS:
                result, tc = TcFaultEvidenceTests().result(checker.FAULTS[case_id])
                (target / "tc-result.json").write_text(json.dumps(tc))
            if case_id == "bpf-test-run-001":
                result = DirectEvidenceTests().fixture(target)
            if case_id == "bpf-load-failure-001":
                result = LoadFailureEvidenceTests().fixture(target)
            if case_id.startswith("batch-"):
                result = InterruptionEvidenceTests().fixture(target, "cancelled" if case_id == "batch-cancel-001" else "timeout")
            (target / "result.json").write_text(json.dumps(result))
            cases.append({"id": case_id, "status": "passed", "expected_exit_code": expected,
                          "report": f"{case_id}/result.json", "execution": {"exit_code": expected, "signal": None,
                          "output_complete": True, "log_truncated": False, "status": "failed" if expected else "passed"}})
        summary = {"schema_version": 6, "scenario": "netns-batch", "status": "passed", "exit_code": 0, "vm_boots": 1, "cases": cases}
        (directory / "result.json").write_text(json.dumps(summary))
        (root / "service-result.txt").write_text("success")
        return root, summary

    def test_expected_route_failure_is_not_any_nonzero_exit(self):
        root, _ = self.fixture()
        self.assertEqual(checker.check(root, "netns-batch"), 0)
        target = root / "netns-batch/route-fault-001/result.json"
        result = json.loads(target.read_text())
        failed = next(s for s in result["steps"] if s["status"] == "failed")
        failed["id"] = "031-unrelated-failure"
        target.write_text(json.dumps(result))
        self.assertEqual(checker.check(root, "netns-batch"), 3)

    def test_missing_case_and_skipped_case_cannot_pass(self):
        root, summary = self.fixture()
        summary["cases"][2]["status"] = "not_run"
        (root / "netns-batch/result.json").write_text(json.dumps(summary))
        self.assertEqual(checker.check(root, "netns-batch"), 3)
        root, summary = self.fixture()
        summary["cases"][2]["report"] = "../outside.json"
        (root / "netns-batch/result.json").write_text(json.dumps(summary))
        self.assertEqual(checker.check(root, "netns-batch"), 3)



class TcEvidenceTests(unittest.TestCase):
    def result(self):
        attachment={"interface":"rt-app","hook":"ingress","attach":"netlink-clsact","aya":"0.13.1","priority":49152,"handle":1,
                    "pinned":False,"program_id":1,"map_ids":[1,2],"object_sha256":"a"*64}
        phases=[]
        previous=[0]*6
        for phase,mode,delta in [("plain",None,[0]*6),("pass",0,[0]*6),("count",1,[4,4,0,4,4,0]),("drop",2,[4,0,4,4,4,0]),("detached",None,[0]*6)]:
            before=previous if mode is not None else [0]*6
            after=[a+b for a,b in zip(before,delta)]
            phases.append({"phase":phase,"before":{"mode":mode,"counters":before},"after":{"mode":mode,"counters":after},"counter_delta":delta,
                           "received":{"phase":phase,"window_ms":500,"payloads_verified":True,"target_sequences":[] if phase=="drop" else [0,1,2,3],"control_sequences":[0,1,2,3]},
                           "sent":{"phase":phase,"source":"10.77.1.10:39100","destination":"10.77.2.10", "packets":[{"port":port,"sequence":seq,"payload":f"VTC1:{phase}:{kind}:{seq}"} for kind,port in enumerate((39101,39102)) for seq in range(4)]}})
            if mode is not None:previous=after
        return {"schema_version":1,"scenario":"tc-ingress","status":"passed","fault":"none","injection":None,"attachment":attachment,"phases":phases,
                "cleanup":{"detached":True,"program_unloaded":True,"maps_unloaded":True,"pinned":False}}

    def test_empty_receiver_without_positive_control_or_kernel_counts_cannot_pass(self):
        self.assertEqual(checker.check_tc(self.result()),0)
        for field,value in (("counter_delta",[0]*6),("received",{"target_sequences":[],"control_sequences":[]})):
            result=self.result();result["phases"][3][field]=value
            self.assertEqual(checker.check_tc(result),3)
        result=self.result();result["phases"][3]["received"]["target_sequences"]=[0]
        self.assertEqual(checker.check_tc(result),3)

    def test_missing_phase_wrong_hook_and_incomplete_cleanup_cannot_pass(self):
        result=self.result();result["phases"].pop();self.assertEqual(checker.check_tc(result),3)
        result=self.result();result["attachment"]["hook"]="egress";self.assertEqual(checker.check_tc(result),3)
        for key in ("detached","program_unloaded","maps_unloaded"):
            result=self.result();result["cleanup"][key]=False;self.assertEqual(checker.check_tc(result),3)


class TcFaultEvidenceTests(unittest.TestCase):
    def result(self, fault):
        tc = TcEvidenceTests().result()
        tc.update(fault=fault, injection=None)
        network = NetworkEvidenceTests().result()
        status = "failed" if fault == "drop-disabled" else "environment_error"
        detail = {"drop-disabled":"TC drop delivery/count assertion failed", "wrong-hook":"TC attachment identity differs",
                  "receiver-stopped":"TC receiver stopped before send"}[fault]
        tc.update(status=status, detail=detail)
        if fault == "drop-disabled":
            tc["phases"] = tc["phases"][:4]
            p = tc["phases"][3]
            p["before"]["mode"] = p["after"]["mode"] = 1
            p["counter_delta"] = [4,4,0,4,4,0]
            p["after"]["counters"] = [a+b for a,b in zip(p["before"]["counters"],p["counter_delta"])]
            p["received"]["target_sequences"] = [0,1,2,3]
            prefix = checker.TC_LABELS[:6]
        elif fault == "wrong-hook":
            tc["phases"] = tc["phases"][:1]
            tc["attachment"]["hook"] = "egress"
            tc["injection"] = {"ingress_filters":[],"egress_filters":[{"kind":"bpf","pref":49152,
                "options":{"prog":{"id":1},"direct-action":True,"handle":"0x1"}}]}
            prefix = checker.TC_LABELS[:3]+["tc-fault-egress"]
        else:
            tc["phases"] = tc["phases"][:3]
            tc["injection"] = {"phase":"drop","ready":{"ports":[39101,39102],"address":"10.77.2.10"},
                "reaped":True,"signal":9,"received_report_present":False,"sender_started":False}
            prefix = checker.TC_LABELS[:5]
        labels = checker.NETWORK_LABELS[:-5]+prefix+checker.NETWORK_LABELS[-2:]
        network.update(status=status, detail=detail, tc_reference=True, tc_fault=fault)
        network["steps"] = [{"id":f"{i+1:03}-{label}","status":"passed","exit_code":int(label.startswith("forwarding-off-")),
            "expected_exit_code":int(label.startswith("forwarding-off-")),"signal":None,"output_complete":True,"log_truncated":False}
            for i,label in enumerate(labels)]
        return network, tc

    def test_exact_faults_and_cleanup_are_required(self):
        for fault in checker.FAULTS.values():
            with self.subTest(fault=fault):
                network,tc = self.result(fault)
                self.assertEqual(checker.check_tc(tc),3)
                self.assertEqual(checker.check_tc_fault(network,tc,fault),0)
                tc["detail"] = "unrelated error"
                self.assertEqual(checker.check_tc_fault(network,tc,fault),3)
                network,tc = self.result(fault)
                tc["cleanup"]["maps_unloaded"] = False
                self.assertEqual(checker.check_tc_fault(network,tc,fault),3)
                network,tc = self.result(fault)
                network["steps"].pop(0)
                self.assertEqual(checker.check_tc_fault(network,tc,fault),3)

    def test_fake_drop_missing_hook_and_dead_receiver_are_not_accepted(self):
        network,tc = self.result("drop-disabled")
        tc["phases"][3]["received"]["target_sequences"] = []
        self.assertEqual(checker.check_tc_fault(network,tc,"drop-disabled"),3)
        network,tc = self.result("wrong-hook")
        tc["injection"]["egress_filters"] = []
        self.assertEqual(checker.check_tc_fault(network,tc,"wrong-hook"),3)
        network,tc = self.result("receiver-stopped")
        tc["injection"]["reaped"] = False
        self.assertEqual(checker.check_tc_fault(network,tc,"receiver-stopped"),3)
        root,_ = BatchEvidenceTests().fixture()
        target = root/"netns-batch/tc-wrong-hook-001/tc-result.json"
        tc = json.loads(target.read_text()); tc["detail"]="unrelated error"; target.write_text(json.dumps(tc))
        self.assertEqual(checker.check(root,"netns-batch"),3)

class DirectEvidenceTests(unittest.TestCase):
    def fixture(self, directory):
        rows = []; counters = [0]*6
        for name,data,mode,error,retval,delta in checker.direct_fixtures():
            (directory/f"{name}.bin").write_bytes(data)
            after = [b+d for b,d in zip(counters,delta)]
            rows.append({"id":name,"status":"passed","mode":mode,"input":f"{name}.bin","input_hex":data.hex(),
                "input_sha256":checker.hashlib.sha256(data).hexdigest(),"errno":error,"retval":retval,
                "kernel_executed":error is None,"output_hex":data.hex() if error is None else "",
                "before":{"mode":mode,"counters":counters},"after":{"mode":mode,"counters":after},"counter_delta":delta})
            counters = after
        return {"schema_version":1,"scenario":"bpf-test-run","status":"passed","api":"BPF_PROG_TEST_RUN","repeat":1,
            "attached":False,"live_frames":False,"program_id":123,"map_ids":[124,125],"object_sha256":"a"*64,"cases":rows,
            "cleanup":{"program_unloaded":True,"maps_unloaded":True,"pinned":False}}

    def test_direct_evidence_requires_execution_input_and_cleanup(self):
        directory = pathlib.Path(tempfile.mkdtemp(prefix="arena-direct-evidence-"))
        result = self.fixture(directory)
        self.assertEqual(checker.check_test_run(result,directory),0)
        import copy
        for field,value in (("errno",None),("kernel_executed",True),("retval",0),("output_hex","ff"),("counter_delta",[1]*6)):
            changed = copy.deepcopy(result); changed["cases"][0][field] = value
            self.assertEqual(checker.check_test_run(changed,directory),3,field)
        changed = copy.deepcopy(result); changed["cases"].pop()
        self.assertEqual(checker.check_test_run(changed,directory),3)
        changed = copy.deepcopy(result); changed["cleanup"]["maps_unloaded"] = False
        self.assertEqual(checker.check_test_run(changed,directory),3)
        changed = copy.deepcopy(result); changed["cases"][-1]["after"]["counters"][0] += 1
        self.assertEqual(checker.check_test_run(changed,directory),3)
        (directory/"prefix-00.bin").write_bytes(b"unexpected")
        self.assertEqual(checker.check_test_run(result,directory),3)

class LoadFailureEvidenceTests(unittest.TestCase):
    def fixture(self,directory):
        import struct
        names = b"\0classifier\0.shstrtab\0"
        data = bytearray(64+3*64+16+len(names));data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<HH",data,16,1,247);struct.pack_into("<Q",data,40,64);struct.pack_into("<HHH",data,58,64,3,2)
        struct.pack_into("<I",data,128,1);struct.pack_into("<QQ",data,128+24,256,16)
        struct.pack_into("<I",data,192,12);struct.pack_into("<QQ",data,192+24,272,len(names))
        data[256:272] = struct.pack("<BBhiBBhi",0x85,0,0,999999,0x95,0,0,0);data[272:] = names
        log = "0: (85) call unknown#999999\ninvalid func unknown#999999\n"
        (directory/"fixture.o").write_bytes(data);(directory/"verifier.log").write_text(log)
        return {"schema_version":1,"scenario":"bpf-load-failure","status":"environment_error","exit_code":3,
            "stage":"bpf_prog_load","elf_parsed":True,"load_outcome":"verifier_rejected","expected_rejection":True,
            "errno":22,"verifier_log":log,"diagnostic_file":"verifier.log","fixture":"fixture.o",
            "object_sha256":checker.hashlib.sha256(data).hexdigest(),"program_name":"arena_rejected","map_ids":[123,124],
            "program_id":None,"program_fd_obtained":False,"program_ids_before":[],"program_ids_after":[],
            "before":{"mode":0,"counters":[0]*6},"after":{"mode":0,"counters":[0]*6},"attached":False,"traffic_started":False,
            "cleanup":{"program_unloaded":True,"maps_unloaded":True,"pinned":False}}

    def test_unrelated_load_error_success_or_leak_cannot_pass(self):
        import copy
        directory=pathlib.Path(tempfile.mkdtemp(prefix="arena-load-evidence-"));r=self.fixture(directory)
        self.assertEqual(checker.check_load_failure(r,directory),0)
        for field,value in (("errno",1),("verifier_log","other error"),("load_outcome","unexpected_success"),("elf_parsed",False),
            ("program_fd_obtained",True),("attached",True),("traffic_started",True),("program_ids_after",[125])):
            changed=copy.deepcopy(r);changed[field]=value
            self.assertEqual(checker.check_load_failure(changed,directory),3,field)
        changed=copy.deepcopy(r);changed["cleanup"]["maps_unloaded"]=False
        self.assertEqual(checker.check_load_failure(changed,directory),3)
        changed=copy.deepcopy(r);changed["after"]["counters"][0]=1
        self.assertEqual(checker.check_load_failure(changed,directory),3)
        (directory/"verifier.log").write_text("unrelated diagnostic")
        self.assertEqual(checker.check_load_failure(r,directory),3)

    def test_malformed_object_is_not_verifier_rejection(self):
        directory=pathlib.Path(tempfile.mkdtemp(prefix="arena-load-object-"));r=self.fixture(directory)
        (directory/"fixture.o").write_bytes(b"not an ELF")
        r["object_sha256"]=checker.hashlib.sha256(b"not an ELF").hexdigest()
        self.assertEqual(checker.check_load_failure(r,directory),3)



class InterruptionEvidenceTests(unittest.TestCase):
    def fixture(self, target, reason):
        import shutil
        root, summary = BatchEvidenceTests().fixture(include_interruptions=False)
        nested = target / "interrupted-batch"; nested.mkdir()
        for case in summary["cases"][:3]:
            shutil.copytree(root / "netns-batch" / case["id"], nested / case["id"])
        code = 130 if reason == "cancelled" else 4
        for case in summary["cases"][4:]:
            case.pop("execution");case.pop("report");case["status"] = "not_run"
        case = summary["cases"][3];case["status"] = "failed"
        execution = {"exit_code":code,"signal":None,"output_complete":True,"log_truncated":False,
                     "termination_escalated":False,"status":reason,"child_pid":120,
                     "stop_request":{"reason":reason,"signal":2,"child_pid":120}}
        if reason == "timeout": execution["readiness_observed"] = True
        case["execution"] = execution
        summary.update(status=reason, exit_code=code, interruption_mode=reason)
        (nested / "result.json").write_text(json.dumps(summary))
        active = nested / "tc-ingress-001";active.mkdir()
        labels = checker.NETWORK_LABELS[:-5] + checker.TC_LABELS[:3] + checker.NETWORK_LABELS[-2:]
        network = NetworkEvidenceTests().result()
        network.update(status=reason, tc_reference=True, tc_fault="interrupt-hold", detail="TC comparison interrupted at attached checkpoint")
        network["steps"] = [{"id":f"{i+1:03}-{label}","status":"passed","exit_code":1 if label.startswith("forwarding-off-") else 0,
            "expected_exit_code":1 if label.startswith("forwarding-off-") else 0,"signal":None,"output_complete":True,"log_truncated":False} for i,label in enumerate(labels)]
        tc = TcEvidenceTests().result();tc.update(status=reason, fault="interrupt-hold", detail=network["detail"])
        tc["phases"] = tc["phases"][:1]
        filters = [{"kind":"bpf","pref":49152,"options":{"prog":{"id":1},"direct-action":True,"handle":"0x1"}}]
        tc["injection"] = {"checkpoint":"pass-attached-before-send","filters":filters}
        ready = {"schema_version":1,"runner_pid":120,"checkpoint":"pass-attached-before-send","attachment":tc["attachment"],"filters":filters}
        for name, data in (("result.json",network),("tc-result.json",tc),("tc-interruption-ready.json",ready),
                           ("interrupt-request.json",execution["stop_request"]),("tc-plain-received.json",tc["phases"][0]["received"])):
            (active / name).write_text(json.dumps(data))
        for label,data in (("tc-filters-before",[]),("tc-filters-attached",filters)):
            (active / f"{labels.index(label)+1:03}-{label}.stdout.log").write_text(json.dumps(data))
        outer = dict(execution, child_pid=110, status="cancelled" if reason == "cancelled" else "failed", readiness_observed=True)
        if reason == "cancelled":
            outer["stop_request"] = {"reason":reason,"signal":2,"child_pid":110}
            (nested / "supervisor-request.json").write_text(json.dumps(outer["stop_request"]))
        else: outer.pop("stop_request")
        return {"schema_version":1,"scenario":"batch-interruption","status":"passed","reason":reason,"cleanup_verified":True,
                "execution":outer,"batch_report":"interrupted-batch/result.json","checkpoint":"tc-ingress-001/tc-interruption-ready.json"}

    def test_graceful_interruption_requires_partial_cleanup_and_actual_attachment(self):
        for reason in ("cancelled", "timeout"):
            target = pathlib.Path(tempfile.mkdtemp(prefix="arena-interruption-evidence-"))
            result = self.fixture(target, reason)
            self.assertEqual(checker.check_interruption(result, target, reason), 0)
            for key,value in (("termination_escalated",True),("signal",9),("output_complete",False)):
                damaged = json.loads(json.dumps(result));damaged["execution"][key] = value
                self.assertEqual(checker.check_interruption(damaged, target, reason), 3)
            active = target / "interrupted-batch/tc-ingress-001"
            for file,key,value in (("tc-interruption-ready.json","runner_pid",121),("tc-interruption-ready.json","filters",[]),
                                   ("result.json","status","passed"),("result.json","cleanup",{})):
                path = active / file;original = path.read_text();damaged = json.loads(original);damaged[key] = value
                path.write_text(json.dumps(damaged))
                self.assertEqual(checker.check_interruption(result, target, reason), 3)
                path.write_text(original)

    def test_skipped_evidence_and_wrong_interrupt_reason_cannot_pass(self):
        target = pathlib.Path(tempfile.mkdtemp(prefix="arena-interruption-evidence-"))
        result = self.fixture(target, "timeout")
        path = target / "interrupted-batch/result.json";summary = json.loads(path.read_text())
        summary["cases"][4]["status"] = "passed";path.write_text(json.dumps(summary))
        self.assertEqual(checker.check_interruption(result, target, "timeout"), 3)
        self.assertEqual(checker.check_interruption(result, target, "cancelled"), 3)
class WatchdogEvidenceTests(unittest.TestCase):
    def fixture(self):
        import shutil
        root=pathlib.Path(tempfile.mkdtemp(prefix="arena-watchdog-evidence-"));nested=root/"netns-batch";nested.mkdir()
        source,summary=BatchEvidenceTests().fixture(include_interruptions=False)
        for case in summary["cases"][:3]: shutil.copytree(source/"netns-batch"/case["id"],nested/case["id"])
        active=nested/"tc-ingress-001";active.mkdir();tc=TcEvidenceTests().result()
        filters=[{"kind":"bpf","pref":49152,"options":{"prog":{"id":1},"direct-action":True,"handle":"0x1"}}]
        checkpoint={"schema_version":1,"checkpoint":"pass-attached-before-send","runner_pid":120,"attachment":tc["attachment"],"filters":filters}
        ready={"schema_version":1,"scenario":"guest-runner-stopped","state":"T","signal":19,"batch_pid":110,"case_pid":120,"observer_pid":111,"checkpoint":checkpoint}
        stop={"schema_version":2,"reason":"watchdog","status":"environment_error","lifecycle_exit_code":3,"qemu_reaped":True,"qemu_exit_code":0,"qemu_signal":None,
              "escalated":False,"sync_error":None,"evidence_synced":True,"evidence_synced_before_stop":True,"watchdog_fixture":True,"checkpoint_seen":True,
              "timeout_after_checkpoint_ms":2000,"checkpoint_elapsed_ms":2050,"startup_timeout_ms":120000,"qemu_pid":200,"controller_pid":201}
        progress={"schema_version":1,"scenario":"netns-batch-progress","status":"incomplete","active_case":"tc-ingress-001","runner_pid":110,"cases":summary["cases"][:3]}
        state={"batch_pid":110,"case_pid":120,"proc_status":"Pid:\t110\nState:\tT (stopped)\n","case_proc_status":"Pid:\t120\nPPid:\t110\n"}
        for name,data in (("vm-stop.json",stop),("watchdog-ready.json",ready),("watchdog-runner-status.json",state),("vm-manifest.json",{"watchdog_fixture_supported":True,"scenario":"netns-batch","network":"no NIC"})):
            (root/name).write_text(json.dumps(data))
        (nested/"batch-progress.json").write_text(json.dumps(progress));(root/"qemu-exit-code.txt").write_text("0\n")
        (root/"guest-cmdline.txt").write_text("arena.netns=1 arena.watchdog=1")
        for name,data in (("tc-interruption-ready.json",checkpoint),("047-tc-filters-before.stdout.log",[]),("049-tc-filters-attached.stdout.log",filters),
                          ("048-tc-plain-send.stdout.log",tc["phases"][0]["sent"]),("tc-plain-received.json",tc["phases"][0]["received"])):
            (active/name).write_text(json.dumps(data))
        return root

    def test_stopped_guest_deadline_and_partial_evidence_are_required(self):
        root=self.fixture();self.assertEqual(checker.check_watchdog(root),0)
        for file,key,value in (("vm-stop.json","checkpoint_seen",False),("vm-stop.json","reason","guest_exit"),
                               ("vm-stop.json","qemu_reaped",False),("vm-stop.json","evidence_synced_before_stop",False),
                               ("vm-stop.json","checkpoint_elapsed_ms",1999),("vm-stop.json","escalated",True),
                               ("watchdog-ready.json","state","S"),("watchdog-runner-status.json","proc_status","Pid:\t110\nState:\tS (sleeping)\n")):
            path=root/file;original=path.read_text();data=json.loads(original);data[key]=value;path.write_text(json.dumps(data))
            self.assertEqual(checker.check_watchdog(root),3);path.write_text(original)

    def test_false_guest_completion_and_missing_controls_cannot_pass(self):
        root=self.fixture();(root/"service-result.txt").write_text("success")
        self.assertEqual(checker.check_watchdog(root),3)
        root=self.fixture();path=root/"netns-batch/tc-ingress-001/tc-plain-received.json"
        data=json.loads(path.read_text());data["control_sequences"]=[];path.write_text(json.dumps(data))
        self.assertEqual(checker.check_watchdog(root),3)

if __name__ == "__main__":
    unittest.main()
