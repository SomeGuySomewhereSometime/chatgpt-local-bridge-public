"""Regression coverage for the host variant's persistence and coordination."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import bridge
import mcp_server
from operation_store import OperationStore
from runtime_support import RuntimeSupport


class ImprovementTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='bridge-improvements-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.private = self.root / 'private'
        self.store = OperationStore(self.private)
        self.runtime = RuntimeSupport(self.store)
        for name, value in [('WORKSPACE', self.work), ('COMMAND_WORKSPACE', self.work),
                            ('BRIDGE_CONFIG', {}), ('operations', self.store)]:
            p = patch.object(bridge, name, value); p.start(); self.addCleanup(p.stop)

    def write(self, content, **kwargs):
        return bridge.write_file(bridge.WriteRequest(path='file.txt', content=content, **kwargs))

    def test_atomic_write_stale_version_and_create_only(self):
        created = self.write('initial', expected_version='missing', request_id='create')
        read = bridge.read_file(bridge.ReadFileRequest(path='file.txt'))
        self.assertEqual(created['version'], read['version'])
        (self.work / 'file.txt').chmod(0o640)
        current = bridge.read_file(bridge.ReadFileRequest(path='file.txt'))['version']
        updated = self.write('new', expected_version=current, request_id='update')
        self.assertEqual((self.work / 'file.txt').stat().st_mode & 0o777, 0o640)
        with self.assertRaises(HTTPException) as error:
            self.write('stale', expected_version=current, request_id='stale')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual((self.work / 'file.txt').read_text(), 'new')
        self.assertNotEqual(updated['version'], current)
        with self.assertRaises(HTTPException):
            self.write('overwrite', expected_version='missing', request_id='duplicate-create')
        self.assertFalse(list(self.work.glob('.bridge-write-*')))

    def test_atomic_replace_failure_keeps_old_content_and_cleans_temp(self):
        self.write('old')
        with patch('runtime_support.os.replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(HTTPException):
                self.write('new', request_id='broken')
        self.assertEqual((self.work / 'file.txt').read_text(), 'old')
        self.assertFalse(list(self.work.glob('.bridge-write-*')))
        self.assertEqual(self.store.get('broken', str(self.work))['state'], 'unknown')

    def test_write_does_not_follow_symlink_or_modify_hardlink_target(self):
        target = self.work / 'target'; target.write_text('original')
        file = self.work / 'file.txt'; file.symlink_to(target)
        with self.assertRaises(HTTPException):
            self.write('symlink write')
        self.assertEqual(target.read_text(), 'original')
        file.unlink(); os.link(target, file)
        self.write('replacement')
        self.assertEqual(target.read_text(), 'original')
        self.assertEqual(file.read_text(), 'replacement')

    def test_lost_reply_replay_survives_another_process_without_content_storage(self):
        initial = self.write('synthetic-secret-file-content', request_id='once')
        # A user's later edit must not be overwritten by retrying the earlier write.
        (self.work / 'file.txt').write_text('user edit')
        code = ('import bridge,json; from pathlib import Path; from operation_store import OperationStore; '
                f'bridge.WORKSPACE=Path({str(self.work)!r}); bridge.BRIDGE_CONFIG={{}}; '
                f'bridge.operations=OperationStore({str(self.private)!r}); '
                'print(json.dumps(bridge.write_file(bridge.WriteRequest(path="file.txt",'
                'content="synthetic-secret-file-content",request_id="once"))))')
        result = subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True, check=True)
        replay = json.loads(result.stdout)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['version'], initial['version'])
        self.assertEqual((self.work / 'file.txt').read_text(), 'user edit')
        self.assertNotIn(b'synthetic-secret-file-content', self.store.path.read_bytes())

    def test_parallel_writes_with_same_version_cannot_both_win(self):
        initial = self.write('base')['version']
        def attempt(i):
            try:
                return self.write(str(i), expected_version=initial, request_id='parallel-' + str(i))['ok']
            except HTTPException as error:
                self.assertEqual(error.status_code, 409)
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, (1, 2)))
        self.assertEqual(results.count(True), 1)

    def test_filesystem_receipts_move_delete_append_and_scope(self):
        bridge.mkdir(bridge.MkdirRequest(path='dir', request_id='mkdir'))
        self.write('content')
        move = bridge.MoveRequest(source='file.txt', destination='dir/file.txt', request_id='move')
        bridge.move(move); self.assertTrue(bridge.move(move)['replayed'])
        delete = bridge.DeleteRequest(path='dir/file.txt', request_id='delete')
        bridge.delete(delete); self.assertTrue(bridge.delete(delete)['replayed'])
        entry = bridge.AIChangeRequest(cwd='dir', task='test', summary='ONE APPEND', request_id='append')
        bridge.append_ai_change(entry); bridge.append_ai_change(entry)
        self.assertEqual((self.work / 'dir/AI_CHANGES.md').read_text().count('ONE APPEND'), 1)
        self.assertEqual(bridge.operation_request('delete')['state'], 'completed')
        with self.assertRaises(HTTPException):
            bridge.operation_request('delete', 'dir')

    def test_request_conflict_and_pre_dispatch_storage_failure(self):
        self.write('one', request_id='same')
        with self.assertRaises(HTTPException):
            self.write('two', request_id='same')
        self.assertEqual((self.work / 'file.txt').read_text(), 'one')
        with patch.object(self.store, 'reserve', side_effect=sqlite3.OperationalError('failure')):
            with self.assertRaises(HTTPException):
                bridge.mkdir(bridge.MkdirRequest(path='never-created', request_id='failed-reserve'))
        self.assertFalse((self.work / 'never-created').exists())

    def test_completion_storage_failure_never_dispatches_a_second_mutation(self):
        with patch.object(self.store, 'finish_mutation', side_effect=sqlite3.OperationalError('failure')):
            with self.assertRaises(HTTPException):
                self.write('effect happened', request_id='lost-completion')
        self.assertEqual((self.work / 'file.txt').read_text(), 'effect happened')
        (self.work / 'file.txt').write_text('user edit')
        replay = self.write('effect happened', request_id='lost-completion')
        self.assertTrue(replay['replayed']); self.assertFalse(replay['ok'])
        self.assertEqual((self.work / 'file.txt').read_text(), 'user edit')

    def test_shared_token_satisfies_combined_desktop_and_editor_reservations(self):
        lease = self.runtime.lease('acquire', 'desktop', 'chat-A')
        self.runtime.lease('acquire', 'editor:blender', 'chat-A', lease['lease_token'])
        with self.runtime.guard(['desktop', 'editor:blender'], 'chat-A', lease['lease_token']):
            pass
        self.runtime.lease('release', 'desktop', 'chat-A', lease['lease_token'])
        with self.assertRaises(HTTPException):
            with self.runtime.guard(['editor:blender'], 'chat-B', ''):
                self.fail('editor lease bypassed')

    def test_upgrade_preserves_legacy_command_receipts_and_fingerprints(self):
        self.store.prepare()
        parameters = {'command': 'touch must-not-run', 'timeout': 60, 'background': False}
        signature = hashlib.sha256(json.dumps([str(self.work), 'run_command', parameters],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with sqlite3.connect(self.store.path) as db:
            db.execute('''CREATE TABLE requests (
                request_id TEXT PRIMARY KEY, signature TEXT NOT NULL, scope TEXT NOT NULL,
                operation TEXT NOT NULL, state TEXT NOT NULL, owner_pid INTEGER, owner_birth TEXT,
                created_at REAL, updated_at REAL, returncode INTEGER, timed_out INTEGER DEFAULT 0,
                session_id TEXT)''')
            db.execute('PRAGMA user_version=1')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('legacy', signature, str(self.work), 'run_command', 'completed', None, None, 1, 1, 0, 0, None))
        replay = bridge.run_command(bridge.CommandRequest(command=parameters['command'], request_id='legacy'))
        self.assertTrue(replay['replayed']); self.assertEqual(replay['state'], 'completed')
        self.assertFalse((self.work / 'must-not-run').exists())
        self.assertEqual(self.store.get('legacy', str(self.work))['returncode'], 0)

    def test_command_stop_respects_project_lease_and_deduplicates(self):
        from unittest.mock import Mock
        lease = self.runtime.lease('acquire', 'path:' + str(self.work), 'chat-A')
        job = Mock(cwd=str(self.work), cleanup_warning=None)
        job.done.is_set.return_value = True
        job.read.return_value = {'running': False, 'returncode': 0}
        with patch.object(bridge.command_sessions, 'get', return_value=job):
            with self.assertRaises(Exception):
                mcp_server.command_session('stop', 'job', request_id='blocked-stop')
            job.stop.assert_not_called()
            args = dict(command='stop', session_id='job', request_id='stop',
                        owner_id='chat-A', lease_token=lease['lease_token'])
            self.assertTrue(mcp_server.command_session(**args)['ok'])
            self.assertTrue(mcp_server.command_session(**args)['replayed'])
            job.stop.assert_called_once()

    def test_project_lease_overlap_credentials_expiry_and_restart(self):
        lease = bridge.resource_lease(bridge.LeaseRequest(action='acquire', resource='path:.', owner_id='chat-A'))
        with self.assertRaises(HTTPException):
            self.write('blocked', request_id='blocked')
        self.write('allowed', request_id='allowed', owner_id='chat-A', lease_token=lease['lease_token'])
        with self.assertRaises(HTTPException):
            bridge.resource_lease(bridge.LeaseRequest(action='acquire', resource='path:file.txt', owner_id='chat-B'))
        restarted = RuntimeSupport(OperationStore(self.private))
        with self.assertRaises(HTTPException):
            with restarted.guard(['path:' + str(self.work / 'file.txt')], 'chat-B', ''):
                self.fail('lease bypassed')
        listed = restarted.lease('list')['leases'][0]
        self.assertNotIn('token', listed)
        restarted.lease('renew', lease['resource'], 'chat-A', lease['lease_token'], 30)
        with self.store.connection() as db:
            db.execute('UPDATE leases SET expires=0')
        with restarted.guard(['path:' + str(self.work)], 'chat-B', ''):
            pass

    def test_desktop_lease_blocks_input_but_allows_observation(self):
        lease = self.runtime.lease('acquire', 'desktop', 'chat-A')
        with patch.object(mcp_server, 'desktop_call', return_value={'ok': True}) as call:
            with self.assertRaises(Exception):
                mcp_server.desktop_act('snapshot', [{'kind': 'focus', 'element': 'e1'}], request_id='blocked-input')
            call.assert_not_called()
            mcp_server.desktop_observe()
            result = mcp_server.desktop_act('snapshot', [{'kind': 'focus', 'element': 'e1'}],
                request_id='input', owner_id='chat-A', lease_token=lease['lease_token'])
            self.assertFalse(result.is_error)
            replay = mcp_server.desktop_act('snapshot', [{'kind': 'focus', 'element': 'e1'}],
                request_id='input', owner_id='chat-A', lease_token=lease['lease_token'])
            self.assertTrue(replay.structured_content['replayed'])
            self.assertEqual(call.call_count, 2)  # One observation, one actual input.

    def test_partial_desktop_receipt_is_unknown_and_not_replayed(self):
        with patch.object(mcp_server, 'desktop_call', return_value={'ok': False, 'completed': 1}) as call:
            mcp_server.desktop_act('snapshot', [{'kind': 'focus', 'element': 'e1'}], request_id='partial')
            replay = mcp_server.desktop_act('snapshot', [{'kind': 'focus', 'element': 'e1'}], request_id='partial')
            self.assertTrue(replay.is_error)
            self.assertEqual(bridge.operation_request('partial')['state'], 'unknown')
            call.assert_called_once()

    def test_project_caches_are_persistent_separate_and_keep_host_home(self):
        a, b = self.work / 'A', self.work / 'B'; a.mkdir(); b.mkdir()
        base = bridge.host_environment()
        before = self.runtime.environment(a, base, prepare=False)[1]
        self.assertFalse(before['prepared']); self.assertFalse(self.private.exists())
        env_a, info = self.runtime.environment(a, base)
        env_b, _ = self.runtime.environment(b, base)
        self.assertEqual(env_a['HOME'], base['HOME'])
        self.assertNotEqual(env_a['PIP_CACHE_DIR'], env_b['PIP_CACHE_DIR'])
        cache_file = Path(env_a['XDG_CACHE_HOME']) / 'persistent'; cache_file.write_text('cache')
        env_again, _ = RuntimeSupport(OperationStore(self.private)).environment(a, base)
        self.assertEqual(env_a, env_again)
        self.assertEqual(cache_file.read_text(), 'cache')
        self.assertEqual(info['project_root'], str(a))

    def test_local_venv_selected_and_managed_venv_has_priority(self):
        project = self.work / 'project'; (project / '.venv/bin').mkdir(parents=True)
        (project / '.venv/bin/python').symlink_to(sys.executable)
        env, info = self.runtime.environment(project, bridge.host_environment())
        self.assertEqual(env['VIRTUAL_ENV'], str(project / '.venv'))
        managed = Path(info['state_directory']) / 'venv'
        (managed / 'bin').mkdir(parents=True); (managed / 'bin/python').symlink_to(sys.executable)
        env, _ = self.runtime.environment(project, bridge.host_environment())
        self.assertEqual(env['VIRTUAL_ENV'], str(managed))
        self.assertTrue(env['PATH'].startswith(str(managed / 'bin')))

    def test_service_prepare_starts_only_inactive_installed_units(self):
        spec = importlib.util.spec_from_file_location('services', Path(__file__).parent / 'ops/mcp-services.py')
        services = importlib.util.module_from_spec(spec); spec.loader.exec_module(services)
        calls = []
        def run(argv, **kwargs):
            calls.append(argv)
            stdout, code = '', 0
            if 'is-active' in argv:
                code = 0 if argv[-1] == 'playwright-browser-mcp.service' else 3
            if 'show' in argv:
                stdout = 'loaded\n'
            return subprocess.CompletedProcess(argv, code, stdout, '')
        with patch.object(services, 'status', side_effect=[{'browser': {'mcp': True}}, {'browser': {'mcp': True}}]), \
             patch.object(services.subprocess, 'run', side_effect=run):
            result = services.prepare('browser')
        self.assertEqual(result['services_started'], ['mcp-tunnel-chatgpt-browser.service'])
        self.assertTrue(result['integration_ready'])
        self.assertFalse(any('restart' in argv for argv in calls))

    def test_service_prepare_missing_unit_and_readiness_are_explicit(self):
        spec = importlib.util.spec_from_file_location('services', Path(__file__).parent / 'ops/mcp-services.py')
        services = importlib.util.module_from_spec(spec); spec.loader.exec_module(services)
        def run(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 3 if 'is-active' in argv else 0, 'not-found\n', '')
        with patch.object(services, 'status', return_value={'blender': {'application': False}}), \
             patch.object(services.subprocess, 'run', side_effect=run):
            result = services.prepare('blender')
        self.assertFalse(result['ok']); self.assertFalse(result['integration_ready'])
        self.assertTrue(result['unavailable_services'])
        with self.assertRaises(ValueError):
            services.prepare('bridge')

    def test_mcp_catalogue_exports_versions_leases_and_preparation(self):
        tools = {tool.name: tool for tool in asyncio.run(mcp_server.mcp.list_tools())}
        for name in ('resource_lease', 'operation_request', 'project_environment', 'integration_prepare'):
            self.assertIn(name, tools)
        self.assertIn('expected_version', tools['write_file'].input_schema['properties'])
        self.assertIn('expected_versions', tools['apply_patch'].input_schema['properties'])
        self.assertIn('project_root', tools['run_command'].input_schema['properties'])


if __name__ == '__main__':
    unittest.main()
