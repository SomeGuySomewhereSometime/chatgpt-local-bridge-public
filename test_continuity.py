import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from continuity import ContinuityStore, MemoryError, readonly_context, markdown
from memory_store import MemoryStore, MemoryService
from operation_store import OperationStore
import bridge
import command_sessions
import mcp_server


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'private/memory.sqlite3'
        self.memory = MemoryStore(self.path)
        self.store = ContinuityStore(self.memory)

    def begin(self, request_id='begin', project='/project/a'):
        return self.store.begin(project, title='Câmara', goal='Validar os disparos à nova distância',
            constraints=['Preservar a versão anterior'], source='user: requested task', request_id=request_id)

    def save(self, task, request_id='checkpoint', expected_revision=0, **kwargs):
        args = dict(task_id=task['task_id'], expected_revision=expected_revision,
                    checkpoint={'completed':['Câmara ajustada'], 'unverified':['Disparos'],
                                'next_step':'Testar o alcance'}, state='in_progress',
                    source='agent: file inspection', request_id=request_id)
        args.update(kwargs)
        return self.store.checkpoint('/project/a', **args)

    def test_restart_persistence_and_immutable_revisions(self):
        task = self.begin(); self.save(task)
        self.save(task, request_id='second', expected_revision=1,
                  checkpoint={'summary':'Disparos validados', 'completed':['Câmara e disparos'],
                      'validation':[{'description':'Disparo entre extremos','status':'passed','evidence':'test_wide.py'}]}, state='completed')
        reopened = ContinuityStore(MemoryStore(self.path))
        old = reopened.context('/project/a',task['task_id'],revision=1)
        new = reopened.context('/project/a',task['task_id'])
        self.assertTrue(old['historical']); self.assertEqual(old['checkpoint']['state'],'in_progress')
        self.assertEqual(new['task']['revision'],2);self.assertEqual(new['checkpoint']['state'],'completed')
        self.assertIn('Testar o alcance',markdown(old))
        self.assertEqual(self.path.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777,0o700)

    def test_exact_project_isolation_and_separate_tasks(self):
        first=self.begin();second=self.begin('other');self.begin('foreign','/project/b')
        self.assertNotEqual(first['task_id'],second['task_id'])
        self.assertEqual(len(self.store.context('/project/a')['tasks']),2)
        with self.assertRaises(MemoryError):self.store.context('/project/b',first['task_id'])
        self.assertNotIn('checkpoint',self.store.context('/project/a'))

    def test_idempotent_retries_return_original_revision(self):
        task=self.begin();self.assertEqual(self.begin()['task_id'],task['task_id'])
        saved=self.save(task);self.save(task,request_id='next',expected_revision=1)
        retry=self.save(task)
        self.assertEqual(retry['revision'],saved['revision']);self.assertTrue(retry['replayed'])
        self.assertEqual(self.store.context('/project/a',task['task_id'])['task']['revision'],2)
        with self.assertRaises(MemoryError):self.save(task,checkpoint={'next_step':'Different content'})

    def test_concurrent_instances_accept_only_one_revision(self):
        task=self.begin()
        def save(n):
            store=ContinuityStore(MemoryStore(self.path))
            try:
                return store.checkpoint('/project/a',task_id=task['task_id'],expected_revision=0,
                    checkpoint={'next_step':str(n)},state='in_progress',source='agent: concurrency test',request_id='update-'+str(n))['revision']
            except MemoryError:return 'conflict'
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(save,range(8)))
        self.assertEqual(results.count(1),1);self.assertEqual(results.count('conflict'),7)

    def test_completed_requires_no_pending_or_unverified_work(self):
        task=self.begin()
        for data in ({'unverified':['Disparos']}, {'pending':[{'description':'Build'}]},
                     {'validation':[{'description':'QA','status':'not_run'}]}):
            with self.assertRaises(MemoryError):self.save(task,checkpoint=data,state='completed')
        with self.assertRaises(MemoryError):self.save(task,checkpoint={},state='paused')
        self.assertEqual(self.store.context('/project/a',task['task_id'])['task']['revision'],0)

    def test_schema_validation_bounded_content_and_redaction(self):
        task=self.begin()
        for data in ({'stdout':'raw output'}, {'next_step':'x'*2001},
                     {'next_step':'Check','files':[{'path':'a','content':'file contents'}]},
                     {'next_step':'Check','validation':[{'description':'QA','status':'success'}]},
                     {'next_step':'Check','completed':['x'*1000]*30}):
            with self.assertRaises(MemoryError):self.save(task,checkpoint=data)
        self.save(task,checkpoint={'next_step':'Inspect api_key=synthetic-private password="hidden"'})
        result=json.dumps(self.store.context('/project/a',task['task_id']))
        self.assertNotIn('synthetic-private',result);self.assertNotIn('hidden',result)

    def test_failed_commit_preserves_previous_checkpoint(self):
        task=self.begin();self.save(task)
        connection=self.memory.connection
        @contextmanager
        def fail():
            with connection() as db:
                yield db
                raise sqlite3.OperationalError('synthetic storage failure')
        with patch.object(self.memory,'connection',fail):
            with self.assertRaises(sqlite3.OperationalError):self.save(task,request_id='failed',expected_revision=1)
        result=self.store.context('/project/a',task['task_id'])
        self.assertEqual(result['task']['revision'],1);self.assertEqual(len(result['history']),1)

    def test_legacy_memory_and_schema_version_are_preserved(self):
        note=self.memory.save('/project/a',title='Legacy',body='Evidence',source='user',request_id='note')
        self.begin()
        self.assertEqual(MemoryStore(self.path).get('/project/a',note['id'])['body'],'Evidence')
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)

    def test_panel_read_is_readonly_and_does_not_create_schema(self):
        self.assertEqual(readonly_context(self.path,'/project/a')['status'],'empty')
        self.assertFalse(self.path.parent.exists())
        self.memory.save('/project/a',title='Legacy',body='Evidence',source='user',request_id='note')
        original=self.path.read_bytes()
        self.assertEqual(readonly_context(self.path,'/project/a')['tasks'],[])
        self.assertEqual(self.path.read_bytes(),original)
        task=self.begin();self.save(task)
        self.assertEqual(readonly_context(self.path,'/project/a',task['task_id'])['checkpoint']['revision'],1)

    def test_unsupported_schema_does_not_overwrite_data(self):
        task=self.begin()
        with self.memory.connection() as db:db.execute('UPDATE continuity_meta SET version=99')
        with self.assertRaises(MemoryError):self.store.context('/project/a')
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT goal FROM continuity_tasks WHERE id=?',(task['task_id'],)).fetchone()[0],'Validar os disparos à nova distância')

    def test_capacity_failure_preserves_history(self):
        self.memory.max_entries=1;task=self.begin();self.save(task)
        with self.assertRaises(MemoryError):self.save(task,request_id='overflow',expected_revision=1)
        self.assertEqual(self.store.context('/project/a',task['task_id'])['task']['revision'],1)


class ManagedCommandContinuityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.work=self.root/'work';self.work.mkdir()
        self.memory=MemoryService({});self.memory.enabled=True;self.memory.store=MemoryStore(self.root/'private/memory.sqlite3')
        self.tasks=ContinuityStore(self.memory.store)
        self.task=self.tasks.begin(str(self.work),title='Slow build',goal='Run once and inspect',constraints=[],source='user',request_id='begin')['task_id']
        self.ops=OperationStore(self.root/'operations');self.sessions=command_sessions.CommandSessions();self.addCleanup(self.sessions.close)
        for name,value in [('WORKSPACE',self.work),('COMMAND_WORKSPACE',self.work),('BRIDGE_CONFIG',{}),('memory',self.memory),('operations',self.ops),('command_sessions',self.sessions)]:
            p=patch.object(bridge,name,value);p.start();self.addCleanup(p.stop)
        p=patch.object(command_sessions,'launch_host',side_effect=lambda argv,cwd,environment,**kw:subprocess.Popen(argv,cwd=cwd,env=environment,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True))
        p.start();self.addCleanup(p.stop)

    def command(self, **kw):
        args={'command':"printf x >> count; printf 'ready'; sleep .3; printf 'done'",'request_id':'once','task_id':self.task,'yield_after':.01,'timeout':3}
        args.update(kw)
        return bridge.run_command(bridge.CommandRequest(**args))

    def test_slow_foreground_promotes_same_process_and_poll_does_not_replay(self):
        started=time.monotonic();result=self.command()
        self.assertLess(time.monotonic()-started,.25)
        self.assertTrue(result['running']);self.assertTrue(result['promoted_to_background'])
        self.assertEqual(result['execution_state'],'running')
        replay=self.command();self.assertTrue(replay['replayed']);self.assertEqual(replay['session_id'],result['session_id'])
        with self.assertRaises(BlockingIOError):self.ops.gate(exclusive=True)
        job=self.sessions.get(result['session_id']);self.assertTrue(job.done.wait(3))
        final=job.read();self.assertEqual(final['execution_state'],'completed');self.assertIn('readydone',final['stdout'])
        self.assertEqual((self.work/'count').read_text(),'x')
        context=mcp_server.task_context(str(self.work),self.task)
        self.assertEqual(context['operations'][0]['state'],'completed')
        self.assertNotIn('stdout',json.dumps(context));self.assertFalse(context['automatic_retry'])

    def test_fast_commands_remain_foreground_results(self):
        result=self.command(command='printf fast',yield_after=1)
        self.assertFalse(result['running']);self.assertFalse(result['promoted_to_background'])
        self.assertEqual(result['returncode'],0);self.assertEqual(result['stdout'],'fast')

    def test_large_initial_output_is_paged_without_reexecution(self):
        result=self.command(command="head -c 24000 /dev/zero | tr '\\0' x; printf TAIL",yield_after=1)
        self.assertFalse(result['running']);self.assertTrue(result['output_pending'])
        self.assertEqual(len(result['stdout']),16000)
        tail=mcp_server.command_session(session_id=result['session_id'],stdout_offset=result['stdout_offset'])
        self.assertEqual(result['stdout']+tail['stdout'],'x'*24000+'TAIL')
        self.assertFalse(tail['output_pending'])

    def test_completion_refuses_active_linked_job_and_closed_task_requires_reopen(self):
        result=self.command()
        with self.assertRaises(Exception):
            mcp_server.task_checkpoint(str(self.work),self.task,0,{},'agent','finish',state='completed')
        self.assertTrue(self.sessions.get(result['session_id']).done.wait(3))
        mcp_server.task_checkpoint(str(self.work),self.task,0,{},'agent','finish',state='completed')
        self.assertTrue(self.command()['replayed'])
        with self.assertRaises(Exception):self.command(request_id='after-completion')
        self.assertEqual((self.work/'count').read_text(),'x')

    def test_committed_completion_retry_survives_later_reopen_and_running_work(self):
        result=self.command(yield_after=1)
        self.assertFalse(result['running'])
        mcp_server.task_checkpoint(str(self.work),self.task,0,{},'agent','finish',state='completed')
        mcp_server.task_checkpoint(str(self.work),self.task,1,{'next_step':'Next phase'},'agent','reopen')
        active=self.command(request_id='next-command')
        retry=mcp_server.task_checkpoint(str(self.work),self.task,0,{},'agent','finish',state='completed')
        self.assertTrue(retry['replayed']);self.assertEqual(retry['revision'],1)
        current=mcp_server.task_context(str(self.work),self.task)
        self.assertEqual(current['task']['revision'],2);self.assertEqual(current['task']['state'],'in_progress')
        self.sessions.get(active['session_id']).stop()

    def test_cross_project_task_is_rejected_before_dispatch(self):
        with self.assertRaises(Exception):self.command(task_id='not-this-project')
        self.assertFalse((self.work/'count').exists());self.assertEqual(len(self.sessions.jobs),0)

    def test_task_linkage_failure_does_not_duplicate_execution(self):
        with patch.object(ContinuityStore,'link_operation',side_effect=sqlite3.OperationalError('disk issue')):
            result=self.command(command='printf x >> count',yield_after=1)
        self.assertIn('receipt_warning',result);self.assertEqual((self.work/'count').read_text(),'x')
        self.assertTrue(self.command(command='printf x >> count',yield_after=1)['replayed'])
        self.assertEqual((self.work/'count').read_text(),'x')

    def test_timeout_has_explicit_outcome_and_never_retries(self):
        result=self.command(command='printf x >> count; sleep 10',timeout=1)
        job=self.sessions.get(result['session_id']);self.assertTrue(job.done.wait(4))
        self.assertEqual(job.read()['execution_state'],'timed_out');self.assertTrue(job.read()['timed_out'])
        self.assertFalse(job.read()['automatic_retry']);self.assertEqual((self.work/'count').read_text(),'x')

    def test_history_failure_cannot_leave_job_running_forever(self):
        class History:
            def finish(self,*args,**kwargs):raise OSError('synthetic failure')
        p=subprocess.Popen([sys.executable,'-c','print("done")'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        job=command_sessions.Job(p,2,'.',history=History());self.addCleanup(job.stop)
        self.assertTrue(job.done.wait(3));self.assertEqual(job.read()['execution_state'],'unknown')

    def test_session_context_lists_tasks_without_selecting_another_chat(self):
        context=mcp_server.get_session_context(str(self.work))
        self.assertEqual(context['continuity']['tasks'][0]['id'],self.task)
        self.assertNotIn('checkpoint',context['continuity'])
        self.assertEqual(mcp_server.get_session_context(str(self.work),self.task)['continuity']['task']['id'],self.task)


if __name__=='__main__':unittest.main()
