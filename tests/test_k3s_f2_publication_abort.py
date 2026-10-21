"""Receiver publication races and host aborts must not alter the test contract."""
import contextlib
import errno
import io
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import k3s_f2_guest as guest
import k3s_f2_host as host


class ReceiverPublication(unittest.TestCase):
    def exercise(self, protocol="tcp", deny=False, publications=(0, 1), late=False, api_error=False):
        clock = [0.0]
        requests = []
        nonce_payload = []
        snapshots = list(publications)
        def kube(args, end, required=True):
            requests.append(("exec", end))
            payload = f"arena-f2:{protocol}:{args[-1]}"
            nonce_payload.append(payload)
            clock[0] += .2
            row = {"protocol": protocol, "host": "10.42.0.9", "addresses": ["10.42.0.9"], "payload": payload}
            row.update({"outcome": "denied", "errno": errno.ECONNREFUSED} if deny else {"received": payload, "count": 1})
            return subprocess.CompletedProcess([], 0, json.dumps(row).encode(), b"")
        def logs(end):
            requests.append(("logs", end))
            if api_error:
                raise guest.Failure("baseline", "log transport failed", 3)
            clock[0] = end if late else clock[0] + min(.01, (end-clock[0])/2)
            count = snapshots.pop(0) if snapshots else publications[-1]
            return [{"protocol": protocol, "payload": nonce_payload[0]}] * count
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(guest.time, "monotonic", side_effect=lambda: clock[0]), patch.object(guest.time, "sleep", side_effect=sleep):
            try:
                result = guest.traffic_probe(kube, logs, ["fixed"], "deny" if deny else "baseline", 600,
                                             "client", protocol, "10.42.0.9", deny)
                return result, requests, clock[0], None
            except guest.Failure as error:
                return None, requests, clock[0], error

    def test_delayed_tcp_udp_publication_passes_without_retransmission(self):
        for protocol in ["tcp", "udp"]:
            with self.subTest(protocol=protocol):
                row, requests, elapsed, error = self.exercise(protocol=protocol)
                self.assertIsNone(error)
                self.assertEqual(row["receiver_count"], 1)
                self.assertEqual(sum(kind=="exec" for kind,end in requests), 1)
                self.assertEqual(sum(kind=="logs" for kind,end in requests), 2)
                self.assertTrue(all(end == 5 for kind,end in requests))
                self.assertLess(elapsed, 5)

    def test_missing_or_duplicate_nonce_never_passes(self):
        for counts in [(0,), (2,)]:
            with self.subTest(counts=counts):
                row, requests, elapsed, error = self.exercise(publications=counts)
                self.assertIsNone(row)
                self.assertEqual(error.code, 1)
                self.assertLessEqual(elapsed, 5)
                self.assertEqual(sum(kind=="exec" for kind,end in requests), 1)

    def test_late_deny_evidence_is_failure_not_success(self):
        row, requests, elapsed, error = self.exercise(deny=True, publications=(0,0,1))
        self.assertIsNone(row)
        self.assertEqual(error.code, 1)
        self.assertEqual(sum(kind=="exec" for kind,end in requests), 1)
        self.assertEqual(sum(kind=="logs" for kind,end in requests), 3)
        self.assertLess(elapsed, 5)

    def test_deny_without_record_is_observed_to_the_same_deadline(self):
        row, requests, elapsed, error = self.exercise(deny=True, publications=(0,))
        self.assertIsNone(error)
        self.assertEqual(row["receiver_count"], 0)
        self.assertEqual(elapsed, 5)
        self.assertGreater(sum(kind=="logs" for kind,end in requests), 1)
        self.assertTrue(all(end==5 for kind,end in requests))
        self.assertEqual(sum(kind=="exec" for kind,end in requests), 1)

    def test_api_error_and_late_response_remain_environment_error(self):
        for options in [dict(api_error=True),dict(late=True)]:
            with self.subTest(options=options):
                row, requests, elapsed, error = self.exercise(**options)
                self.assertIsNone(row)
                self.assertEqual(error.code, 3)
                self.assertEqual(sum(kind=="logs" for kind,end in requests), 1)


class FixturePublication(unittest.TestCase):
    def test_tcp_udp_publish_receiver_record_before_reply(self):
        spec = importlib.util.spec_from_file_location("f2_publication_fixture", SCRIPTS.parent / "tests/k3s/f2_fixture.py")
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        class Finished(Exception):
            pass
        for protocol in ["tcp", "udp"]:
            events=[]
            listener=Mock()
            listener.__enter__=Mock(return_value=listener)
            listener.__exit__=Mock(return_value=False)
            connection=Mock()
            connection.__enter__=Mock(return_value=connection)
            connection.__exit__=Mock(return_value=False)
            connection.recv.return_value=b"fixed-nonce"
            connection.sendall.side_effect=lambda payload:events.append(("reply",payload))
            listener.sendto.side_effect=lambda payload,peer:events.append(("reply",payload))
            listener.accept.side_effect=[(connection, ("peer",1)),Finished()]
            listener.recvfrom.side_effect=[(b"fixed-nonce",("peer",1)),Finished()]
            def thread(*,target,daemon):
                return Mock(start=Mock(side_effect=target if protocol=="tcp" else None))
            with self.subTest(protocol=protocol), patch.object(fixture.socket,"socket",return_value=listener), \
                    patch.object(fixture.threading,"Thread",side_effect=thread), \
                    patch("builtins.print",side_effect=lambda row,**kwargs:events.append(("record",json.loads(row)))):
                with self.assertRaises(Finished): fixture.serve()
            self.assertEqual(events,[("record",{"protocol":protocol,"payload":"fixed-nonce"}),("reply",b"fixed-nonce")])


