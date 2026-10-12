import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from memory_store import MemoryStore, MemoryService, MemoryError
from command_sessions import Job


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bridge-memory-test-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'private' / 'memory.db'
        self.store = MemoryStore(self.path)

    def save(self, store=None, project='/project/a', **kwargs):
        args = dict(title='Decisão do projeto', body='Usar comando de verificação', source='user: explicit request', request_id='first')
        args.update(kwargs)
        return (store or self.store).save(project, **args)

    def test_restart_persistence_unicode_and_project_isolation(self):
        a = self.save()
        self.save(project='/project/b', body='Outro projeto')
        reopened = MemoryStore(self.path)
        self.assertEqual(reopened.get('/project/a', a['id'])['body'], a['body'])
        self.assertEqual(len(reopened.search('/project/a', 'decisao verificacao')), 1)
        self.assertEqual(reopened.search('/project/b', 'verificação'), [])
        with self.assertRaises(MemoryError):
            reopened.get('/project/b', a['id'])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)

    def test_retry_updates_conflicts_and_index_deletion(self):
        first = self.save()
        self.assertEqual(self.save()['id'], first['id'])
        with self.assertRaises(MemoryError):
            self.save(body='changed')
        changed = self.save(entry_id=first['id'], expected_version=1, request_id='second', body='Substituição nova')
        self.assertEqual(changed['version'], 2)
        self.assertEqual(self.store.search('/project/a', 'verificação'), [])
        self.assertEqual(len(self.store.search('/project/a', 'substituicao')), 1)
        with self.assertRaises(MemoryError):
            self.save(entry_id=first['id'], expected_version=1, request_id='third')
        with self.assertRaises(MemoryError):
            self.store.delete('/project/a', first['id'], 1)
        self.assertTrue(self.store.delete('/project/a', first['id'], 2)['deleted'])
        self.assertFalse(self.store.delete('/project/a', first['id'], 2)['deleted'])
        self.assertEqual(self.store.search('/project/a', 'substituição'), [])
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM requests').fetchone()[0], 0)

    def test_concurrent_instances_deduplicate_and_detect_stale_versions(self):
        def save(_):
            return self.save(store=MemoryStore(self.path))['id']
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(save, range(8)))
        self.assertEqual(len(set(ids)), 1)
        def update(n):
            try:
                self.save(store=MemoryStore(self.path), entry_id=ids[0], expected_version=1, request_id=str(n), body=f'change {n}')
                return 'ok'
            except MemoryError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(update, range(4)))
        self.assertEqual(results.count('ok'), 1)
        self.assertEqual(results.count('conflict'), 3)

    def test_context_bounded_latest_checkpoint_and_capacity(self):
        for n in range(8):
            self.save(request_id=str(n), body='x' * 8000)
        self.save(kind='checkpoint', request_id='old', priority=2, title='old')
        self.save(kind='checkpoint', request_id='new', priority=0, title='new', body='z' * 8000)
        context = self.store.context('/project/a')
        self.assertEqual(context['checkpoint'][0]['title'], 'new')
        self.assertEqual(len(context['checkpoint'][0]['body']), 3000)
        self.assertEqual(len(context['notes']), 5)
        self.assertLess(len(json.dumps(context)), 10000)
        limited = MemoryStore(self.path, max_entries=10)
        with self.assertRaises(MemoryError):
            self.save(store=limited, request_id='overflow')

    def test_query_input_is_literal_and_limits_validated(self):
        self.save()
        self.assertEqual(self.store.search('/project/a', '" OR *'), [])
        for kwargs in ({'limit': 51}, {'query': 'x' * 501}, {'kind': 'sql'}):
            with self.assertRaises(MemoryError):
                self.store.search('/project/a', **kwargs)
        with self.assertRaises(MemoryError):
            self.save(body='x' * 8001)

    def test_sensitive_text_redacted_before_storage(self):
        self.save(body='password="synthetic password" Bearer synthetic-token', source='agent: api_key=synthetic-key')
        with self.store.connection() as db:
            text = str([tuple(r) for r in db.execute('SELECT * FROM entries')])
        for value in ('synthetic password', 'synthetic-token', 'synthetic-key'):
            self.assertNotIn(value, text)

    def test_real_process_death_marks_history_unknown_without_touching_live_owner(self):
        live = self.store.command_start('/project/a')
        code = 'from memory_store import MemoryStore; import sys; print(MemoryStore(sys.argv[1]).command_start("/project/a"))'
        dead = subprocess.check_output([sys.executable, '-B', '-c', code, str(self.path)], text=True).strip()
        reopened = MemoryStore(self.path)
        self.assertEqual(reopened.get('/project/a', dead)['state'], 'unknown')
        self.assertEqual(reopened.get('/project/a', live)['state'], 'running')

    def test_retention_caps_history_preserves_notes_and_running_jobs(self):
        self.save()
        limited = MemoryStore(self.path, max_commands=2)
        live = limited.command_start('/project/a')
        for _ in range(4):
            sid = limited.command_start('/project/a')
            limited.command_finish('/project/a', sid, 0)
        self.assertEqual(len(limited.search('/project/a', kind='command')), 3)
        self.assertEqual(limited.get('/project/a', live)['state'], 'running')
        with limited.connection() as db:
            db.execute("UPDATE entries SET updated_at=0 WHERE kind='command' AND state='completed'")
        self.assertEqual(len(limited.search('/project/a', kind='command')), 1)
        self.assertEqual(len(limited.search('/project/a', kind='note')), 1)

    def test_backup_is_consistent_and_future_schema_refused(self):
        first = self.save()
        destination = self.path.parent / 'backup.db'
        self.store.backup(destination)
        self.assertEqual(MemoryStore(destination).get('/project/a', first['id'])['id'], first['id'])
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            self.store.backup(destination)
        with sqlite3.connect(destination) as db:
            db.execute('PRAGMA user_version=99')
        with self.assertRaisesRegex(MemoryError, 'Unsupported'):
            MemoryStore(destination).search('/project/a')

    def service(self):
        service = MemoryService({})
        service.enabled, service.store = True, self.store
        return service

    def test_unavailable_storage_does_not_mask_real_command_result(self):
        service = self.service()
        receipt = service.begin('/project/a')
        with patch.object(self.store, 'command_finish', side_effect=sqlite3.OperationalError('locked')):
            p = subprocess.Popen([sys.executable, '-c', 'print("executed once")'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            job = Job(p, 3, '/project/a', service, receipt)
            self.addCleanup(job.stop)
            self.assertTrue(job.done.wait(4))
            self.assertEqual(job.read()['returncode'], 0)
            self.assertEqual(job.read()['stdout'].count('executed once'), 1)
            self.assertEqual(job.read()['history']['status'], 'unavailable')
        with patch.object(self.store, 'command_start', side_effect=OSError('full')):
            self.assertEqual(service.begin('/project/a')['status'], 'unavailable')
        with patch.object(self.store, 'context', side_effect=sqlite3.DatabaseError('corrupt')):
            self.assertEqual(service.context('/project/a')['status'], 'unavailable')

    def test_service_requires_protected_storage_and_can_be_disabled(self):
        with patch.dict(os.environ, {'BRIDGE_MEMORY_DISABLED': '0'}):
            with self.assertRaises(MemoryError):
                MemoryService({'memory': {'enabled': True, 'path': str(self.path)}})
        self.assertEqual(MemoryService({}).context('/project/a')['status'], 'disabled')

    def test_database_lock_wait_is_bounded(self):
        self.save()
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        db.execute('BEGIN IMMEDIATE')
        with self.assertRaises(sqlite3.OperationalError):
            self.save(request_id='blocked')
        db.rollback()
        self.assertFalse(self.save(request_id='blocked')['replayed'])


class MCPTests(unittest.TestCase):
    setUp = StoreTests.setUp
    service = StoreTests.service
    def test_mcp_roundtrip_schema_and_policy(self):
        import bridge
        import mcp_server
        from fastapi import HTTPException
        root = Path(self.tmp.name)
        (root / 'a').mkdir()
        (root / 'b').mkdir()
        with patch.object(bridge, 'WORKSPACE', root), patch.object(bridge, 'BRIDGE_CONFIG', {}), patch.object(bridge, 'memory', self.service()):
            saved = mcp_server.memory_save('a', 'Title', 'Evidence', 'agent: test', 'wire')['result']
            self.assertEqual(mcp_server.memory_get('a', saved['id'])['result']['body'], 'Evidence')
            with self.assertRaises(Exception):
                mcp_server.memory_get('b', saved['id'])
            context = mcp_server.get_session_context('a')
            self.assertEqual(context['memory']['notes'][0]['id'], saved['id'])
            with patch.object(bridge, 'BRIDGE_CONFIG', {'filesystem': {'denied_paths': [str(root / 'a')]}}):
                with self.assertRaises(HTTPException):
                    mcp_server.memory_get('a', saved['id'])
            async def wire():
                catalog = {t.name: t for t in await mcp_server.mcp.list_tools()}
                for name in ('memory_save', 'memory_search', 'memory_get', 'memory_delete'):
                    self.assertIn(name, catalog)
                    self.assertIn('cwd', catalog[name].input_schema['required'])
                result = await mcp_server.mcp.call_tool('memory_search', {'cwd': 'a', 'query': 'Evidence'})
                self.assertIsNotNone(result)
            asyncio.run(wire())
