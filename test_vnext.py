"""Profile isolation, JS capability limits, and durable recovery across client death."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mcp.server import MCPServer
from code_mode import register
from operation_store import OperationStore
from persistent_jobs import PersistentSessions, read_json, atomic_json, MAX_STREAM

ROOT=Path(__file__).resolve().parent


class CodeModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=OperationStore(self.root/'ops')
        self.bridge=type('Bridge',(),{'operations':self.store,'WORKSPACE':self.root})()
        self.server=MCPServer('test')
        self.count=0
        @self.server.tool()
        def read_file(path:str)->dict:return {'path':path,'value':7}
        @self.server.tool()
        def write_file(path:str,request_id:str)->dict:
            self.count+=1
            return {'request_id':request_id,'path':path,'ok':True}
        register(self.server,self.bridge)

    async def run_code(self,code,key='test'):
        result=await self.server.call_tool('code_mode',{'code':code,'request_id':key})
        return json.loads(result.content[0].text)

    async def test_branches_results_and_no_host_globals(self):
        r=await self.run_code("const r=await tools.read_file({path:'x'}); if(r.value===7)text(r.path); text([typeof process,typeof require,typeof fetch]);")
        self.assertTrue(r['ok'],r)
        self.assertEqual(r['outputs'],['x',['undefined','undefined','undefined']])

    async def test_replay_and_conflict_do_not_repeat_or_corrupt_receipt(self):
        code="text(tools.write_file({path:'x'}))"
        a=await self.run_code(code)
        self.assertTrue(a['ok'],a); self.assertEqual(self.count,1)
        self.assertEqual(a['calls'][0]['request_id'],'test.1')
        b=await self.run_code(code);self.assertTrue(b['replayed']);self.assertEqual(self.count,1)
        c=await self.run_code('text(123)');self.assertFalse(c['ok'])
        self.assertEqual(self.store.get('test',str(self.root))['state'],'completed')

    async def test_partial_execution_is_not_rolled_back_or_replayed(self):
        code="tools.write_file({path:'x'}); throw new Error('after mutation');"
        r=await self.run_code(code);self.assertFalse(r['ok']);self.assertEqual(self.count,1)
        self.assertEqual(len(r['calls']),1)
        await self.run_code(code);self.assertEqual(self.count,1)

    async def test_unknown_tool_and_reserved_request_id(self):
        for i,code in enumerate(["tools.desktop_act({})","tools.write_file({path:'x',request_id:'foreign'})","tools.code_mode({})"]):
            r=await self.run_code(code,str(i));self.assertFalse(r['ok'],r)
        self.assertEqual(self.count,0)

    async def test_call_output_cpu_and_memory_budgets(self):
        for i,code in enumerate(["for(let i=0;i<30;i++)tools.read_file({path:'x'});",
                "text('x'.repeat(40000))", "while(true){}", "text('x'.repeat(100000000))"]):
            r=await self.run_code(code,'limit'+str(i));self.assertFalse(r['ok'],r)
            self.assertLessEqual(len(r['calls']),24)

    async def test_no_result_content_persisted(self):
        await self.run_code("text('synthetic-private-content');throw new Error('synthetic-private-error')")
        data=(self.root/'ops/requests.sqlite3').read_bytes()
        self.assertNotIn(b'synthetic-private-content',data)
        self.assertNotIn(b'synthetic-private-error',data)


class ProfileTests(unittest.TestCase):
    def test_catalogues_and_dispatch_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            for profile in ('core','desktop'):
                code="""import asyncio,json,mcp_server
async def run():
 names=[t.name for t in await mcp_server.mcp.list_tools()]
 forbidden='desktop_act' if 'run_command' in names else 'run_command'
 try: await mcp_server.mcp.call_tool(forbidden,{})
 except Exception: rejected=True
 else: rejected=False
 print(json.dumps({'names':names,'rejected':rejected}))
asyncio.run(run())"""
                r=subprocess.run([sys.executable,'-B','-c',code],cwd=ROOT,env=dict(os.environ,
                    BRIDGE_PROFILE=profile,BRIDGE_MEMORY_DISABLED='1',BRIDGE_OPERATIONS_DIR=tmp),capture_output=True,text=True,check=True)
                data=json.loads(r.stdout)
                self.assertTrue(data['rejected'])
                self.assertEqual('code_mode' in data['names'],profile=='core')
                self.assertEqual('desktop_act' in data['names'],profile=='desktop')
                self.assertEqual('write_file' in data['names'],profile=='core')


@unittest.skipUnless(os.environ.get('BRIDGE_HOST_TESTS')=='1','requires user systemd')
class PersistentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bridge-persist-test-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.jobs=PersistentSessions(self.root/'ops')
        self.addCleanup(self.stop_jobs)
    def stop_jobs(self):
        for meta in self.jobs.list():
            if meta['running']:self.jobs.get(meta['session_id']).stop()
    def start(self,command,timeout=5):
        key=self.jobs.start(self.root,self.root,{},command,timeout)
        return self.jobs.get(key)

    def test_client_exit_then_reconnect_output_and_single_execution(self):
        code="""import os,json,sys
