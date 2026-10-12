"""Acceptance: Workspace + real Bridge MCP, disposable state, simulated ChatGPT DOM."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from coordinator import Coordinator
from work_context import WorkContext


async def main():
    bridge = Path(os.environ.get('BRIDGE_TEST_ROOT', Path(__file__).resolve().parent.parent))
    with tempfile.TemporaryDirectory(prefix='workspace-mcp-') as temporary:
        root=Path(temporary);project=root/'project';project.mkdir();control=root/'control';control.mkdir()
        config={'filesystem':{'denied_paths':[str(root/'private')],'read_only_paths':[str(control)]},
                'memory':{'enabled':True,'path':str(root/'private/memory.sqlite3')},
                'operations':{'directory':str(root/'private/operations')}}
        cfg=control/'config.json';cfg.write_text(json.dumps(config))
        env=dict(os.environ,BRIDGE_CONFIG=str(cfg),BRIDGE_WORKSPACE=str(root),BRIDGE_MEMORY_DISABLED='0',
                 BRIDGE_OPERATIONS_DIR=str(root/'private/operations'),PYTHONDONTWRITEBYTECODE='1')
        c=Coordinator(root/'companion',work=WorkContext(config))
        try:
            base={'url':'https://chatgpt.com/c/workspace-acceptance','client':'test-client','tab':1}
            call=lambda action,**p:c.dispatch({**base,'action':action,**p})
            call('bind',project=str(project),task='');call('observe',state='idle')
            snap={'state':'idle','terminal':True,'document_id':'fixture-document-123456','user_count':0}
            run=call('loop_start',goal='Verify the fixture command using Bridge tools',criteria='The fixture command exits successfully',
                     work_request_id=uuid.uuid4().hex,limit=1,snapshot=snap)
            tid=run['loop']['work_task_id']
            call('loop_result',id=run['send']['id'],state='observed_sent')
            @asynccontextmanager
            async def server():
                params=StdioServerParameters(command=str(bridge/'.venv/bin/python'),args=['-B',str(bridge/'mcp_server.py')],env=env)
                async with stdio_client(params) as (read,write):
                    async with ClientSession(read,write) as session:
                        await asyncio.wait_for(session.initialize(),15)
                        yield session
            async def tool(session,name,args):
                result=await asyncio.wait_for(session.call_tool(name,args),20)
                assert not result.is_error,(name,result)
                return result.structured_content or json.loads(result.content[0].text)
            async with server() as session:
                initial=await tool(session,'task_context',{'cwd':str(project),'task_id':tid})
                assert initial['task']['goal']=='Verify the fixture command using Bridge tools'
                args={'cwd':str(project),'command':'printf workspace-evidence','timeout':5,'yield_after':0,'request_id':'fixture-command','task_id':tid}
                job=await tool(session,'run_command',args)
                done=await tool(session,'command_session',{'session_id':job['session_id'],'wait_ms':3000})
                assert not done['running'] and done['execution_state']=='completed' and done['stdout']=='workspace-evidence'
                feedback=call('loop_tick',snapshot={**snap,'state':'completed','user_count':1,'marker':{'nonce':run['loop']['nonce'],'decision':'DONE'}})
                assert feedback['loop']['feedback_count']==1 and feedback['loop']['used']==1,feedback
                assert 'No checkpoint' in feedback['send']['text'] and 'fixture-command' in feedback['send']['text']
                call('loop_result',id=feedback['send']['id'],state='observed_sent')
                await tool(session,'task_checkpoint',{'cwd':str(project),'task_id':tid,'expected_revision':0,'state':'completed',
                    'source':'MCP acceptance: simulated model','request_id':'fixture-checkpoint',
                    'checkpoint':{'summary':'Fixture verified','completed':['Command returned expected output'],
                        'validation':[{'description':'The fixture command exits successfully','status':'passed','evidence':'[receipt:fixture-command] expected output and exit zero'}]}})
            result=call('loop_tick',snapshot={**snap,'state':'completed','user_count':2,'marker':{'nonce':feedback['loop']['nonce'],'decision':'DONE'}})
            assert result['loop']['state']=='completed',result
            observed=call('status')['work'];assert observed['receipts'][0]['returncode']==0
            assert observed['evidence_checks'][0]['state']=='receipt_confirmed'
            assert observed['context']['selected_revision']==1 and observed['unresolved_receipts']==0
            print('PASS: workspace-created task read by real MCP, tracked command, MCP checkpoint, bounded feedback, observed receipt reference and completion gate',flush=True)
            async with server() as session:
                await tool(session,'task_checkpoint',{'cwd':str(project),'task_id':tid,'expected_revision':1,'state':'paused',
                    'source':'MCP acceptance: resumable checkpoint','request_id':'fixture-pause',
                    'checkpoint':{'summary':'Fixture verified; further work staged','completed':['Fixture command done'],'next_step':'Inspect current results before further changes'}})
                previous=await tool(session,'task_context',{'cwd':str(project),'task_id':tid,'revision':1})
                assert previous['checkpoint']['state']=='completed' and previous['historical']
            call('observe',state='completed')
            update_id=uuid.uuid4().hex
            call('update_add',task_id=tid,id=update_id,text='Preserve the fixture result. Inspect the existing receipt without executing the command again.')
            reviewed=call('work_resume_preview');assert reviewed['resume']['revision']==2
            resumed=call('loop_start',goal=run['loop']['goal'],limit=1,snapshot={**snap,'state':'completed','user_count':2},resume=reviewed['resume'])
            assert resumed['loop']['work_task_id']==tid and resumed['send']['id']!=run['send']['id']
            assert 'Inspect current results' in resumed['send']['text']
            assert 'Preserve the fixture result' in resumed['send']['text']
            call('loop_result',id=resumed['send']['id'],state='observed_sent')
            async with server() as session:
                await tool(session,'task_checkpoint',{'cwd':str(project),'task_id':tid,'expected_revision':2,'state':'completed',
                    'source':'MCP acceptance: update applied','request_id':'fixture-update-checkpoint',
                    'checkpoint':{'summary':'Existing receipt inspected; user update applied without replay',
                        'completed':['Fixture result preserved'],
                        'validation':[{'description':'The fixture command exits successfully','status':'passed','evidence':'[receipt:fixture-command] expected output and exit zero'}]}})
            ended=call('loop_tick',snapshot={**snap,'state':'completed','user_count':3,'marker':{'nonce':resumed['loop']['nonce'],'decision':'DONE'}})
            assert ended['loop']['state']=='completed',ended
            final=call('status')['work'];assert final['updates'][0]['state']=='sent' and final['receipt_count']==1
            print('PASS: queued update joined explicit resume, fresh MCP checkpoint allowed completion, command receipt count remained one',flush=True)
            print('PASS: independent MCP lifetime preserved revisions; explicit resume reused task and allocated a new message without command replay',flush=True)
        finally:c.db.close()

if __name__=='__main__':asyncio.run(main())
