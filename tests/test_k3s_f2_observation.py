"""Local receiver evidence removes repeated log API startup from the probe budget."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import k3s_f2_guest as guest


def record(payload='nonce'):
    return b'2026-10-21T00:00:00Z stdout F ' + json.dumps({'protocol':'tcp','payload':payload}).encode()+b'\n'


class LocalReceiver(unittest.TestCase):
    def test_append_and_duplicate_counts_without_api(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'0.log';path.write_bytes(b'')
            reader=guest.ReceiverLog(path)
            with patch.object(guest.subprocess,'run',side_effect=AssertionError('unexpected API')):
                self.assertEqual(reader.rows('deny'),[])
                path.write_bytes(record())
                self.assertEqual(reader.rows('deny'),[{'protocol':'tcp','payload':'nonce'}])
                with path.open('ab') as f:f.write(record())
                self.assertEqual(len(reader.rows('deny')),2)

    def test_bad_missing_oversize_and_non_regular_logs_are_environment_errors(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            cases=[b'not-cri\n',record()[:-1],record().replace(b' F ',b' P '),
                   record().replace(b' stdout ',b' stderr '),b'x'*65537,
                   b't stdout F []\n',b't stdout F {"protocol":"icmp","payload":"x"}\n']
            for i,data in enumerate(cases):
                p=root/str(i);p.write_bytes(data)
                with self.subTest(i=i),self.assertRaises(guest.Failure) as e:guest.ReceiverLog(p).rows('deny')
                self.assertEqual(e.exception.code,3)
            p=root/'link';p.symlink_to(root/'0')
            for path in [p,root/'missing',root]:
                with self.subTest(path=path),self.assertRaises(guest.Failure):guest.ReceiverLog(path).rows('deny')

    def test_rotation_truncation_and_rewrite_are_not_zero_receipt(self):
        for operation in ['rotation','truncate','rewrite']:
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as d:
                p=Path(d)/'0.log';p.write_bytes(record())
                reader=guest.ReceiverLog(p);reader.rows('deny')
                if operation=='rotation':
                    p.rename(p.with_suffix('.old'));p.write_bytes(record())
                else:p.write_bytes(b'' if operation=='truncate' else record('other'))
                with self.assertRaises(guest.Failure):reader.rows('deny')

    def test_runtime_path_is_bound_to_the_exact_pod_and_container(self):
        uid='00000000-0000-0000-0000-000000000001';cid='a'*64
        pod={'metadata':{'uid':uid},'status':{'containerStatuses':[{'containerID':'containerd://'+cid}]}}
        status={'id':cid,'state':'CONTAINER_RUNNING','metadata':{'name':'server'},
                'logPath':f'/var/log/pods/arena-f2_server_{uid}/server/0.log'}
        self.assertEqual(str(guest.receiver_log_source(pod,{'status':status})),status['logPath'])
        for field,value in [('id','b'*64),('state','CONTAINER_EXITED'),('logPath','/tmp/unrelated')]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                guest.receiver_log_source(pod,{'status':{**status,field:value}})

    def test_local_observation_waits_to_original_end_and_catches_late_record(self):
        for late in [False,True]:
            with self.subTest(late=late),tempfile.TemporaryDirectory() as d:
                p=Path(d)/'0.log';p.write_bytes(b'')
                reader=guest.ReceiverLog(p);clock=[.5];reads=[]
                def rows(end):
                    reads.append(clock[0])
                    if late and clock[0]>=4.8:p.write_bytes(record())
                    return reader.rows('deny')
                with patch.object(guest.time,'monotonic',side_effect=lambda:clock[0]), \
                        patch.object(guest.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)), \
                        patch.object(guest.subprocess,'run',side_effect=AssertionError('log API restarted')):
                    count=guest.receiver_count_until_published(rows,'tcp','nonce',5,600,'deny')
                self.assertEqual(count,1 if late else 0)
                self.assertLessEqual(clock[0],5)
                if not late:self.assertEqual(clock[0],5)
                self.assertGreater(reads[-1],4.7)


class CommandEvidence(unittest.TestCase):
    def test_timeout_expired_budget_and_late_response_have_exact_argv_and_budget(self):
        for behavior in ['timeout','expired','late']:
            with self.subTest(behavior=behavior),tempfile.TemporaryDirectory() as d:
                out=Path(d);clock=[10.0];args=['k3s','kubectl','-n','arena-f2','logs','server']
                def execute(argv,**kwargs):
                    clock[0]=10.4
                    if behavior=='timeout':raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
                    return subprocess.CompletedProcess(argv,0,b'',b'')
                with patch.object(guest.time,'monotonic',side_effect=lambda:clock[0]),patch.object(guest.subprocess,'run',side_effect=execute) as command:
                    with self.assertRaises(guest.Failure) as error:
                        guest.timed_command(args,9 if behavior=='expired' else 10.32,600,'deny',out,data=b'private-input')
                row=json.loads((out/'last-command-timeout.json').read_text())
                self.assertEqual(row['argv'],args)
                self.assertEqual(row['stage'],'deny')
                self.assertEqual(error.exception.code,3)
                self.assertLessEqual(row['remaining_seconds'],0)
                self.assertAlmostEqual(row['budget_seconds'],0 if behavior=='expired' else .32)
                self.assertNotIn('private-input',json.dumps(row))
                self.assertEqual(command.call_count,0 if behavior=='expired' else 1)

    def test_real_owned_timeout_child_is_reaped_and_next_command_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            out=Path(d);children=[]
            original=subprocess.Popen
            def popen(*args,**kwargs):
                child=original(*args,**kwargs);children.append(child);return child
            with patch.object(subprocess,'Popen',side_effect=popen):
                with self.assertRaises(guest.Failure) as error:
                    guest.timed_command([sys.executable,'-c','import time;time.sleep(30)'],time.monotonic()+.15,time.monotonic()+600,'deny',out)
                self.assertEqual(error.exception.code,3)
                self.assertIsNotNone(children[0].poll())
                cp=guest.timed_command([sys.executable,'-c','print("ready")'],time.monotonic()+5,time.monotonic()+600,'recovery',out)
                self.assertEqual(cp.stdout.strip(),b'ready')
            self.assertTrue(all(c.poll() is not None for c in children))

    def test_workflow_timeout_keeps_exit_four(self):
        with tempfile.TemporaryDirectory() as d,patch.object(guest.time,'monotonic',return_value=600):
            with self.assertRaises(guest.Failure) as error:guest.timed_command(['uname','-r'],700,600,'deny',Path(d))
            self.assertEqual(error.exception.code,4)


if __name__=='__main__':unittest.main()
