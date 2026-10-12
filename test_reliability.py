"""Receipt, recovery and bounded-read regressions using disposable data only."""
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

from fastapi import HTTPException
import bridge
import command_sessions
from memory_store import MemoryService
from operation_store import OperationStore
from ops.bridge_watchdog import Watchdog


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='bridge-reliability-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.work = self.root / 'work'
        self.work.mkdir()
        self.store = OperationStore(self.root / 'private')
        self.sessions = command_sessions.CommandSessions()
        self.addCleanup(self.sessions.close)
        for name, value in [('WORKSPACE',self.work), ('COMMAND_WORKSPACE',self.work),
                            ('BRIDGE_CONFIG',{}), ('operations',self.store),
                            ('memory',MemoryService({})), ('command_sessions',self.sessions)]:
            p = patch.object(bridge, name, value); p.start(); self.addCleanup(p.stop)
        if os.environ.get('BRIDGE_HOST_TESTS') != '1':
            # Pure receipt tests can use disposable processes; host lifecycle is tested separately.
            def launch(workspace, cwd, config, argv, **kwargs):
                return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=kwargs.get('timeout',5))
            p = patch.object(bridge, 'run_host', side_effect=launch); p.start(); self.addCleanup(p.stop)
            p = patch.object(command_sessions, 'launch_host', side_effect=lambda argv,cwd,environment,**kw: subprocess.Popen(argv,cwd=cwd,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True))
            p.start(); self.addCleanup(p.stop)

    def run_request(self, **kwargs):
        return bridge.run_command(bridge.CommandRequest(command='printf x >> count; printf done', request_id='one', **kwargs))

    def test_repeated_and_concurrent_requests_execute_once(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.run_request(), range(4)))
        self.assertEqual((self.work/'count').read_text(), 'x')
        self.assertEqual(sum(not r['replayed'] for r in results), 1)
        self.assertEqual(bridge.command_request('one')['state'], 'completed')
        self.assertTrue(bridge.command_request('one')['output_available'])
        self.assertEqual(bridge.command_request('one')['stdout'], 'done')
        self.assertTrue(self.run_request()['replayed'])

    def test_request_conflicts_and_scope_isolation(self):
        self.run_request()
        with self.assertRaises(HTTPException):
            bridge.run_command(bridge.CommandRequest(command='printf changed',request_id='one'))
        other = self.work/'other'; other.mkdir()
        with self.assertRaises(HTTPException):
            bridge.command_request('one',str(other))
        with self.assertRaises(ValueError):
            self.store.reserve('one',str(self.work),'write_file',{'content':'different operation'})
        self.assertEqual((self.work/'count').read_text(), 'x')

    def test_generic_parameters_are_order_independent(self):
        _, fresh = self.store.reserve('generic','scope','write_file',{'path':'a','content':'b'})
        _, replay = self.store.reserve('generic','scope','write_file',{'content':'b','path':'a'})
        self.assertTrue(fresh); self.assertFalse(replay)
        self.assertEqual(self.store.get('generic','scope')['operation'],'write_file')

    def test_real_owner_death_is_unknown_and_never_reclaimed(self):
        code = "from operation_store import OperationStore; import sys; s=OperationStore(sys.argv[1]); s.reserve('dead','scope','run_command',{}); s.starting('dead')"
        subprocess.run([sys.executable,'-B','-c',code,str(self.store.directory)],check=True)
        self.assertEqual(self.store.get('dead','scope')['state'],'unknown')
        _, fresh = self.store.reserve('dead','scope','run_command',{})
        self.assertFalse(fresh)
        self.assertEqual(self.store.blockers(),{'unknown':1})

    def test_before_and_after_dispatch_storage_failures(self):
        with patch.object(self.store,'reserve',side_effect=sqlite3.OperationalError('synthetic failure')):
            with self.assertRaises(HTTPException): self.run_request()
        self.assertFalse((self.work/'count').exists())
        with patch.object(self.store,'finish',side_effect=sqlite3.OperationalError('synthetic failure')):
            result = self.run_request()
        self.assertEqual(result['returncode'],0)
        self.assertIn('receipt_warning',result)
        self.assertEqual((self.work/'count').read_text(),'x')
        self.assertTrue(self.run_request()['replayed'])
        self.assertEqual((self.work/'count').read_text(),'x')

    def test_background_gate_lasts_until_process_completion(self):
        result = bridge.run_command(bridge.CommandRequest(command='sleep 1; printf done',background=True,timeout=5,request_id='bg'))
        with self.assertRaises(BlockingIOError): self.store.gate(exclusive=True)
        replay = bridge.run_command(bridge.CommandRequest(command='sleep 1; printf done',background=True,timeout=5,request_id='bg'))
        self.assertEqual(replay['session_id'],result['session_id'])
        self.assertTrue(self.sessions.get(result['session_id']).done.wait(6))
        self.assertEqual(bridge.command_request('bg')['state'],'completed')
        fd = self.store.gate(exclusive=True); os.close(fd)
        self.assertEqual(bridge.command_request('bg')['stdout'],'done')

    def test_new_store_recovers_metadata_without_output(self):
        self.run_request()
        reopened = OperationStore(self.store.directory)
        self.assertEqual(reopened.get('one',str(self.work))['returncode'],0)
        data = reopened.path.read_bytes()
        self.assertNotIn(b'printf x',data)
        self.assertEqual(reopened.path.stat().st_mode & 0o777,0o600)

    def watchdog(self):
        w = Watchdog(self.store,now=0); self.addCleanup(w.close)
        return w

    def tick(self, w, healthy=False, now=1000, monotonic=300):
        return w.tick({'remote_connected':healthy,'last_success_at':now if healthy else 1},True,True,now=now,monotonic=monotonic)

    def test_watchdog_transient_failure_and_startup_grace(self):
        w = self.watchdog()
        for _ in range(4): self.assertFalse(self.tick(w,monotonic=10)[1])
        self.assertFalse(self.tick(w,True)[1])
        self.assertEqual(w.failures,0)
        self.assertFalse(self.tick(w)[1]); self.assertFalse(self.tick(w)[1])
        result,restart = self.tick(w)
        self.assertTrue(restart); self.assertEqual(result['recovery_state'],'restarting')
        with self.assertRaises(BlockingIOError): self.store.gate()

    def test_watchdog_defers_for_active_or_unknown_and_preserves_budget(self):
        w = self.watchdog(); fd = self.store.gate()
        try:
            for _ in range(3): result,restart = self.tick(w)
            self.assertFalse(restart)
            self.assertEqual(result['recovery_state'],'deferred_active_commands')
        finally: os.close(fd)
        self.store.reserve('uncertain','scope','test',{})
        self.store.finish('uncertain',None,unknown=True)
        result,restart = self.tick(w)
        self.assertFalse(restart)
        self.assertEqual(result['blockers'],{'unknown':1})
        self.assertEqual(w.recoveries,[])

    def test_watchdog_restart_budget_survives_new_monitor(self):
        for now in (1000,1200):
            w = self.watchdog()
            for _ in range(3): result,restart = self.tick(w,now=now)
            self.assertTrue(restart)
            w.close()
        w = self.watchdog()
        for _ in range(3): result,restart = self.tick(w,now=1400)
        self.assertFalse(restart)
        self.assertEqual(result['recovery_state'],'rate_limited')

    def test_watchdog_storage_failure_never_restarts(self):
        w = self.watchdog()
        with patch.object(w,'save',side_effect=OSError('full')):
            for _ in range(3): result,restart = self.tick(w)
        self.assertFalse(restart)
        fd=self.store.gate(); os.close(fd)

    def test_pages_reassemble_unicode_and_long_lines(self):
        content = 'á🙂' * 20000 + '\n' + 'fim\n' * 300
        (self.work/'text').write_text(content)
        args = {'path':'text'}; chunks=[]
        while True:
            result = bridge.read_file(bridge.ReadFileRequest(**args))
            chunks.append(result['content'])
            self.assertLessEqual(result['bytes_read'],65536)
            if not result['has_more']: break
            args.update(byte_offset=result['next_byte_offset'],start_line=result['next_line'],expected_version=result['version'])
        self.assertEqual(''.join(chunks),content)

    def test_changed_file_version_refused_and_full_is_explicit(self):
        file=self.work/'text'; file.write_text('old\n'*300)
        result=bridge.read_file(bridge.ReadFileRequest(path='text'))
        self.assertTrue(result['has_more'])
        file.write_text('new\n'*300)
        with self.assertRaises(HTTPException):
            bridge.read_file(bridge.ReadFileRequest(path='text',expected_version=result['version']))
        self.assertEqual(bridge.read_file(bridge.ReadFileRequest(path='text',full=True))['content'],file.read_text())

    def test_large_file_partial_read_and_output_reduction(self):
        file=self.work/'large'; file.write_text('hello world\n'*200000)
        result=bridge.read_file(bridge.ReadFileRequest(path='large'))
        self.assertEqual(result['bytes_read'],2400)
        self.assertGreater(file.stat().st_size/result['bytes_read'],900)
        with self.assertRaises(HTTPException):
            bridge.read_file(bridge.ReadFileRequest(path='large',full=True))

    def test_search_limits_pagination_literal_regex_and_glob(self):
        (self.work/'a.txt').write_text('a.b\naxb\na.b\n')
        (self.work/'b.md').write_text('a.b\n')
        first=bridge.search(bridge.SearchRequest(query='a.b',glob='*.txt',limit=1))
        self.assertEqual(first['matches'][0]['line'],1)
        self.assertEqual(first['next_offset'],1)
        second=bridge.search(bridge.SearchRequest(query='a.b',glob='*.txt',offset=1))
        self.assertEqual([r['line'] for r in second['matches']],[3])
        regex=bridge.search(bridge.SearchRequest(query='a.b',glob='*.txt',literal=False))
        self.assertEqual(regex['count'],3)
        with self.assertRaises(HTTPException):
            bridge.search(bridge.SearchRequest(query='[',literal=False))

    def test_search_dense_file_is_bounded(self):
        (self.work/'dense').write_text('match\n'*100000)
        result=bridge.search(bridge.SearchRequest(query='match',limit=5))
        self.assertEqual(result['count'],5)
        self.assertTrue(result['truncated'])
        self.assertLess(len(json.dumps(result)),3000)

    def test_operator_resolution_keeps_retry_key_and_unblocks_recovery(self):
        self.store.reserve('unknown','scope','run_command',{})
        self.store.finish('unknown',None,unknown=True)
        self.store.resolve_unknown('unknown','scope')
        self.assertEqual(self.store.blockers(),{})
        row,fresh=self.store.reserve('unknown','scope','run_command',{})
        self.assertFalse(fresh)
        self.assertEqual(row['state'],'resolved')
        self.store.reserve('active','scope','run_command',{})
        with self.assertRaises(ValueError): self.store.resolve_unknown('active','scope')

    def test_invalid_recovery_history_stays_fail_closed_across_monitors(self):
        self.store.prepare()
        (self.store.directory/'watchdog.json').write_text('{invalid')
        for _ in range(2):
            w=self.watchdog()
            for _ in range(3): result,restart=self.tick(w)
            self.assertFalse(restart)
            self.assertEqual(result['recovery_state'],'invalid_recovery_history')