from pathlib import Path
from persistent_jobs import PersistentSessions
from operation_store import OperationStore,TrackedHistory
from memory_store import MemoryService
root=Path(sys.argv[1]); store=OperationStore(root/'ops')
store.reserve('durable',str(root),'run_command',{});store.starting('durable')
h=TrackedHistory(store,'durable',MemoryService({}),store.gate())
jobs=PersistentSessions(root/'ops')
key=jobs.start(root,root,{},"printf x >> count; printf 'Olá'; sleep 2; printf final",5,history=h)
print(key,flush=True)
os._exit(0)
"""
        r=subprocess.run([sys.executable,'-B','-c',code,str(self.root)],cwd=ROOT,env=dict(os.environ,BRIDGE_MEMORY_DISABLED='1'),capture_output=True,text=True,check=True,timeout=15)
        key=r.stdout.strip();j=self.jobs.get(key)
        self.assertTrue(j.read()['running'])
        store=OperationStore(self.root/'ops')
        self.assertEqual(store.transport_blockers(),{})
        self.assertTrue(j.done.wait(6));out=j.read();self.assertEqual(out['execution_state'],'completed',out)
        self.assertEqual(out['stdout'],'Oláfinal')
        self.assertEqual((self.root/'count').read_text(),'x')
        self.assertEqual(store.get('durable',str(self.root))['state'],'completed')
        fresh=PersistentSessions(self.root/'ops').get(key)
        self.assertEqual(fresh.read()['stdout'],'Oláfinal')
        self.assertEqual(fresh.read(out['stdout_offset'])['stdout'],'')

    def test_incremental_output_timeout_and_cancel(self):
        j=self.start("printf ready; sleep 20",1)
        deadline=time.monotonic()+.8
        while not j.read()['stdout'] and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(j.read()['stdout'],'ready')
        self.assertTrue(j.read()['running'])
        self.assertTrue(j.done.wait(5));self.assertEqual(j.read()['execution_state'],'timed_out')
        k=self.start('sleep 20',0);k.stop();self.assertEqual(k.read()['execution_state'],'cancelled')

    def test_output_cap_and_duplicate_worker_never_relaunches(self):
        j=self.start("printf x >> once; head -c 5000000 /dev/zero",10)
        self.assertTrue(j.done.wait(12));self.assertTrue(j.read()['output_truncated'])
        self.assertEqual((j.directory/'stdout.bin').stat().st_size,MAX_STREAM)
        r=subprocess.run([sys.executable,'-B','persistent_job_worker.py',str(j.directory)],cwd=ROOT)
        self.assertEqual(r.returncode,125);self.assertEqual((self.root/'once').read_text(),'x')

    def test_dead_worker_becomes_unknown(self):
        j=self.start('printf done');self.assertTrue(j.done.wait(5))
        state=read_json(j.directory/'state.json');state.update(execution_state='running',owner_pid=99999999)
        atomic_json(j.directory/'state.json',state)
        self.assertEqual(j.read()['execution_state'],'unknown')

    def test_systemd_kills_escaped_descendant_on_stop(self):
        import shlex
        command='import subprocess,pathlib,time; p=subprocess.Popen(["sleep","30"],start_new_session=True);pathlib.Path("child").write_text(str(p.pid));time.sleep(30)'
        j=self.start(sys.executable+' -c '+shlex.quote(command),0)
        end=time.monotonic()+3
        while not (self.root/'child').exists() and time.monotonic()<end:time.sleep(.02)
        pid=int((self.root/'child').read_text());j.stop()
        end=time.monotonic()+3
        while Path(f'/proc/{pid}/stat').exists() and time.monotonic()<end:
            if Path(f'/proc/{pid}/stat').read_text().split(')')[1].strip().startswith('Z'):break
            time.sleep(.02)
        if Path(f'/proc/{pid}/stat').exists():
            self.assertTrue(Path(f'/proc/{pid}/stat').read_text().split(')')[1].strip().startswith('Z'))

    def test_invalid_memory_startup_is_unknown_not_stuck_accepted(self):
        cfg={'memory':{'enabled':True,'path':str(self.root/'unprotected.sqlite')}}
        with patch.dict(os.environ,{'BRIDGE_MEMORY_DISABLED':'0'}):
            key=self.jobs.start(self.root,self.root,cfg,'touch must-not-exist',5)
        j=self.jobs.get(key)
        self.assertTrue(j.done.wait(3));self.assertEqual(j.read()['execution_state'],'unknown')
        self.assertFalse((self.root/'must-not-exist').exists())
