"""Host execution contracts, using only owned processes and synthetic fixtures."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import bridge
from command_sessions import CommandSessions
from security import host_environment, run_host


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def wait_gone(test, pid):
    deadline = time.monotonic() + 4
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(.02)
    test.assertFalse(alive(pid), f'Owned process {pid} survived cleanup')


@unittest.skipUnless(os.environ.get('BRIDGE_HOST_TESTS') == '1', 'requires the host user systemd manager')
class HostExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bridge-host-contract-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_host_access_outside_workspace_and_environment_scrubbing(self):
        work = self.root / 'work'; work.mkdir()
        marker = self.root / 'outside'; marker.write_text('synthetic-host-marker')
        code = ('import os,json,pathlib; print(json.dumps({'
                f'"marker":pathlib.Path({str(marker)!r}).read_text(),'
                '"home":os.environ["HOME"],"uid":os.getuid(),'
                '"secret_forwarded":"CONTROL_PLANE_API_KEY" in os.environ,'
                '"agent":os.environ.get("SSH_AUTH_SOCK")}))')
        with patch.dict(os.environ, {'CONTROL_PLANE_API_KEY': 'synthetic-secret',
                                    'SSH_AUTH_SOCK': '/synthetic/agent.sock'}):
            result = run_host(work, work, {}, [sys.executable, '-c', code], timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['marker'], 'synthetic-host-marker')
        self.assertEqual(data['home'], str(Path.home()))
        self.assertEqual(data['uid'], os.getuid())
        self.assertFalse(data['secret_forwarded'])
        self.assertEqual(data['agent'], '/synthetic/agent.sock')

    def test_cancellation_kills_descendants_that_start_a_new_session(self):
        jobs = CommandSessions(); self.addCleanup(jobs.close)
        pidfile = self.root / 'child.pid'
        code = ('import subprocess,pathlib,time; '
                'p=subprocess.Popen(["/usr/bin/sleep","30"],start_new_session=True); '
                f'pathlib.Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(30)')
        command = f'{sys.executable} -c ' + __import__('shlex').quote(code)
        session = jobs.start(self.root, self.root, {}, command, 0)
        job = jobs.get(session)
        deadline = time.monotonic() + 3
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(pidfile.exists())
        pid = int(pidfile.read_text()); self.assertTrue(alive(pid))
        job.stop(); wait_gone(self, pid)
        self.assertTrue(job.done.is_set())
        self.assertTrue(all(not reader.is_alive() for reader in job.readers))
        self.assertIsNone(job.cleanup_warning)

    def test_owner_death_cleans_up_its_transient_unit(self):
        pidfile = self.root / 'owner-child.pid'
        child = ('import os,pathlib,time; '
                 f'pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)')
        owner = ('from pathlib import Path; from host_execution import launch_host; '
                 'from security import host_environment; import sys; '
                 f'p=launch_host([sys.executable,"-c",{child!r}],Path({str(self.root)!r}),host_environment())')
        subprocess.run([sys.executable, '-B', '-c', owner], cwd=Path(__file__).parent,
                       check=True, timeout=12)
        self.assertTrue(pidfile.exists())
        wait_gone(self, int(pidfile.read_text()))

    def test_timeout_keeps_partial_output_and_finishes_owned_processes(self):
        with self.assertRaises(HTTPException) as error:
            run_host(self.root, self.root, {},
                     [sys.executable, '-u', '-c', 'import time; print("partial"); time.sleep(30)'], timeout=1)
        self.assertEqual(error.exception.status_code, 408)
        self.assertIn('partial', error.exception.detail['stdout'])

    def test_search_validates_inputs_and_does_not_read_denied_aliases(self):
        private = self.root / 'private'; private.mkdir()
        control = self.root / 'control'; control.mkdir()
        (private / 'secret').write_text('synthetic-denied-search-marker')
        (self.root / 'alias').symlink_to(private, target_is_directory=True)
        (self.root / 'valid').write_text('synthetic-valid-search-marker')
        policy = {'filesystem': {'denied_paths': [str(private)], 'read_only_paths': [str(control)]}}
        with patch.object(bridge, 'WORKSPACE', self.root), patch.object(bridge, 'BRIDGE_CONFIG', policy):
            result = bridge.search(bridge.SearchRequest(query='synthetic-', glob='**'))
            self.assertEqual([m['path'] for m in result['matches']], ['valid'])
            with self.assertRaises(HTTPException):
                bridge.search(bridge.SearchRequest(path='alias/secret', query='synthetic-'))

    def test_patch_cannot_rename_a_protected_file(self):
        control = self.root / 'control'; control.mkdir()
        private = self.root / 'private'; private.mkdir()
        protected = control / 'keep'; protected.write_text('original\n')
        policy = {'filesystem': {'denied_paths': [str(private)], 'read_only_paths': [str(control)]}}
        change = ('diff --git a/control/keep b/moved\nsimilarity index 100%\n'
                  'rename from control/keep\nrename to moved\n')
        with patch.object(bridge, 'WORKSPACE', self.root), patch.object(bridge, 'BRIDGE_CONFIG', policy):
            with self.assertRaises(HTTPException):
                bridge.apply_patch(bridge.PatchRequest(patch=change))
        self.assertEqual(protected.read_text(), 'original\n')
        self.assertFalse((self.root / 'moved').exists())

    def test_git_diagnostics_exclude_denied_tracked_files(self):
        private = self.root / 'private'; private.mkdir()
        control = self.root / 'control'; control.mkdir()
        secret = private / 'secret'; secret.write_text('old\n')
        public = self.root / 'public'; public.write_text('old\n')
        subprocess.run(['git', '-c', 'init.defaultBranch=main', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', '--', 'public', 'private/secret'], check=True)
        secret.write_text('synthetic-denied-git-marker\n')
        public.write_text('visible-public-change\n')
        policy = {'filesystem': {'denied_paths': [str(private)], 'read_only_paths': [str(control)]}}
        with patch.object(bridge, 'WORKSPACE', self.root), patch.object(bridge, 'BRIDGE_CONFIG', policy):
            diff = bridge.git_diff(bridge.GitRequest())
            status = bridge.git_status(bridge.GitRequest())
        self.assertEqual(diff['returncode'], 0, diff)
        self.assertIn('visible-public-change', diff['diff'])
        self.assertNotIn('synthetic-denied-git-marker', diff['diff'])
        self.assertNotIn('private/secret', status['stdout'])