class HostAbortSummary(unittest.TestCase):
    def exercise(self, phase, signum=None, bad_cleanup=False):
        with tempfile.TemporaryDirectory() as temporary:
            base=Path(temporary)
            inputs={role:{"role":role,"scenario":"k3s-f2","hostUid":501,"system":"aarch64-linux","fixture_config_digest":"fixed"} for role in host.ROLES}
            clock=[0.0]
            handlers={}
            children={}
            triggered=[False]
            original_exists=Path.exists
            def register(s,handler):
                previous=handlers.get(s, signal.SIG_DFL)
                handlers[s]=handler
                return previous
            def trigger():
                if not triggered[0]:
                    triggered[0]=True
                    if signum is None: clock[0]=600
                    else: handlers[signum](signum,None)
            def exists(path):
                if path.name=="lan.sock":
                    if phase in {"lan_startup", "lan_ready"}:
                        trigger()
                        return phase == "lan_ready"
                    return True
                return original_exists(path)
            def spawn(args,**kwargs):
                out=Path(args[5]);role=out.name
                child=Mock();child.poll.return_value=None;child.returncode=0
                children[role]=child
                (out/"vm-stop.json").write_text(json.dumps({"qemu_reaped":True,"escalated":bad_cleanup and role=="server","evidence_synced":True}))
                if phase=="running" and role=="agent": trigger()
                return child
            with patch.object(host,"check",side_effect=lambda p:(inputs[p.name],{"files":{}})), \
                    patch.object(host.os,"getuid",return_value=501),patch.object(host.os,"cpu_count",return_value=6), \
                    patch.object(host, "execution", return_value={"arch": "aarch64", "accelerator": "tcg", "cpu": "max"}), \
                    patch.object(host,"available_memory_mib",return_value=30000), \
                    patch.object(host.shutil,"disk_usage",return_value=Mock(free=200*1024**3)), \
                    patch.object(host,"qemu_args",return_value=["owned-qemu"]),patch.object(host,"sha",return_value="fixed"), \
                    patch.object(host.subprocess,"run"),patch.object(host.subprocess,"Popen",side_effect=spawn), \
                    patch.object(Path,"exists",exists),patch.object(host.signal,"signal",side_effect=register), \
                    patch.object(host.time,"monotonic",side_effect=lambda:clock[0]):
                with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                    code=host.workflow(501,{role:base/role for role in host.ROLES},"daily",base/"output")
            summary=json.loads((base/"output/summary.json").read_text())
            for child in children.values():
                child.send_signal.assert_called_once_with(signal.SIGTERM)
                child.wait.assert_called_once_with(timeout=15)
            return code,summary,children

    def test_all_signals_keep_130_during_lan_startup_and_live_cluster(self):
        for phase in ["lan_startup","lan_ready","running"]:
            for signum in [signal.SIGINT,signal.SIGTERM,signal.SIGHUP]:
                with self.subTest(phase=phase,signum=signum):
                    code,summary,children=self.exercise(phase,signum)
                    run=summary["runs"][0]
                    self.assertEqual(code,130)
                    self.assertEqual(summary["status"],"cancelled")
                    self.assertEqual(run["status"],"cancelled")
                    self.assertEqual(run["exit_code"],130)
                    self.assertEqual(run["stage"],"lan_startup" if phase=="lan_ready" else phase)
                    self.assertEqual(summary["detail"],run["detail"])
                    self.assertEqual(run["host_abort"]["exit_code"],130)
                    self.assertEqual(len(children),1 if phase.startswith("lan_") else 2)
                    self.assertEqual(run["cleanup_errors"],[])

    def test_global_deadline_keeps_4_during_lan_startup_and_live_cluster(self):
        for phase in ["lan_startup","lan_ready","running"]:
            with self.subTest(phase=phase):
                code,summary,children=self.exercise(phase)
                run=summary["runs"][0]
                self.assertEqual(code,4)
                self.assertEqual(summary["status"],"timeout")
                self.assertEqual(run["status"],"timeout")
                self.assertEqual(run["exit_code"],4)
                self.assertEqual(run["stage"],"lan_startup" if phase=="lan_ready" else phase)
                self.assertEqual(summary["detail"],run["detail"])
                self.assertEqual(run["cleanup_errors"],[])

    def test_bad_cleanup_keeps_priority_and_original_host_abort(self):
        for phase in ["lan_startup","lan_ready","running"]:
            with self.subTest(phase=phase):
                code,summary,children=self.exercise(phase,signal.SIGTERM,bad_cleanup=True)
                run=summary["runs"][0]
                self.assertEqual(code,3)
                self.assertEqual(summary["status"],"environment_error")
                self.assertEqual(run["exit_code"],3)
                self.assertEqual(run["stage"],"cleanup")
                self.assertEqual(run["host_abort"]["exit_code"],130)


if __name__=="__main__":unittest.main()
