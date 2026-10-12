"""Panel observations are read-only and lifecycle actions preserve active work."""
import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from interface import backend
from operation_store import OperationStore


class PanelBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bridge-panel-tests-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'operations'
        self.store = OperationStore(self.directory)
        self.config = {'operations': {'directory': str(self.directory)}}
        env = patch.dict(os.environ, {}, clear=False); env.start(); self.addCleanup(env.stop)
        os.environ.pop('BRIDGE_OPERATIONS_DIR', None)
        for obj, name, value in ((backend, 'settings', lambda: self.config),
                                 (backend, 'PROFILE', self.root / 'profile.yaml')):
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)

    def states(self, installed=True, active='active'):
        return {'bridge': {'name': 'Bridge', 'installed': installed, 'active_state': active}}

    def test_empty_observation_does_not_create_state(self):
        result = backend.registry_snapshot(self.directory)
        self.assertEqual(result['storage_status'], 'empty')
        self.assertFalse(self.directory.exists())

    def test_observation_does_not_migrate_legacy_schema(self):
        self.directory.mkdir()
        path = self.directory / 'requests.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('''CREATE TABLE requests(request_id TEXT, signature TEXT, scope TEXT,
                operation TEXT, state TEXT, owner_pid INTEGER, owner_birth TEXT,
                created_at REAL, updated_at REAL, returncode INTEGER, timed_out INTEGER, session_id TEXT)''')
            db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                ('dead', 'hash', '/project', 'run_command', 'running', 99999999, 'birth', 1, 1, None, 0, None))
            db.commit()
        original = path.read_bytes()
        result = backend.registry_snapshot(self.directory)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(result['receipts'][0]['state'], 'unknown')
        self.assertEqual(result['counts']['unknown'], 1)
        self.assertEqual(result['leases'], [])
        with closing(sqlite3.connect(path)) as db:
            self.assertNotIn('result_json', {row[1] for row in db.execute('PRAGMA table_info(requests)')})

    def test_receipts_are_bounded_and_lease_tokens_are_hidden(self):
        for index in range(45):
            key = 'request-' + str(index)
            self.store.reserve(key, '/project', 'write_file', {'index': index})
            self.store.finish_mutation(key, {'path': 'file.txt', 'ok': True})
        with self.store.connection() as db:
            db.execute('CREATE TABLE leases(resource TEXT,owner TEXT,token TEXT,expires REAL)')
            db.execute('INSERT INTO leases VALUES(?,?,?,?)', ('desktop', 'other-chat', 'DO-NOT-EXPOSE-TOKEN', 9999999999))
        result = backend.registry_snapshot(self.directory)
        self.assertEqual(len(result['receipts']), 40)
        self.assertEqual(result['counts']['completed'], 45)
        self.assertNotIn('DO-NOT-EXPOSE-TOKEN', json.dumps(result))
        self.assertEqual(result['leases'][0]['owner_id'], 'other-chat')

    def test_corrupt_database_is_reported_without_overwriting(self):
        self.directory.mkdir(); path = self.directory / 'requests.sqlite3'; path.write_bytes(b'not sqlite')
        self.assertEqual(backend.registry_snapshot(self.directory)['storage_status'], 'unavailable')
        self.assertEqual(path.read_bytes(), b'not sqlite')

    def test_absent_service_is_not_installed_or_started(self):
        with patch.object(backend, 'service_states', return_value=self.states(False)), patch.object(backend, 'run') as run:
            with self.assertRaises(ValueError):
                backend.control_service('start')
            run.assert_not_called()
        self.assertFalse(self.directory.exists())

    def test_restart_and_stop_refuse_live_work(self):
        gate = self.store.gate()
        try:
            with patch.object(backend, 'service_states', return_value=self.states()), patch.object(backend, 'run') as run:
                for action in ('stop', 'restart'):
                    with self.assertRaises(ValueError):
                        backend.control_service(action)
                run.assert_not_called()
        finally:
            os.close(gate)

    def test_restart_refuses_unknown_receipts(self):
        self.store.reserve('unknown', '/project', 'write_file', {})
        self.store.finish_mutation('unknown', {}, unknown=True)
        with patch.object(backend, 'service_states', return_value=self.states()), patch.object(backend, 'run') as run:
            with self.assertRaises(ValueError):
                backend.control_service('restart')
            run.assert_not_called()

    def test_service_action_targets_only_the_variant(self):
        with patch.object(backend, 'service_states', return_value=self.states()), \
             patch.object(backend, 'run', return_value=subprocess.CompletedProcess([], 0, '', '')) as run:
            backend.control_service('stop')
            self.assertEqual(run.call_args.args[0], ['systemctl', '--user', 'stop', backend.UNIT])
            self.assertNotEqual(backend.UNIT, 'mcp-tunnel-chatgpt-local-bridge.service')

    def test_service_states_do_not_confuse_running_with_ready(self):
        output = ('Id=' + backend.UNIT + '\nLoadState=loaded\nActiveState=active\nSubState=running\n\n'
                  'Id=mcp-tunnel-chatgpt-browser.service\nLoadState=not-found\nActiveState=inactive\nSubState=dead\n')
        with patch.object(backend, 'run', return_value=subprocess.CompletedProcess([], 0, output, '')):
            result = backend.service_states()
        self.assertTrue(result['bridge']['installed'])
        self.assertFalse(result['browser']['installed'])
        self.assertIsNone(result['unity']['installed'])
        self.assertNotIn('integration_ready', result['bridge'])

    def test_unknown_actions_and_original_bridge_prepare_are_refused(self):
        with self.assertRaises(ValueError):
            backend.dispatch({'action': 'arbitrary-shell-command'})
        with self.assertRaises(ValueError):
            backend.dispatch({'action': 'probe', 'component': 'bridge'})

    def test_project_status_is_a_successful_read_only_panel_response(self):
        import bridge
        with patch.object(bridge, 'project_environment', return_value={'project_root': '/project', 'prepared': False}):
            result = backend.dispatch({'action': 'project', 'cwd': '/project', 'command': 'status'})
        self.assertTrue(result['ok'])
        self.assertFalse(result['prepared'])


if __name__ == '__main__':
    unittest.main()
