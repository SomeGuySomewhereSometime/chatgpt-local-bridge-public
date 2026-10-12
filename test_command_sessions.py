import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from command_sessions import Buffer, Job, CommandSessions
from diagnostics import redact


class OutputTests(unittest.TestCase):
    def test_bounded_cursor_reports_loss_and_no_duplicate_text(self):
        b=Buffer(10);b.append('12345678')
        self.assertEqual(b.read(0,4),('1234',4,False))
        b.append('abcdef')
        self.assertEqual(b.read(0,20),('5678abcdef',14,True))
        self.assertEqual(b.read(14),('',14,False))

    def test_real_output_arrives_before_exit_and_unicode_is_preserved(self):
        p=subprocess.Popen([sys.executable,'-u','-c',"import time; print('Olá'); time.sleep(.2); print('fim')"],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        job=Job(p,3,'.');self.addCleanup(job.stop)
        end=time.monotonic()+2
        while job.stdout.end==0 and time.monotonic()<end:time.sleep(.01)
        first=job.read()
        self.assertIn('Olá',first['stdout'])
        self.assertTrue(first['running'])
        self.assertTrue(job.done.wait(3))
        last=job.read(first['stdout_offset'],first['stderr_offset'])
        self.assertNotIn('Olá',last['stdout'])
        self.assertIn('fim',last['stdout'])
        self.assertEqual(last['returncode'],0)

    def test_real_timeout_and_cancellation(self):
        for timeout in (.1,0):
            p=subprocess.Popen(['/usr/bin/sleep','30'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
            job=Job(p,timeout,'.');self.addCleanup(job.stop)
            if timeout:self.assertTrue(job.done.wait(3))
            else:job.stop()
            self.assertIsNotNone(p.poll())
            self.assertEqual(job.timed_out,bool(timeout))

    def test_exited_shell_does_not_leave_children_holding_output_open(self):
        p=subprocess.Popen(['/bin/bash','-c','sleep 30 &'],stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE,start_new_session=True)
        job=Job(p,3,'.');self.addCleanup(job.stop)
        self.assertTrue(job.done.wait(3))
        self.assertTrue(all(not reader.is_alive() for reader in job.readers))

    def test_real_large_output_is_bounded_and_reports_loss(self):
        p=subprocess.Popen([sys.executable,'-c',"print('x'*500000, end='TAIL')"],
                           stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        job=Job(p,3,'.');self.addCleanup(job.stop)
        self.assertTrue(job.done.wait(3))
        result=job.read(limit=262144)
        self.assertTrue(result['output_truncated'])
        self.assertEqual(len(result['stdout']),262144)
        self.assertTrue(result['stdout'].endswith('TAIL'))

    def test_diagnostic_redaction_retains_useful_error_context(self):
        value='DNS failed api_key=synthetic-key password="my password" Bearer abc.def --token xyz https://user:password@example.com/'
        clean=redact(value)
        for secret in ('synthetic-key','my password','abc.def','xyz','user:password'):
            self.assertNotIn(secret,clean)
        self.assertIn('DNS failed',clean)
        self.assertIn('example.com',clean)


@unittest.skipUnless(os.environ.get('BRIDGE_HOST_TESTS')=='1','requires the host user systemd manager')
class HostJobTests(unittest.TestCase):
    def test_managed_job_network_flag_output_and_owned_stop(self):
        with tempfile.TemporaryDirectory(prefix='bridge-session-test-') as tmp:
            root=Path(tmp)
            (root/'private').mkdir();(root/'control').mkdir()
            policy={'filesystem':{'denied_paths':[str(root/'private')],'read_only_paths':[str(root/'control')]},'sandbox':{'network':True}}
            jobs=CommandSessions();self.addCleanup(jobs.close)
            sid=jobs.start(root,root,policy,"printf 'ready\\n'; sleep 30",0)
            job=jobs.get(sid)
            end=time.monotonic()+3
            while job.stdout.end==0 and time.monotonic()<end:time.sleep(.02)
            self.assertIn('ready',job.read()['stdout'])
            self.assertIsNone(job.process.poll())
            job.stop();self.assertIsNotNone(job.process.poll())
            with self.assertRaisesRegex(ValueError,'Unknown command session'):jobs.get('not-owned')
